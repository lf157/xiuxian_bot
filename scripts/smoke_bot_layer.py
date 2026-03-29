"""Bot-layer smoke test (simulates Telegram updates without network).

Covers most major bot flows by feeding Update objects into the handlers and
routing bot HTTP calls to the local Flask core test client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


@dataclass
class SimpleChat:
    id: int
    type: str = "private"


@dataclass
class SentMessage:
    chat: SimpleChat
    message_id: int
    text: str
    reply_markup: Any = None

    @property
    def chat_id(self) -> int:
        return self.chat.id


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.answered: list[dict] = []
        self.events: list[dict] = []
        self._next_msg_id = 1000
        self.id = 0
        self.username = "test_bot"
        # mimic telegram.Bot.defaults usage
        from types import SimpleNamespace
        self.defaults = SimpleNamespace(
            tzinfo=None,
            do_quote=False,
            quote=None,
            parse_mode=None,
            disable_web_page_preview=None,
        )

    async def initialize(self):
        return None

    async def shutdown(self):
        return None

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self._next_msg_id += 1
        msg = SentMessage(SimpleChat(int(chat_id)), self._next_msg_id, text, kwargs.get("reply_markup"))
        payload = {"type": "send", "chat_id": int(chat_id), "text": text, "reply_markup": kwargs.get("reply_markup"), "message": msg}
        self.sent.append(payload)
        self.events.append(payload)
        return msg

    async def edit_message_text(self, text: str, chat_id: Optional[int] = None, message_id: Optional[int] = None, **kwargs):
        chat_id = int(chat_id or 0)
        mid = int(message_id or 0) or self._next_msg_id
        msg = SentMessage(SimpleChat(chat_id), mid, text, kwargs.get("reply_markup"))
        payload = {"type": "edit", "chat_id": chat_id, "text": text, "reply_markup": kwargs.get("reply_markup"), "message": msg}
        self.edited.append(payload)
        self.events.append(payload)
        return msg

    async def answer_callback_query(self, *args, **kwargs):
        self.answered.append({"args": args, "kwargs": kwargs})
        return True

    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return None
        return _noop


class UpdateFactory:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot
        self.update_id = 1
        self.message_id = 1
        self.callback_id = 1

    def _next_update(self) -> int:
        self.update_id += 1
        return self.update_id

    def _next_message_id(self) -> int:
        self.message_id += 1
        return self.message_id

    def _next_callback_id(self) -> str:
        self.callback_id += 1
        return f"cb_{self.callback_id}"

    def message_update(self, *, user_id: int, text: str, chat_id: Optional[int] = None, first_name: str = "User"):
        from telegram import Update

        chat_id = int(chat_id or user_id)
        mid = self._next_message_id()
        entities = None
        if text.startswith("/"):
            cmd = text.split()[0]
            entities = [{"offset": 0, "length": len(cmd), "type": "bot_command"}]
        update_json = {
            "update_id": self._next_update(),
            "message": {
                "message_id": mid,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": first_name},
                "text": text,
                **({"entities": entities} if entities else {}),
            },
        }
        return Update.de_json(update_json, self.bot)

    def callback_update(self, *, user_id: int, data: str, chat_id: Optional[int] = None, message_id: Optional[int] = None, first_name: str = "User"):
        from telegram import Update

        chat_id = int(chat_id or user_id)
        mid = int(message_id or self._next_message_id())
        update_json = {
            "update_id": self._next_update(),
            "callback_query": {
                "id": self._next_callback_id(),
                "from": {"id": user_id, "is_bot": False, "first_name": first_name},
                "message": {
                    "message_id": mid,
                    "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": 0, "is_bot": True, "first_name": "Bot"},
                    "text": "panel",
                },
                "chat_instance": "test",
                "data": data,
            },
        }
        return Update.de_json(update_json, self.bot)


def _last_event(bot: FakeBot, *, chat_id: Optional[int] = None) -> Optional[dict]:
    for evt in reversed(bot.events):
        if chat_id is None or int(evt.get("chat_id", 0)) == int(chat_id):
            return evt
    return None


def _find_callback(markup, prefix: str, *, contains: Optional[str] = None) -> Optional[str]:
    if not markup:
        return None
    keyboard = getattr(markup, "inline_keyboard", None)
    if not keyboard:
        return None
    for row in keyboard:
        for btn in row:
            data = getattr(btn, "callback_data", None)
            if data and str(data).startswith(prefix):
                if contains and contains not in str(data):
                    continue
                return str(data)
    return None


def _find_callbacks(markup, prefix: str) -> list[str]:
    if not markup:
        return []
    keyboard = getattr(markup, "inline_keyboard", None)
    if not keyboard:
        return []
    results: list[str] = []
    for row in keyboard:
        for btn in row:
            data = getattr(btn, "callback_data", None)
            if data and str(data).startswith(prefix):
                results.append(str(data))
    return results


def _has_callback(markup, data: str) -> bool:
    if not markup:
        return False
    keyboard = getattr(markup, "inline_keyboard", None)
    if not keyboard:
        return False
    for row in keyboard:
        for btn in row:
            if getattr(btn, "callback_data", None) == data:
                return True
    return False


def _find_event(bot: FakeBot, *, chat_id: int, contains: str) -> Optional[dict]:
    for evt in reversed(bot.events):
        if int(evt.get("chat_id", 0)) != int(chat_id):
            continue
        if contains in (evt.get("text") or ""):
            return evt
    return None


def _extract_sect_id(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"ID\s+([A-Za-z0-9]+)", text)
    return m.group(1) if m else None


class _ErrorCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_last_text(bot: FakeBot, *, chat_id: int, label: str, contains: Optional[str] = None) -> dict:
    evt = _last_event(bot, chat_id=chat_id)
    if not evt:
        raise AssertionError(f"{label}: no bot output for chat {chat_id}")
    text = evt.get("text") or ""
    text_normalized = text
    if "\\u" in text:
        try:
            text_normalized = text.encode("utf-8").decode("unicode_escape")
        except Exception:
            text_normalized = text
    if "服务器错误" in text:
        raise AssertionError(f"{label}: server error response: {text}")
    if contains and contains not in text and contains not in text_normalized:
        raise AssertionError(f"{label}: expected '{contains}' in output, got: {text}")
    return evt


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.environ["XXBOT_INTERNAL_API_TOKEN"] = "test_internal_token"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    error_capture = _ErrorCapture()
    logging.getLogger().addHandler(error_capture)

    from core.database.connection import (
        connect_db,
        create_tables,
        execute,
        close_db,
        get_user_by_platform,
        add_item,
    )
    from core.database.migrations import run_migrations
    from core.server import create_app
    from core.game.items import (
        generate_material,
        generate_pill,
        generate_equipment,
        get_item_by_id,
        Quality,
    )
    from core.game.realms import get_next_realm

    connect_db()
    create_tables()
    run_migrations()

    app = create_app()
    client = app.test_client()

    import adapters.telegram.bot as tg
    tg.INTERNAL_API_TOKEN = os.environ["XXBOT_INTERNAL_API_TOKEN"]

    async def http_get(url, **kwargs):
        kwargs.pop("timeout", None)
        tg._inject_internal_token(kwargs, url=url)
        path = urlparse(str(url)).path
        params = kwargs.get("params") or None
        headers = kwargs.get("headers") or {}
        resp = client.get(path, query_string=params, headers=headers)
        return resp.get_json(silent=True) or {}

    async def http_post(url, **kwargs):
        kwargs.pop("timeout", None)
        tg._inject_internal_token(kwargs, url=url)
        path = urlparse(str(url)).path
        headers = kwargs.get("headers") or {}
        payload = kwargs.get("json")
        resp = client.post(path, data=json.dumps(payload or {}), headers=headers, content_type="application/json")
        return resp.get_json(silent=True) or {}

    tg.http_get = http_get
    tg.http_post = http_post

    fake_bot = FakeBot()

    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

    bot_app = Application.builder().bot(fake_bot).build()
    bot_app.add_handler(CommandHandler("start", tg.start_cmd))
    bot_app.add_handler(CommandHandler("register", tg.register_cmd))
    bot_app.add_handler(CommandHandler(["stat", "status"], tg.stat_cmd))
    bot_app.add_handler(CommandHandler(["cul", "cultivate"], tg.cultivate_cmd))
    bot_app.add_handler(CommandHandler("hunt", tg.hunt_cmd))
    bot_app.add_handler(CommandHandler(["break", "breakthrough"], tg.breakthrough_cmd))
    bot_app.add_handler(CommandHandler(["sign", "signin"], tg.signin_cmd))
    bot_app.add_handler(CommandHandler("shop", tg.shop_cmd))
    bot_app.add_handler(CommandHandler(["bag", "inventory"], tg.bag_cmd))
    bot_app.add_handler(CommandHandler(["quest", "quests", "task"], tg.quest_cmd))
    bot_app.add_handler(CommandHandler(["skills", "skill"], tg.skills_cmd))
    bot_app.add_handler(CommandHandler(["secret", "mystic"], tg.secret_realms_cmd))
    bot_app.add_handler(CommandHandler(["rank", "leaderboard"], tg.leaderboard_cmd))
    bot_app.add_handler(CommandHandler("pvp", tg.pvp_cmd))
    bot_app.add_handler(CommandHandler(["chat", "dao"], tg.chat_cmd))
    bot_app.add_handler(CommandHandler("sect", tg.sect_cmd))
    bot_app.add_handler(CommandHandler("alchemy", tg.alchemy_cmd))
    bot_app.add_handler(CommandHandler("convert", tg.convert_cmd))
    bot_app.add_handler(CommandHandler("gacha", tg.gacha_cmd))
    bot_app.add_handler(CommandHandler(["achievements", "ach"], tg.achievements_cmd))
    bot_app.add_handler(CommandHandler("codex", tg.codex_cmd))
    bot_app.add_handler(CommandHandler("events", tg.events_cmd))
    bot_app.add_handler(CommandHandler(["worldboss", "boss"], tg.worldboss_cmd))
    bot_app.add_handler(CommandHandler(["guide", "realms"], tg.guide_cmd))
    bot_app.add_handler(CommandHandler("version", tg.version_cmd))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg.text_message_handler))
    bot_app.add_handler(CallbackQueryHandler(tg.callback_handler))
    bot_app.add_error_handler(tg.global_error_handler)

    await bot_app.initialize()

    factory = UpdateFactory(fake_bot)

    user_a = 10001
    user_b = 10002
    user_c = 10003
    name_a = f"测试甲{int(time.time()) % 1000}"
    name_b = f"测试乙{int(time.time()) % 1000}"
    name_c = f"测试丙{int(time.time()) % 1000}"

    async def send(user_id: int, text: str, *, label: str | None = None, contains: str | None = None):
        await bot_app.process_update(factory.message_update(user_id=user_id, text=text, first_name="User"))
        if label:
            return _assert_last_text(fake_bot, chat_id=user_id, label=label, contains=contains)
        return _last_event(fake_bot, chat_id=user_id)

    async def click(
        user_id: int,
        data: str,
        *,
        label: str | None = None,
        contains: str | None = None,
        require_button: bool = True,
    ):
        evt = _last_event(fake_bot, chat_id=user_id)
        if not evt:
            raise AssertionError(f"click {data}: no prior bot panel for chat {user_id}")
        if require_button:
            markup = evt.get("reply_markup")
            if not _has_callback(markup, data):
                raise AssertionError(f"click {data}: callback not present on current panel")
        before_events = len(fake_bot.events)
        mid = evt.get("message").message_id if evt.get("message") else None
        await bot_app.process_update(factory.callback_update(user_id=user_id, data=data, message_id=mid, first_name="User"))
        if len(fake_bot.events) <= before_events:
            raise AssertionError(f"click {data}: no new bot output event generated")
        if label:
            return _assert_last_text(fake_bot, chat_id=user_id, label=label, contains=contains)
        return _last_event(fake_bot, chat_id=user_id)

    try:
        await send(user_a, "/start", label="start", contains="修仙")
        await send(user_a, f"/register {name_a}", label="register A", contains="注册成功")
        await send(user_b, f"/register {name_b}", label="register B", contains="注册成功")
        await send(user_c, "/start", label="start C", contains="修仙之旅")
        await click(user_c, "register", label="register button", contains="五行")
        await click(user_c, "register_fire", label="register element", contains="请输入游戏名")
        await send(user_c, name_c, label="register C", contains="注册成功")

        uid_a = (get_user_by_platform("telegram", str(user_a)) or {}).get("user_id")
        uid_b = (get_user_by_platform("telegram", str(user_b)) or {}).get("user_id")
        uid_c = (get_user_by_platform("telegram", str(user_c)) or {}).get("user_id")
        _assert(uid_a and uid_b and uid_c, "register failed: missing user ids")

        now = int(time.time())
        rank = 3
        execute(
            "UPDATE users SET copper = %s, gold = %s, stamina = %s, stamina_updated_at = %s, rank = %s, last_hunt_time = 0, last_secret_time = 0, last_enhance_time = 0 WHERE user_id = %s",
            (20000, 50, 24, now, rank, uid_a),
        )
        execute(
            "UPDATE users SET copper = %s, gold = %s, stamina = %s, stamina_updated_at = %s, rank = %s, last_hunt_time = 0, last_secret_time = 0 WHERE user_id = %s",
            (20000, 50, 24, now, rank, uid_b),
        )

        next_realm = get_next_realm(rank)
        if next_realm:
            execute("UPDATE users SET exp = %s WHERE user_id = %s", (int(next_realm.get("exp_required", 0) or 0), uid_a))

        # Seed items/materials for flows
        add_item(uid_a, generate_material("herb", 10))
        add_item(uid_a, generate_material("iron_ore", 30))
        add_item(uid_a, generate_pill("small_exp_pill", 2))
        add_item(uid_a, generate_pill("breakthrough_pill", 2))

        base_weapon = get_item_by_id("spirit_sword")
        base_armor = get_item_by_id("spirit_armor")
        _assert(base_weapon and base_armor, "missing base equipment definitions")
        eq1_id = add_item(uid_a, generate_equipment(base_weapon, Quality.COMMON, level=1))
        eq2_id = add_item(uid_a, generate_equipment(base_armor, Quality.COMMON, level=1))
        _assert(eq1_id and eq2_id, "failed to create equipment")
        # Seed extra equipment entries to ensure bag pagination callbacks exist.
        for _ in range(10):
            add_item(uid_a, generate_equipment(base_weapon, Quality.COMMON, level=1))

        await send(user_a, "/stat", label="stat", contains="境界")
        await send(user_a, "/start", label="menu_status", contains="修仙")
        await click(user_a, "status", label="status_callback", contains="境界")
        await click(user_a, "signin", label="signin_callback")
        await click(user_a, "guide", label="guide_callback", contains="玩法")

        # Cultivation flow (callback)
        await send(user_a, "/start", label="menu_cultivate", contains="修仙")
        evt = await click(user_a, "cultivate", label="cultivate_menu")
        start_cb = _find_callback(evt.get("reply_markup"), "cultivate_start") if evt else None
        if start_cb:
            await click(user_a, start_cb, label="cultivate_start")
            stat_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "cultivate_stat")
            if stat_cb:
                await click(user_a, stat_cb, label="cultivate_stat")
            end_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "cultivate_end")
            if end_cb:
                await click(user_a, end_cb, label="cultivate_end")

        # Shop flow (callback)
        await send(user_a, "/start", label="menu_shop", contains="修仙")
        evt = await click(user_a, "shop_all", label="shop_menu", contains="商店")
        shop_markup = evt.get("reply_markup") if evt else None
        shop_tabs = [x for x in _find_callbacks(shop_markup, "shop_") if x != "shop_all"]
        if shop_tabs:
            await click(user_a, shop_tabs[0], label="shop_tab_switch")
            shop_markup = _last_event(fake_bot, chat_id=user_a).get("reply_markup")
        buy_cb = _find_callback(shop_markup, "buy_")
        if buy_cb:
            await click(user_a, buy_cb, label="shop_buy")

        # Bag flow: use/equip/enhance/unequip/decompose
        await send(user_a, "/start", label="menu_bag", contains="修仙")
        evt = await click(user_a, "bag", label="bag", contains="背包")
        markup = evt.get("reply_markup") if evt else None
        equip_cb = _find_callback(markup, "equip_")
        decompose_cb = _find_callback(markup, "decompose_")
        _assert(bool(equip_cb), "bag missing equip button")
        _assert(bool(decompose_cb), "bag missing decompose button")
        equip_item_db_id = int(str(equip_cb).split("_", 1)[1])
        bag_pages = _find_callbacks(markup, "bag_")
        _assert(bool(bag_pages), "bag pagination callback missing")
        await click(user_a, bag_pages[0], label="bag_page")
        await send(user_a, "/start")
        evt = await click(user_a, "bag", label="bag_back_from_page")
        markup = evt.get("reply_markup") if evt else None
        use_cb = _find_callback(markup, "use_")
        if use_cb:
            await click(user_a, use_cb, label="use_item")
            await click(user_a, "bag", label="bag_after_use")
        await click(user_a, equip_cb, label="equip_item")
        await click(user_a, "bag", label="bag_after_equip")
        await click(user_a, f"enhance_{equip_item_db_id}", label="enhance_menu", contains="强化")
        await click(user_a, f"enhance_do_{equip_item_db_id}_steady", label="enhance_do")
        execute("UPDATE users SET last_enhance_time = 0 WHERE user_id = %s", (uid_a,))
        await click(user_a, "bag", label="bag_after_steady")
        await click(user_a, f"enhance_{equip_item_db_id}", label="enhance_menu_risky")
        await click(user_a, f"enhance_do_{equip_item_db_id}_risky", label="enhance_do_risky")
        execute("UPDATE users SET last_enhance_time = 0 WHERE user_id = %s", (uid_a,))
        await click(user_a, "bag", label="bag_after_risky")
        await click(user_a, f"enhance_{equip_item_db_id}", label="enhance_menu_focused")
        await click(user_a, f"enhance_do_{equip_item_db_id}_focused", label="enhance_do_focused")
        await click(user_a, "bag", label="bag_after_focused")
        # Ensure equipped panel has at least one equipped item before asserting unequip callbacks.
        execute("UPDATE users SET equipped_weapon = %s WHERE user_id = %s", (equip_item_db_id, uid_a))
        await click(user_a, "equipped_view", label="equipped_view")
        eq_markup = _last_event(fake_bot, chat_id=user_a).get("reply_markup")
        unequip_equipped_cb = _find_callback(eq_markup, "unequip_equipped_")
        legacy_unequip_cb = _find_callback(eq_markup, "unequip_") if not unequip_equipped_cb else None
        _assert(bool(unequip_equipped_cb or legacy_unequip_cb), "equipped panel missing unequip callback")
        if unequip_equipped_cb:
            await click(user_a, unequip_equipped_cb, label="unequip")
        else:
            await click(user_a, legacy_unequip_cb, label="unequip_legacy")
        await send(user_a, "/start")
        await click(user_a, "bag", label="bag_after_unequip")
        current_bag_markup = _last_event(fake_bot, chat_id=user_a).get("reply_markup")
        decompose_cb_current = _find_callback(current_bag_markup, "decompose_")
        _assert(bool(decompose_cb_current), "bag missing decompose callback after unequip")
        await click(user_a, decompose_cb_current, label="decompose")

        # Skills flow (learn + equip + unequip)
        await send(user_a, "/start", label="menu_skills", contains="修仙")
        evt = await click(user_a, "skills", label="skills", contains="技能")
        learned_skill_id: str | None = None
        learn_cb = _find_callback(evt.get("reply_markup"), "skill_learn_") if evt else None
        if learn_cb:
            learned_skill_id = learn_cb[len("skill_learn_"):]
            await click(user_a, learn_cb, label="skill_learn")
        evt = await click(user_a, "skills", label="skills_refresh")
        equip_cb = _find_callback(evt.get("reply_markup"), "skill_equip_") if evt else None
        if equip_cb:
            await click(user_a, equip_cb, label="skill_equip")
            evt = await click(user_a, "skills", label="skills_after_equip")
        unequip_cb = _find_callback((evt or {}).get("reply_markup"), "skill_unequip_") if evt else None
        if unequip_cb:
            await click(user_a, unequip_cb, label="skill_unequip")

        # Breakthrough (callback)
        await send(user_a, "/start", label="menu_breakthrough", contains="修仙")
        evt = await click(user_a, "breakthrough", label="breakthrough", contains="突破")
        bt_variants = ("breakthrough_steady", "breakthrough_protect", "breakthrough_desperate")
        for idx, bt_variant in enumerate(bt_variants):
            bt_cb = _find_callback((evt or {}).get("reply_markup"), bt_variant) if evt else None
            _assert(bool(bt_cb), f"{bt_variant} callback missing")
            await click(user_a, bt_cb, label=f"{bt_variant}_exec")
            if idx < len(bt_variants) - 1:
                await send(user_a, "/start")
                evt = await click(user_a, "breakthrough")
        # Backward-compatible callback forms (not exposed in current panel buttons).
        await click(user_a, "breakthrough_start", label="breakthrough_start_legacy", require_button=False)
        await click(user_a, "breakthrough_pill", label="breakthrough_pill_legacy", require_button=False)

        # Convert (callback)
        await send(user_a, "/start", label="menu_convert", contains="修仙")
        evt = await click(user_a, "convert_menu", label="convert", contains="转化")
        route_cb = _find_callback(evt.get("reply_markup"), "convert_route_") if evt else None
        if route_cb:
            await click(user_a, route_cb, label="convert_route")
            do_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "convert_do")
            if do_cb:
                _assert(do_cb.startswith("convert_do|"), "convert route panel should provide convert_do| callback")
                await click(user_a, do_cb, label="convert_do")
        # Legacy convert callback form.
        await click(user_a, "convert_do_steady_small_exp_pill_1", label="convert_do_legacy", require_button=False)

        # Alchemy (callback)
        await send(user_a, "/start", label="menu_alchemy", contains="修仙")
        evt = await click(user_a, "alchemy_menu", label="alchemy", contains="炼丹")
        brew_cb = _find_callback(evt.get("reply_markup"), "alchemy_brew_") if evt else None
        if brew_cb:
            await click(user_a, brew_cb, label="alchemy_brew")

        # Secret realms (callback), retry to ensure sbt_ branch execution
        secret_action_done = False
        for i in range(8):
            execute(
                "UPDATE users SET last_secret_time = 0, secret_realm_attempts = 0, stamina = 24, stamina_updated_at = %s WHERE user_id = %s",
                (int(time.time()), uid_a),
            )
            await send(user_a, "/start")
            evt = await click(
                user_a,
                "secret_realms",
                label="secret" if i == 0 else None,
                contains="秘境" if i == 0 else None,
            )
            info_cb = _find_callback(evt.get("reply_markup"), "secret_realm_info_") if evt else None
            if not info_cb:
                continue
            await click(user_a, info_cb, label="secret_info" if i == 0 else None)
            explore_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "secret_realm_explore_")
            if not explore_cb:
                continue
            await click(user_a, explore_cb, label="secret_explore" if i == 0 else None)
            skill_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "sbt_", contains="_s_")
            if not skill_cb:
                skill_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "sbt_")
            if skill_cb:
                await click(user_a, skill_cb, label="secret_action")
                secret_action_done = True
                break
        _assert(secret_action_done, "secret realm battle action callback not reached")

        # Hunt (callback) + recovery panel callbacks
        await send(user_a, "/start", label="menu_hunt", contains="修仙")
        evt = await click(user_a, "hunt", label="hunt", contains="怪物")
        hunt_cb = _find_callback(evt.get("reply_markup"), "hunt_") if evt else None
        if hunt_cb:
            await click(user_a, hunt_cb, label="hunt_start")
            hunt_skill_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "hbt_", contains="_s_")
            if not hunt_skill_cb:
                hunt_skill_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "hbt_")
            if hunt_skill_cb:
                await click(user_a, hunt_skill_cb, label="hunt_action")
        recover_auto_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "recover_auto_")
        if recover_auto_cb:
            await click(user_a, recover_auto_cb, label="recover_auto", contains="自动恢复")
        recovery_menu_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "recovery_menu_")
        if recovery_menu_cb:
            await click(user_a, recovery_menu_cb, label="recovery_menu")
        recover_use_cb = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "recover_use")
        if recover_use_cb:
            await click(user_a, recover_use_cb, label="recover_use")
        # Compatibility callback forms not guaranteed to appear on current panel.
        if not recover_auto_cb:
            await click(user_a, "recover_auto_hunt", label="recover_auto_injected", contains="自动恢复", require_button=False)
        if not recovery_menu_cb:
            await click(user_a, "recovery_menu_hunt", label="recovery_menu_injected", require_button=False)
        if not recover_use_cb:
            await click(user_a, "recover_use|small_exp_pill|hunt", label="recover_use_injected", require_button=False)
        await click(user_a, "recover_use_small_exp_pill", label="recover_use_legacy", require_button=False)

        # Forge (normal + targeted)
        await send(user_a, "/start", label="menu_forge", contains="修仙")
        await click(user_a, "forge", label="forge_menu", contains="锻造")
        forge_target = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "forge_target_")
        _assert(bool(forge_target), "forge target callback missing")
        await click(user_a, forge_target, label="forge_target")
        await send(user_a, "/start", label="menu_forge_refresh", contains="修仙")
        await click(user_a, "forge", label="forge_menu_refresh")
        forge_do = _find_callback(_last_event(fake_bot, chat_id=user_a).get("reply_markup"), "forge_do")
        if forge_do:
            await click(user_a, forge_do, label="forge_do")

        # Gacha (callback)
        await send(user_a, "/start", label="menu_gacha", contains="修仙")
        evt = await click(user_a, "gacha_menu", label="gacha", contains="抽")
        gacha_cb = _find_callback(evt.get("reply_markup"), "gacha_pull_") if evt else None
        if gacha_cb:
            await click(user_a, gacha_cb, label="gacha_pull")

        # PVP callbacks
        await send(user_a, "/start", label="menu_pvp", contains="修仙")
        await click(user_a, "social_menu", label="pvp_social_menu", contains="社交")
        evt = await click(user_a, "pvp_menu", label="pvp_menu", contains="PVP")
        await click(user_a, "pvp_records", label="pvp_records")
        await click(user_a, "pvp_menu", label="pvp_back_from_records")
        await click(user_a, "pvp_ranking", label="pvp_ranking")
        await click(user_a, "pvp_menu", label="pvp_back_from_ranking")
        evt = _last_event(fake_bot, chat_id=user_a)
        pvp_cb = _find_callback(evt.get("reply_markup"), "pvp_challenge_") if evt else None
        _assert(bool(pvp_cb), "pvp challenge callback missing")
        await click(user_a, pvp_cb, label="pvp_challenge")

        # Social menu + text-prompt chat + reject
        await send(user_a, "/start", label="menu_social", contains="修仙")
        await click(user_a, "social_menu", label="social_menu", contains="社交")
        await click(user_a, "main_menu", label="main_menu_back", contains="境界")
        await click(user_a, "social_menu", label="social_menu_again", contains="社交")
        await click(user_a, "chat_prompt", label="chat_prompt", contains="请输入要论道")
        await send(user_a, name_b, label="chat_request_text", contains="论道")
        notify_evt = _find_event(fake_bot, chat_id=user_b, contains="论道")
        reject_cb = _find_callback(notify_evt.get("reply_markup"), "chat_reject_") if notify_evt else None
        if reject_cb:
            await click(user_b, reject_cb, label="chat_reject")

        # Chat + accept
        await send(user_a, f"/chat {name_b}", label="chat_request_cmd", contains="论道")
        notify_evt = _find_event(fake_bot, chat_id=user_b, contains="论道")
        accept_cb = _find_callback(notify_evt.get("reply_markup"), "chat_accept_") if notify_evt else None
        if accept_cb:
            await click(user_b, accept_cb, label="chat_accept")

        # Sect menu + create via prompt
        await send(user_a, "/start", label="menu_sect", contains="修仙")
        await click(user_a, "sect_menu", label="sect_menu", contains="宗门")
        await click(user_a, "sect_create_prompt", label="sect_prompt")
        await send(user_a, "天火宗 自动化测试", label="sect_create")
        await send(user_b, "/sect list", label="sect_list", contains="宗门列表")
        evt = _last_event(fake_bot, chat_id=user_b)
        sect_id = _extract_sect_id(evt.get("text", "")) if evt else None
        if sect_id:
            await send(user_b, f"/sect join {sect_id}", label="sect_join")
        await send(user_a, "/sect info", label="sect_info")
        await send(user_a, "/sect donate 100", label="sect_donate")
        await send(user_a, "/sect quests", label="sect_quests", contains="宗门任务")

        # Quests callback
        await send(user_a, "/start", label="menu_quests", contains="修仙")
        await click(user_a, "quests", label="quests", contains="任务")
        q_evt = _last_event(fake_bot, chat_id=user_a)
        q_cb = _find_callback(q_evt.get("reply_markup"), "quest_claim_") if q_evt else None
        if q_cb:
            await click(user_a, q_cb, label="quest_claim")

        # Achievements callback
        await send(user_a, "/start", label="menu_achievements", contains="修仙")
        evt = await click(user_a, "achievements_menu", label="achievements", contains="成就")
        ach_cb = _find_callback(evt.get("reply_markup"), "ach_claim_") if evt else None
        if ach_cb:
            await click(user_a, ach_cb, label="achievement_claim")

        # Events callback
        await send(user_a, "/start", label="menu_events", contains="修仙")
        evt = await click(user_a, "events_menu", label="events", contains="活动")
        event_cb = _find_callback(evt.get("reply_markup"), "event_claim_") if evt else None
        if event_cb:
            await click(user_a, event_cb, label="event_claim")

        # Worldboss callback
        await send(user_a, "/start", label="menu_worldboss", contains="修仙")
        await click(user_a, "worldboss_menu", label="worldboss", contains="BOSS")
        await click(user_a, "worldboss_attack", label="worldboss_attack")

        # Leaderboard callback modes
        await send(user_a, "/start", label="menu_leaderboard", contains="修仙")
        await click(user_a, "leaderboard_stage", label="leaderboard_stage", contains="排行榜")
        await click(user_a, "leaderboard_power", label="leaderboard_power")
        await click(user_a, "leaderboard_exp_growth", label="leaderboard_exp_growth")
        await click(user_a, "leaderboard_realm_loot", label="leaderboard_realm_loot")
        await click(user_a, "leaderboard_alchemy_output", label="leaderboard_alchemy_output")
        await click(user_a, "leaderboard_hunt", label="leaderboard_hunt")

        # Deprecated social callbacks + unknown callback fallback branch
        await click(user_a, "gift_menu", label="social_deprecated", contains="下线", require_button=False)
        await click(user_a, "social_chat_menu", label="social_deprecated2", contains="下线", require_button=False)
        await click(user_a, "friends_menu", label="friends_menu_deprecated", contains="下线", require_button=False)
        await click(user_a, "friends_add_help", label="friends_add_help_deprecated", contains="下线", require_button=False)
        await click(user_a, "friends_list", label="friends_list_deprecated", contains="下线", require_button=False)
        await click(user_a, "friends_requests", label="friends_requests_deprecated", contains="下线", require_button=False)
        await click(user_a, "friends_add_foo", label="friends_prefix_deprecated", contains="下线", require_button=False)
        await click(user_a, "social_chat_foo", label="social_chat_prefix_deprecated", contains="下线", require_button=False)
        await click(user_a, "__unknown_callback__", label="callback_fallback", contains="已失效", require_button=False)

        # Codex command coverage
        await send(user_a, "/codex", label="codex_all", contains="图鉴")
        await send(user_a, "/codex monsters", label="codex_monsters", contains="怪物")
        await send(user_a, "/codex items", label="codex_items", contains="物品")

        # Command-level sanity checks for remaining entry points
        await send(user_a, "/events", label="events_cmd", contains="活动")
        await send(user_a, "/worldboss", label="worldboss_cmd", contains="BOSS")
        await send(user_a, "/rank", label="rank_cmd", contains="排行榜")
        await send(user_a, "/version", label="version", contains="修仙Bot")
        # Trigger command aliases to cover all CommandHandler entry aliases.
        for cmd in (
            "/status",
            "/cul",
            "/cultivate",
            "/hunt",
            "/break",
            "/breakthrough",
            "/sign",
            "/signin",
            "/shop",
            "/bag",
            "/inventory",
            "/quest",
            "/quests",
            "/task",
            "/skills",
            "/skill",
            "/secret",
            "/mystic",
            "/leaderboard",
            "/pvp",
            "/dao",
            "/alchemy",
            "/convert",
            "/gacha",
            "/achievements",
            "/ach",
            "/boss",
            "/guide",
            "/realms",
        ):
            await send(user_a, cmd, label=f"alias_{cmd[1:]}")

        if error_capture.records:
            details = "; ".join(f"{r.name}:{r.getMessage()}" for r in error_capture.records[:3])
            raise AssertionError(f"bot errors captured: {details}")

        print("OK: bot-layer smoke suite passed.")
    finally:
        await bot_app.shutdown()
        close_db()


if __name__ == "__main__":
    asyncio.run(main())
