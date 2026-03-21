import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from adapters.telegram import bot as telegram_bot


class _Message:
    def __init__(self, *, chat_id: int = 123, chat_type: str = "private", message_id: int = 1):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.chat_id = chat_id
        self.message_id = message_id
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.message_id + len(self.sent) + 1)


class _Query:
    def __init__(self, *, clicker_id: int, message, data: str):
        self.id = "cb-intro"
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


def _callbacks(reply_markup):
    if reply_markup is None:
        return []
    rows = getattr(reply_markup, "inline_keyboard", []) or []
    return [getattr(btn, "callback_data", "") for row in rows for btn in row]


def test_feature_intro_shown_once_for_cultivate(monkeypatch, tmp_path):
    intro_cache = tmp_path / "feature_intro_seen.json"
    panel_cache = tmp_path / "panel_owners.json"
    monkeypatch.setattr(telegram_bot, "FEATURE_INTRO_CACHE_PATH", str(intro_cache))
    monkeypatch.setattr(telegram_bot, "PANEL_OWNER_CACHE_PATH", str(panel_cache))
    monkeypatch.setattr(telegram_bot, "_FEATURE_INTRO_TEXTS", {"cultivate": "剧情片段：你盘膝而坐，灵气入体。"})

    first_context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    first_query = _Query(clicker_id=777, message=_Message(), data="cultivate")
    first_update = SimpleNamespace(callback_query=first_query)

    asyncio.run(telegram_bot.callback_handler(first_update, first_context))

    assert first_query.edited
    assert "修炼 · 初见" in first_query.edited[-1][0]
    assert "剧情片段" in first_query.edited[-1][0]
    first_callbacks = _callbacks(first_query.edited[-1][1].get("reply_markup"))
    assert "cultivate" in first_callbacks
    assert intro_cache.exists()
    payload = json.loads(intro_cache.read_text(encoding="utf-8"))
    assert int(payload.get("777", {}).get("cultivate", 0) or 0) >= int(telegram_bot.FEATURE_INTRO_VERSION)

    async def fake_http_get(url, **kwargs):
        if url.endswith("/api/user/lookup"):
            return {"success": True, "user_id": "u777"}
        if url.endswith("/api/cultivate/status/u777"):
            return {"success": True, "state": False}
        return {"success": False}

    monkeypatch.setattr(telegram_bot, "http_get", fake_http_get)

    second_context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    second_query = _Query(clicker_id=777, message=_Message(message_id=2), data="cultivate")
    second_update = SimpleNamespace(callback_query=second_query)

    asyncio.run(telegram_bot.callback_handler(second_update, second_context))

    assert second_query.edited
    assert "开始修炼？" in second_query.edited[-1][0]
    second_callbacks = _callbacks(second_query.edited[-1][1].get("reply_markup"))
    assert "cultivate_start" in second_callbacks
