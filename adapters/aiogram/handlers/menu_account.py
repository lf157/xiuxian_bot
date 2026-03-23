"""Main menu and account handlers."""

from __future__ import annotations

import re
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

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
from adapters.aiogram.states.common import MenuAccountFSM

router = Router(name="menu_account")

_USERNAME_ALLOWED = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff]")


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


def _pick_username(user: User, explicit_name: str | None = None) -> str:
    raw = (explicit_name or "").strip() or (user.full_name or "").strip() or (user.username or "").strip()
    raw = _USERNAME_ALLOWED.sub("", raw)
    if len(raw) < 2:
        raw = f"修士{str(user.id)[-6:]}"
    return raw[:16]


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


async def _show_home_message(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    await state.set_state(MenuAccountFSM.menu_home)
    if not uid:
        await message.answer(
            "欢迎来到修仙世界。你还没有角色，先点下方按钮注册。",
            reply_markup=ui.main_menu_keyboard(registered=False),
        )
        return
    stat = await api_get(f"/api/stat/{uid}", actor_uid=uid)
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {})
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await message.answer(text, reply_markup=ui.main_menu_keyboard(registered=True), parse_mode=ParseMode.MARKDOWN)


async def _show_home_query(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _uid_from_query(query, state)
    await state.set_state(MenuAccountFSM.menu_home)
    if not uid:
        await respond_query(
            query,
            "欢迎来到修仙世界。你还没有角色，先点击注册。",
            reply_markup=ui.main_menu_keyboard(registered=False),
        )
        return
    stat = await api_get(f"/api/stat/{uid}", actor_uid=uid)
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {})
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await respond_query(query, text, reply_markup=ui.main_menu_keyboard(registered=True), parse_mode=ParseMode.MARKDOWN)


async def _show_stat_message(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    stat = await api_get(f"/api/stat/{uid}", actor_uid=uid)
    await state.set_state(MenuAccountFSM.viewing_stat)
    if not stat.get("success"):
        await message.answer(f"❌ {_error_text(stat, '状态获取失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await message.answer(
        ui.format_status_card(stat.get("status") or {}),
        reply_markup=ui.main_menu_keyboard(registered=True),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _show_stat_query(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    stat = await api_get(f"/api/stat/{uid}", actor_uid=uid)
    await state.set_state(MenuAccountFSM.viewing_stat)
    if not stat.get("success"):
        await respond_query(query, f"❌ {_error_text(stat, '状态获取失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await respond_query(
        query,
        ui.format_status_card(stat.get("status") or {}),
        reply_markup=ui.main_menu_keyboard(registered=True),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _register_from_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    existing_uid = await resolve_uid(int(message.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await message.answer("你已注册过角色。", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    explicit_name = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            explicit_name = parts[1].strip()
    username = _pick_username(message.from_user, explicit_name=explicit_name)
    payload = {"platform": "telegram", "platform_id": str(message.from_user.id), "username": username}
    result = await api_post("/api/register", payload=payload, actor_uid=None)
    if not result.get("success") and str(result.get("code", "")).upper() == "USERNAME_TAKEN":
        payload["username"] = _pick_username(message.from_user, explicit_name=f"{username}{str(message.from_user.id)[-2:]}")[:16]
        result = await api_post("/api/register", payload=payload, actor_uid=None)
    if result.get("success") or str(result.get("code", "")).upper() == "ALREADY_EXISTS":
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await _show_stat_message(message, state)
        return
    await message.answer(f"❌ 注册失败：{_error_text(result, '注册失败')}", reply_markup=ui.main_menu_keyboard(registered=False))


async def _register_from_query(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        await safe_answer(query)
        return
    existing_uid = await resolve_uid(int(query.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await _show_stat_query(query, state)
        return
    username = _pick_username(query.from_user)
    payload = {"platform": "telegram", "platform_id": str(query.from_user.id), "username": username}
    result = await api_post("/api/register", payload=payload, actor_uid=None)
    if not result.get("success") and str(result.get("code", "")).upper() == "USERNAME_TAKEN":
        payload["username"] = _pick_username(query.from_user, explicit_name=f"{username}{str(query.from_user.id)[-2:]}")[:16]
        result = await api_post("/api/register", payload=payload, actor_uid=None)
    if result.get("success") or str(result.get("code", "")).upper() == "ALREADY_EXISTS":
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await _show_stat_query(query, state)
        return
    await respond_query(query, f"❌ 注册失败：{_error_text(result, '注册失败')}", reply_markup=ui.main_menu_keyboard(registered=False))


@router.message(Command("xian_start", "start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_home_message(message, state)


@router.message(Command("xian_register", "register"))
async def cmd_register(message: Message, state: FSMContext) -> None:
    await _register_from_message(message, state)


@router.message(Command("xian_stat", "xian_status", "stat", "status"))
async def cmd_stat(message: Message, state: FSMContext) -> None:
    await _show_stat_message(message, state)


@router.message(Command("xian_version", "version"))
async def cmd_version(message: Message, state: FSMContext) -> None:
    await state.set_state(MenuAccountFSM.version_view)
    await message.answer("版本信息：\n- Adapter: aiogram-fsm\n- Callback: v3", reply_markup=ui.main_menu_keyboard(registered=True))


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, _ = parsed
    if domain != "menu":
        await handle_expired_callback(query)
        return
    if action in {"home", "back"}:
        await _show_home_query(query, state)
        return
    if action == "register":
        await _register_from_query(query, state)
        return
    if action == "stat":
        await _show_stat_query(query, state)
        return
    await handle_expired_callback(query)
