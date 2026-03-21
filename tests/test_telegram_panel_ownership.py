import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from adapters.telegram import bot as telegram_bot


class _DummyQuery:
    def __init__(self, clicker_id: int, message, data: str = "main_menu"):
        self.id = "cb-1"
        self.from_user = SimpleNamespace(id=clicker_id)
        self.message = message
        self.data = data
        self.answered = []

    async def answer(self, text=None, show_alert=False):
        self.answered.append((text, bool(show_alert)))

    async def edit_message_text(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("foreign click should return before any edit")


def _group_message(owner_tg_id: int):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group"),
        chat_id=-100123,
        message_id=77,
        reply_to_message=SimpleNamespace(
            from_user=SimpleNamespace(id=owner_tg_id, is_bot=False)
        ),
    )


def test_infer_panel_owner_from_reply_sender():
    message = _group_message(owner_tg_id=123456)
    assert telegram_bot._infer_panel_owner_from_message(message) == "123456"


def test_infer_panel_owner_from_private_chat():
    message = SimpleNamespace(
        chat=SimpleNamespace(id=998877, type="private"),
        reply_to_message=None,
    )
    assert telegram_bot._infer_panel_owner_from_message(message) == "998877"


def test_callback_rejects_foreign_click_with_inferred_owner():
    message = _group_message(owner_tg_id=10001)
    query = _DummyQuery(clicker_id=20002, message=message, data="status")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert query.answered
    assert query.answered[0][0] == "这不是你的操作面板"
    assert query.answered[0][1] is True
    owners = context.application.bot_data.get("panel_owners", {})
    assert owners.get(f"{message.chat_id}:{message.message_id}") == "10001"
