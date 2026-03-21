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
