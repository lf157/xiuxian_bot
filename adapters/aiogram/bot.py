"""aiogram-based Telegram adapter (AIO + FSM)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from adapters.actor_paths import compiled_actor_path_patterns
from core.config import config
from core.utils.runtime_logging import setup_runtime_logging


SERVER_URL = str(config.core_server_url).rstrip("/")
INTERNAL_API_TOKEN = str(config.internal_api_token or "")
TELEGRAM_TOKEN = str(config.telegram_token or "")
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = setup_runtime_logging("aiogram", project_root=ROOT_DIR, stats_interval_seconds=120)
_ACTOR_PATTERNS = compiled_actor_path_patterns()
_HTTP_SESSION: aiohttp.ClientSession | None = None


class PublishBountyFSM(StatesGroup):
    waiting_item = State()
    waiting_qty = State()
    waiting_reward = State()
    waiting_desc = State()


@dataclass
class SessionContext:
    uid: str
    wanted_item_id: str = ""
    wanted_quantity: int = 0
    reward_spirit_low: int = 0


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 状态", callback_data="main_status"), InlineKeyboardButton(text="💱 货币", callback_data="main_currency")],
            [InlineKeyboardButton(text="📜 悬赏会", callback_data="main_bounty")],
        ]
    )


def _extract_actor_user_id(url: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> str | None:
    payload = payload or {}
    params = params or {}
    if payload.get("user_id"):
        return str(payload.get("user_id"))
    if params.get("user_id"):
        return str(params.get("user_id"))
    path = urlparse(str(url or "")).path
    for p in _ACTOR_PATTERNS:
        m = p.match(path)
        if m:
            return str(m.group(1))
    return None


async def _get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        _HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _HTTP_SESSION


async def _request_json(method: str, url: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN
    actor_uid = _extract_actor_user_id(url, payload=payload, params=params)
    if actor_uid:
        headers["X-Actor-User-Id"] = actor_uid
    sess = await _get_http_session()
    async with sess.request(method, url, json=payload, params=params, headers=headers) as resp:
        try:
            data = await resp.json(content_type=None)
        except Exception:
            text = await resp.text()
            logger.error("aiogram_non_json_response method=%s url=%s status=%s body=%s", method, url, resp.status, text[:300])
            return {"success": False, "message": "Core returned non-json response", "status_code": int(resp.status)}
    return data if isinstance(data, dict) else {"success": False, "message": "Invalid response format"}


async def api_get(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request_json("GET", f"{SERVER_URL}{path}", params=params)


async def api_post(path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
    return await _request_json("POST", f"{SERVER_URL}{path}", payload=payload)


async def resolve_uid(tg_user_id: int) -> str | None:
    res = await api_get("/api/user/lookup", params={"platform": "telegram", "platform_id": str(tg_user_id)})
    if res.get("success"):
        return str(res.get("user_id"))
    return None


router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(json.dumps({"event": "start", "tg_user": message.from_user.id if message.from_user else None}, ensure_ascii=False))
    uid = await resolve_uid(int(message.from_user.id))
    if not uid:
        await message.answer("欢迎来到修仙世界，请先在旧版入口注册账号。", reply_markup=_main_menu())
        return
    stat = await api_get(f"/api/stat/{uid}")
    if stat.get("success"):
        s = stat.get("status", {})
        text = (
            "👤 修仙状态\n"
            f"境界: {s.get('realm_name', '凡人')}\n"
            f"修为: {s.get('exp', 0)}\n"
            f"下品灵石: {s.get('copper', 0)}\n"
            f"中品灵石: {s.get('gold', 0)}"
        )
    else:
        text = "已连接 aiogram 适配器。"
    await message.answer(text, reply_markup=_main_menu())


@router.message(Command("currency"))
async def cmd_currency(message: Message):
    if not message.from_user:
        return
    uid = await resolve_uid(int(message.from_user.id))
    if not uid:
        await message.answer("❌ 未找到账号")
        return
    parts = (message.text or "").split()
    if len(parts) >= 3:
        direction = parts[1].lower()
        try:
            amount = int(parts[2])
        except (TypeError, ValueError):
            await message.answer("❌ 数量必须是整数")
            return
        from_currency = "copper" if direction in ("up", "to_mid") else "gold"
        r = await api_post("/api/currency/exchange", payload={"user_id": uid, "from_currency": from_currency, "amount": amount})
        await message.answer(("✅ " if r.get("success") else "❌ ") + r.get("message", "兑换失败"))
        return
    data = await api_get(f"/api/currency/{uid}")
    if not data.get("success"):
        await message.answer(f"❌ {data.get('message', '获取失败')}")
        return
    wallet = data.get("wallet", {})
    await message.answer(
        "💱 统一货币\n"
        f"下品灵石: {wallet.get('spirit_low', 0)}\n"
        f"中品灵石: {wallet.get('spirit_mid', 0)}\n"
        f"上品灵石: {wallet.get('spirit_high', 0)}\n\n"
        "用法: /currency up 1000  或 /currency down 1"
    )


@router.message(Command("bounty"))
async def cmd_bounty(message: Message, state: FSMContext):
    if not message.from_user:
        return
    uid = await resolve_uid(int(message.from_user.id))
    if not uid:
        await message.answer("❌ 未找到账号")
        return
    parts = (message.text or "").split()
    if len(parts) <= 1 or parts[1].lower() == "list":
        data = await api_get("/api/bounties", params={"status": "open", "limit": 10})
        if not data.get("success"):
            await message.answer("❌ 获取悬赏失败")
            return
        rows = data.get("bounties", []) or []
        if not rows:
            await message.answer("📜 当前暂无公开悬赏")
            return
        lines = ["📜 悬赏会"]
        for row in rows:
            lines.append(
                f"#{row.get('id')} {row.get('wanted_item_name', row.get('wanted_item_id'))} x{row.get('wanted_quantity')} "
                f"=> {row.get('reward_spirit_low')} 下品灵石"
            )
        await message.answer("\n".join(lines))
        return

    action = parts[1].lower()
    if action == "accept" and len(parts) >= 3:
        try:
            bounty_id = int(parts[2])
        except (TypeError, ValueError):
            await message.answer("❌ 悬赏ID必须是整数")
            return
        res = await api_post("/api/bounty/accept", payload={"user_id": uid, "bounty_id": bounty_id})
        await message.answer(("✅ " if res.get("success") else "❌ ") + res.get("message", "接取失败"))
        return
    if action == "submit" and len(parts) >= 3:
        try:
            bounty_id = int(parts[2])
        except (TypeError, ValueError):
            await message.answer("❌ 悬赏ID必须是整数")
            return
        res = await api_post("/api/bounty/submit", payload={"user_id": uid, "bounty_id": bounty_id})
        await message.answer(("✅ " if res.get("success") else "❌ ") + res.get("message", "提交失败"))
        return
    if action == "publish":
        await state.set_state(PublishBountyFSM.waiting_item)
        await state.update_data(session=SessionContext(uid=uid).__dict__)
        await message.answer("请输入道具ID（例如：beast_hide）")
        return

    await message.answer("用法：/bounty [list|publish|accept <ID>|submit <ID>]")


@router.message(PublishBountyFSM.waiting_item)
async def fsm_bounty_item(message: Message, state: FSMContext):
    data = await state.get_data()
    session = SessionContext(**(data.get("session") or {}))
    session.wanted_item_id = str((message.text or "").strip())
    await state.update_data(session=session.__dict__)
    await state.set_state(PublishBountyFSM.waiting_qty)
    await message.answer("请输入需求数量")


@router.message(PublishBountyFSM.waiting_qty)
async def fsm_bounty_qty(message: Message, state: FSMContext):
    data = await state.get_data()
    session = SessionContext(**(data.get("session") or {}))
    try:
        session.wanted_quantity = int((message.text or "").strip())
    except (TypeError, ValueError):
        await message.answer("数量必须是整数，请重新输入")
        return
    await state.update_data(session=session.__dict__)
    await state.set_state(PublishBountyFSM.waiting_reward)
    await message.answer("请输入奖励下品灵石数量")


@router.message(PublishBountyFSM.waiting_reward)
async def fsm_bounty_reward(message: Message, state: FSMContext):
    data = await state.get_data()
    session = SessionContext(**(data.get("session") or {}))
    try:
        session.reward_spirit_low = int((message.text or "").strip())
    except (TypeError, ValueError):
        await message.answer("奖励必须是整数，请重新输入")
        return
    await state.update_data(session=session.__dict__)
    await state.set_state(PublishBountyFSM.waiting_desc)
    await message.answer("请输入备注描述（可输入“-”跳过）")


@router.message(PublishBountyFSM.waiting_desc)
async def fsm_bounty_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    session = SessionContext(**(data.get("session") or {}))
    desc = (message.text or "").strip()
    if desc == "-":
        desc = ""
    payload = {
        "user_id": session.uid,
        "wanted_item_id": session.wanted_item_id,
        "wanted_quantity": session.wanted_quantity,
        "reward_spirit_low": session.reward_spirit_low,
        "description": desc,
    }
    res = await api_post("/api/bounty/publish", payload=payload)
    await state.clear()
    await message.answer(("✅ " if res.get("success") else "❌ ") + res.get("message", "发布失败"))


@router.callback_query(F.data == "main_status")
async def cb_status(query: CallbackQuery):
    if not query.from_user:
        return
    uid = await resolve_uid(int(query.from_user.id))
    if not uid:
        await query.answer("未找到账号", show_alert=True)
        return
    stat = await api_get(f"/api/stat/{uid}")
    if not stat.get("success"):
        await query.answer("状态获取失败", show_alert=True)
        return
    s = stat.get("status", {})
    text = (
        "📊 状态\n"
        f"境界: {s.get('realm_name', '凡人')}\n"
        f"修为: {s.get('exp', 0)}\n"
        f"下品灵石: {s.get('copper', 0)}\n"
        f"中品灵石: {s.get('gold', 0)}"
    )
    await query.message.edit_text(text, reply_markup=_main_menu())
    await query.answer()


@router.callback_query(F.data == "main_currency")
async def cb_currency(query: CallbackQuery):
    if not query.from_user:
        return
    uid = await resolve_uid(int(query.from_user.id))
    if not uid:
        await query.answer("未找到账号", show_alert=True)
        return
    data = await api_get(f"/api/currency/{uid}")
    wallet = data.get("wallet", {}) if data.get("success") else {}
    text = (
        "💱 统一货币\n"
        f"下品灵石: {wallet.get('spirit_low', 0)}\n"
        f"中品灵石: {wallet.get('spirit_mid', 0)}\n"
        "命令：/currency up 1000 或 /currency down 1"
    )
    await query.message.edit_text(text, reply_markup=_main_menu())
    await query.answer()


@router.callback_query(F.data == "main_bounty")
async def cb_bounty(query: CallbackQuery):
    data = await api_get("/api/bounties", params={"status": "open", "limit": 8})
    rows = data.get("bounties", []) if data.get("success") else []
    if not rows:
        text = "📜 当前暂无公开悬赏\n\n命令：/bounty publish ... /bounty accept <ID>"
    else:
        lines = ["📜 悬赏会"]
        for row in rows:
            lines.append(
                f"#{row.get('id')} {row.get('wanted_item_name', row.get('wanted_item_id'))} "
                f"x{row.get('wanted_quantity')} => {row.get('reward_spirit_low')} 下品灵石"
            )
        lines.append("\n命令：/bounty publish ... /bounty accept <ID> /bounty submit <ID>")
        text = "\n".join(lines)
    await query.message.edit_text(text, reply_markup=_main_menu())
    await query.answer()


async def _close_http_session() -> None:
    global _HTTP_SESSION
    if _HTTP_SESSION is not None and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
    _HTTP_SESSION = None


async def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN/XXBOT_TELEGRAM_TOKEN")
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("aiogram adapter starting")
    try:
        await dp.start_polling(bot)
    finally:
        await _close_http_session()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
