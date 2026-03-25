"""Skills domain handlers."""

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
from adapters.aiogram.states.inventory import SkillsFSM

router = Router(name="skills")


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


async def _uid_from_message(message: Message, state: FSMContext) -> str | None:
    if message.from_user is None:
        return None
    data = await state.get_data()
    uid = str(data.get("uid") or "").strip()
    if uid:
        return uid
    uid = await resolve_uid(int(message.from_user.id))
    if uid:
        await state.update_data(uid=uid)
    return uid


async def _uid_from_query(query: CallbackQuery, state: FSMContext) -> str | None:
    if query.from_user is None:
        return None
    data = await state.get_data()
    uid = str(data.get("uid") or "").strip()
    if uid:
        return uid
    uid = await resolve_uid(int(query.from_user.id))
    if uid:
        await state.update_data(uid=uid)
    return uid


async def _fetch_skills(uid: str) -> tuple[list[dict[str, Any]], str | None]:
    data = await api_get(f"/api/skills/{uid}", actor_uid=uid)
    if not data.get("success"):
        return [], _error_text(data, "技能数据获取失败")
    learned_raw = list(data.get("learned") or data.get("skills") or [])
    unlockable_raw = list(data.get("unlockable") or [])

    def _normalize(row: dict[str, Any], *, learned: bool) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        skill_id = str(row.get("skill_id") or row.get("id") or "").strip()
        if not skill_id:
            return None
        name = str(row.get("name") or skill_id)
        equipped = bool(row.get("equipped"))
        item: dict[str, Any] = dict(row)
        item["id"] = skill_id
        item["skill_id"] = skill_id
        item["name"] = name
        item["learned"] = learned
        item["equipped"] = equipped
        if learned and equipped:
            item["name"] = f"✅ {name}"
        elif learned:
            item["name"] = f"📘 {name}"
        else:
            item["name"] = f"🆕 {name}"
        return item

    skills: list[dict[str, Any]] = []
    learned_ids: set[str] = set()
    for row in learned_raw:
        item = _normalize(row, learned=True)
        if item is None:
            continue
        learned_ids.add(item["id"])
        skills.append(item)

    for row in unlockable_raw:
        item = _normalize(row, learned=False)
        if item is None:
            continue
        if item["id"] in learned_ids:
            continue
        skills.append(item)
    return skills, None


def _learn_only_keyboard(skill_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 学习", callback_data=f"skill:learn:{skill_id}")
    builder.button(text="📘 返回技能列表", callback_data="skill:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


async def _show_skill_menu_message(message: Message, state: FSMContext, uid: str) -> None:
    skills, err = await _fetch_skills(uid)
    await state.set_state(SkillsFSM.listing)
    if err:
        await reply_or_answer(message, f"❌ {err}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await reply_or_answer(message, ui.format_skill_panel(skills), reply_markup=ui.skill_list_keyboard(skills))


async def _show_skill_menu_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    skills, err = await _fetch_skills(uid)
    await state.set_state(SkillsFSM.listing)
    if err:
        await respond_query(query, f"❌ {err}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await respond_query(query, ui.format_skill_panel(skills), reply_markup=ui.skill_list_keyboard(skills))


async def _show_skill_menu_with_hint(query: CallbackQuery, state: FSMContext, uid: str, hint: str) -> None:
    skills, err = await _fetch_skills(uid)
    await state.set_state(SkillsFSM.listing)
    if err:
        await respond_query(
            query,
            f"{hint}\n\n❌ {err}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    await respond_query(
        query,
        f"{hint}\n\n{ui.format_skill_panel(skills)}",
        reply_markup=ui.skill_list_keyboard(skills),
    )


@router.message(Command("xian_skills"))
async def cmd_skills(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_skill_menu_message(message, state, uid)


@router.callback_query(F.data.startswith("skill:"))
async def cb_skills(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "skill":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if action == "list":
        await _show_skill_menu_query(query, state, uid)
        return
    if action == "detail":
        if not args:
            await _show_skill_menu_with_hint(query, state, uid, "缺少技能参数，已返回技能列表，请重新选择技能。")
            return
        skill_id = args[0]
        skills, err = await _fetch_skills(uid)
        if err:
            await respond_query(query, f"❌ {err}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        row = next((item for item in skills if str(item.get("id")) == skill_id), None)
        if row is None:
            await _show_skill_menu_with_hint(query, state, uid, f"未找到技能 {skill_id}，可能已变更，请从列表重新进入。")
            return
        learned = bool(row.get("learned"))
        name = str(row.get("name") or skill_id).strip()
        text_lines = [f"📘 技能详情：{name}", f"技能ID：{skill_id}"]
        if row.get("desc"):
            text_lines.append(str(row.get("desc")))
        if row.get("unlock_rank") is not None:
            text_lines.append(f"解锁境界：{row.get('unlock_rank')}")
        text_lines.append(f"状态：{'已学会' if learned else '未学会'}")
        await respond_query(
            query,
            "\n".join(text_lines),
            reply_markup=ui.skill_detail_keyboard(skill_id) if learned else _learn_only_keyboard(skill_id),
        )
        return
    if action in {"learn", "equip", "unequip"}:
        if not args:
            await _show_skill_menu_with_hint(query, state, uid, "缺少技能参数，已返回技能列表，请重新操作。")
            return
        skill_id = args[0]
        endpoint = f"/api/skills/{action}"
        result = await api_post(
            endpoint,
            payload={"user_id": uid, "skill_id": skill_id},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await _show_skill_menu_with_hint(query, state, uid, f"❌ {_error_text(result, '技能操作失败')}")
            return
        await _show_skill_menu_query(query, state, uid)
        return
    await _show_skill_menu_with_hint(query, state, uid, "该技能按钮已失效，已返回技能列表。")
