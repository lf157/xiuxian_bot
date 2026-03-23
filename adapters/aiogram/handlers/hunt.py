"""Hunt combat domain handlers."""

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
from adapters.aiogram.states.combat import HuntFSM

router = Router(name="hunt")


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


def _is_session_lost(message: str) -> bool:
    text = (message or "").lower()
    return "战斗已失效" in text or "会话已失效" in text or ("session" in text and "invalid" in text)


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


async def _show_hunt_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    monsters_data = await api_get("/api/monsters", params={"user_id": uid}, actor_uid=uid)
    status_data = await api_get(f"/api/hunt/status/{uid}", actor_uid=uid)
    if not monsters_data.get("success"):
        await message.answer(
            f"❌ {_error_text(monsters_data, '获取怪物列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    monsters = monsters_data.get("monsters") or []
    can_hunt = bool((status_data or {}).get("can_hunt", True))
    cooldown_remaining = int((status_data or {}).get("cooldown_remaining", 0) or 0)
    await state.set_state(HuntFSM.selecting_monster)
    await state.update_data(uid=uid)
    await message.answer(
        ui.format_hunt_panel(monsters, cooldown_remaining=cooldown_remaining, can_hunt=can_hunt),
        reply_markup=ui.hunt_monsters_keyboard(monsters),
    )


async def _show_hunt_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    monsters_data = await api_get("/api/monsters", params={"user_id": uid}, actor_uid=uid)
    status_data = await api_get(f"/api/hunt/status/{uid}", actor_uid=uid)
    if not monsters_data.get("success"):
        await respond_query(
            query,
            f"❌ {_error_text(monsters_data, '获取怪物列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    monsters = monsters_data.get("monsters") or []
    can_hunt = bool((status_data or {}).get("can_hunt", True))
    cooldown_remaining = int((status_data or {}).get("cooldown_remaining", 0) or 0)
    await state.set_state(HuntFSM.selecting_monster)
    await state.update_data(uid=uid)
    await respond_query(
        query,
        ui.format_hunt_panel(monsters, cooldown_remaining=cooldown_remaining, can_hunt=can_hunt),
        reply_markup=ui.hunt_monsters_keyboard(monsters),
    )


async def _do_hunt_action(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    *,
    action: str,
    skill_id: str | None = None,
) -> None:
    data = await state.get_data()
    session_id = str(data.get("hunt_session_id") or "").strip()
    if not session_id:
        await _show_hunt_panel_query(query, state, uid)
        return
    payload: dict[str, Any] = {
        "user_id": uid,
        "session_id": session_id,
        "action": "skill" if action == "act_skill" else "normal",
        "request_id": new_request_id(),
    }
    if skill_id:
        payload["skill_id"] = skill_id
    result = await api_post("/api/hunt/turn/action", payload=payload, actor_uid=uid, request_id=new_request_id())
    if not result.get("success"):
        err = _error_text(result, "战斗处理失败")
        if _is_session_lost(err):
            await state.update_data(hunt_session_id="")
            await state.set_state(HuntFSM.selecting_monster)
            await _show_hunt_panel_query(query, state, uid)
            return
        await respond_query(query, f"❌ {err}", reply_markup=ui.hunt_battle_keyboard([]))
        return
    if result.get("finished") is False:
        await state.set_state(HuntFSM.in_battle)
        await respond_query(
            query,
            ui.format_battle_round(result, title="🦴 狩猎战斗"),
            reply_markup=ui.hunt_battle_keyboard(result.get("active_skills") or []),
        )
        return
    await state.update_data(hunt_session_id="")
    await state.set_state(HuntFSM.settlement)
    await respond_query(
        query,
        ui.format_hunt_settlement(result),
        reply_markup=ui.hunt_settlement_keyboard(),
    )


@router.message(Command("xian_hunt", "hunt"))
async def cmd_hunt(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_hunt_panel_message(message, state, uid)


@router.callback_query(F.data.startswith("hunt:"))
async def cb_hunt(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "hunt":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if action in {"list", "settle"}:
        await _show_hunt_panel_query(query, state, uid)
        return
    if action == "start":
        if not args:
            await handle_expired_callback(query)
            return
        monster_id = args[0]
        result = await api_post(
            "/api/hunt/turn/start",
            payload={"user_id": uid, "monster_id": monster_id},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '发起狩猎失败')}", reply_markup=ui.hunt_settlement_keyboard())
            return
        session_id = str(result.get("session_id") or "").strip()
        if not session_id:
            await respond_query(query, "❌ 未获取到战斗会话，请重试。", reply_markup=ui.hunt_settlement_keyboard())
            return
        await state.set_state(HuntFSM.in_battle)
        await state.update_data(uid=uid, hunt_session_id=session_id)
        await respond_query(
            query,
            ui.format_hunt_battle_open(result),
            reply_markup=ui.hunt_battle_keyboard(result.get("active_skills") or []),
        )
        return
    if action == "act_normal":
        await _do_hunt_action(query, state, uid, action=action)
        return
    if action == "act_skill":
        if not args:
            await handle_expired_callback(query)
            return
        await _do_hunt_action(query, state, uid, action=action, skill_id=args[0])
        return
    if action == "exit":
        await state.update_data(hunt_session_id="")
        await state.set_state(HuntFSM.selecting_monster)
        await respond_query(query, "已结束狩猎。", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await handle_expired_callback(query)
