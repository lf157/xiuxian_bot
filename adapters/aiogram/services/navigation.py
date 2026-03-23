"""Shared query/navigation helpers for aiogram routers."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery

from adapters.aiogram import ui


async def safe_answer(query: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Best-effort callback answer to stop Telegram loading indicator."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        return


async def respond_query(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: ParseMode | str | None = None,
    fallback_to_send: bool = True,
) -> None:
    """Respond by editing current message first, then fallback to send."""
    if query.message is not None:
        try:
            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception:
            if fallback_to_send:
                try:
                    await query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
                    return
                except Exception:
                    pass
    await safe_answer(query, text="操作失败，请稍后重试", show_alert=False)


async def handle_expired_callback(query: CallbackQuery) -> None:
    """Unified behavior for unknown/expired callback buttons."""
    await safe_answer(query, text="按钮已过期，请重新打开菜单", show_alert=False)
    await respond_query(
        query,
        "该消息按钮已失效，请点击下方主菜单继续。",
        reply_markup=ui.main_menu_keyboard(registered=True),
    )

