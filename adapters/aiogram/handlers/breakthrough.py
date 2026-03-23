"""Breakthrough domain handlers."""

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
from adapters.aiogram.states.inventory import BreakthroughFSM

router = Router(name="breakthrough")


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


async def _preview(uid: str, strategy: str, call_for_help: bool) -> dict[str, Any]:
    return await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
            "call_for_help": "true" if call_for_help else "false",
        },
        actor_uid=uid,
    )


async def _show_preview_query(query: CallbackQuery, state: FSMContext, uid: str, strategy: str, call_for_help: bool) -> None:
    preview_resp = await _preview(uid, strategy, call_for_help)
    if not preview_resp.get("success"):
        await respond_query(query, f"❌ {_error_text(preview_resp, '突破预览失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    preview = preview_resp.get("preview") or {}
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await respond_query(
        query,
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help=call_for_help),
    )


async def _show_preview_message(message: Message, state: FSMContext, uid: str, strategy: str, call_for_help: bool) -> None:
    preview_resp = await _preview(uid, strategy, call_for_help)
    if not preview_resp.get("success"):
        await message.answer(f"❌ {_error_text(preview_resp, '突破预览失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    preview = preview_resp.get("preview") or {}
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await message.answer(
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help=call_for_help),
    )


@router.message(Command("xian_break", "xian_breakthrough", "break", "breakthrough"))
async def cmd_breakthrough(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_preview_message(message, state, uid, strategy="normal", call_for_help=True)


@router.callback_query(F.data.startswith("break:"))
async def cb_breakthrough(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "break":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    data = await state.get_data()
    strategy = str(data.get("breakthrough_strategy") or "normal")
    call_for_help = bool(data.get("breakthrough_call_for_help", True))

    if action == "preview":
        picked = str(args[0] if args else strategy or "normal").lower()
        await _show_preview_query(query, state, uid, picked, call_for_help)
        return
    if action == "help_toggle":
        await _show_preview_query(query, state, uid, strategy, not call_for_help)
        return
    if action == "confirm":
        await state.set_state(BreakthroughFSM.confirm)
        payload = {
            "user_id": uid,
            "strategy": strategy,
            "use_pill": False,
            "call_for_help": call_for_help,
            "request_id": new_request_id(),
        }
        result = await api_post("/api/breakthrough", payload=payload, actor_uid=uid, request_id=new_request_id())
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '突破失败')}", reply_markup=ui.breakthrough_keyboard(strategy, call_for_help=call_for_help))
            return
        await state.set_state(BreakthroughFSM.result)
        await respond_query(query, ui.format_breakthrough_result(result), reply_markup=ui.main_menu_keyboard(registered=True))
        return
    if action == "cancel":
        await state.set_state(BreakthroughFSM.selecting_strategy)
        await respond_query(query, "已取消本次突破。", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await handle_expired_callback(query)
