"""Skills domain handlers."""

from __future__ import annotations

from typing import Any

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


async def _fetch_skills(uid: str) -> list[dict[str, Any]]:
    data = await api_get(f"/api/skills/{uid}", actor_uid=uid)
    if not data.get("success"):
        return []
    return list(data.get("skills") or [])


async def _show_skill_menu_message(message: Message, state: FSMContext, uid: str) -> None:
    skills = await _fetch_skills(uid)
    await state.set_state(SkillsFSM.listing)
    await message.answer(ui.format_skill_panel(skills), reply_markup=ui.skill_list_keyboard(skills))


async def _show_skill_menu_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    skills = await _fetch_skills(uid)
    await state.set_state(SkillsFSM.listing)
    await respond_query(query, ui.format_skill_panel(skills), reply_markup=ui.skill_list_keyboard(skills))


@router.message(Command("xian_skills", "xian_skill", "skills", "skill"))
async def cmd_skills(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_skill_menu_message(message, state, uid)


@router.callback_query(F.data.startswith("skill:"))
async def cb_skills(query: CallbackQuery, state: FSMContext) -> None:
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
            await handle_expired_callback(query)
            return
        await respond_query(query, f"📘 技能详情: {args[0]}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    if action in {"learn", "equip", "unequip"}:
        if not args:
            await handle_expired_callback(query)
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
            await respond_query(query, f"❌ {_error_text(result, '技能操作失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        await _show_skill_menu_query(query, state, uid)
        return
    await handle_expired_callback(query)
