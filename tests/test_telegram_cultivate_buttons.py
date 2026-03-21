import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from adapters.telegram import bot as telegram_bot


class _DummyMessage:
    def __init__(self, *, chat_id: int = 1, chat_type: str = "private", message_id: int = 10):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.chat_id = chat_id
        self.message_id = message_id
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.message_id + len(self.sent) + 1)


class _DummyQuery:
    def __init__(self, *, clicker_id: int, message, data: str):
        self.id = "cb-cultivate"
        self.from_user = SimpleNamespace(id=clicker_id)
        self.message = message
        self.data = data
        self.answered = []
        self.edited = []

    async def answer(self, text=None, show_alert=False):
        self.answered.append((text, bool(show_alert)))

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))
        return SimpleNamespace(chat_id=self.message.chat_id, message_id=self.message.message_id)


def _extract_callbacks(reply_markup):
    if reply_markup is None:
        return []
    rows = getattr(reply_markup, "inline_keyboard", []) or []
    return [getattr(btn, "callback_data", "") for row in rows for btn in row]


def test_cultivate_cmd_start_has_main_menu_button(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_bot, "PANEL_OWNER_CACHE_PATH", str(tmp_path / "panel_owners.json"))
    captured = {}

    async def fake_http_get(url, **kwargs):
        if url.endswith("/api/user/lookup"):
            return {"success": True, "user_id": "u1", "username": "Tester"}
        if url.endswith("/api/cultivate/status/u1"):
            return {"success": True, "state": False}
        return {"success": False}

    async def fake_http_post(url, **kwargs):
        if url.endswith("/api/cultivate/start"):
            return {"success": True}
        return {"success": False}

    async def fake_reply_with_owned_panel(update, context, text, **kwargs):
        captured["text"] = text
        captured["reply_markup"] = kwargs.get("reply_markup")
        return SimpleNamespace(chat_id=1, message_id=99)

    monkeypatch.setattr(telegram_bot, "http_get", fake_http_get)
    monkeypatch.setattr(telegram_bot, "http_post", fake_http_post)
    monkeypatch.setattr(telegram_bot, "_reply_with_owned_panel", fake_reply_with_owned_panel)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345, first_name="Tester"),
        message=_DummyMessage(),
    )
    context = SimpleNamespace(
        args=[],
        user_data={},
        application=SimpleNamespace(bot_data={}),
    )

    asyncio.run(telegram_bot.cultivate_cmd(update, context))

    callbacks = _extract_callbacks(captured.get("reply_markup"))
    assert "main_menu" in callbacks


def test_cultivate_start_callback_has_main_menu_button(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_bot, "PANEL_OWNER_CACHE_PATH", str(tmp_path / "panel_owners.json"))

    async def fake_http_get(url, **kwargs):
        if url.endswith("/api/user/lookup"):
            return {"success": True, "user_id": "u1"}
        return {"success": False}

    async def fake_http_post(url, **kwargs):
        if url.endswith("/api/cultivate/start"):
            return {"success": True}
        return {"success": False}

    monkeypatch.setattr(telegram_bot, "http_get", fake_http_get)
    monkeypatch.setattr(telegram_bot, "http_post", fake_http_post)

    message = _DummyMessage(chat_id=12345, chat_type="private", message_id=88)
    query = _DummyQuery(clicker_id=12345, message=message, data="cultivate_start")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )

    asyncio.run(telegram_bot.callback_handler(update, context))

    assert query.edited
    callbacks = _extract_callbacks(query.edited[-1][1].get("reply_markup"))
    assert "main_menu" in callbacks
