"""Shared query/navigation helpers for aiogram routers."""

from __future__ import annotations

from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from adapters.aiogram import ui


async def safe_answer(query: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Best-effort callback answer to stop Telegram loading indicator."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        return


async def reply_or_answer(message: Any, text: str, *, reply_markup=None, parse_mode=None) -> None:
    """In groups use reply (preserves sender info for auth); in private use answer."""
    from aiogram.types import Message as AiogramMessage
    msg: AiogramMessage = message
    if msg.chat.type == "private":
        await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        await msg.reply(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def respond_query(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: ParseMode | str | None = None,
    fallback_to_send: bool = True,
    toast_if_unchanged: str | None = None,
) -> None:
    """Respond by editing current message first, then fallback to send."""
    if query.message is not None:
        try:
            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                if toast_if_unchanged:
                    await safe_answer(query, text=toast_if_unchanged, show_alert=False)
                return
            if fallback_to_send:
                try:
                    await query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
                    return
                except Exception:
                    pass
        except Exception:
            if fallback_to_send:
                try:
                    await query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
                    return
                except Exception:
                    pass
    await safe_answer(query, text="操作失败，请稍后重试", show_alert=False)


def is_query_owner(query: CallbackQuery) -> bool:
    """Check if the person clicking the button is the original message recipient."""
    if query.message is None or query.from_user is None:
        return True
    msg = query.message
    # In private chats, only the user themselves can receive messages
    if msg.chat.type == "private":
        return True
    # In groups, compare with the user the bot replied to
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return query.from_user.id == msg.reply_to_message.from_user.id
    return True


async def reject_non_owner(query: CallbackQuery) -> bool:
    """Answer with alert and return True if the clicker is not the owner."""
    if not is_query_owner(query):
        await safe_answer(query, text="这是别人的面板，请自己发起操作。", show_alert=True)
        return True
    return False


async def handle_expired_callback(query: CallbackQuery) -> None:
    """Unified behavior for unknown/expired callback buttons."""
    await safe_answer(query, text="按钮已过期，请重新打开菜单", show_alert=False)
    await respond_query(
        query,
        "该消息按钮已失效，请点击下方主菜单继续。",
        reply_markup=ui.main_menu_keyboard(registered=True),
    )

