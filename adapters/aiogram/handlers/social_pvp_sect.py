"""Social/PVP/sect router."""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_get,
    api_post,
    handle_expired_callback,
    new_request_id,
    parse_callback,
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.social_admin import SocialPvpSectFSM

router = Router(name="social_pvp_sect")


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


def _pvp_menu_keyboard(opponents: list[dict[str, Any]]) -> Any:
    builder = InlineKeyboardBuilder()
    first_opponent_id = ""
    for row in opponents[:6]:
        opponent_id = str(row.get("user_id", "")).strip()
        if not opponent_id:
            continue
        if not first_opponent_id:
            first_opponent_id = opponent_id
        name = str(row.get("username") or row.get("name") or opponent_id)
        rating = row.get("rating")
        if rating is not None:
            name = f"{name} ({rating})"
        builder.button(text=f"⚔️ 挑战 {name}", callback_data=f"pvp:match:{opponent_id}")
    if first_opponent_id:
        builder.button(text="🔥 快速决斗", callback_data=f"pvp:duel:{first_opponent_id}")
    builder.button(text="📜 对战记录", callback_data="pvp:history")
    builder.button(text="🔄 刷新对手", callback_data="pvp:refresh")
    builder.button(text="📦 每日领取", callback_data="pvp:claim_daily")
    builder.button(text="⬅️ 返回社交", callback_data="social:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    base_rows = [1] * min(len(opponents), 6)
    if first_opponent_id:
        base_rows.append(1)
    builder.adjust(*base_rows, 2, 1, 1, 1)
    return builder.as_markup()


def _format_pvp_menu(data: dict[str, Any]) -> tuple[str, Any]:
    opponents = list(data.get("opponents") or []) if isinstance(data, dict) else []
    lines = ["⚔️ PVP 对战", ""]
    if not opponents:
        lines.append("暂无可挑战对手，请稍后刷新。")
    else:
        for row in opponents[:6]:
            name = row.get("username") or row.get("name") or row.get("user_id")
            rating = row.get("rating", "-")
            lines.append(f"• {name} 评分:{rating}")
    return "\n".join(lines), _pvp_menu_keyboard(opponents)


def _member_in_sect(data: dict[str, Any] | None) -> bool:
    payload = data if isinstance(data, dict) else {}
    sect = payload.get("sect")
    return isinstance(sect, dict) and bool(sect)


def _sect_menu_keyboard(*, in_sect: bool) -> Any:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 刷新信息", callback_data="sect:info")
    if in_sect:
        builder.button(text="📦 每日领取", callback_data="sect:contribute")
        builder.button(text="🟦 捐献100灵石", callback_data="sect:donate:100:0")
        builder.button(text="🧘 宗门修炼", callback_data="sect:train")
        builder.button(text="🚪 离开宗门", callback_data="sect:leave")
    else:
        builder.button(text="🛕 创建宗门", callback_data="sect:create")
        builder.button(text="📜 可加入宗门", callback_data="sect:members")
    builder.button(text="⬅️ 返回社交", callback_data="social:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    if in_sect:
        builder.adjust(2, 2, 1, 1, 1)
    else:
        builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def _format_sect_menu(data: dict[str, Any] | None) -> tuple[str, Any]:
    payload = data if isinstance(data, dict) else {}
    in_sect = _member_in_sect(payload)
    lines = [ui.format_sect_panel(payload), ""]
    if in_sect:
        lines.extend(["可操作：", "• 每日领取资源", "• 捐献灵石", "• 退出宗门"])
    else:
        lines.extend(["当前未加入宗门。", "可先查看“可加入宗门”列表并申请加入。"])
    return "\n".join(lines), _sect_menu_keyboard(in_sect=in_sect)


def _sect_list_keyboard(rows: list[dict[str, Any]]) -> Any:
    builder = InlineKeyboardBuilder()
    joinable_rows: list[dict[str, Any]] = []
    for row in rows:
        if bool(row.get("can_join")):
            joinable_rows.append(row)
    for row in joinable_rows[:6]:
        sect_id = str(row.get("id") or row.get("sect_id") or "").strip()
        if not sect_id:
            continue
        name = str(row.get("name") or sect_id)
        builder.button(text=f"✅ 加入 {name}", callback_data=f"sect:members:join:{sect_id}")
    builder.button(text="🔄 刷新列表", callback_data="sect:members")
    builder.button(text="⬅️ 宗门面板", callback_data="sect:menu")
    builder.button(text="⬅️ 返回社交", callback_data="social:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    joinable_count = min(len(joinable_rows), 6)
    if joinable_count:
        builder.adjust(*([1] * joinable_count), 2, 1, 1)
    else:
        builder.adjust(2, 1, 1)
    return builder.as_markup()


def _format_sect_list(data: dict[str, Any]) -> tuple[str, Any]:
    rows = list(data.get("sects") or []) if isinstance(data, dict) else []
    lines = ["🏯 可加入宗门", ""]
    if not rows:
        lines.append("暂无可展示宗门，请稍后重试。")
        return "\n".join(lines), _sect_list_keyboard([])

    joinable_count = 0
    for row in rows[:10]:
        sect_id = str(row.get("id") or row.get("sect_id") or "-")
        name = str(row.get("name") or sect_id)
        tier = row.get("tier", "-")
        can_join = bool(row.get("can_join"))
        if can_join:
            joinable_count += 1
        state = "✅ 可加入" if can_join else "🔒 条件不足"
        reason = str(row.get("reason") or "").strip()
        line = f"• {name} ({sect_id}) T{tier} {state}"
        if reason and not can_join:
            line += f"｜{reason}"
        lines.append(line)
    lines.extend(["", f"可直接加入：{joinable_count} 个"])
    return "\n".join(lines), _sect_list_keyboard(rows)


async def _load_sect_member(uid: str) -> dict[str, Any]:
    data = await api_get(f"/api/sect/member/{uid}", actor_uid=uid)
    if isinstance(data, dict) and data.get("success"):
        return data
    message = ""
    if isinstance(data, dict):
        message = str(data.get("message") or "")
    return {"success": False, "message": message or "当前未加入宗门。"}


def _format_claim_rewards(result: dict[str, Any]) -> str:
    if not result.get("success"):
        return str(result.get("message") or "领取失败")

    rewards = result.get("rewards") if isinstance(result.get("rewards"), dict) else {}
    lines = ["📦 每日资源领取成功"]
    lines.append(f"• 下品灵石 +{int(rewards.get('copper', 0) or 0)}")
    lines.append(f"• 修为 +{int(rewards.get('exp', 0) or 0)}")
    items = list(rewards.get("items") or [])
    for item in items[:5]:
        name = str(item.get("name") or item.get("item_id") or "物品")
        qty = int(item.get("qty", item.get("quantity", 1)) or 1)
        lines.append(f"• {name} +{qty}")
    mentality_cost = int(rewards.get("mentality_cost", 0) or 0)
    if mentality_cost > 0:
        lines.append(f"• 心境 -{mentality_cost}")
    return "\n".join(lines)


async def _respond_and_ack(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    toast: str | None = None,
) -> None:
    await respond_query(query, text, reply_markup=reply_markup)
    await safe_answer(query, text=toast)


@router.message(Command("xian_chat"))
async def cmd_social(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.social_menu)
    await reply_or_answer(message, ui.format_social_panel({"uid": uid}), reply_markup=ui.social_menu_keyboard())


@router.message(Command("xian_pvp"))
async def cmd_pvp(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
    text, keyboard = _format_pvp_menu(data)
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.pvp_menu)
    await reply_or_answer(message, text, reply_markup=keyboard)


@router.message(Command("xian_sect"))
async def cmd_sect(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await _load_sect_member(uid)
    text, keyboard = _format_sect_menu(data)
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.sect_menu)
    await reply_or_answer(message, text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("social:") | F.data.startswith("pvp:") | F.data.startswith("sect:"))
async def cb_social_pvp_sect(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain not in {"social", "pvp", "sect"}:
        await handle_expired_callback(query)
        return

    uid = await _uid_from_query(query)
    if not uid:
        await _respond_and_ack(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard(), toast="请先注册")
        return

    if domain == "social":
        await state.set_state(SocialPvpSectFSM.social_menu)
        if action == "menu":
            await _respond_and_ack(query, ui.format_social_panel({"uid": uid}), reply_markup=ui.social_menu_keyboard())
            return
        if action in {"chat", "dao", "friend", "reply"}:
            await _respond_and_ack(
                query,
                ui.format_social_panel({"uid": uid, "action": action, "arg": args[0] if args else ""}),
                reply_markup=ui.social_menu_keyboard(),
            )
            return
        await _respond_and_ack(
            query,
            f"该社交按钮已失效，已返回社交面板。\n\n{ui.format_social_panel({'uid': uid})}",
            reply_markup=ui.social_menu_keyboard(),
            toast="已返回社交面板",
        )
        return

    if domain == "pvp":
        await state.set_state(SocialPvpSectFSM.pvp_menu)
        if action in {"menu", "refresh"}:
            data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
            text, keyboard = _format_pvp_menu(data)
            await _respond_and_ack(query, text, reply_markup=keyboard)
            return
        if action in {"match", "duel"}:
            if not args:
                menu_data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
                text, keyboard = _format_pvp_menu(menu_data)
                await _respond_and_ack(
                    query,
                    f"缺少对手参数，请从列表重新选择目标。\n\n{text}",
                    reply_markup=keyboard,
                    toast="请重新选择对手",
                )
                return
            payload: dict[str, str] = {"user_id": uid}
            payload["opponent_id"] = args[0]
            result = await api_post("/api/pvp/challenge", payload, actor_uid=uid, request_id=new_request_id())
            menu_data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
            _, keyboard = _format_pvp_menu(menu_data)
            await _respond_and_ack(
                query,
                result.get("message", "挑战完成"),
                reply_markup=keyboard,
                toast="挑战完成" if result.get("success") else "挑战失败",
            )
            return
        if action == "history":
            data = await api_get(f"/api/pvp/records/{uid}", params={"limit": 10}, actor_uid=uid)
            lines = ["📜 PVP 对战记录", ""]
            records = list(data.get("records") or []) if isinstance(data, dict) else []
            if not records:
                lines.append("暂无记录。")
            else:
                for row in records[:10]:
                    lines.append(str(row.get("summary") or row.get("result") or row))
            menu_data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
            _, keyboard = _format_pvp_menu(menu_data)
            await _respond_and_ack(query, "\n".join(lines), reply_markup=keyboard)
            return
        if action == "claim_daily":
            menu_data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
            _, keyboard = _format_pvp_menu(menu_data)
            await _respond_and_ack(
                query,
                "当前版本未开放 PVP 每日领取。\n你可以先挑战对手提升排名，或查看对战记录复盘。",
                reply_markup=keyboard,
                toast="暂未开放",
            )
            return
        menu_data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
        text, keyboard = _format_pvp_menu(menu_data)
        await _respond_and_ack(
            query,
            f"该 PVP 按钮已失效，已返回对战面板。\n\n{text}",
            reply_markup=keyboard,
            toast="已返回对战面板",
        )
        return

    await state.set_state(SocialPvpSectFSM.sect_menu)
    if action in {"menu", "info"}:
        member_data = await _load_sect_member(uid)
        text, keyboard = _format_sect_menu(member_data)
        await _respond_and_ack(query, text, reply_markup=keyboard)
        return

    if action == "create":
        sect_name = args[0] if args else f"新宗门{uid[-4:]}"
        result = await api_post("/api/sect/create", {"user_id": uid, "name": sect_name}, actor_uid=uid, request_id=new_request_id())
        member_data = await _load_sect_member(uid)
        menu_text, keyboard = _format_sect_menu(member_data)
        result_text = str(result.get("message") or ("创建成功" if result.get("success") else "创建失败"))
        await _respond_and_ack(
            query,
            f"{result_text}\n\n{menu_text}",
            reply_markup=keyboard,
            toast="创建成功" if result.get("success") else "创建失败",
        )
        return

    if action == "members":
        if args and args[0] == "join":
            if len(args) < 2:
                list_data = await api_get(f"/api/sects/available/{uid}", actor_uid=uid)
                text, keyboard = _format_sect_list(list_data)
                await _respond_and_ack(query, text, reply_markup=keyboard, toast="缺少宗门ID")
                return
            sect_id = args[1]
            result = await api_post(
                "/api/sect/join",
                {"user_id": uid, "sect_id": sect_id},
                actor_uid=uid,
                request_id=new_request_id(),
            )
            member_data = await _load_sect_member(uid)
            menu_text, keyboard = _format_sect_menu(member_data)
            result_text = str(result.get("message") or ("加入成功" if result.get("success") else "加入失败"))
            await _respond_and_ack(
                query,
                f"{result_text}\n\n{menu_text}",
                reply_markup=keyboard,
                toast="加入成功" if result.get("success") else "加入失败",
            )
            return

        list_data = await api_get(f"/api/sects/available/{uid}", actor_uid=uid)
        text, keyboard = _format_sect_list(list_data)
        await _respond_and_ack(query, text, reply_markup=keyboard)
        return

    if action == "donate":
        copper = 100
        gold = 0
        amount_invalid = False
        if args:
            try:
                copper = int(args[0])
                gold = int(args[1]) if len(args) > 1 else 0
            except (TypeError, ValueError):
                copper = 100
                gold = 0
                amount_invalid = True
        result = await api_post(
            "/api/sect/donate",
            {"user_id": uid, "copper": max(0, copper), "gold": max(0, gold)},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        member_data = await _load_sect_member(uid)
        menu_text, keyboard = _format_sect_menu(member_data)
        result_text = str(result.get("message") or ("捐献成功" if result.get("success") else "捐献失败"))
        if amount_invalid:
            result_text = f"捐献参数异常，已按默认 100 下品灵石处理。\n{result_text}"
        await _respond_and_ack(
            query,
            f"{result_text}\n\n{menu_text}",
            reply_markup=keyboard,
            toast="捐献成功" if result.get("success") else "捐献失败",
        )
        return

    if action == "contribute":
        result = await api_post("/api/sect/daily_claim", {"user_id": uid}, actor_uid=uid, request_id=new_request_id())
        member_data = await _load_sect_member(uid)
        menu_text, keyboard = _format_sect_menu(member_data)
        claim_text = _format_claim_rewards(result)
        await _respond_and_ack(
            query,
            f"{claim_text}\n\n{menu_text}",
            reply_markup=keyboard,
            toast="领取成功" if result.get("success") else "领取失败",
        )
        return

    if action == "leave":
        result = await api_post("/api/sect/leave", {"user_id": uid}, actor_uid=uid, request_id=new_request_id())
        member_data = await _load_sect_member(uid)
        menu_text, keyboard = _format_sect_menu(member_data)
        result_text = str(result.get("message") or ("退出完成" if result.get("success") else "退出失败"))
        await _respond_and_ack(
            query,
            f"{result_text}\n\n{menu_text}",
            reply_markup=keyboard,
            toast="退出完成" if result.get("success") else "退出失败",
        )
        return

    if action == "train":
        member_data = await _load_sect_member(uid)
        menu_text, keyboard = _format_sect_menu(member_data)
        await _respond_and_ack(
            query,
            f"宗门修炼入口暂未接入，请先使用已有宗门功能。\n\n{menu_text}",
            reply_markup=keyboard,
            toast="暂未开放",
        )
        return

    member_data = await _load_sect_member(uid)
    text, keyboard = _format_sect_menu(member_data)
    await _respond_and_ack(
        query,
        f"该宗门按钮已失效，已返回宗门面板。\n\n{text}",
        reply_markup=keyboard,
        toast="已返回宗门面板",
    )
