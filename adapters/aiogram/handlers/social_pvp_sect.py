"""Social/PVP/sect router."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_get,
    api_post,
    handle_expired_callback,
    new_request_id,
    parse_callback,
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


async def _respond_and_ack(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    toast: str | None = None,
) -> None:
    await respond_query(query, text, reply_markup=reply_markup)
    await safe_answer(query, text=toast)


@router.message(Command("xian_chat", "xian_dao", "chat", "dao"))
async def cmd_social(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.social_menu)
    await message.answer(ui.format_social_panel({"uid": uid}), reply_markup=ui.social_menu_keyboard())


@router.message(Command("xian_pvp", "pvp"))
async def cmd_pvp(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.pvp_menu)
    await message.answer(ui.format_pvp_panel(data), reply_markup=ui.social_menu_keyboard())


@router.message(Command("xian_sect", "sect"))
async def cmd_sect(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/sect/member/{uid}", actor_uid=uid)
    await state.update_data(uid=uid)
    await state.set_state(SocialPvpSectFSM.sect_menu)
    await message.answer(ui.format_sect_panel(data), reply_markup=ui.social_menu_keyboard())


@router.callback_query(F.data.startswith("social:") | F.data.startswith("pvp:") | F.data.startswith("sect:"))
async def cb_social_pvp_sect(query: CallbackQuery, state: FSMContext) -> None:
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
        await handle_expired_callback(query)
        return

    if domain == "pvp":
        await state.set_state(SocialPvpSectFSM.pvp_menu)
        if action in {"menu", "refresh"}:
            data = await api_get(f"/api/pvp/opponents/{uid}", params={"limit": 3}, actor_uid=uid)
            await _respond_and_ack(query, ui.format_pvp_panel(data), reply_markup=ui.social_menu_keyboard())
            return
        if action in {"match", "duel"}:
            payload: dict[str, str] = {"user_id": uid}
            if args:
                payload["target_user_id"] = args[0]
            payload["mode"] = action
            result = await api_post("/api/pvp/challenge", payload, actor_uid=uid, request_id=new_request_id())
            await _respond_and_ack(
                query,
                ui.format_pvp_panel(result),
                reply_markup=ui.social_menu_keyboard(),
                toast="挑战完成" if result.get("success") else "挑战失败",
            )
            return
        if action == "history":
            data = await api_get(f"/api/pvp/records/{uid}", params={"limit": 10}, actor_uid=uid)
            await _respond_and_ack(query, ui.format_pvp_panel(data), reply_markup=ui.social_menu_keyboard())
            return
        if action == "claim_daily":
            result = await api_post("/api/pvp/daily_claim", {"user_id": uid}, actor_uid=uid, request_id=new_request_id())
            await _respond_and_ack(
                query,
                ui.format_pvp_panel(result),
                reply_markup=ui.social_menu_keyboard(),
                toast="领取完成" if result.get("success") else "领取失败",
            )
            return
        await handle_expired_callback(query)
        return

    await state.set_state(SocialPvpSectFSM.sect_menu)
    if action == "menu":
        data = await api_get(f"/api/sect/member/{uid}", actor_uid=uid)
        await _respond_and_ack(query, ui.format_sect_panel(data), reply_markup=ui.social_menu_keyboard())
        return
    if action == "create":
        sect_name = args[0] if args else "新宗门"
        result = await api_post("/api/sect/create", {"user_id": uid, "name": sect_name}, actor_uid=uid, request_id=new_request_id())
        await _respond_and_ack(
            query,
            ui.format_sect_panel(result),
            reply_markup=ui.social_menu_keyboard(),
            toast="创建成功" if result.get("success") else "创建失败",
        )
        return
    if action == "leave":
        result = await api_post("/api/sect/leave", {"user_id": uid}, actor_uid=uid, request_id=new_request_id())
        await _respond_and_ack(
            query,
            ui.format_sect_panel(result),
            reply_markup=ui.social_menu_keyboard(),
            toast="退出完成" if result.get("success") else "退出失败",
        )
        return
    if action in {"info", "members", "contribute", "donate", "train"}:
        await _respond_and_ack(
            query,
            ui.format_sect_panel({"uid": uid, "action": action, "arg": args[0] if args else ""}),
            reply_markup=ui.social_menu_keyboard(),
        )
        return
    await handle_expired_callback(query)
