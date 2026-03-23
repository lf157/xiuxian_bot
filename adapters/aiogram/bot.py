"""aiogram adapter entrypoint (full FSM coverage)."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.fsm.strategy import FSMStrategy

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from adapters.aiogram.handlers import root_router
from adapters.aiogram.services.api_client import close_http_session
from core.config import config
from core.utils.runtime_logging import setup_runtime_logging

logger = setup_runtime_logging("aiogram", project_root=ROOT_DIR, stats_interval_seconds=120)


def _telegram_token() -> str:
    token_source = config.telegram_token
    if callable(token_source):
        return str(token_source() or "").strip()
    return str(token_source or "").strip()


def _redis_client_from_storage(storage: RedisStorage) -> Any | None:
    client = getattr(storage, "redis", None)
    if client is not None:
        return client
    return getattr(storage, "_redis", None)


async def _ensure_redis_available(storage: RedisStorage) -> None:
    client = _redis_client_from_storage(storage)
    if client is None:
        raise RuntimeError("RedisStorage unavailable: redis client not initialized")
    try:
        await client.ping()
    except Exception as exc:
        raise RuntimeError(f"RedisStorage unavailable: {exc}") from exc


async def _purge_legacy_fsm_keys(storage: RedisStorage) -> None:
    if not config.redis_purge_legacy_fsm_prefixes:
        return
    client = _redis_client_from_storage(storage)
    if client is None:
        logger.warning("legacy_fsm_purge_skipped: redis client unavailable")
        return

    match_prefix = "xxbot:fsm:*"
    keep_prefix = str(config.redis_fsm_key_prefix or "xxbot:fsm:v2:").strip() or "xxbot:fsm:v2:"
    deleted = 0
    cursor: int | str = 0

    while True:
        cursor, keys = await client.scan(cursor=cursor, match=match_prefix, count=200)
        for key in keys:
            text = key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key)
            if text.startswith(keep_prefix):
                continue
            await client.delete(key)
            deleted += 1
        if str(cursor) == "0":
            break

    logger.info("legacy_fsm_purge_deleted=%s", deleted)


async def main() -> None:
    token = _telegram_token()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN/XXBOT_TELEGRAM_TOKEN")
    if not config.redis_enabled:
        raise RuntimeError("Redis FSM is required, but redis.enabled is false")

    bot = Bot(token=token)
    storage = RedisStorage.from_url(
        config.redis_url,
        key_builder=DefaultKeyBuilder(with_destiny=False, prefix=config.redis_fsm_key_prefix),
    )
    dispatcher = Dispatcher(storage=storage, fsm_strategy=FSMStrategy.USER_IN_CHAT)
    dispatcher.include_router(root_router)

    logger.info("aiogram adapter starting (redis_fsm)")
    try:
        await _ensure_redis_available(storage)
        await _purge_legacy_fsm_keys(storage)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await dispatcher.storage.close()
        await close_http_session()
        await bot.session.close()
        logger.info("aiogram adapter stopped")


if __name__ == "__main__":
    asyncio.run(main())
