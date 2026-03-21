import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

_HAS_DEPS = all(
    importlib.util.find_spec(name) is not None
    for name in ("aiogram", "telegram", "psutil")
)

if _HAS_DEPS:
    from adapters.aiogram import legacy_bridge
else:  # pragma: no cover - skip-only branch for minimal test envs
    legacy_bridge = None

pytestmark = pytest.mark.skipif(
    not _HAS_DEPS,
    reason="requires aiogram/telegram/psutil",
)


class _DummyBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        return None


class _ButtonTypeInvalidBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        reply_markup = kwargs.get("reply_markup")
        rows = getattr(reply_markup, "inline_keyboard", []) if reply_markup is not None else []
        has_web_app = any(getattr(btn, "web_app", None) is not None for row in rows for btn in row)
        if has_web_app:
            raise Exception("Telegram server says - Bad Request: BUTTON_TYPE_INVALID")
        return None


def test_reply_text_keeps_message_thread_id():
    bot = _DummyBot()
    msg = legacy_bridge._CompatMessage(
        bot,
        None,
        chat_id=-10012345,
        message_id=11,
        message_thread_id=77,
        chat=SimpleNamespace(id=-10012345),
    )

    asyncio.run(msg.reply_text("hello"))

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == -10012345
    assert bot.calls[0]["message_thread_id"] == 77


def test_reply_text_thread_id_can_be_overridden():
    bot = _DummyBot()
    msg = legacy_bridge._CompatMessage(
        bot,
        None,
        chat_id=-10012345,
        message_id=12,
        message_thread_id=77,
        chat=SimpleNamespace(id=-10012345),
    )

    asyncio.run(msg.reply_text("hello", message_thread_id=99))

    assert len(bot.calls) == 1
    assert bot.calls[0]["message_thread_id"] == 99


def test_normalize_message_kwargs_keeps_reply_context():
    payload = legacy_bridge._normalize_message_kwargs(
        {
            "reply_to_message_id": "123",
            "message_thread_id": "456",
            "allow_sending_without_reply": True,
        }
    )

    assert payload["reply_to_message_id"] == 123
    assert payload["message_thread_id"] == 456
    assert payload["allow_sending_without_reply"] is True


def test_compat_message_keeps_reply_to_message_reference():
    bot = _DummyBot()
    reply = SimpleNamespace(from_user=SimpleNamespace(id=123456, is_bot=False))
    msg = legacy_bridge._CompatMessage(
        bot,
        None,
        chat_id=-10012345,
        message_id=13,
        chat=SimpleNamespace(id=-10012345, type="group"),
        reply_to_message=reply,
    )

    assert msg.reply_to_message is reply
    assert msg.reply_to_message.from_user.id == 123456


def test_convert_inline_keyboard_preserves_web_app_button():
    rows = legacy_bridge._convert_inline_keyboard(
        [[{"text": "🏯 进入修仙世界", "web_app": {"url": "https://ohxiuxian.cc.cd"}}]]
    )

    assert len(rows) == 1
    assert len(rows[0]) == 1
    button = rows[0][0]
    assert str(button.text) == "🏯 进入修仙世界"
    assert button.web_app is not None
    assert str(button.web_app.url) == "https://ohxiuxian.cc.cd"


def test_convert_inline_keyboard_downgrades_web_app_button_when_requested():
    rows = legacy_bridge._convert_inline_keyboard(
        [[{"text": "🏯 进入修仙世界", "web_app": {"url": "https://ohxiuxian.cc.cd"}}]],
        drop_web_app=True,
    )

    assert len(rows) == 1
    assert len(rows[0]) == 1
    button = rows[0][0]
    assert str(button.text) == "🏯 进入修仙世界"
    assert button.web_app is None
    assert str(button.callback_data) == "miniapp_private_hint"


def test_reply_text_retries_with_downgraded_web_app_button():
    bot = _ButtonTypeInvalidBot()
    msg = legacy_bridge._CompatMessage(
        bot,
        None,
        chat_id=10001,
        message_id=1,
        chat=SimpleNamespace(id=10001),
    )

    asyncio.run(
        msg.reply_text(
            "hello",
            reply_markup={
                "inline_keyboard": [[{"text": "🏯 进入修仙世界", "web_app": {"url": "https://ohxiuxian.cc.cd"}}]]
            },
        )
    )

    assert len(bot.calls) == 2
    first_rows = getattr(bot.calls[0].get("reply_markup"), "inline_keyboard", [])
    second_rows = getattr(bot.calls[1].get("reply_markup"), "inline_keyboard", [])
    assert getattr(first_rows[0][0], "web_app", None) is not None
    assert getattr(second_rows[0][0], "web_app", None) is None
    assert str(getattr(second_rows[0][0], "callback_data", "")) == "miniapp_private_hint"
