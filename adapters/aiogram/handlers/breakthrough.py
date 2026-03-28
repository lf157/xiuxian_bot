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
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.inventory import BreakthroughFSM

router = Router(name="breakthrough")

_VALID_STRATEGIES = {"steady", "protect", "desperate"}


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    return str(payload.get("message") or payload.get("error") or default).strip()


def _normalize_strategy(raw: Any, default: str = "steady") -> str:
    value = str(raw or default).strip().lower()
    if value in _VALID_STRATEGIES:
        return value
    return default


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


async def _preview(uid: str, strategy: str) -> dict[str, Any]:
    return await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
        },
        actor_uid=uid,
    )


async def _show_preview_query(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    strategy: str,
    notice: str | None = None,
) -> None:
    normalized_strategy = _normalize_strategy(strategy)
    preview_resp = await _preview(uid, normalized_strategy)
    if not preview_resp.get("success"):
        await respond_query(query, f"❌ {_error_text(preview_resp, '突破预览失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    preview = preview_resp.get("preview") or {}
    resource_ok = bool(preview.get("resource_ok", True))
    is_tribulation = bool(preview.get("is_tribulation", False))
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=normalized_strategy,
    )
    text = ui.format_breakthrough_preview(preview)
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(
        query,
        text,
        reply_markup=ui.breakthrough_keyboard(normalized_strategy, resource_ok=resource_ok, is_tribulation=is_tribulation),
    )


async def _show_preview_message(message: Message, state: FSMContext, uid: str, strategy: str) -> None:
    normalized_strategy = _normalize_strategy(strategy)
    preview_resp = await _preview(uid, normalized_strategy)
    if not preview_resp.get("success"):
        await reply_or_answer(message, f"❌ {_error_text(preview_resp, '突破预览失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    preview = preview_resp.get("preview") or {}
    resource_ok = bool(preview.get("resource_ok", True))
    is_tribulation = bool(preview.get("is_tribulation", False))
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=normalized_strategy,
    )
    await reply_or_answer(message,
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(normalized_strategy, resource_ok=resource_ok, is_tribulation=is_tribulation),
    )


@router.message(Command("xian_break"))
async def cmd_breakthrough(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_preview_message(message, state, uid, strategy="steady")


@router.callback_query(F.data.startswith("break:"))
async def cb_breakthrough(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
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
    strategy = _normalize_strategy(data.get("breakthrough_strategy"), default="steady")

    if action == "preview":
        picked_raw = str(args[0] if args else strategy or "steady").strip().lower()
        picked = _normalize_strategy(picked_raw, default=strategy)
        notice = None
        if picked_raw != picked:
            notice = "策略参数异常，已恢复为当前策略。"
        await _show_preview_query(query, state, uid, picked, notice=notice)
        return
    if action == "confirm":
        await state.set_state(BreakthroughFSM.confirm)
        payload = {
            "user_id": uid,
            "strategy": strategy,
            "use_pill": False,
            "request_id": new_request_id(),
        }
        result = await api_post("/api/breakthrough", payload=payload, actor_uid=uid)
        if not result.get("success"):
            error_code = str(result.get("code") or "").strip()
            if error_code == "REALM_TRIAL":
                trial_reqs = result.get("trial_requirements") or {}
                await respond_query(
                    query,
                    f"❌ {_error_text(result, '突破失败')}",
                    reply_markup=ui.breakthrough_trial_keyboard(trial_reqs),
                )
            else:
                await respond_query(query, f"❌ {_error_text(result, '突破失败')}", reply_markup=ui.breakthrough_keyboard(strategy))
            return
        await state.set_state(BreakthroughFSM.result)
        await respond_query(query, ui.format_breakthrough_result(result), reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await _show_preview_query(query, state, uid, strategy, notice="该突破按钮已失效，已返回突破预览。")
