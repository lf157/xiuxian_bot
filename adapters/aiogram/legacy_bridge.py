"""Compatibility bridge: run legacy telegram bot handlers on top of aiogram updates.

This allows migrating transport layer to aiogram while keeping full legacy gameplay coverage.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ForceReply as AioForceReply,
    InlineKeyboardButton as AioInlineKeyboardButton,
    InlineKeyboardMarkup as AioInlineKeyboardMarkup,
    Message,
)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from adapters.telegram import bot as legacy_bot
from core.config import config

logger = logging.getLogger("adapters.aiogram.legacy_bridge")
router = Router(name="aiogram_full_fsm_bridge")

legacy_bot.SERVER_URL = str(getattr(config, "core_server_url", "") or f"http://127.0.0.1:{int(config.core_server_port)}").rstrip("/")
legacy_bot.INTERNAL_API_TOKEN = str(config.internal_api_token or "").strip()

_BOT_DATA: dict[str, Any] = {}


class _CompatApplication:
    def __init__(self) -> None:
        self.bot_data = _BOT_DATA

    def stop_running(self) -> None:
        return


_APP = _CompatApplication()


@dataclass
class _CompatContext:
    args: list[str]
    user_data: dict[str, Any]
    chat_data: dict[str, Any]
    bot: "_CompatBot"
    application: _CompatApplication


class _CompatBot:
    def __init__(self, bot) -> None:
        self._bot = bot

    async def send_message(self, chat_id: int | str, text: str, **kwargs):
        payload = _normalize_message_kwargs(kwargs)
        sent = await self._bot.send_message(chat_id=chat_id, text=text, **payload)
        return _CompatMessage(self._bot, sent)


class _CompatMessage:
    def __init__(
        self,
        bot,
        message: Message | None,
        *,
        chat_id: int | None = None,
        message_id: int | None = None,
        message_thread_id: int | None = None,
        chat: Any | None = None,
        from_user: Any | None = None,
        text: str | None = None,
        reply_to_message: Any | None = None,
    ) -> None:
        self._bot = bot
        self._raw = message
        if isinstance(message, Message):
            self.chat = message.chat
            self.chat_id = int(message.chat.id)
            self.message_id = int(message.message_id)
            self.message_thread_id = int(getattr(message, "message_thread_id", 0) or 0)
            self.from_user = message.from_user
            self.text = message.text
            raw_reply = getattr(message, "reply_to_message", None)
            if isinstance(raw_reply, Message):
                self.reply_to_message = _CompatMessage(bot, raw_reply)
            else:
                self.reply_to_message = raw_reply
            return

        self.chat = chat
        fallback_chat_id = int(getattr(chat, "id", 0) or 0) if chat is not None else 0
        self.chat_id = int(chat_id or fallback_chat_id)
        self.message_id = int(message_id or 0)
        self.message_thread_id = int(message_thread_id or 0)
        self.from_user = from_user
        self.text = text
        self.reply_to_message = reply_to_message

    async def reply_text(self, text: str, **kwargs):
        payload = _normalize_message_kwargs(kwargs)
        if self.message_thread_id > 0 and "message_thread_id" not in payload:
            payload["message_thread_id"] = self.message_thread_id
        sent = await self._bot.send_message(chat_id=self.chat_id, text=text, **payload)
        return _CompatMessage(self._bot, sent)


class _CompatCallbackQuery:
    def __init__(self, bot, query: CallbackQuery) -> None:
        self._bot = bot
        self._raw = query
        self.id = query.id
        self.data = str(query.data or "")
        self.from_user = query.from_user
        if isinstance(query.message, Message):
            self.message = _CompatMessage(bot, query.message)
        elif query.message is not None:
            fallback_chat = getattr(query.message, "chat", None)
            fallback_chat_id = int(getattr(fallback_chat, "id", 0) or 0)
            fallback_mid = int(getattr(query.message, "message_id", 0) or 0)
            fallback_thread_id = int(getattr(query.message, "message_thread_id", 0) or 0)
            fallback_reply = getattr(query.message, "reply_to_message", None)
            self.message = _CompatMessage(
                bot,
                None,
                chat_id=fallback_chat_id,
                message_id=fallback_mid,
                message_thread_id=fallback_thread_id,
                chat=fallback_chat,
                from_user=query.from_user,
                text=None,
                reply_to_message=fallback_reply,
            )
        else:
            self.message = _CompatMessage(
                bot,
                None,
                chat_id=int(getattr(query.from_user, "id", 0) or 0),
                message_id=0,
                chat=None,
                from_user=query.from_user,
                text=None,
            )

    async def answer(self, text: str | None = None, show_alert: bool = False):
        await self._bot.answer_callback_query(
            callback_query_id=self.id,
            text=text,
            show_alert=show_alert,
        )

    async def edit_message_text(self, text: str, **kwargs):
        payload = _normalize_message_kwargs(kwargs)
        if isinstance(self._raw.message, Message):
            edited = await self._raw.message.edit_text(text=text, **payload)
            if isinstance(edited, Message):
                return _CompatMessage(self._bot, edited)
            return self.message
        if self.message is not None:
            edited = await self._bot.edit_message_text(
                chat_id=self.message.chat_id,
                message_id=self.message.message_id,
                text=text,
                **payload,
            )
            if isinstance(edited, Message):
                return _CompatMessage(self._bot, edited)
        return self.message


@dataclass
class _CompatUpdate:
    message: _CompatMessage | None = None
    callback_query: _CompatCallbackQuery | None = None
    effective_user: Any | None = None
    effective_message: _CompatMessage | None = None


def _normalize_parse_mode(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(getattr(value, "value", "") or "")
    return str(value)


def _convert_inline_keyboard(raw_rows: Any) -> list[list[AioInlineKeyboardButton]]:
    rows: list[list[AioInlineKeyboardButton]] = []
    if not isinstance(raw_rows, list):
        return rows
    for raw_row in raw_rows:
        if not isinstance(raw_row, list):
            continue
        row: list[AioInlineKeyboardButton] = []
        for raw_btn in raw_row:
            btn = raw_btn
            if not isinstance(btn, dict):
                if hasattr(btn, "to_dict"):
                    try:
                        btn = btn.to_dict()
                    except Exception:
                        btn = {}
                else:
                    btn = {
                        "text": getattr(raw_btn, "text", "按钮"),
                        "callback_data": getattr(raw_btn, "callback_data", None),
                        "url": getattr(raw_btn, "url", None),
                    }
            text = str(btn.get("text") or "按钮")
            callback_data = btn.get("callback_data")
            url = btn.get("url")
            if callback_data is not None:
                row.append(AioInlineKeyboardButton(text=text, callback_data=str(callback_data)))
            elif url:
                row.append(AioInlineKeyboardButton(text=text, url=str(url)))
            else:
                row.append(AioInlineKeyboardButton(text=text, callback_data="main_menu"))
        if row:
            rows.append(row)
    return rows


def _convert_reply_markup(markup: Any):
    if markup is None:
        return None
    if isinstance(markup, (AioInlineKeyboardMarkup, AioForceReply)):
        return markup

    data: dict[str, Any] | None = None
    if isinstance(markup, dict):
        data = markup
    elif hasattr(markup, "to_dict"):
        try:
            data = markup.to_dict()
        except Exception:
            data = None

    if isinstance(data, dict):
        if "inline_keyboard" in data:
            rows = _convert_inline_keyboard(data.get("inline_keyboard"))
            return AioInlineKeyboardMarkup(inline_keyboard=rows)
        if bool(data.get("force_reply")):
            return AioForceReply(
                selective=bool(data.get("selective", False)),
                input_field_placeholder=data.get("input_field_placeholder"),
            )

    cls = markup.__class__.__name__
    if cls == "InlineKeyboardMarkup":
        raw_rows = getattr(markup, "inline_keyboard", None)
        rows = _convert_inline_keyboard(raw_rows)
        return AioInlineKeyboardMarkup(inline_keyboard=rows)
    if cls == "ForceReply":
        return AioForceReply(
            selective=bool(getattr(markup, "selective", False)),
            input_field_placeholder=getattr(markup, "input_field_placeholder", None),
        )

    return None


def _normalize_message_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    parse_mode = _normalize_parse_mode(kwargs.get("parse_mode"))
    if parse_mode:
        out["parse_mode"] = parse_mode
    reply_markup = _convert_reply_markup(kwargs.get("reply_markup"))
    if reply_markup is not None:
        out["reply_markup"] = reply_markup
    if "disable_web_page_preview" in kwargs:
        out["disable_web_page_preview"] = bool(kwargs.get("disable_web_page_preview"))
    reply_to_message_id = kwargs.get("reply_to_message_id")
    if reply_to_message_id is not None:
        try:
            out["reply_to_message_id"] = int(reply_to_message_id)
        except (TypeError, ValueError):
            pass
    message_thread_id = kwargs.get("message_thread_id")
    if message_thread_id is not None:
        try:
            out["message_thread_id"] = int(message_thread_id)
        except (TypeError, ValueError):
            pass
    if "allow_sending_without_reply" in kwargs:
        out["allow_sending_without_reply"] = bool(kwargs.get("allow_sending_without_reply"))
    return out


def _parse_command_text(text: str | None) -> tuple[str, list[str]]:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return "", []
    parts = raw.split()
    cmd = parts[0][1:]
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    return cmd.lower().strip(), parts[1:]


def _ensure_fsm_payload(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    if not isinstance(payload.get("_legacy_user_data"), dict):
        payload["_legacy_user_data"] = {}
    if not isinstance(payload.get("_legacy_chat_data"), dict):
        payload["_legacy_chat_data"] = {}
    return payload


def _build_context(*, bot, args: list[str], fsm_payload: dict[str, Any]) -> _CompatContext:
    return _CompatContext(
        args=list(args or []),
        user_data=fsm_payload["_legacy_user_data"],
        chat_data=fsm_payload["_legacy_chat_data"],
        bot=_CompatBot(bot),
        application=_APP,
    )


def _build_message_update(bot, message: Message) -> _CompatUpdate:
    msg = _CompatMessage(bot, message)
    return _CompatUpdate(
        message=msg,
        callback_query=None,
        effective_user=message.from_user,
        effective_message=msg,
    )


def _build_callback_update(bot, query: CallbackQuery) -> _CompatUpdate:
    cb = _CompatCallbackQuery(bot, query)
    return _CompatUpdate(
        message=cb.message,
        callback_query=cb,
        effective_user=query.from_user,
        effective_message=cb.message,
    )


_COMMAND_DISPATCH: dict[str, Any] = {
    "start": legacy_bot.start_cmd,
    "xian_start": legacy_bot.start_cmd,
    "register": legacy_bot.register_cmd,
    "xian_register": legacy_bot.register_cmd,
    "stat": legacy_bot.stat_cmd,
    "status": legacy_bot.stat_cmd,
    "xian_stat": legacy_bot.stat_cmd,
    "xian_status": legacy_bot.stat_cmd,
    "cul": legacy_bot.cultivate_cmd,
    "cultivate": legacy_bot.cultivate_cmd,
    "xian_cul": legacy_bot.cultivate_cmd,
    "xian_cultivate": legacy_bot.cultivate_cmd,
    "hunt": legacy_bot.hunt_cmd,
    "xian_hunt": legacy_bot.hunt_cmd,
    "break": legacy_bot.breakthrough_cmd,
    "breakthrough": legacy_bot.breakthrough_cmd,
    "xian_break": legacy_bot.breakthrough_cmd,
    "xian_breakthrough": legacy_bot.breakthrough_cmd,
    "shop": legacy_bot.shop_cmd,
    "xian_shop": legacy_bot.shop_cmd,
    "bag": legacy_bot.bag_cmd,
    "inventory": legacy_bot.bag_cmd,
    "xian_bag": legacy_bot.bag_cmd,
    "xian_inventory": legacy_bot.bag_cmd,
    "quest": legacy_bot.quest_cmd,
    "quests": legacy_bot.quest_cmd,
    "task": legacy_bot.quest_cmd,
    "xian_quest": legacy_bot.quest_cmd,
    "xian_quests": legacy_bot.quest_cmd,
    "xian_task": legacy_bot.quest_cmd,
    "skills": legacy_bot.skills_cmd,
    "skill": legacy_bot.skills_cmd,
    "xian_skills": legacy_bot.skills_cmd,
    "xian_skill": legacy_bot.skills_cmd,
    "secret": legacy_bot.secret_realms_cmd,
    "mystic": legacy_bot.secret_realms_cmd,
    "xian_secret": legacy_bot.secret_realms_cmd,
    "xian_mystic": legacy_bot.secret_realms_cmd,
    "rank": legacy_bot.leaderboard_cmd,
    "leaderboard": legacy_bot.leaderboard_cmd,
    "xian_rank": legacy_bot.leaderboard_cmd,
    "xian_leaderboard": legacy_bot.leaderboard_cmd,
    "pvp": legacy_bot.pvp_cmd,
    "xian_pvp": legacy_bot.pvp_cmd,
    "chat": legacy_bot.chat_cmd,
    "dao": legacy_bot.chat_cmd,
    "xian_chat": legacy_bot.chat_cmd,
    "xian_dao": legacy_bot.chat_cmd,
    "sect": legacy_bot.sect_cmd,
    "xian_sect": legacy_bot.sect_cmd,
    "alchemy": legacy_bot.alchemy_cmd,
    "xian_alchemy": legacy_bot.alchemy_cmd,
    "currency": legacy_bot.currency_cmd,
    "xian_currency": legacy_bot.currency_cmd,
    "convert": legacy_bot.convert_cmd,
    "xian_convert": legacy_bot.convert_cmd,
    "ach": legacy_bot.achievements_cmd,
    "achievements": legacy_bot.achievements_cmd,
    "xian_ach": legacy_bot.achievements_cmd,
    "xian_achievements": legacy_bot.achievements_cmd,
    "codex": legacy_bot.codex_cmd,
    "xian_codex": legacy_bot.codex_cmd,
    "events": legacy_bot.events_cmd,
    "xian_events": legacy_bot.events_cmd,
    "bounty": legacy_bot.bounty_cmd,
    "xian_bounty": legacy_bot.bounty_cmd,
    "worldboss": legacy_bot.worldboss_cmd,
    "boss": legacy_bot.worldboss_cmd,
    "xian_worldboss": legacy_bot.worldboss_cmd,
    "xian_boss": legacy_bot.worldboss_cmd,
    "guide": legacy_bot.guide_cmd,
    "realms": legacy_bot.guide_cmd,
    "xian_guide": legacy_bot.guide_cmd,
    "xian_realms": legacy_bot.guide_cmd,
    "version": legacy_bot.version_cmd,
    "xian_version": legacy_bot.version_cmd,
}


@router.message(Command(commands=list(_COMMAND_DISPATCH.keys())))
async def bridge_command_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    command, args = _parse_command_text(message.text)
    handler = _COMMAND_DISPATCH.get(command)
    if handler is None:
        return

    fsm_payload = _ensure_fsm_payload(await state.get_data())
    try:
        update = _build_message_update(message.bot, message)
        context = _build_context(
            bot=message.bot,
            args=args,
            fsm_payload=fsm_payload,
        )
        await handler(update, context)
    except Exception as exc:
        logger.exception("legacy_command_bridge_failed cmd=%s error=%s", command, type(exc).__name__)
        try:
            await message.answer("❌ 指令处理失败，请稍后重试")
        except Exception:
            pass
    finally:
        try:
            await state.set_data(fsm_payload)
        except Exception:
            pass


@router.message(F.text)
async def bridge_text_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    if str(message.text or "").startswith("/"):
        return

    fsm_payload = _ensure_fsm_payload(await state.get_data())
    try:
        update = _build_message_update(message.bot, message)
        context = _build_context(
            bot=message.bot,
            args=[],
            fsm_payload=fsm_payload,
        )
        await legacy_bot.text_message_handler(update, context)
    except Exception as exc:
        logger.exception("legacy_text_bridge_failed error=%s", type(exc).__name__)
        try:
            await message.answer("❌ 消息处理失败，请稍后重试")
        except Exception:
            pass
    finally:
        try:
            await state.set_data(fsm_payload)
        except Exception:
            pass


@router.callback_query()
async def bridge_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    if not query.from_user:
        return

    chat_id = 0
    if isinstance(query.message, Message):
        chat_id = int(query.message.chat.id)
    elif query.message is not None:
        chat_id = int(getattr(getattr(query.message, "chat", None), "id", 0) or 0)

    fsm_payload = _ensure_fsm_payload(await state.get_data())
    try:
        update = _build_callback_update(query.bot, query)
        context = _build_context(
            bot=query.bot,
            args=[],
            fsm_payload=fsm_payload,
        )
        await legacy_bot.callback_handler(update, context)
    except Exception as exc:
        logger.exception("legacy_callback_bridge_failed error=%s", type(exc).__name__)
        try:
            await query.answer("处理失败，请稍后重试", show_alert=True)
        except Exception:
            pass
    finally:
        try:
            if chat_id:
                fsm_payload["_legacy_chat_data"]["chat_id"] = chat_id
            await state.set_data(fsm_payload)
        except Exception:
            pass


async def close_legacy_bridge() -> None:
    try:
        await legacy_bot._close_http_session()
    except Exception:
        pass
