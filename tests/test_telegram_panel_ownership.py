import asyncio
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from adapters.telegram import bot as telegram_bot


@pytest.fixture(autouse=True)
def _isolated_panel_owner_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "PANEL_OWNER_CACHE_PATH", str(tmp_path / "panel_owners.json"))


class _PanelMessage:
    def __init__(self, *, chat_id: int, chat_type: str, message_id: int, reply_to_message=None):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.chat_id = chat_id
        self.message_id = message_id
        self.reply_to_message = reply_to_message
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        next_id = self.message_id + len(self.sent) + 1
        return SimpleNamespace(chat_id=self.chat_id, message_id=next_id)


class _DummyQuery:
    def __init__(self, clicker_id: int, message, data: str = "main_menu", fail_answer: bool = False):
        self.id = "cb-1"
        self.from_user = SimpleNamespace(id=clicker_id)
        self.message = message
        self.data = data
        self.fail_answer = bool(fail_answer)
        self.answered = []
        self.edited = []

    async def answer(self, text=None, show_alert=False):
        if self.fail_answer:
            raise RuntimeError("query is too old")
        self.answered.append((text, bool(show_alert)))

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))
        return SimpleNamespace(chat_id=self.message.chat_id, message_id=self.message.message_id)


def _group_message():
    return _PanelMessage(
        chat_id=-100123,
        chat_type="group",
        message_id=77,
        reply_to_message=None,
    )


def test_callback_rejects_foreign_click_with_bound_owner():
    message = _group_message()
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )
    telegram_bot._bind_panel_owner(context, message, "10001")
    query = _DummyQuery(clicker_id=20002, message=message, data="status")
    update = SimpleNamespace(callback_query=query)

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert query.answered
    assert query.answered[0][0] == "这不是你的操作面板"
    assert query.answered[0][1] is True
    assert not query.edited
    owners = context.application.bot_data.get("panel_owners", {})
    assert owners.get(f"{message.chat_id}:{message.message_id}") == "10001"


def test_callback_rejects_group_panel_when_owner_unknown():
    message = _PanelMessage(
        chat_id=-100123,
        chat_type="group",
        message_id=88,
        reply_to_message=None,
    )
    query = _DummyQuery(clicker_id=30003, message=message, data="status")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert query.answered
    assert query.answered[0][0] == "面板已失效，请输入 /xian_start 打开自己的面板"
    assert query.answered[0][1] is True
    assert not query.edited


def test_callback_reject_unknown_owner_falls_back_to_chat_message_when_answer_expired():
    message = _PanelMessage(
        chat_id=-100123,
        chat_type="group",
        message_id=188,
        reply_to_message=None,
    )
    query = _DummyQuery(
        clicker_id=30003,
        message=message,
        data="status",
        fail_answer=True,
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert not query.answered
    assert not query.edited
    assert message.sent
    assert "面板已失效" in str(message.sent[-1][0])


def test_callback_private_panel_without_owner_binds_clicker():
    message = _PanelMessage(
        chat_id=40004,
        chat_type="private",
        message_id=99,
        reply_to_message=None,
    )
    query = _DummyQuery(clicker_id=40004, message=message, data="__noop__")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert query.answered
    assert query.answered[0][0] is None
    assert query.answered[0][1] is False
    owners = context.application.bot_data.get("panel_owners", {})
    assert owners.get(f"{message.chat_id}:{message.message_id}") == "40004"


def test_panel_owner_binding_persists_to_disk():
    cache_path = telegram_bot.PANEL_OWNER_CACHE_PATH
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )
    message = _PanelMessage(
        chat_id=-100200,
        chat_type="group",
        message_id=1234,
        reply_to_message=None,
    )

    telegram_bot._bind_panel_owner(context, message, "90001")

    assert os.path.exists(cache_path)

    fresh_context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )
    assert telegram_bot._get_panel_owner(fresh_context, message) == "90001"
