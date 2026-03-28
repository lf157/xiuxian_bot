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
    reject_non_owner,
    reply_or_answer,
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


def _is_cultivating_state(state_raw: Any) -> bool:
    if isinstance(state_raw, bool):
        return state_raw
    if isinstance(state_raw, (int, float)):
        return bool(state_raw)
    text = str(state_raw or "").strip().lower()
    if not text:
        return False
    if text in {"1", "true", "yes", "on", "active", "running", "cultivating", "in_progress", "busy"}:
        return True
    if text in {"0", "false", "no", "off", "idle", "stopped", "stop", "ended", "done", "finished", "completed"}:
        return False
    return True


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


async def _panel_status(uid: str) -> tuple[str, bool]:
    status = await api_get(f"/api/cultivate/status/{uid}", actor_uid=uid)
    if not status.get("success"):
        return f"🧘 修炼\n\n❌ {_error_text(status, '无法读取修炼状态')}", False
    is_active = _is_cultivating_state(status.get("state"))
    gained = int(status.get("current_gain", status.get("exp_gained", 0)) or 0)
    hours_raw = status.get("hours")
    if hours_raw is None:
        elapsed_seconds = int(status.get("elapsed_seconds", 0) or 0)
        hours_raw = float(elapsed_seconds) / 3600 if elapsed_seconds > 0 else 0
    try:
        hours = float(hours_raw or 0)
    except (TypeError, ValueError):
        hours = 0.0
    hours_text = f"{int(hours)}h" if hours.is_integer() else f"{hours:.2f}h"
    is_capped = bool(status.get("is_capped"))
    state_label = "进行中" if is_active else "未开始"
    lines = [
        "🧘 修炼",
        "",
        f"状态：{state_label}",
        f"修炼时长：{hours_text}",
        f"当前修为：{gained}",
    ]
    if is_capped:
        lines.append("⚠️ 修炼收益已达上限，请及时结算。")
    text = "\n".join(lines)
    return text, is_active


async def _show_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    text, is_active = await _panel_status(uid)
    await state.set_state(CultivationFSM.idle)
    await reply_or_answer(message,
        text,
        reply_markup=ui.cultivation_keyboard(is_cultivating=is_active),
    )


async def _show_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    text, is_active = await _panel_status(uid)
    await state.set_state(CultivationFSM.idle)
    await respond_query(
        query,
        text,
        reply_markup=ui.cultivation_keyboard(is_cultivating=is_active),
    )


@router.message(Command("xian_cul"))
async def cmd_cultivation(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_panel_message(message, state, uid)


@router.callback_query(F.data.startswith("cul:"))
async def cb_cultivation(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
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
            await respond_query(query, f"❌ {_error_text(data, '开始修炼失败')}", reply_markup=ui.cultivation_keyboard(is_cultivating=False))
            return
        await state.set_state(CultivationFSM.cultivating)
        await _show_panel_query(query, state, uid)
        return
    if action == "end":
        data = await api_post("/api/cultivate/end", payload={"user_id": uid}, actor_uid=uid)
        if not data.get("success"):
            await respond_query(query, f"❌ {_error_text(data, '结束修炼失败')}", reply_markup=ui.cultivation_keyboard(is_cultivating=True))
            return
        gain = int(data.get("gain") or 0)
        hours = float(data.get("hours") or 0)
        tip = str(data.get("tip") or "").strip()
        can_break = bool(data.get("can_breakthrough"))
        hours_text = f"{int(hours)}h" if hours == int(hours) else f"{hours:.2f}h"
        lines = [
            "🧘 修炼结算",
            "",
            f"⏱️ 修炼时长：{hours_text}",
            f"✨ 获得修为：+{gain:,}",
        ]
        if tip:
            lines.append(f"📝 {tip}")
        if can_break:
            lines.append("⚡ 修为已足够突破，前往「突破」提升境界！")
        await state.set_state(CultivationFSM.idle)
        await respond_query(query, "\n".join(lines), reply_markup=ui.cultivation_keyboard(is_cultivating=False))
        return
    await _show_panel_query(query, state, uid)
