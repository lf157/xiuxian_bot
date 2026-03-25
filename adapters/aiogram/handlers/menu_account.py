"""Main menu and account handlers."""

from __future__ import annotations

import asyncio
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
    reject_non_owner,
    reply_or_answer,
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


def _merge_quests(quests_data: dict) -> list | None:
    """Merge quest progress rows with quest_defs to get name field."""
    if not quests_data.get("success"):
        return None
    rows = list(quests_data.get("quests") or [])
    defs = {d["id"]: d for d in (quests_data.get("quest_defs") or [])}
    merged = []
    for row in rows:
        qdef = defs.get(str(row.get("quest_id") or ""))
        entry = dict(row)
        if qdef:
            entry.setdefault("name", qdef.get("name", ""))
            entry.setdefault("goal", qdef.get("goal", 1))
        merged.append(entry)
    return merged or None


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
        await reply_or_answer(message,
            "欢迎来到修仙世界。你还没有角色，先点下方按钮注册。",
            reply_markup=ui.main_menu_keyboard(registered=False),
        )
        return
    stat, quests_data = await asyncio.gather(
        api_get(f"/api/stat/{uid}", actor_uid=uid),
        api_get(f"/api/quests/{uid}", actor_uid=uid),
    )
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {}, quests=_merge_quests(quests_data))
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await reply_or_answer(message, text, reply_markup=ui.main_menu_keyboard(registered=True), parse_mode=ParseMode.MARKDOWN)


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
    stat, quests_data = await asyncio.gather(
        api_get(f"/api/stat/{uid}", actor_uid=uid),
        api_get(f"/api/quests/{uid}", actor_uid=uid),
    )
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {}, quests=_merge_quests(quests_data))
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await respond_query(query, text, reply_markup=ui.main_menu_keyboard(registered=True), parse_mode=ParseMode.MARKDOWN)


async def _show_stat_message(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    stat, quests_data = await asyncio.gather(
        api_get(f"/api/stat/{uid}", actor_uid=uid),
        api_get(f"/api/quests/{uid}", actor_uid=uid),
    )
    await state.set_state(MenuAccountFSM.viewing_stat)
    if not stat.get("success"):
        await reply_or_answer(message, f"❌ {_error_text(stat, '状态获取失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await reply_or_answer(message,
        ui.format_status_card(stat.get("status") or {}, quests=_merge_quests(quests_data)),
        reply_markup=ui.main_menu_keyboard(registered=True),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _show_stat_query(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    stat, quests_data = await asyncio.gather(
        api_get(f"/api/stat/{uid}", actor_uid=uid),
        api_get(f"/api/quests/{uid}", actor_uid=uid),
    )
    await state.set_state(MenuAccountFSM.viewing_stat)
    if not stat.get("success"):
        await respond_query(query, f"❌ {_error_text(stat, '状态获取失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await respond_query(
        query,
        ui.format_status_card(stat.get("status") or {}, quests=_merge_quests(quests_data)),
        reply_markup=ui.main_menu_keyboard(registered=True),
        parse_mode=ParseMode.MARKDOWN,
        toast_if_unchanged="✅ 状态已是最新",
    )


async def _register_from_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    existing_uid = await resolve_uid(int(message.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await reply_or_answer(message, "你已注册过角色。", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await state.update_data(pending_platform_id=str(message.from_user.id))
    await state.set_state(MenuAccountFSM.registering)
    await reply_or_answer(message,
        "🌫️ 天地初开，你从虚无中醒来。\n\n你叫什么名字？\n（2-16字，支持中文和英文）"
    )


async def _register_from_query(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        await safe_answer(query)
        return
    existing_uid = await resolve_uid(int(query.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await _show_stat_query(query, state)
        return
    await state.update_data(pending_platform_id=str(query.from_user.id))
    await state.set_state(MenuAccountFSM.registering)
    await respond_query(
        query,
        "🌫️ 天地初开，你从虚无中醒来。\n\n你叫什么名字？\n（2-16字，支持中文和英文）",
    )


@router.message(Command("xian_start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_home_message(message, state)


@router.message(Command("xian_register"))
async def cmd_register(message: Message, state: FSMContext) -> None:
    await _register_from_message(message, state)


@router.message(Command("xian_stat"))
async def cmd_stat(message: Message, state: FSMContext) -> None:
    await _show_stat_message(message, state)


@router.message(Command("xian_version"))
async def cmd_version(message: Message, state: FSMContext) -> None:
    await state.set_state(MenuAccountFSM.version_view)
    await reply_or_answer(message, "版本信息：\n- Adapter: aiogram-fsm\n- Callback: v3", reply_markup=ui.main_menu_keyboard(registered=True))


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
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
    await _show_home_query(query, state)


@router.message(MenuAccountFSM.registering)
async def cb_input_name(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    raw = _USERNAME_ALLOWED.sub("", raw)
    if len(raw) < 2:
        await reply_or_answer(message, "名字太短或包含不支持的字符，请重新输入（2-16字，中文或英文）：")
        return
    if len(raw) > 16:
        raw = raw[:16]
    await state.update_data(pending_username=raw)
    await state.set_state(MenuAccountFSM.picking_element)
    await reply_or_answer(message,
        f"「{raw}」，愿你此去，问道长生。\n\n请选择你的灵根：\n\n🔥 火灵根：攻击+20%，技能伤害+10%\n🌊 水灵根：闪避+8%，技能伤害+6%，防御+5%\n🌿 木灵根：生命恢复+25%，基础吸血+2%\n🗡 金灵根：攻击+15%，暴击率+5%\n🪨 土灵根：生命+15%，防御+10%",
        reply_markup=ui.element_keyboard(),
    )


@router.callback_query(F.data.startswith("register:element:"))
async def cb_pick_element(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    await safe_answer(query)
    element = str(query.data or "").split(":")[-1]
    data = await state.get_data()
    username = data.get("pending_username", "")
    platform_id = data.get("pending_platform_id", "")
    if not username or not platform_id:
        await respond_query(query, "❌ 注册信息丢失，请重新点击注册。", reply_markup=ui.main_menu_keyboard(registered=False))
        return
    payload = {"platform": "telegram", "platform_id": platform_id, "username": username, "element": element}
    result = await api_post("/api/register", payload=payload, actor_uid=None)
    if not result.get("success") and str(result.get("code", "")).upper() == "USERNAME_TAKEN":
        payload["username"] = f"{username}{platform_id[-2:]}"[:16]
        result = await api_post("/api/register", payload=payload, actor_uid=None)
    if result.get("success") or str(result.get("code", "")).upper() == "ALREADY_EXISTS":
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await _show_stat_query(query, state)
        return
    await respond_query(query, f"❌ 注册失败：{_error_text(result, '注册失败')}", reply_markup=ui.main_menu_keyboard(registered=False))
