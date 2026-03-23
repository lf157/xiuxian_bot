"""Cultivation domain handlers."""

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
    parse_callback,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.combat import CultivationFSM

router = Router(name="cultivation")


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


async def _panel_text(uid: str) -> str:
    status = await api_get(f"/api/cultivate/status/{uid}", actor_uid=uid)
    if not status.get("success"):
        return f"🧘 修炼\n\n❌ {_error_text(status, '无法读取修炼状态')}"
    is_active = bool(status.get("is_cultivating"))
    elapsed = int(status.get("elapsed_seconds", 0) or 0)
    gained = int(status.get("exp_gained", 0) or 0)
    state_label = "进行中" if is_active else "未开始"
    return (
        "🧘 修炼\n\n"
        f"状态：{state_label}\n"
        f"时长：{elapsed}s\n"
        f"累计修为：{gained}"
    )


async def _show_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    text = await _panel_text(uid)
    await state.set_state(CultivationFSM.idle)
    await message.answer(
        text,
        reply_markup=ui.main_menu_keyboard(registered=True),
    )


async def _show_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    text = await _panel_text(uid)
    await state.set_state(CultivationFSM.idle)
    await respond_query(
        query,
        text,
        reply_markup=ui.main_menu_keyboard(registered=True),
    )


@router.message(Command("xian_cul", "xian_cultivate", "cul", "cultivate"))
async def cmd_cultivation(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_panel_message(message, state, uid)


@router.callback_query(F.data.startswith("cul:"))
async def cb_cultivation(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, _ = parsed
    if domain != "cul":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if action == "status":
        await _show_panel_query(query, state, uid)
        return
    if action == "start":
        data = await api_post("/api/cultivate/start", payload={"user_id": uid}, actor_uid=uid)
        if not data.get("success"):
            await respond_query(query, f"❌ {_error_text(data, '开始修炼失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        await state.set_state(CultivationFSM.cultivating)
        await _show_panel_query(query, state, uid)
        return
    if action == "end":
        data = await api_post("/api/cultivate/end", payload={"user_id": uid}, actor_uid=uid)
        if not data.get("success"):
            await respond_query(query, f"❌ {_error_text(data, '结束修炼失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        await state.set_state(CultivationFSM.reward_preview)
        await _show_panel_query(query, state, uid)
        await state.set_state(CultivationFSM.idle)
        return
    await handle_expired_callback(query)
