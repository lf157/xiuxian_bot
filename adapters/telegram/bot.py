"""
修仙 Telegram Bot - 完整版
"""

from __future__ import annotations

import atexit
import os
import sys
import logging
import json
import re
import time
import uuid
import random
import aiohttp
import psutil
from functools import wraps
from urllib.parse import urlparse

from telegram import (
    Update,
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ForceReply,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.config import config
from core.utils.runtime_logging import setup_runtime_logging, install_asyncio_exception_logging
from adapters.actor_paths import compiled_actor_path_patterns
from core.commands.registry import registry, CommandDef
from core.utils.account_status import format_status_text
from core.game.realms import (
    format_realm_progress, get_all_realms_summary, 
    can_breakthrough, attempt_breakthrough, calculate_breakthrough_cost, get_next_realm, get_realm_by_id,
    ELEMENT_BONUSES, REALMS
)
from core.game.combat import (
    get_available_monsters, hunt_monster, format_monster_list, get_monster_by_id
)
from core.game.skills import format_skill_mp_cost, get_skill
from core.game.secret_realms import get_available_secret_realms
from core.game.items import (
    get_item_by_id as get_item_def,
    get_currency_role,
    get_progression_stage_theme,
    TARGETED_REALM_DROPS,
)
from core.game.quests import get_quest_def, get_all_quest_defs

PLATFORM = "telegram"

TELEGRAM_VERSION = "2.0.0"
CORE_VERSION = os.getenv("CORE_VERSION", "DEV")

LOG_DIR = os.path.abspath(os.path.join(ROOT_DIR, "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
PANEL_OWNER_CACHE_PATH = os.path.join(LOG_DIR, "panel_owners.json")
PANEL_OWNER_CACHE_LIMIT = 5000
FEATURE_INTRO_CACHE_PATH = os.path.join(LOG_DIR, "feature_intro_seen.json")
FEATURE_INTRO_USER_LIMIT = 50000
FEATURE_INTRO_VERSION = 2
_FEATURE_INTRO_KEYS = frozenset({"shop_all", "cultivate", "sect_menu", "world_map"})
_FEATURE_INTRO_TEXTS: dict[str, str] | None = None

_LOG_HANDLERS = [logging.StreamHandler()]
if os.getenv("XIUXIAN_CAPTURED_STDIO") != "1":
    _LOG_HANDLERS.insert(0, logging.FileHandler(os.path.join(LOG_DIR, "telegram.log"), encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_LOG_HANDLERS,
)
logger = setup_runtime_logging("telegram", project_root=ROOT_DIR, stats_interval_seconds=120)
logging.getLogger("httpx").setLevel(logging.WARNING)
_PID_LOCK_PATH = os.path.join(LOG_DIR, "telegram.pid")
_MENU_COMMANDS = [
    BotCommand("xian_start", "Start"),
    BotCommand("xian_register", "Register"),
    BotCommand("xian_stat", "Status"),
    BotCommand("xian_cul", "Cultivate"),
    BotCommand("xian_hunt", "Hunt"),
    BotCommand("xian_break", "Breakthrough"),
    BotCommand("xian_shop", "Shop"),
    BotCommand("xian_bag", "Inventory"),
    BotCommand("xian_quest", "Quests"),
    BotCommand("xian_skills", "Skills"),
    BotCommand("xian_secret", "Secret realm"),
    BotCommand("xian_rank", "Rankings"),
    BotCommand("xian_pvp", "PVP"),
    BotCommand("xian_chat", "Chat"),
    BotCommand("xian_sect", "Sect"),
    BotCommand("xian_alchemy", "Alchemy"),
    BotCommand("xian_currency", "Currency"),
    BotCommand("xian_convert", "Convert"),
    BotCommand("xian_achievements", "Achievements"),
    BotCommand("xian_codex", "Codex"),
    BotCommand("xian_events", "Events"),
    BotCommand("xian_bounty", "Bounty"),
    BotCommand("xian_worldboss", "World boss"),
    BotCommand("xian_guide", "Guide"),
    BotCommand("xian_version", "Version"),
]

ADMIN_GIVE_CURRENCY_FIELDS: dict[str, tuple[str, str]] = {
    "low": ("copper", "下品灵石"),
    "mid": ("gold", "中品灵石"),
    "high": ("spirit_high", "上品灵石"),
    "uhigh": ("spirit_exquisite", "精品灵石"),
    "xhigh": ("spirit_supreme", "极品灵石"),
}

ADMIN_PANEL_ACTION_LABELS: dict[str, str] = {
    "set": "设置",
    "add": "增加",
    "minus": "扣减",
}

ADMIN_PRESET_SPECS: list[dict[str, object]] = [
    {"id": "c1k", "label": "下灵+1k", "kind": "modify", "field": "copper", "action": "add", "value": 1_000},
    {"id": "c1w", "label": "下灵+1w", "kind": "modify", "field": "copper", "action": "add", "value": 10_000},
    {"id": "c10w", "label": "下灵+10w", "kind": "modify", "field": "copper", "action": "add", "value": 100_000},
    {"id": "g10", "label": "中灵+10", "kind": "modify", "field": "gold", "action": "add", "value": 10},
    {"id": "g100", "label": "中灵+100", "kind": "modify", "field": "gold", "action": "add", "value": 100},
    {"id": "g1000", "label": "中灵+1000", "kind": "modify", "field": "gold", "action": "add", "value": 1_000},
    {"id": "h10", "label": "上灵+10", "kind": "modify", "field": "spirit_high", "action": "add", "value": 10},
    {"id": "u10", "label": "精品+10", "kind": "modify", "field": "spirit_exquisite", "action": "add", "value": 10},
    {"id": "x10", "label": "极品+10", "kind": "modify", "field": "spirit_supreme", "action": "add", "value": 10},
    {"id": "e1w", "label": "修为+1w", "kind": "modify", "field": "exp", "action": "add", "value": 10_000},
    {"id": "e10w", "label": "修为+10w", "kind": "modify", "field": "exp", "action": "add", "value": 100_000},
    {"id": "r1", "label": "境界+1", "kind": "modify", "field": "rank", "action": "add", "value": 1},
    {"id": "st24", "label": "精力=24", "kind": "modify", "field": "stamina", "action": "set", "value": 24},
    {"id": "st10", "label": "精力+10", "kind": "modify", "field": "stamina", "action": "add", "value": 10},
    {"id": "pity1", "label": "破境计数+1", "kind": "modify", "field": "breakthrough_pity", "action": "add", "value": 1},
    {"id": "heal", "label": "满血满蓝", "kind": "heal_full"},
    {"id": "hunt0", "label": "狩猎清零", "kind": "hunt_reset"},
    {"id": "pvp0", "label": "PVP日清", "kind": "pvp_daily_reset"},
]
ADMIN_PRESET_MAP: dict[str, dict[str, object]] = {
    str(item.get("id") or ""): item for item in ADMIN_PRESET_SPECS if str(item.get("id") or "")
}


def _is_live_telegram_adapter(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return False
        cmdline = " ".join(process.cmdline()).lower()
        return "adapters" in cmdline and "telegram" in cmdline and "bot.py" in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _release_pid_lock() -> None:
    try:
        with open(_PID_LOCK_PATH, "r", encoding="utf-8") as handle:
            current_pid = handle.read().strip()
    except OSError:
        return

    if current_pid != str(os.getpid()):
        return

    try:
        os.remove(_PID_LOCK_PATH)
    except OSError:
        pass


def _acquire_pid_lock() -> bool:
    for _attempt in range(2):
        try:
            fd = os.open(_PID_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing_pid = None
            try:
                with open(_PID_LOCK_PATH, "r", encoding="utf-8") as handle:
                    existing_pid = int((handle.read() or "").strip())
            except (OSError, ValueError):
                existing_pid = None

            if existing_pid and existing_pid != os.getpid() and _is_live_telegram_adapter(existing_pid):
                logger.error(
                    "Telegram adapter already running with PID %s; refusing to start a second polling instance",
                    existing_pid,
                )
                return False

            try:
                os.remove(_PID_LOCK_PATH)
            except OSError:
                return False
            continue

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        atexit.register(_release_pid_lock)
        return True

    return False


def _mask_bot_token(token_value: str) -> str:
    token = str(token_value or "")
    if not token:
        return "****"
    if len(token) <= 10:
        return token[:2] + "****"
    return token[:6] + "..." + token[-4:]


def _mask_telegram_bot_urls(text: str) -> str:
    return re.sub(
        r"(https://api\.telegram\.org/bot)([^/\s]+)",
        lambda m: m.group(1) + _mask_bot_token(m.group(2)),
        str(text or ""),
    )


def _markdown_safe_tg_mention(username: str | None) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "", str(username or "").strip())
    if not clean:
        return ""
    return "@" + clean.replace("_", "\\_")


class _TelegramTokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        masked = _mask_telegram_bot_urls(msg)
        if masked != msg:
            record.msg = masked
            record.args = ()
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_TelegramTokenRedactionFilter())

DEFAULT_SERVER_PORT = 11450
SERVER_URL = str(getattr(config, "core_server_url", "") or f"http://127.0.0.1:{DEFAULT_SERVER_PORT}").rstrip("/")
INTERNAL_API_TOKEN = (config.internal_api_token or "").strip()
MINIAPP_URL = str(getattr(config, "miniapp_url", "") or "").strip()
TELEGRAM_POLL_TIMEOUT = max(1, int(os.getenv("TELEGRAM_POLL_TIMEOUT", "3")))
TELEGRAM_PROXY_URL = (os.getenv("TELEGRAM_PROXY_URL", "") or "").strip() or None
_HTTP_SESSION: aiohttp.ClientSession | None = None
_ACTOR_PATH_PATTERNS = compiled_actor_path_patterns()


def _cfg_int(*path: str, default: int) -> int:
    try:
        return int(config.get_nested(*path, default=default))
    except (TypeError, ValueError):
        return int(default)


def _cfg_float(*path: str, default: float) -> float:
    try:
        return float(config.get_nested(*path, default=default))
    except (TypeError, ValueError):
        return float(default)


def _vitals_regen_desc() -> str:
    regen_seconds = max(1, _cfg_int("battle", "mp", "regen_seconds", default=60))
    regen_pct = max(0.0, _cfg_float("battle", "mp", "regen_pct", default=0.03))
    pct = regen_pct * 100.0
    pct_text = f"{int(round(pct))}%" if abs(pct - round(pct)) < 1e-6 else f"{pct:.1f}%"
    cadence = "每分钟" if regen_seconds == 60 else f"每{regen_seconds}秒"
    return f"HP/MP {cadence}恢复 {pct_text}"


def _gacha_free_daily_limit() -> int:
    return max(0, _cfg_int("gacha", "free_daily_limit", default=3))


def _gacha_paid_daily_limit() -> int:
    return max(1, _cfg_int("gacha", "paid_daily_limit", default=15))


def _gacha_five_pull_count() -> int:
    return max(2, _cfg_int("gacha", "five_pull_count", default=5))


def _gacha_five_pull_price_gold() -> int:
    return max(1, _cfg_int("gacha", "five_pull_price_gold", default=4))


def _gacha_five_pull_stamina() -> int:
    return max(1, _cfg_int("gacha", "five_pull_stamina", default=4))


def _gacha_single_pull_stamina() -> int:
    return max(0, _cfg_int("gacha", "single_pull_stamina", default=1))


def _gacha_five_pull_price_mult_non_gold() -> int:
    return max(1, _cfg_int("gacha", "five_pull_price_mult_non_gold", default=4))


def _sect_text_cfg() -> dict[str, float | int]:
    return {
        "create_copper": max(0, _cfg_int("sect", "create_copper", default=5000)),
        "create_gold": max(0, _cfg_int("sect", "create_gold", default=10)),
        "base_max_members": max(1, _cfg_int("sect", "base_max_members", default=10)),
        "branch_max": max(1, _cfg_int("sect", "branch_max", default=5)),
        "branch_create_copper": max(0, _cfg_int("sect", "branch_create_copper", default=3000)),
        "branch_create_gold": max(0, _cfg_int("sect", "branch_create_gold", default=3)),
        "branch_max_members": max(1, _cfg_int("sect", "branch_max_members", default=5)),
        "branch_buff_rate": max(0.0, _cfg_float("sect", "branch_buff_rate", default=0.9)),
        "default_cultivation_buff_pct": _cfg_float("sect", "default_cultivation_buff_pct", default=10.0),
        "default_stat_buff_pct": _cfg_float("sect", "default_stat_buff_pct", default=5.0),
        "default_battle_reward_buff_pct": _cfg_float("sect", "default_battle_reward_buff_pct", default=10.0),
    }


def _breakthrough_fire_bonus() -> float:
    return _cfg_float("balance", "breakthrough", "fire_bonus", default=0.03)


def _breakthrough_steady_bonus() -> float:
    return _cfg_float("balance", "breakthrough", "steady_bonus", default=0.10)


def _breakthrough_stamina_cost() -> int:
    return max(1, _cfg_int("balance", "breakthrough", "stamina_cost", default=1))


def _breakthrough_ally_help_bonus() -> float:
    return _cfg_float("balance", "breakthrough", "ally_help_bonus", default=0.06)


def _breakthrough_spirit_density_bonus_scale() -> float:
    return _cfg_float("balance", "breakthrough", "spirit_density_bonus_scale", default=0.20)


def _breakthrough_tribulation_flat_penalty() -> float:
    return max(0.0, min(0.95, _cfg_float("balance", "breakthrough", "tribulation_flat_penalty", default=0.10)))


def _breakthrough_tribulation_rate_multiplier() -> float:
    return max(0.05, min(1.0, _cfg_float("balance", "breakthrough", "tribulation_rate_multiplier", default=0.70)))


def _breakthrough_tribulation_extra_cost_multiplier() -> float:
    return max(1.0, _cfg_float("balance", "breakthrough", "tribulation_extra_cost_multiplier", default=1.20))


def _breakthrough_tribulation_extra_stamina() -> int:
    return max(0, _cfg_int("balance", "breakthrough", "tribulation_extra_stamina", default=1))


def _breakthrough_protect_material_need(rank: int) -> int:
    base = max(0, _cfg_int("balance", "breakthrough", "protect_material_base", default=2))
    per_10 = max(0, _cfg_int("balance", "breakthrough", "protect_material_per_10_rank", default=1))
    return base + max(0, int(rank or 1) // 10) * per_10


def _is_consummation_breakthrough_rank(rank: int) -> bool:
    realm = get_realm_by_id(int(rank or 1)) or {}
    try:
        sub_level = int(realm.get("sub_level", 0) or 0)
    except (TypeError, ValueError):
        sub_level = 0
    if sub_level == 4:
        return True
    return "圆满" in str(realm.get("name") or "")


def _super_admin_tg_ids() -> set[str]:
    raw = str(os.getenv("SUPER_ADMIN_TG_IDS", "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _is_super_admin_tg(user_id: str | int | None) -> bool:
    return str(user_id or "").strip() in _super_admin_tg_ids()


def _build_telegram_request(*, for_updates: bool) -> HTTPXRequest:
    timeout = TELEGRAM_POLL_TIMEOUT + 5 if for_updates else 15
    return HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=float(timeout),
        write_timeout=15.0,
        pool_timeout=5.0,
        proxy=TELEGRAM_PROXY_URL,
        httpx_kwargs={"trust_env": False},
    )


# ==================== HTTP 工具 ====================

def _extract_actor_user_id(url: str, kwargs: dict) -> str | None:
    payload = kwargs.get("json")
    if isinstance(payload, dict):
        actor_user_id = payload.get("user_id")
        if actor_user_id:
            return str(actor_user_id)
    params = kwargs.get("params")
    if isinstance(params, dict):
        actor_user_id = params.get("user_id")
        if actor_user_id:
            return str(actor_user_id)
    path = urlparse(str(url or "")).path
    for pattern in _ACTOR_PATH_PATTERNS:
        matched = pattern.match(path)
        if matched:
            return str(matched.group(1))
    return None


def _inject_internal_token(kwargs: dict, *, url: str) -> None:
    headers = dict(kwargs.get("headers") or {})
    if INTERNAL_API_TOKEN:
        headers.setdefault("X-Internal-Token", INTERNAL_API_TOKEN)
    actor_user_id = _extract_actor_user_id(url, kwargs)
    if actor_user_id:
        headers.setdefault("X-Actor-User-Id", actor_user_id)
    kwargs["headers"] = headers

async def _get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        _HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _HTTP_SESSION


async def _close_http_session() -> None:
    global _HTTP_SESSION
    if _HTTP_SESSION is not None and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
    _HTTP_SESSION = None


async def _request_json(method: str, url: str, **kwargs):
    kwargs.pop("timeout", None)
    _inject_internal_token(kwargs, url=url)
    session = await _get_http_session()
    async with session.request(method, url, **kwargs) as resp:
        try:
            return await resp.json(content_type=None)
        except Exception:
            text = await resp.text()
            try:
                return json.loads(text)
            except Exception:
                logger.error(
                    "core_non_json_response method=%s url=%s status=%s content_type=%s body=%s",
                    method,
                    url,
                    int(getattr(resp, "status", 0) or 0),
                    getattr(resp, "headers", {}).get("Content-Type", ""),
                    text[:500],
                )
                return {
                    "success": False,
                    "code": "NON_JSON_RESPONSE",
                    "message": "Core returned non-JSON response",
                    "status_code": int(getattr(resp, "status", 0) or 0),
                    "raw_text": text[:500],
                }


async def http_get(url, **kwargs):
    return await _request_json("GET", url, **kwargs)


async def http_post(url, **kwargs):
    return await _request_json("POST", url, **kwargs)


def _new_request_id(context: ContextTypes.DEFAULT_TYPE | None = None) -> str:
    req_id = str(uuid.uuid4())
    if context is not None:
        context.user_data["last_request_id"] = req_id
    return req_id


def _panel_key(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


def _load_panel_owners_from_disk() -> dict[str, str]:
    try:
        with open(PANEL_OWNER_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("failed to load panel owner cache: %s", exc)
        return {}

    if not isinstance(payload, dict):
        return {}

    owners: dict[str, str] = {}
    for raw_key, raw_owner in payload.items():
        key = str(raw_key or "").strip()
        owner = str(raw_owner or "").strip()
        if key and owner:
            owners[key] = owner
    while len(owners) > PANEL_OWNER_CACHE_LIMIT:
        owners.pop(next(iter(owners)))
    return owners


def _persist_panel_owners(owners: dict[str, str]) -> None:
    try:
        tmp_path = f"{PANEL_OWNER_CACHE_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(owners, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, PANEL_OWNER_CACHE_PATH)
    except Exception as exc:
        logger.warning("failed to persist panel owner cache: %s", exc)


def _panel_owners(context: ContextTypes.DEFAULT_TYPE) -> dict[str, str]:
    owners = context.application.bot_data.get("panel_owners")
    if isinstance(owners, dict):
        return owners
    owners = _load_panel_owners_from_disk()
    context.application.bot_data["panel_owners"] = owners
    return owners


def _bind_panel_owner(context: ContextTypes.DEFAULT_TYPE, message, owner_id: str) -> None:
    if not message:
        return
    owner = str(owner_id or "").strip()
    if not owner:
        return
    owners = _panel_owners(context)
    owners[_panel_key(message.chat_id, message.message_id)] = owner
    while len(owners) > PANEL_OWNER_CACHE_LIMIT:
        owners.pop(next(iter(owners)))
    _persist_panel_owners(owners)


def _get_panel_owner(context: ContextTypes.DEFAULT_TYPE, message) -> str | None:
    if not message:
        return None
    owners = _panel_owners(context)
    return owners.get(_panel_key(message.chat_id, message.message_id))


def _load_feature_intro_seen_from_disk() -> dict[str, dict[str, int]]:
    try:
        with open(FEATURE_INTRO_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("failed to load feature intro cache: %s", exc)
        return {}

    if not isinstance(payload, dict):
        return {}

    cleaned: dict[str, dict[str, int]] = {}
    for raw_uid, raw_flags in payload.items():
        uid = str(raw_uid or "").strip()
        if not uid:
            continue
        if not isinstance(raw_flags, dict):
            continue
        flags: dict[str, int] = {}
        for raw_key, raw_val in raw_flags.items():
            key = str(raw_key or "").strip()
            if key not in _FEATURE_INTRO_KEYS:
                continue
            version = 0
            if isinstance(raw_val, bool):
                version = 1 if raw_val else 0
            else:
                try:
                    version = int(raw_val or 0)
                except Exception:
                    version = 0
            if version > 0:
                flags[key] = version
        if flags:
            cleaned[uid] = flags
    while len(cleaned) > FEATURE_INTRO_USER_LIMIT:
        cleaned.pop(next(iter(cleaned)))
    return cleaned


def _persist_feature_intro_seen(state: dict[str, dict[str, int]]) -> None:
    try:
        tmp_path = f"{FEATURE_INTRO_CACHE_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, FEATURE_INTRO_CACHE_PATH)
    except Exception as exc:
        logger.warning("failed to persist feature intro cache: %s", exc)


def _feature_intro_seen(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, int]]:
    state = context.application.bot_data.get("feature_intro_seen")
    if isinstance(state, dict):
        return state
    state = _load_feature_intro_seen_from_disk()
    context.application.bot_data["feature_intro_seen"] = state
    return state


def _has_seen_feature_intro(context: ContextTypes.DEFAULT_TYPE, user_id: str, feature_key: str) -> bool:
    state = _feature_intro_seen(context)
    return int(state.get(str(user_id), {}).get(feature_key, 0) or 0) >= FEATURE_INTRO_VERSION


def _mark_feature_intro_seen(context: ContextTypes.DEFAULT_TYPE, user_id: str, feature_key: str) -> None:
    if feature_key not in _FEATURE_INTRO_KEYS:
        return
    state = _feature_intro_seen(context)
    uid = str(user_id)
    user_flags = state.setdefault(uid, {})
    if int(user_flags.get(feature_key, 0) or 0) >= FEATURE_INTRO_VERSION:
        return
    user_flags[feature_key] = int(FEATURE_INTRO_VERSION)
    while len(state) > FEATURE_INTRO_USER_LIMIT:
        state.pop(next(iter(state)))
    _persist_feature_intro_seen(state)


def _extract_story_excerpt(text: str, *, max_lines: int = 4) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if str(line or "").strip()]
    if not lines:
        return ""
    return "\n".join(lines[:max(1, int(max_lines))])


def _load_feature_intro_texts() -> dict[str, str]:
    base_dir = os.path.abspath(os.path.join(ROOT_DIR, "texts"))
    result: dict[str, str] = {}

    def _extract_feature_intro_block(raw_text: str, key: str) -> str:
        lines = str(raw_text or "").splitlines()
        in_section = False
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if stripped == "feature_intro:":
                in_section = True
                idx += 1
                continue
            if in_section and stripped and not line.startswith("  "):
                break
            if in_section and stripped == f"{key}: |":
                block: list[str] = []
                idx += 1
                while idx < len(lines):
                    body = lines[idx]
                    if body.startswith("    "):
                        block.append(body[4:])
                        idx += 1
                        continue
                    if not body.strip():
                        block.append("")
                        idx += 1
                        continue
                    break
                return "\n".join(block).strip()
            idx += 1
        return ""

    def _extract_prologue_scene_text(raw_text: str, scene_id: str) -> str:
        lines = str(raw_text or "").splitlines()
        marker = f"- id: {scene_id}"
        idx = 0
        while idx < len(lines):
            if lines[idx].strip() == marker:
                probe = idx + 1
                while probe < len(lines):
                    line = lines[probe]
                    if probe > idx + 1 and line.startswith("    - id: "):
                        break
                    if line.strip() == "text: |":
                        block: list[str] = []
                        probe += 1
                        while probe < len(lines):
                            body = lines[probe]
                            if body.startswith("        "):
                                block.append(body[8:])
                                probe += 1
                                continue
                            if not body.strip():
                                block.append("")
                                probe += 1
                                continue
                            break
                        return "\n".join(block).strip()
                    probe += 1
            idx += 1
        return ""

    def _extract_market_intro(raw_text: str) -> str:
        lines = str(raw_text or "").splitlines()
        idx = 0
        in_vendor = False
        in_atmosphere = False
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if stripped == "mystery_vendor:":
                in_vendor = True
                in_atmosphere = False
                idx += 1
                continue
            if in_vendor and not line.startswith("  ") and stripped:
                break
            if in_vendor and stripped == "atmosphere:":
                in_atmosphere = True
                idx += 1
                continue
            if in_vendor and in_atmosphere and stripped == "- |":
                block: list[str] = []
                idx += 1
                while idx < len(lines):
                    body = lines[idx]
                    if body.startswith("      "):
                        block.append(body[6:])
                        idx += 1
                        continue
                    if not body.strip():
                        block.append("")
                        idx += 1
                        continue
                    break
                return "\n".join(block).strip()
            idx += 1
        return ""

    # 优先读取专用“首次功能剧情”文本（更长、更聚焦关键人物）
    try:
        feature_intro_path = os.path.join(base_dir, "story", "feature_first_click.yaml")
        with open(feature_intro_path, "r", encoding="utf-8") as handle:
            feature_intro_raw = handle.read()
        for feature_key in ("shop_all", "cultivate", "sect_menu", "world_map"):
            block = _extract_feature_intro_block(feature_intro_raw, feature_key)
            if block:
                result[feature_key] = _extract_story_excerpt(block, max_lines=12)
    except Exception:
        pass

    try:
        prologue_path = os.path.join(base_dir, "story", "prologue.yaml")
        with open(prologue_path, "r", encoding="utf-8") as handle:
            prologue_raw = handle.read()
        if not result.get("cultivate"):
            result["cultivate"] = _extract_story_excerpt(
                _extract_prologue_scene_text(prologue_raw, "scene_07_first_cultivation"),
                max_lines=6,
            )
        if not result.get("world_map"):
            result["world_map"] = _extract_story_excerpt(
                _extract_prologue_scene_text(prologue_raw, "scene_08_system_unlock"),
                max_lines=6,
            )
        if not result.get("sect_menu"):
            result["sect_menu"] = _extract_story_excerpt(
                _extract_prologue_scene_text(prologue_raw, "scene_09_hint"),
                max_lines=5,
            )
    except Exception as exc:
        logger.warning("failed to extract prologue intro snippets: %s", exc)

    try:
        market_path = os.path.join(base_dir, "social", "market_trade.yaml")
        with open(market_path, "r", encoding="utf-8") as handle:
            market_raw = handle.read()
        if not result.get("shop_all"):
            result["shop_all"] = _extract_story_excerpt(_extract_market_intro(market_raw), max_lines=6)
    except Exception as exc:
        logger.warning("failed to extract market intro snippets: %s", exc)

    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None

    if yaml is not None:
        # 修炼 / 世界地图 / 宗门：来自序章剧情
        try:
            prologue_path = os.path.join(base_dir, "story", "prologue.yaml")
            with open(prologue_path, "r", encoding="utf-8") as handle:
                prologue_data = yaml.safe_load(handle) or {}
            scenes = (((prologue_data.get("prologue") or {}).get("scenes")) or [])
            scene_map = {str(scene.get("id", "")): scene for scene in scenes if isinstance(scene, dict)}
            if not result.get("cultivate"):
                result["cultivate"] = _extract_story_excerpt(
                    str((scene_map.get("scene_07_first_cultivation") or {}).get("text", "")),
                    max_lines=6,
                )
            if not result.get("world_map"):
                result["world_map"] = _extract_story_excerpt(
                    str((scene_map.get("scene_08_system_unlock") or {}).get("text", "")),
                    max_lines=6,
                )
            if not result.get("sect_menu"):
                result["sect_menu"] = _extract_story_excerpt(
                    str((scene_map.get("scene_09_hint") or {}).get("text", "")),
                    max_lines=5,
                )
        except Exception as exc:
            logger.warning("failed to load prologue intro snippets: %s", exc)

        # 万宝楼：来自坊市交易文本
        try:
            market_path = os.path.join(base_dir, "social", "market_trade.yaml")
            with open(market_path, "r", encoding="utf-8") as handle:
                market_data = yaml.safe_load(handle) or {}
            atmosphere = (((market_data.get("mystery_vendor") or {}).get("atmosphere")) or [])
            if not result.get("shop_all") and isinstance(atmosphere, list) and atmosphere:
                result["shop_all"] = _extract_story_excerpt(str(atmosphere[0]), max_lines=6)
        except Exception as exc:
            logger.warning("failed to load market intro snippets: %s", exc)

    defaults = {
        "shop_all": "你踏入坊市深处，灵光闪动，奇货可居。万宝楼的掌柜抬眼一笑：“道友，今日想看点什么？”",
        "cultivate": "你盘膝而坐，呼吸吐纳之间，第一缕灵气沿经脉缓缓流转。修行之路，从这一息开始。",
        "sect_menu": "宗门不会主动来找你。唯有修为、道心与机缘并进，方能叩开山门。",
        "world_map": "天地辽阔，诸域相连。先看清你脚下这一城一地，再谈远行万里。",
    }
    for key, value in defaults.items():
        if key not in result or not result[key]:
            result[key] = value
    return result


def _get_feature_intro_text(feature_key: str) -> str:
    global _FEATURE_INTRO_TEXTS
    if _FEATURE_INTRO_TEXTS is None:
        _FEATURE_INTRO_TEXTS = _load_feature_intro_texts()
    return _FEATURE_INTRO_TEXTS.get(feature_key, "")


def _is_message_not_modified_error(exc: Exception) -> bool:
    return "not modified" in str(exc or "").lower()


def _is_retry_after_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "retry after" in text or "too many requests" in text or "flood control exceeded" in text


def _is_button_type_invalid_error(exc: Exception) -> bool:
    return "BUTTON_TYPE_INVALID" in str(exc or "").upper()


def _strip_web_app_from_reply_markup(reply_markup):
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup
    changed = False
    rows = []
    for row in (reply_markup.inline_keyboard or []):
        new_row = []
        for btn in row:
            web_app = getattr(btn, "web_app", None)
            if web_app is not None:
                changed = True
                text = str(getattr(btn, "text", "") or "🏯 进入修仙世界")
                new_row.append(
                    InlineKeyboardButton(
                        text=text,
                        callback_data="miniapp_private_hint",
                    )
                )
            else:
                new_row.append(btn)
        if new_row:
            rows.append(new_row)
    if not changed:
        return reply_markup
    return InlineKeyboardMarkup(rows)


def _is_private_panel_message(message) -> bool:
    if not message:
        return False
    chat = getattr(message, "chat", None)
    chat_type = str(getattr(chat, "type", "") or "").lower()
    if chat_type == "private":
        return True
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None and chat is not None:
        chat_id = getattr(chat, "id", None)
    try:
        return int(chat_id) > 0
    except Exception:
        return False


def _set_pending_action(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    action: str,
    prompt_message,
    user_id: str,
) -> None:
    context.user_data["pending_action"] = action
    context.user_data["pending_prompt_id"] = int(getattr(prompt_message, "message_id", 0) or 0)
    context.user_data["pending_chat_id"] = int(getattr(getattr(prompt_message, "chat", None), "id", 0) or 0)
    context.user_data["pending_user_id"] = str(user_id)


def _clear_pending_action(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "pending_action",
        "pending_prompt_id",
        "pending_chat_id",
        "pending_user_id",
        "pending_element",
        "pending_shop_buy_currency",
        "pending_shop_buy_item_id",
        "pending_shop_buy_item_name",
        "pending_shop_buy_category",
    ):
        context.user_data.pop(key, None)


def _matches_pending_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> bool:
    message = getattr(update, "message", None)
    if message is None or not message.text:
        return False
    if context.user_data.get("pending_action") != action:
        return False
    if str(getattr(update.effective_user, "id", "")) != str(context.user_data.get("pending_user_id", "")):
        return False
    if int(context.user_data.get("pending_chat_id", 0) or 0) != int(getattr(getattr(message, "chat", None), "id", 0) or 0):
        return False
    # Allow direct text input without mandatory reply-to in both private/group chats.
    return True


async def _reply_with_owned_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    reply_markup=None,
    parse_mode=None,
    owner_id: str | None = None,
):
    sent = await _reply_text(update, text, reply_markup=reply_markup, parse_mode=parse_mode)
    effective_user = getattr(update, "effective_user", None)
    fallback_owner = str(effective_user.id) if effective_user is not None else None
    if reply_markup is not None:
        _bind_panel_owner(context, sent, owner_id or fallback_owner)
    return sent


def _get_reply_message(update_or_query):
    if hasattr(update_or_query, "message") and getattr(update_or_query, "message") is not None:
        return update_or_query.message
    if hasattr(update_or_query, "effective_message") and getattr(update_or_query, "effective_message") is not None:
        return update_or_query.effective_message
    return None


async def _reply_text(update_or_query, text: str, **kwargs):
    message = _get_reply_message(update_or_query)
    if message is None:
        raise ValueError("No replyable message found")
    try:
        return await message.reply_text(text, **kwargs)
    except Exception as exc:
        if not _is_button_type_invalid_error(exc):
            raise
        downgraded_kwargs = dict(kwargs)
        downgraded_markup = _strip_web_app_from_reply_markup(downgraded_kwargs.get("reply_markup"))
        if downgraded_markup is None:
            downgraded_kwargs.pop("reply_markup", None)
        else:
            downgraded_kwargs["reply_markup"] = downgraded_markup
        logger.warning("reply_text fallback: downgraded web_app buttons due to BUTTON_TYPE_INVALID")
        return await message.reply_text(text, **downgraded_kwargs)


async def _on_app_init(_app: Application) -> None:
    install_asyncio_exception_logging("telegram")
    _app.bot_data["panel_owners"] = _load_panel_owners_from_disk()
    _app.bot_data["feature_intro_seen"] = _load_feature_intro_seen_from_disk()
    await _get_http_session()
    try:
        await _app.bot.set_my_commands([
            BotCommand("xian_start", "修仙世界入口"),
            BotCommand("xian_register", "踏入修仙之路"),
            BotCommand("xian_stat", "查看修仙状态"),
            BotCommand("xian_cul", "打坐修炼"),
            BotCommand("xian_hunt", "外出历练"),
            BotCommand("xian_break", "尝试突破境界"),
            BotCommand("xian_shop", "灵石商铺"),
            BotCommand("xian_bag", "储物袋"),
            BotCommand("xian_quest", "任务面板"),
            BotCommand("xian_sect", "宗门系统"),
            BotCommand("xian_currency", "统一货币"),
            BotCommand("xian_bounty", "全服悬赏会"),
            BotCommand("xian_pvp", "切磋挑战"),
            BotCommand("xian_rank", "修仙排行榜"),
            BotCommand("xian_guide", "修仙指南"),
        ])
        await _app.bot.set_my_commands(_MENU_COMMANDS)
        await _app.bot.set_my_commands(_MENU_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await _app.bot.set_my_commands(_MENU_COMMANDS, scope=BotCommandScopeAllGroupChats())
        if MINIAPP_URL:
            await _app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="进入修仙世界",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            )
        else:
            await _app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Telegram commands synced: %s", len(_MENU_COMMANDS))
    except Exception as e:
        logger.warning("Failed to set bot commands: %s", e)


async def _on_app_shutdown(_app: Application) -> None:
    await _close_http_session()


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.error(
            "Telegram polling conflict detected. Another bot instance is using the same token; stopping this process."
        )
        context.application.stop_running()
        return
    logger.exception("Unhandled telegram handler error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message is not None:
            await update.effective_message.reply_text("❌ 服务器繁忙，请稍后重试")
    except Exception as notify_exc:
        logger.warning("failed_to_notify_user_on_error error=%s", type(notify_exc).__name__)


# ==================== 装饰器 ====================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _matches_pending_action(update, context, "sect_create"):
        message = update.message
        if message is None:
            return
        text = (message.text or "").strip()
        if text in ("/cancel", "取消"):
            _clear_pending_action(context)
            await message.reply_text("已取消创建宗门。", reply_markup=get_main_menu_keyboard())
            return
        parts = text.split()
        if not parts:
            await message.reply_text(
                "请输入宗门名称与描述（回复此消息）：\n示例：风霜宗 坚许风霜罢了",
                reply_markup=ForceReply(selective=True),
            )
            return
        name = parts[0].strip()
        desc = " ".join(parts[1:]).strip()
        _clear_pending_action(context)
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": str(update.effective_user.id)},
                timeout=15,
            )
            if not r.get("success"):
                await message.reply_text("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
                return
            uid = r["user_id"]
            data = await http_post(
                f"{SERVER_URL}/api/sect/create",
                json={"user_id": uid, "name": name, "description": desc},
                timeout=15,
            )
            await message.reply_text(
                "✅ 创建成功" if data.get("success") else f"❌ {data.get('message', '失败')}",
                reply_markup=get_main_menu_keyboard(),
            )
        except Exception as exc:
            logger.error(f"sect create text error: {exc}")
            await message.reply_text("❌ 服务器错误，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if _matches_pending_action(update, context, "register_name"):
        message = update.message
        if message is None:
            return
        text = (message.text or "").strip()
        if text in ("/cancel", "取消"):
            _clear_pending_action(context)
            await message.reply_text("已取消注册。", reply_markup=get_main_menu_keyboard())
            return
        username = text.split()[0] if text else ""
        element = context.user_data.get("pending_element")
        if not username:
            prompt = await message.reply_text(
                "请输入游戏名（2-16 位中文/字母/数字）：",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="register_name", prompt_message=prompt, user_id=message.from_user.id)
            if element:
                context.user_data["pending_element"] = element
            return
        if not re.fullmatch(r"^[A-Za-z0-9\u4e00-\u9fff]{2,16}$", username):
            prompt = await message.reply_text(
                "游戏名格式不正确，请输入 2-16 位中文、字母或数字：",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="register_name", prompt_message=prompt, user_id=message.from_user.id)
            if element:
                context.user_data["pending_element"] = element
            return
        _clear_pending_action(context)
        await do_register(update, context, str(message.from_user.id), username, element)
        return

    if _matches_pending_action(update, context, "chat_request"):
        message = update.message
        if message is None:
            return
        text = (message.text or "").strip()
        if text in ("/cancel", "取消"):
            _clear_pending_action(context)
            await message.reply_text("已取消论道请求。", reply_markup=get_main_menu_keyboard())
            return
        target_name = text.split()[0] if text else ""
        _clear_pending_action(context)
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": str(update.effective_user.id)},
                timeout=15,
            )
            if not r.get("success"):
                await message.reply_text("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
                return
            uid = r["user_id"]
            await _send_chat_request(context, message, uid=uid, target_name=target_name)
        except Exception as exc:
            logger.error(f"chat request text error: {exc}")
            await message.reply_text("❌ 服务器错误，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if _matches_pending_action(update, context, "shop_buy_qty"):
        message = update.message
        if message is None:
            return
        text = (message.text or "").strip()
        category = str(context.user_data.get("pending_shop_buy_category", "all") or "all").strip().lower()
        if category not in ("all", "pill", "material", "special"):
            category = "all"
        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏪 返回商店", callback_data=f"shop_{category}")],
            [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
        ])
        if text in ("/cancel", "取消"):
            _clear_pending_action(context)
            await message.reply_text("已取消购买。", reply_markup=back_keyboard)
            return

        qty_text = text.split()[0] if text else ""
        try:
            quantity = int(qty_text)
        except (TypeError, ValueError):
            prompt = await message.reply_text(
                "请输入有效数量（1-999），输入“取消”可终止购买。",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="shop_buy_qty", prompt_message=prompt, user_id=message.from_user.id)
            return

        if quantity <= 0 or quantity > 999:
            prompt = await message.reply_text(
                "购买数量范围为 1-999，请重新输入。",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="shop_buy_qty", prompt_message=prompt, user_id=message.from_user.id)
            return

        currency = str(context.user_data.get("pending_shop_buy_currency", "") or "").strip().lower()
        item_id = str(context.user_data.get("pending_shop_buy_item_id", "") or "").strip()
        item_name = str(context.user_data.get("pending_shop_buy_item_name", "") or "").strip() or _item_display_name(item_id)
        if currency not in ("copper", "gold", "spirit_high") or not item_id:
            _clear_pending_action(context)
            await message.reply_text("❌ 购买状态已失效，请重新打开商店。", reply_markup=back_keyboard)
            return

        _clear_pending_action(context)
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": str(update.effective_user.id)},
                timeout=15,
            )
            if not r.get("success"):
                await message.reply_text("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
                return
            uid = r["user_id"]
            result = await http_post(
                f"{SERVER_URL}/api/shop/buy",
                json={"user_id": uid, "item_id": item_id, "quantity": quantity, "currency": currency},
                timeout=15,
            )
            if result.get("success"):
                await message.reply_text(
                    f"✅ 已购买 {item_name} x{quantity}\n{result.get('message', '')}".strip(),
                    reply_markup=back_keyboard,
                )
            else:
                await message.reply_text(
                    f"❌ {result.get('message', '购买失败')}",
                    reply_markup=back_keyboard,
                )
        except Exception as exc:
            logger.error(f"shop buy qty text error: {exc}")
            await message.reply_text("❌ 服务器错误，请稍后重试", reply_markup=back_keyboard)
        return

    if _matches_pending_action(update, context, "admin_modify"):
        message = update.message
        if message is None:
            return
        operator = str(getattr(update.effective_user, "id", "") or "")
        if not _is_super_admin_tg(operator):
            _clear_pending_action(context)
            await message.reply_text("❌ 权限不足：仅超管可使用该功能。")
            return

        text = (message.text or "").strip()
        if text in ("/cancel", "取消"):
            _clear_pending_action(context)
            await message.reply_text("已取消管理修改。")
            return

        default_action = str(context.user_data.get("admin_panel_action", "set") or "set").strip().lower()
        if default_action not in ADMIN_PANEL_ACTION_LABELS:
            default_action = "set"
        reply_target = _admin_reply_target_token(message)
        fallback_target = (
            reply_target
            or str(context.user_data.get("admin_target_uid", "") or "").strip()
            or str(context.user_data.get("admin_target_token", "") or "").strip()
        )

        parts = text.split()
        target_token = ""
        action = default_action
        field = ""
        value_raw = ""

        if len(parts) >= 4:
            target_token = str(parts[0] or "").strip()
            action = str(parts[1] or "").strip().lower()
            field = str(parts[2] or "").strip()
            value_raw = str(parts[3] or "").strip()
        elif len(parts) == 3:
            if str(parts[0] or "").strip().lower() in ADMIN_PANEL_ACTION_LABELS:
                target_token = fallback_target
                action = str(parts[0] or "").strip().lower()
                field = str(parts[1] or "").strip()
                value_raw = str(parts[2] or "").strip()
            else:
                target_token = str(parts[0] or "").strip()
                field = str(parts[1] or "").strip()
                value_raw = str(parts[2] or "").strip()
        elif len(parts) == 2:
            target_token = fallback_target
            field = str(parts[0] or "").strip()
            value_raw = str(parts[1] or "").strip()
        else:
            await message.reply_text(
                "❌ 输入格式错误。\n"
                "请输入：`字段 数值` 或 `UID 字段 数值` 或 `UID 操作 字段 数值`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if action not in ADMIN_PANEL_ACTION_LABELS:
            await message.reply_text("❌ 操作类型错误，仅支持 set / add / minus。")
            return
        if not target_token:
            await message.reply_text("❌ 未指定目标，请回复目标玩家消息或在输入中携带 UID/TG_ID。")
            return
        if not field or not value_raw:
            await message.reply_text("❌ 参数错误，field 和 value 不能为空。")
            return

        try:
            ok, result_text, resolved_uid = await _admin_apply_modify(
                operator_tg_id=operator,
                target_token=target_token,
                action=action,
                field=field,
                value_raw=value_raw,
            )
        except Exception as exc:
            logger.error("admin_pending_modify_failed error=%s", type(exc).__name__)
            _clear_pending_action(context)
            await message.reply_text("❌ 管理操作失败，请稍后重试。")
            return
        if resolved_uid:
            context.user_data["admin_target_uid"] = resolved_uid
        context.user_data["admin_target_token"] = target_token
        context.user_data["admin_panel_action"] = action
        _clear_pending_action(context)
        await message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)

        if ok:
            await _show_admin_test_panel(update, context, selected_action=action)
        return

    message = update.message
    if message is not None and getattr(getattr(message, "chat", None), "type", "") == "private":
        await message.reply_text(
            "当前私聊不支持自由聊天，请使用 /start、命令菜单，或点击下方按钮。",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    return

def require_account(handler):
    """需要已注册账号"""
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if not r.get("success"):
                keyboard = [[InlineKeyboardButton("📝 注册账号", callback_data="register")]]
                await _reply_with_owned_panel(
                    update,
                    context,
                    "❌ 你还没有账号！点击下方按钮注册：",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            context.user_data["uid"] = r["user_id"]
            context.user_data["username"] = r.get("username", update.effective_user.first_name)
        except Exception as exc:
            logger.error(f"lookup error: {exc}")
            await update.message.reply_text("❌ 服务器错误，请稍后重试")
            return
        await handler(update, context)
    return wrapper


# ==================== 主菜单 ====================

def get_main_menu_keyboard(*, include_miniapp: bool = True):
    """获取主菜单键盘"""
    keyboard = []

    # MiniApp 入口（最顶部，最醒目）
    if include_miniapp and MINIAPP_URL:
        keyboard.append([
            InlineKeyboardButton(
                "🏯 进入修仙世界",
                web_app=WebAppInfo(url=MINIAPP_URL),
            ),
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("📊 状态", callback_data="status"),
            InlineKeyboardButton("🧘 修炼", callback_data="cultivate"),
        ],
        [
            InlineKeyboardButton("⚔️ 狩猎", callback_data="hunt"),
            InlineKeyboardButton("🔥 突破", callback_data="breakthrough"),
        ],
        [
            InlineKeyboardButton("🏛️ 宗门", callback_data="sect_menu"),
            InlineKeyboardButton("🏪 万宝楼", callback_data="shop_all"),
        ],
        [
            InlineKeyboardButton("🎒 储物袋", callback_data="bag"),
            InlineKeyboardButton("👕 灵装", callback_data="equipment"),
            InlineKeyboardButton("✨ 技能", callback_data="skills"),
        ],
        [
            InlineKeyboardButton("🗺️ 秘境", callback_data="secret_realms"),
            InlineKeyboardButton("🏆 排行", callback_data="leaderboard_stage"),
        ],
        [
            InlineKeyboardButton("👥 社交", callback_data="social_menu"),
            InlineKeyboardButton("🗺️ 大地图", callback_data="world_map"),
        ],
        [
            InlineKeyboardButton("💱 货币", callback_data="currency_menu"),
            InlineKeyboardButton("🔁 转化", callback_data="convert_menu"),
            InlineKeyboardButton("🧪 炼丹", callback_data="alchemy_menu"),
        ],
        [
            InlineKeyboardButton("🏅 成就", callback_data="achievements_menu"),
            InlineKeyboardButton("📜 剧情", callback_data="story_menu"),
        ],
        [
            InlineKeyboardButton("📜 任务", callback_data="quests"),
            InlineKeyboardButton("🔨 锻造", callback_data="forge"),
            InlineKeyboardButton("📖 说明", callback_data="guide"),
        ],
        [
            InlineKeyboardButton("🎉 活动", callback_data="events_menu"),
            InlineKeyboardButton("🐲 世界BOSS", callback_data="worldboss_menu"),
            InlineKeyboardButton("📜 悬赏会", callback_data="bounty_menu"),
        ],
    ])
    return InlineKeyboardMarkup(keyboard)


# ==================== 命令处理器 ====================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start 命令"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    
    # 检查是否已注册
    try:
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        
        if r.get("success"):
            # 已注册，显示主菜单
            story_hint = ""
            location_hint = ""
            uid = r.get("user_id")
            try:
                if uid:
                    stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                    if stat_r.get("success"):
                        status = stat_r.get("status") or {}
                        location = status.get("current_map_name") or status.get("current_map")
                        if location:
                            location_hint = f"\n📍 *所在地*：{location}\n"
            except Exception:
                location_hint = ""
            try:
                if not uid:
                    raise RuntimeError("missing user_id for story hint")
                story_r = await http_get(f"{SERVER_URL}/api/story/{uid}", timeout=15)
                pending = ((story_r or {}).get("story") or {}).get("pending_claims") or []
                if pending:
                    first_chapter = pending[0]
                    title = str(first_chapter.get("title") or "主线")
                    detail = str(first_chapter.get("narrative") or first_chapter.get("summary") or "")
                    story_hint = f"\n📜 *{title}*\n{detail}\n"
            except Exception:
                story_hint = ""
            miniapp_hint = ""
            if MINIAPP_URL:
                miniapp_hint = "\n🏯 点击下方「进入修仙世界」打开沉浸式修仙界面\n"
            text = f"""
👋 欢迎回来，*{r.get('username', user_name)}*！

🕯️ 修仙之路漫漫，吾将上下而求索。
{location_hint}{miniapp_hint}
选择下方按钮开始你的修仙之旅：
{story_hint}
"""
            await _reply_with_owned_panel(
                update,
                context,
                text, 
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
        else:
            # 未注册，直接进入创建角色流程（先选五行，再输入角色名）
            keyboard = [
                [
                    InlineKeyboardButton("金 ⚔️", callback_data="register_gold"),
                    InlineKeyboardButton("木 🌿", callback_data="register_wood"),
                ],
                [
                    InlineKeyboardButton("水 💧", callback_data="register_water"),
                    InlineKeyboardButton("火 🔥", callback_data="register_fire"),
                ],
                [
                    InlineKeyboardButton("土 🏔️", callback_data="register_earth"),
                ],
            ]
            text = f"""
👋 欢迎，*{user_name}*！

🧬 第一步：先选择你的五行属性。
📝 第二步：直接发送角色名（无需回复某条消息）。

角色名规则：2-16 位中文、字母或数字。
"""
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as exc:
        logger.error(f"start error: {exc}")
        await update.message.reply_text("❌ 服务器错误，请稍后重试")


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/register 命令"""
    user_id = str(update.effective_user.id)
    
    # 检查是否已注册
    try:
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        if r.get("success"):
            await update.message.reply_text(
                f"✅ 你已经注册过了！\nUID: `{r['user_id']}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    except Exception as exc:
        logger.warning(
            "register_precheck_failed telegram_user_id=%s error=%s",
            user_id,
            type(exc).__name__,
        )
        await update.message.reply_text("⚠️ 服务繁忙，请稍后重试")
        return

    if len(context.args) < 1:
        # 显示五行选择界面
        keyboard = [
            [
                InlineKeyboardButton("金 ⚔️", callback_data="register_gold"),
                InlineKeyboardButton("木 🌿", callback_data="register_wood"),
            ],
            [
                InlineKeyboardButton("水 💧", callback_data="register_water"),
                InlineKeyboardButton("火 🔥", callback_data="register_fire"),
            ],
            [
                InlineKeyboardButton("土 🏔️", callback_data="register_earth"),
            ],
        ]
        text = """
🌟 *选择你的五行属性*

游戏名规则：
• 选完五行后直接发送角色名（无需回复某条消息）
• 也可使用 `/register 游戏名`
• 仅允许 2-16 位中文、字母或数字
• 游戏名唯一，不能重复

每个属性都有独特加成：

⚔️ *金* - 攻击+15%，暴击+5%
🌿 *木* - 生命+10%，恢复+20%
💧 *水* - 法力+10%，恢复+30%
🔥 *火* - 攻击+20%，技能伤害+10%
🏔️ *土* - 生命+15%，防御+10%

选择后将无法更改，请慎重！
"""
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 兼容旧格式：/register 用户名
    username = context.args[0].strip()
    await do_register(update, context, user_id, username, None)

async def do_register(update, context: ContextTypes.DEFAULT_TYPE, user_id: str, username: str, element: str = None):
    """执行注册"""
    payload = {
        "platform": "telegram",
        "platform_id": user_id,
        "username": username,
        "element": element,
    }
    try:
        data = await http_post(f"{SERVER_URL}/api/register", json=payload, timeout=15)
        if data.get("success"):
            # 注册成功后强制播放序章剧情
            context.user_data["prologue_page"] = 0
            context.user_data["registered_uid"] = data["user_id"]
            context.user_data["registered_name"] = username
            context.user_data["registered_element"] = element or "未选择"
            await _send_prologue_page(update, context, 0)
        else:
            await _reply_text(
                update,
                f"❌ {data.get('message', '注册失败')}\n\n"
                "游戏名仅允许 2-16 位中文、字母或数字，且必须唯一。\n"
                "可使用 `/register 游戏名` 重新注册。",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as exc:
        logger.error(f"register error: {exc}")
        await _reply_text(update, "❌ 服务器错误")


# ============================================================
# 序章剧情引擎
# ============================================================

def _load_prologue_scenes():
    """从 texts/story/prologue.yaml 加载序章场景列表"""
    import yaml
    from pathlib import Path
    yaml_path = Path(__file__).resolve().parent.parent.parent / "texts" / "story" / "prologue.yaml"
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return (data.get("prologue") or {}).get("scenes") or []
    except Exception as e:
        logger.error(f"Failed to load prologue: {e}")
        return []

# 缓存序章场景（启动时加载一次）
_PROLOGUE_SCENES = None

def _get_prologue_scenes():
    global _PROLOGUE_SCENES
    if _PROLOGUE_SCENES is None:
        _PROLOGUE_SCENES = _load_prologue_scenes()
    return _PROLOGUE_SCENES


def _render_scene(scene: dict) -> str:
    """将一个场景 dict 渲染为 TG 消息文本。

    对话格式：名字：话
    旁白直接写。
    """
    scene_type = scene.get("type", "narration")

    if scene_type == "narration":
        return scene.get("text", "").strip()

    if scene_type == "dialogue":
        lines_data = scene.get("lines", [])
        parts = []
        for line in lines_data:
            speaker = line.get("speaker")
            text = (line.get("text") or "").strip()
            if speaker:
                # 对话行：名字：话
                parts.append(f"*{speaker}*：{text}")
            else:
                # 旁白行：直接写
                parts.append(text)
        return "\n\n".join(parts)

    if scene_type == "choice":
        # 选择型场景在序章中不做实际分支，只展示文本
        choices = scene.get("choices", [])
        parts = ["请选择："]
        for i, c in enumerate(choices, 1):
            parts.append(f"  {i}. {c.get('label', '...')}")
        return "\n".join(parts)

    return scene.get("text", "").strip()


async def _send_prologue_page(update, context, page_index: int):
    """发送序章剧情的第 page_index 页"""
    scenes = _get_prologue_scenes()
    if page_index >= len(scenes):
        # 序章播放完毕，显示注册成功信息和主菜单
        username = context.user_data.get("registered_name", "修士")
        uid = context.user_data.get("registered_uid", "???")
        element = context.user_data.get("registered_element", "未选择")
        text = f"""
✅ *注册成功！*

👤 角色: {username}
🆔 UID: `{uid}`
🌟 五行灵根: {element}

序章完毕，你的修仙之路正式开始！
"""
        # 清理序章状态
        context.user_data.pop("prologue_page", None)
        context.user_data.pop("registered_uid", None)
        context.user_data.pop("registered_name", None)
        context.user_data.pop("registered_element", None)

        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_keyboard()
        )
        return

    scene = scenes[page_index]
    text = _render_scene(scene)

    # 添加页码信息
    total = len(scenes)
    text = f"📜 序章 ({page_index + 1}/{total})\n{'─' * 25}\n\n{text}"

    # 下一页按钮
    if page_index < total - 1:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ 继续", callback_data=f"prologue_next_{page_index + 1}")],
        ])
    else:
        # 最后一页：进入游戏
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 进入修仙世界", callback_data=f"prologue_next_{page_index + 1}")],
        ])

    await _reply_with_owned_panel(
        update,
        context,
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


# ============================================================
# 剧情系统 – 逐行展示引擎
# ============================================================

async def _handle_story_menu(update, context, user_id, _safe_edit):
    """Show the story chapter list with available chapters."""
    try:
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        if not r.get("success"):
            await _safe_edit("❌ 请先注册")
            return
        uid = r["user_id"]
        vol_r = await http_get(f"{SERVER_URL}/api/story/volumes/{uid}", timeout=15)
        if not vol_r.get("success"):
            await _safe_edit("❌ 获取剧情失败")
            return

        chapters = vol_r.get("available_chapters", [])
        if not chapters:
            text = "📜 *主线剧情*\n\n暂无可阅读的章节。\n继续修炼、历练以解锁更多剧情！"
            keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = "📜 *主线剧情*\n\n"
        buttons: list = []
        current_volume = ""
        for ch in chapters[:15]:  # show at most 15 chapters
            vol_title = ch.get("volume_title", "")
            if vol_title != current_volume:
                current_volume = vol_title
                text += f"\n*{vol_title}*\n"

            ch_id = ch["chapter_id"]
            title = ch.get("title", ch_id)
            cur = ch.get("current_line", 0)
            total = ch.get("total_lines", 0)
            is_new = ch.get("is_new", False)

            if cur >= total and total > 0:
                status_icon = "✅"
                progress = "已读完"
            elif cur > 0:
                status_icon = "📖"
                progress = f"{cur}/{total}"
            else:
                status_icon = "🆕" if is_new else "📕"
                progress = "未读"

            text += f"  {status_icon} {title} ({progress})\n"
            btn_label = f"{'🆕 ' if is_new else ''}{title}"
            if len(btn_label) > 30:
                btn_label = btn_label[:28] + "…"
            buttons.append([InlineKeyboardButton(btn_label, callback_data=f"story_read_{ch_id}")])

        buttons.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
        await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"story menu error: {e}")
        await _safe_edit("❌ 加载剧情失败，请稍后重试")


async def _handle_story_read(update, context, user_id, chapter_id, _safe_edit):
    """Read next batch of lines for a chapter and display them line by line."""
    try:
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        if not r.get("success"):
            await _safe_edit("❌ 请先注册")
            return
        uid = r["user_id"]

        # Fetch next batch of lines (5 lines at a time)
        read_r = await http_post(
            f"{SERVER_URL}/api/story/read",
            json={"user_id": uid, "chapter_id": chapter_id, "count": 5},
            timeout=15,
        )
        if not read_r.get("success"):
            code = read_r.get("code", "")
            if code == "CHAPTER_NOT_FOUND":
                await _safe_edit("❌ 章节不存在")
            else:
                await _safe_edit(f"❌ {read_r.get('message', '读取失败')}")
            return

        title = read_r.get("title", chapter_id)
        vol_title = read_r.get("volume_title", "")
        lines = read_r.get("lines", [])
        current_line = read_r.get("current_line", 0)
        total_lines = read_r.get("total_lines", 0)
        is_finished = read_r.get("is_finished", False)

        # Build the display text – each line separated by blank line
        header = f"📜 *{vol_title}*\n*{title}*\n"
        header += f"{'─' * 25}\n"
        progress_text = f"({current_line}/{total_lines})"

        body_parts = []
        for line in lines:
            ltype = line.get("type", "narration")
            text = line.get("text", "")
            speaker = line.get("speaker")
            if ltype == "dialogue" and speaker:
                body_parts.append(f"*{speaker}*：{text}")
            elif ltype == "choice":
                body_parts.append(f"  {text}")
            else:
                body_parts.append(text)

        body = "\n\n".join(body_parts)
        full_text = f"{header}\n{body}\n\n{'─' * 25}\n{progress_text}"

        # Build buttons
        buttons: list = []
        if not is_finished:
            buttons.append([InlineKeyboardButton("▶️ 继续阅读", callback_data=f"story_next_{chapter_id}")])
        else:
            buttons.append([InlineKeyboardButton("🔄 重新阅读", callback_data=f"story_reread_{chapter_id}")])

        buttons.append([InlineKeyboardButton("📜 章节列表", callback_data="story_menu")])
        buttons.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])

        await _safe_edit(full_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"story read error: {e}")
        await _safe_edit("❌ 读取剧情失败，请稍后重试")


async def _handle_story_reread(update, context, user_id, chapter_id, _safe_edit):
    """Reset progress and start reading from the beginning."""
    try:
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        if not r.get("success"):
            await _safe_edit("❌ 请先注册")
            return
        uid = r["user_id"]

        await http_post(
            f"{SERVER_URL}/api/story/reread",
            json={"user_id": uid, "chapter_id": chapter_id},
            timeout=15,
        )
        # Now read from the beginning
        await _handle_story_read(update, context, user_id, chapter_id, _safe_edit)
    except Exception as e:
        logger.error(f"story reread error: {e}")
        await _safe_edit("❌ 重置失败，请稍后重试")


@require_account
async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stat 命令"""
    uid = context.user_data["uid"]
    try:
        r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
        if r.get("success"):
            status = r["status"]
            equipped_items = await _get_equipped_item_names(uid, status)
            text = format_status_text(status, "CHS", platform="telegram", equipped_items=equipped_items)
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await update.message.reply_text("❌ 获取状态失败")
    except Exception as exc:
        logger.error(f"stat error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def cultivate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cul 修炼命令"""
    uid = context.user_data["uid"]
    action = context.args[0] if context.args else None
    
    try:
        status = await http_get(f"{SERVER_URL}/api/cultivate/status/{uid}", timeout=15)
        if not status.get("success"):
            await update.message.reply_text("❌ 修炼状态获取失败，请稍后重试")
            return
        cultivating = status.get("state", False)
        
        if action == "stat":
            if not cultivating:
                await update.message.reply_text("🧘 当前未在修炼")
            else:
                current = status.get("current_gain", 0)
                extra = "\n修炼经验已满，请及时结算。" if status.get("is_capped") else ""
                text = f"""
🧘 *修炼中*

已获得修为: *{current:,}* 点
{extra}

点击下方按钮结束修炼
"""
                keyboard = [
                    [InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")],
                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                ]
                await _reply_with_owned_panel(
                    update,
                    context,
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            return
        
        if not cultivating:
            # 开始修炼
            data = await http_post(
                f"{SERVER_URL}/api/cultivate/start",
                json={"user_id": uid},
                timeout=15,
            )
            if data.get("success"):
                text = """
🧘 *开始修炼*

你进入冥想状态，灵气缓缓流入体内...
修炼期间可以随时结束领取修为。

点击查看修炼状态
"""
                keyboard = [
                    [InlineKeyboardButton("📊 查看进度", callback_data="cultivate_stat")],
                    [InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")],
                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                ]
                await _reply_with_owned_panel(
                    update,
                    context,
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(f"❌ {data.get('message', '开始修炼失败')}")
        else:
            # 结束修炼
            data = await http_post(f"{SERVER_URL}/api/cultivate/end", json={"user_id": uid}, timeout=15)
            if data.get("success"):
                gain = data.get("gain", 0)
                text = f"""
✅ *修炼结束*

获得修为: *{gain:,}* 点

继续努力修炼吧！
"""
                await _reply_with_owned_panel(
                    update,
                    context,
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text(f"❌ {data.get('message', '结束修炼失败')}")
                
    except Exception as exc:
        logger.error(f"cultivate error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def hunt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hunt 狩猎命令"""
    uid = context.user_data["uid"]
    
    try:
        cooldown = await http_get(f"{SERVER_URL}/api/hunt/status/{uid}", timeout=15)
        if cooldown.get("success") and cooldown.get("cooldown_remaining", 0) > 0:
            remaining = cooldown.get("cooldown_remaining", 0)
            await update.message.reply_text(
                f"⏳ 狩猎冷却中，请等待 {remaining} 秒",
                reply_markup=get_main_menu_keyboard()
            )
            return
        # 获取用户状态
        r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
        if not r.get("success"):
            await update.message.reply_text("❌ 获取状态失败")
            return
        
        user_rank = r["status"].get("rank", 1)
        monsters = get_available_monsters(user_rank)
        
        if len(context.args) > 0:
            # 统一到回合制入口，避免与按钮链路分叉。
            monster_id = context.args[0]
            monster = get_monster_by_id(monster_id)
            if not monster:
                await update.message.reply_text("❌ 怪物不存在，请检查怪物ID")
                return
            if int(monster.get("min_rank", 1) or 1) > int(user_rank or 1):
                await update.message.reply_text(f"❌ 需要达到 {monster.get('min_rank')} 级才能挑战该怪物")
                return
            diff = "简单" if monster["min_rank"] <= user_rank - 2 else ("普通" if monster["min_rank"] <= user_rank else "困难")
            text = (
                f"👹 *准备挑战*: {monster.get('name')}\n"
                f"难度: {diff}\n"
                f"属性: HP {monster.get('hp')} / ATK {monster.get('attack')} / DEF {monster.get('defense')}\n\n"
                "点击下方按钮进入回合战斗。"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"⚔️ 开始挑战 {monster.get('name')}", callback_data=f"hunt_{monster_id}")],
                [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
            ])
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
        else:
            # 显示怪物列表
            text = format_monster_list(user_rank)
            text += "\n\n点击怪物名称挑战，或使用 /hunt <怪物ID>"
            
            # 创建怪物按钮
            keyboard = []
            for m in monsters[:4]:  # 最多显示4个
                keyboard.append([
                    InlineKeyboardButton(
                        f"{m['name']} (修为:{m['exp_reward']})", 
                        callback_data=f"hunt_{m['id']}"
                    )
                ])
            
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
            
    except Exception as exc:
        logger.error(f"hunt error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def breakthrough_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/breakthrough 突破命令"""
    uid = context.user_data["uid"]
    
    try:
        r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
        if not r.get("success"):
            await update.message.reply_text("❌ 获取状态失败")
            return
        
        user_data = r["status"]
        can_break = can_breakthrough(user_data.get("exp", 0), user_data.get("rank", 1))
        
        if not can_break:
            text = f"""
🔥 *突破*

{format_realm_progress(user_data)}

修为不足，无法突破。
继续修炼积累修为吧！
"""
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
        else:
            trial = await _fetch_realm_trial(uid)
            trial_blocked = bool(trial) and int(trial.get("completed", 0) or 0) != 1
            if trial_blocked:
                text = f"""
🔥 *突破*

{format_realm_progress(user_data)}

⚠️ 修为已满足，但境界试炼尚未完成。

{_format_realm_trial_text(trial)}

完成试炼后即可突破。
"""
                keyboard = [
                    [
                        InlineKeyboardButton("⚔️ 去狩猎", callback_data="hunt"),
                        InlineKeyboardButton("🗺️ 去秘境", callback_data="secret_realms"),
                    ],
                    [InlineKeyboardButton("❌ 返回", callback_data="main_menu")],
                ]
            else:
                is_tribulation = _is_consummation_breakthrough_rank(int(user_data.get("rank", 1) or 1))
                preview_block = await _build_breakthrough_preview_block(uid, user_data, strategy="steady")
                if is_tribulation:
                    text = f"""
🔥 *突破*

{format_realm_progress(user_data)}

✨ 可以渡劫！

当前已至圆满关口，本次突破将直接触发天雷劫。

请确认准备后执行：

{preview_block}
"""
                    keyboard = [
                        [InlineKeyboardButton("⛈️ 渡劫突破", callback_data="breakthrough_tribulation")],
                        [InlineKeyboardButton("❌ 取消", callback_data="main_menu")],
                    ]
                else:
                    text = f"""
🔥 *突破*

{format_realm_progress(user_data)}

✨ 可以突破！

这一步是你当前最重要的一次破境尝试。

请选择冲关策略：

{preview_block}
"""
                    keyboard = [
                        [
                            InlineKeyboardButton("🛡️ 稳妥突破", callback_data="breakthrough_steady"),
                            InlineKeyboardButton("🌿 护脉突破", callback_data="breakthrough_protect"),
                        ],
                        [InlineKeyboardButton("⚡ 生死突破", callback_data="breakthrough_desperate")],
                        [InlineKeyboardButton("❌ 取消", callback_data="main_menu")],
                    ]
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as exc:
        logger.error(f"breakthrough error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


def _build_guide_text() -> str:
    return """
📖 *玩法说明*

🧭 *基础说明*
• 修炼：挂机积累修为，是最稳定的成长来源
• 狩猎：打怪拿修为、下品灵石、掉落和部分装备
• 突破：修为足够后冲击下一境界，失败也有保底推进
• 秘境：每天限次探索，按路线拿不同收益
• 炼丹：做阶段目标丹、路线丹和稀有配方
• 锻造：强化装备、定向锻造、分解重复装备

🗺️ *玩法阶段*
• 练气到筑基：以基础资源、低级炼丹、日常成长为主
• 金丹到元婴：以突破准备、装备强化、妖丹积累为主
• 化神以上：以高阶秘境、中品灵石资源、稀有材料追逐为主

💰 *资源说明*
• 下品灵石：日常消耗资源，用于补给、基础炼丹、普通强化
• 中品灵石：稀缺推进资源，用于关键购买、突破窗口、高价值机会

🧱 *材料流派*
• 铁矿石：强化流，主要用于锻造和强化
• 灵草 / 仙草：炼丹流，主要用于丹药与恢复
• 妖丹：突破流，主要用于冲关准备
• 龙鳞 / 凤羽：高阶合成流，主要用于高阶目标

🗺️ *秘境路线*
• 稳妥探索：保底更稳，适合补日常收益
• 冒险探索：高阶掉落更好，适合追装备和高价值奖励
• 寻宝路线：素材、残页、配方类收益更高

🔨 *强化与炼丹*
• 强化可走保守、冲击、材料专精三种路线
• 炼丹分阶段目标丹、路线丹、稀有配方
• 不同路线转化效率不同，需要自己做选择

👥 *社交玩法*
• 玩家对战与排行榜
• 可加入宗门参与宗门成长
• 排行榜可查看不同方向的成长表现
"""


def _format_post_battle_status(status: dict | None) -> str:
    if not status:
        return ""
    return (
        "\n📊 *战后状态:*\n"
        f"• HP: {status.get('hp', 0)} / {status.get('max_hp', 0)}\n"
        f"• MP: {status.get('mp', 0)} / {status.get('max_mp', 0)}\n"
        f"• 精力: {status.get('stamina', 0)} / {status.get('max_stamina', 24)}\n"
        f"• 境界: {status.get('realm_name', '凡人')}\n"
        f"• 攻击: {status.get('attack', 0)}\n"
        f"• 防御: {status.get('defense', 0)}\n"
        f"• 修为: {status.get('exp', 0):,}\n"
        f"• 下品灵石: {status.get('copper', 0):,}\n"
        f"• 中品灵石: {status.get('gold', 0):,}\n"
    )


_CHAT_REWARD_TEMPLATES = [
    "与你与{other}论道良久，心境澄明，{stamina}，{exp}",
    "与{other}畅聊修行，悟性提升，{stamina}，{exp}",
    "论道有成，{other}点拨几句，{stamina}，{exp}",
    "道心共振，灵感迸发，{stamina}，{exp}",
    "畅聊收获很多，心情愉悦，{stamina}，{exp}",
    "听君一席话，胜读十年书，{stamina}，{exp}",
    "与你共悟一程，心神更稳，{stamina}，{exp}",
]


def _format_chat_gain(value: float) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 0.0
    if val <= 0:
        return "精力已满"
    if abs(val - int(val)) < 1e-6:
        return f"精力+{int(val)}"
    return f"精力+{val:.1f}".rstrip("0").rstrip(".")


def _build_chat_reward_text(other_name: str, stamina_gain: float, exp_gain: int) -> str:
    stamina_text = _format_chat_gain(stamina_gain)
    exp_text = f"经验+{int(exp_gain or 0)}"
    template = random.choice(_CHAT_REWARD_TEMPLATES)
    return template.format(other=other_name or "道友", stamina=stamina_text, exp=exp_text)


def _build_social_menu_text() -> tuple[str, list[list[InlineKeyboardButton]]]:
    text = (
        "👥 *社交*\n\n"
        "这里集中处理玩家对战相关功能。\n\n"
        "• PVP：挑战其他玩家，获取排名和奖励\n"
        "• 论道：与其他玩家交流，可获得精力与经验\n"
        "  使用 `/chat 玩家名` 发起论道\n"
    )
    keyboard = [
        [
            InlineKeyboardButton("🏟️ PVP", callback_data="pvp_menu"),
            InlineKeyboardButton("🗣️ 论道", callback_data="chat_prompt"),
        ],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, keyboard


async def _send_chat_request(context: ContextTypes.DEFAULT_TYPE, reply_message, *, uid: str, target_name: str):
    target_name = (target_name or "").strip().lstrip("@")
    if not target_name:
        await reply_message.reply_text("请输入玩家名，例如：/chat 风霜宗")
        return
    try:
        result = await http_post(
            f"{SERVER_URL}/api/social/chat/request",
            json={"user_id": uid, "target_name": target_name},
            timeout=15,
        )
    except Exception as exc:
        logger.error(f"chat request error: {exc}")
        await reply_message.reply_text("❌ 论道发起失败，请稍后重试")
        return
    if not result.get("success"):
        await reply_message.reply_text(f"❌ {result.get('message', '论道发起失败')}")
        return

    target_username = result.get("target_username", target_name)
    request_id = result.get("request_id")
    await reply_message.reply_text(f"✅ 已向 {target_username} 发起论道，等待对方接受。")

    target_tid = result.get("target_telegram_id")
    if not target_tid:
        await reply_message.reply_text("⚠️ 对方暂时无法接收通知。")
        return
    from_name = result.get("from_username") or "道友"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 接受论道", callback_data=f"chat_accept_{request_id}")],
        [InlineKeyboardButton("❌ 拒绝", callback_data=f"chat_reject_{request_id}")],
    ])
    try:
        await context.bot.send_message(
            chat_id=target_tid,
            text=f"🗣️ {from_name} 向你发起论道，是否接受？",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.warning(f"chat notify failed: {exc}")
        await reply_message.reply_text("⚠️ 已发起，但通知对方失败（可能未开启私聊）。")


async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/guide 玩法说明"""
    await _reply_with_owned_panel(
        update,
        context,
        _build_guide_text(),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(),
    )


# ==================== 工具函数 ====================

async def _get_equipped_item_names(uid, status):
    """Fetch equipped item names for status display."""
    equipped = {}
    slot_labels = {
        "equipped_weapon": "⚔️",
        "equipped_armor": "🛡️",
        "equipped_accessory1": "💍",
        "equipped_accessory2": "💍2",
    }
    items_r = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)
    all_items = {}
    if items_r.get("success"):
        all_items = {i["id"]: i for i in items_r.get("items", [])}
    for slot, label in slot_labels.items():
        db_id = status.get(slot)
        if db_id and db_id in all_items:
            equipped[slot] = f"{label} {all_items[db_id]['item_name']}"
    return equipped if equipped else None


async def _build_pvp_menu(uid: str):
    data = await http_get(f"{SERVER_URL}/api/pvp/opponents/{uid}", params={"limit": 3}, timeout=15)
    if not data.get("success"):
        return "❌ 获取PVP对手失败", [[InlineKeyboardButton("🔙 返回", callback_data="social_menu")]]
    opponents = data.get("opponents", [])
    text = "🏟️ *PVP 面板*\n\n"
    if not opponents:
        text += "暂无可匹配对手，请稍后再试。\n"
        keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="social_menu")]]
        return text, keyboard

    text += "选择对手进行挑战：\n\n"
    keyboard = []
    for op in opponents:
        name = op.get("username", "未知修士")
        text += f"• {name} ｜ {op.get('realm_name', '凡人')} ｜ ELO {op.get('pvp_rating', 1000)}\n"
        keyboard.append([InlineKeyboardButton(f"⚔️ {name}", callback_data=f"pvp_challenge_{op.get('user_id')}")])
    keyboard.append([
        InlineKeyboardButton("📋 记录", callback_data="pvp_records"),
        InlineKeyboardButton("🏆 排行", callback_data="pvp_ranking"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="social_menu")])
    return text, keyboard


async def _build_pvp_records(uid: str):
    data = await http_get(f"{SERVER_URL}/api/pvp/records/{uid}", params={"limit": 10}, timeout=15)
    if not data.get("success"):
        return "❌ 获取PVP记录失败", [[InlineKeyboardButton("🔙 返回", callback_data="pvp_menu")]]
    records = data.get("records", [])
    text = "📋 *PVP 战斗记录*\n\n"
    if not records:
        text += "暂无记录。\n"
    else:
        for row in records[:10]:
            winner = row.get("winner_id")
            challenger = row.get("challenger_name", row.get("challenger_id"))
            defender = row.get("defender_name", row.get("defender_id"))
            result = "平局" if not winner else ("胜" if winner == uid else "负")
            text += f"{challenger} vs {defender} — {result} (回合 {row.get('rounds', 0)})\n"
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="pvp_menu")]]
    return text, keyboard


async def _build_pvp_ranking():
    data = await http_get(f"{SERVER_URL}/api/pvp/ranking", params={"limit": 10}, timeout=15)
    if not data.get("success"):
        return "❌ 获取PVP排行失败", [[InlineKeyboardButton("🔙 返回", callback_data="pvp_menu")]]
    entries = data.get("entries", [])
    text = "🏆 *PVP 排行榜*\n\n"
    for idx, row in enumerate(entries[:10], 1):
        text += f"{idx}. {row.get('username')} ｜ ELO {row.get('pvp_rating')} ｜ 胜{row.get('pvp_wins')}\n"
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="pvp_menu")]]
    return text, keyboard


def _format_leaderboard_text(mode: str, entries: list[dict], stage_goal: dict | None = None) -> str:
    title_map = {
        "power": "战力",
        "exp": "修为",
        "exp_growth": "修为增长",
        "hunt": "狩猎",
        "realm_loot": "秘境收获",
        "alchemy_output": "炼丹产出",
    }
    text = ""
    if stage_goal:
        stage_label = stage_goal.get("label", "当前阶段")
        stage_theme = stage_goal.get("theme", "")
        goal_label = stage_goal.get("goal_label", "")
        text += f"🎯 阶段：{stage_label}"
        if stage_theme:
            text += f" · {stage_theme}"
        if goal_label:
            text += f"\n阶段目标：{goal_label}"
        text += "\n\n"
    text += f"🏆 *{title_map.get(mode, '战力')}排行榜*\n\n"
    for idx, row in enumerate(entries[:10], 1):
        extra = f"{row.get('realm_name','凡人')} ｜ 战力{row['power']} ｜ 修为{row['exp']} ｜ 狩猎{row['dy_times']}"
        if mode == "realm_loot":
            extra = f"{row.get('realm_name','凡人')} ｜ 秘境分 {row.get('realm_loot', 0)} ｜ 战力{row['power']}"
        elif mode == "alchemy_output":
            extra = f"{row.get('realm_name','凡人')} ｜ 炼丹分 {row.get('alchemy_output', 0)} ｜ 修为{row['exp']}"
        elif mode in ("exp", "exp_growth"):
            extra = f"{row.get('realm_name','凡人')} ｜ 修为{row['exp']} ｜ 战力{row['power']} ｜ 秘境{row.get('realm_loot', 0)}"
        text += f"{idx}. {row['name']}\n   {extra}\n"
    return text


async def _build_currency_menu(uid: str):
    data = await http_get(f"{SERVER_URL}/api/currency/{uid}", timeout=15)
    if not data.get("success"):
        return "❌ 获取货币信息失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    tiers = data.get("tiers", []) or []
    rules = data.get("rules", {})
    rate = int(rules.get("exchange_rate", 1000) or 1000)
    text = "💱 *统一货币*\n\n"
    spirit_rows = [r for r in tiers if str(r.get("group")) == "spirit"]
    immortal_rows = [r for r in tiers if str(r.get("group")) == "immortal"]
    if spirit_rows:
        text += "灵石：\n"
        for row in spirit_rows:
            text += f"• {row.get('label')}: {int(row.get('amount', 0) or 0):,}\n"
        text += "\n"
    if immortal_rows:
        text += "仙石：\n"
        for row in immortal_rows:
            unlocked = bool(row.get("unlocked"))
            amt_text = f"{int(row.get('amount', 0) or 0):,}" if unlocked else "未开启"
            text += f"• {row.get('label')}: {amt_text}\n"
        if not all(bool(r.get("unlocked")) for r in immortal_rows):
            text += "提示：你已知晓仙石体系，需个人飞升仙界后开启。\n"
        text += "\n"
    else:
        text += "仙石：当前境界尚未知晓。\n\n"
    text += (
        f"兑换规则：相邻档位 1:{rate}\n"
        "用法：`/currency up <下品数量>`、`/currency down <中品数量>`\n"
        "进阶：`/currency <源货币ID> <目标货币ID> <数量>`"
    )
    keyboard = [
        [
            InlineKeyboardButton("⬆️ 下转中(1000)", callback_data="currency_exchange_up_1000"),
            InlineKeyboardButton("⬇️ 中转下(1)", callback_data="currency_exchange_down_1"),
        ],
        [InlineKeyboardButton("🔄 刷新", callback_data="currency_menu")],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, keyboard


async def _build_bounty_menu():
    data = await http_get(f"{SERVER_URL}/api/bounties", params={"status": "open", "limit": 12}, timeout=15)
    if not data.get("success"):
        return "❌ 获取悬赏会失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    rows = data.get("bounties", []) or []
    text = "📜 *悬赏会（全服）*\n\n"
    if not rows:
        text += "当前暂无公开悬赏。\n"
    else:
        for row in rows[:12]:
            wanted_name = row.get("wanted_item_name") or _item_display_name(str(row.get("wanted_item_id") or ""))
            text += (
                f"#{row.get('id')} ｜ {row.get('poster_name', row.get('poster_user_id', '未知'))}\n"
                f"需求: {wanted_name} x{row.get('wanted_quantity', 0)}\n"
                f"奖励: {row.get('reward_spirit_low', 0)} 下品灵石\n"
            )
            if row.get("description"):
                text += f"备注: {row.get('description')}\n"
            text += "\n"
    text += "命令：`/bounty publish <道具ID> <数量> <奖励下品灵石> [描述]`\n"
    text += "命令：`/bounty accept <悬赏ID>` ｜ `/bounty submit <悬赏ID>`"
    keyboard = [
        [InlineKeyboardButton("🔄 刷新", callback_data="bounty_menu")],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, keyboard


async def _build_convert_menu(uid: str):
    data = await http_get(f"{SERVER_URL}/api/convert/options/{uid}", timeout=15)
    if not data.get("success"):
        return "❌ 获取转化列表失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]

    routes = data.get("routes", {})
    targets = data.get("targets", [])
    rank = int(data.get("rank", 1) or 1)
    next_unlock_rank = data.get("next_unlock_rank")
    configured_target_count = int(data.get("configured_target_count", 0) or 0)
    text = "🔁 *资源转化*\n\n"
    if routes:
        text += "路线说明：\n"
        for key, info in routes.items():
            text += f"• {info.get('name', key)}：{info.get('desc', '')}\n"
        text += "\n"
    text += "说明：稳妥/投机仅消耗下品灵石与精力；专精路线会额外消耗催化材料。\n\n"
    if targets:
        text += "可转资源（每批基础下品灵石消耗）：\n"
        for row in targets[:12]:
            target_name = row.get("name") or _item_display_name(str(row.get("item_id") or ""))
            text += f"• {target_name} - {row.get('base_copper')} 下品灵石\n"
        text += "\n"
        text += "请选择路线："
        keyboard = [
            [
                InlineKeyboardButton("稳妥路线", callback_data="convert_route_steady"),
                InlineKeyboardButton("投机路线", callback_data="convert_route_risky"),
            ],
            [InlineKeyboardButton("专精路线", callback_data="convert_route_focused")],
            [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
        ]
        return text, keyboard

    text += "当前没有可转化目标。\n"
    if next_unlock_rank and rank < int(next_unlock_rank):
        from core.game.realms import format_realm_display
        text += f"你当前境界为{format_realm_display(rank)}，达到{format_realm_display(int(next_unlock_rank))}后可解锁转化目标。\n"
    elif configured_target_count <= 0:
        text += "当前转化目标配置为空或无效，请联系管理员检查配置。\n"
    else:
        text += "当前条件下暂无可转目标，请稍后再试。\n"
    return text, [
        [InlineKeyboardButton("🔄 刷新", callback_data="convert_menu")],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]


async def _build_convert_route(uid: str, route: str):
    data = await http_get(f"{SERVER_URL}/api/convert/options/{uid}", timeout=15)
    if not data.get("success"):
        return "❌ 获取转化列表失败", [[InlineKeyboardButton("🔙 返回", callback_data="convert_menu")]]

    routes = data.get("routes", {})
    targets = data.get("targets", [])
    rank = int(data.get("rank", 1) or 1)
    next_unlock_rank = data.get("next_unlock_rank")
    configured_target_count = int(data.get("configured_target_count", 0) or 0)
    route_info = routes.get(route, {})
    text = f"🔁 *{route_info.get('name', route)}*\n"
    if route_info.get("desc"):
        text += f"{route_info.get('desc')}\n"
    text += "\n说明：稳妥/投机路线不需要背包材料，仅消耗下品灵石和精力。\n"
    if route == "focused":
        text += "专精路线需要对应催化材料。\n"
    text += "\n"
    if not targets:
        text += "当前没有可转化目标。\n"
        if next_unlock_rank and rank < int(next_unlock_rank):
            from core.game.realms import format_realm_display
            text += f"你当前境界为{format_realm_display(rank)}，达到{format_realm_display(int(next_unlock_rank))}后可解锁目标。\n"
        elif configured_target_count <= 0:
            text += "当前转化目标配置为空或无效，请联系管理员检查配置。\n"
        else:
            text += "当前条件下暂无可转目标，请稍后再试。\n"
        return text, [
            [InlineKeyboardButton("🔙 选择路线", callback_data="convert_menu")],
            [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
        ]

    text += "选择目标与数量：\n"
    keyboard = []
    for row in targets[:12]:
        catalyst = row.get("focused_catalyst")
        if route == "focused" and catalyst:
            catalyst_name = _item_display_name(str(catalyst))
            text += f"• {row.get('name')} - {row.get('base_copper')} 下品灵石/批（专精材料: {catalyst_name}）\n"
        else:
            text += f"• {row.get('name')} - {row.get('base_copper')} 下品灵石/批\n"
        keyboard.append([
            InlineKeyboardButton(f"{row.get('name')} x1", callback_data=f"convert_do|{route}|{row.get('item_id')}|1"),
            InlineKeyboardButton("x5", callback_data=f"convert_do|{route}|{row.get('item_id')}|5"),
        ])
    keyboard.append([InlineKeyboardButton("🔙 选择路线", callback_data="convert_menu")])
    return text, keyboard


async def _build_alchemy_menu(uid: str):
    data = await http_get(f"{SERVER_URL}/api/alchemy/recipes", params={"user_id": uid}, timeout=15)
    if not data.get("success"):
        return "❌ 获取炼丹配方失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    recipes = data.get("recipes", [])
    featured = set(data.get("featured_recipe_ids", []))
    category_labels = data.get("category_labels", {})
    text = "🧪 *炼丹配方*\n\n灵草系偏日常补给，妖丹系偏突破准备，凤羽则是高阶丹方材料。\n\n"
    keyboard = []
    if not recipes:
        text += "暂无可用配方。\n"
    else:
        for r in recipes[:8]:
            mat_parts = []
            for m in r.get("materials", []):
                item = get_item_def(m["item_id"])
                mat_name = item["name"] if item else m["item_id"]
                mat_parts.append(f"{mat_name}x{m['quantity']}")
            mats = "、".join(mat_parts)
            rate = int(float(r.get("success_rate", 1.0)) * 100)
            category_text = category_labels.get(r.get("category"), r.get("category", "配方"))
            featured_mark = "⭐ " if r.get("id") in featured else ""
            efficiency_text = {
                "stage_goal": "高效率转化",
                "route": "功能型转化",
                "rare": "高损耗高上限",
            }.get(r.get("category"), "常规转化")
            text += (
                f"• {featured_mark}{r.get('name')} ({category_text}, 成功率 {rate}%)\n"
                f"  路线: {r.get('focus', '炼丹流')} ｜ 阶段: {r.get('stage_hint', '当前阶段')}\n"
                f"  转化: {efficiency_text}\n"
                f"  材料: {mats}\n"
                f"  花费: {r.get('copper_cost', 0)} 下品灵石\n\n"
            )
            keyboard.append([InlineKeyboardButton(f"炼制 {r.get('name')}", callback_data=f"alchemy_brew_{r.get('id')}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


async def _build_forge_menu(uid: str):
    st = await http_get(f"{SERVER_URL}/api/forge/{uid}", timeout=15)
    if not st.get("success"):
        return st.get("message", "锻造信息获取失败"), [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    catalog = await http_get(f"{SERVER_URL}/api/forge/catalog/{uid}", timeout=15)
    material_item_name = st.get("material_item_name") or _item_display_name(str(st.get("material_item_id", "iron_ore") or "iron_ore"))
    text = (
        "🔨 *锻造/祭炼*\n\n"
        "铁矿石属于强化流核心材料，用来把日常下品灵石转成装备与强化机会。\n"
        "普通锻造：随机出当前阶段装备，适合补装和开图鉴。\n"
        "定向锻造：指定做已收录装备，适合追目标和刷更高品质同名装备，消耗更高。\n"
        "定向锻造要求图鉴里已经收录过该装备。\n\n"
        f"普通锻造: {st.get('cost_copper',0)} 下品灵石 + {st.get('material_need',0)} 个 {material_item_name}\n"
        f"定向锻造: {st.get('cost_copper',0) * 2} 下品灵石 + {st.get('material_need',0) * 2} 个 {material_item_name}\n\n"
    )
    keyboard = [[InlineKeyboardButton("🔨 普通锻造", callback_data="forge_do")]]
    catalog_items = catalog.get("items", []) if catalog.get("success") else []
    if catalog_items:
        text += "图鉴定向锻造:\n"
        for row in catalog_items[:4]:
            text += f"• {row.get('name')} ｜ 需{row.get('realm_name','凡人')} ｜ 已获得 {row.get('obtained', 0)} 次\n"
            keyboard.append([InlineKeyboardButton(f"🎯 定向 {row.get('name')}", callback_data=f"forge_target_{row.get('item_id')}")])
    else:
        text += "图鉴尚未收录装备，先去狩猎或秘境拿到一件再回来定向。\n"
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


async def _build_gacha_menu(uid: str | None = None):
    data = await http_get(f"{SERVER_URL}/api/gacha/banners", timeout=15)
    if not data.get("success"):
        return "❌ 获取卡池失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    banners = data.get("banners", [])
    status = {}
    if uid:
        status_resp = await http_get(f"{SERVER_URL}/api/gacha/status/{uid}", timeout=15)
        if status_resp.get("success"):
            status = status_resp.get("status", {})
    free_limit = int(status.get("free_limit", _gacha_free_daily_limit()) or _gacha_free_daily_limit())
    paid_limit = int(status.get("paid_limit", _gacha_paid_daily_limit()) or _gacha_paid_daily_limit())
    five_pull_count = int(status.get("five_pull_count", _gacha_five_pull_count()) or _gacha_five_pull_count())
    five_pull_price_gold = int(status.get("five_pull_price_gold", _gacha_five_pull_price_gold()) or _gacha_five_pull_price_gold())
    five_pull_stamina = int(status.get("five_pull_stamina", _gacha_five_pull_stamina()) or _gacha_five_pull_stamina())
    single_pull_stamina = int(status.get("single_pull_stamina", _gacha_single_pull_stamina()) or _gacha_single_pull_stamina())
    five_pull_mult_non_gold = int(
        status.get("five_pull_price_mult_non_gold", _gacha_five_pull_price_mult_non_gold())
        or _gacha_five_pull_price_mult_non_gold()
    )
    free_remaining = int(status.get("free_remaining", free_limit) or 0)
    paid_remaining = int(status.get("paid_remaining", paid_limit) or 0)
    text = (
        "🎲 *抽奖*\n\n"
        f"今日免费：剩余 {free_remaining}/{free_limit} 次，不消耗精力\n"
        f"今日付费：剩余 {paid_remaining}/{paid_limit} 次\n"
        f"规则：免费抽奖不耗精力；付费单抽按卡池价格 + {single_pull_stamina} 精力；"
        f"{five_pull_count}连消耗 {five_pull_stamina} 精力（中品灵石池默认 {five_pull_price_gold} 中品灵石）。\n\n"
    )
    free_label = "免费1抽" if free_remaining > 0 else "免费已用尽"
    free_callback = "gacha_free_limit" if free_remaining <= 0 else None
    keyboard = []
    if not banners:
        text += "暂无开放卡池。\n"
    else:
        for b in banners[:5]:
            currency = b.get("currency", "gold")
            currency_name = "中品灵石" if currency == "gold" else "下品灵石"
            single_price = int(b.get("price_single", 1) or 1)
            five_price = (
                five_pull_price_gold
                if currency == "gold"
                else max(1, single_price * five_pull_mult_non_gold)
            )
            text += f"• {b.get('title')} (ID {b.get('banner_id')})\n  {b.get('description')}\n\n"
            this_free_callback = free_callback or f"gacha_pull_{b.get('banner_id')}_1"
            keyboard.append([
                InlineKeyboardButton(free_label, callback_data=this_free_callback),
                InlineKeyboardButton(f"付费1抽 {single_price}{currency_name}", callback_data=f"gacha_pull_{b.get('banner_id')}_paid1"),
            ])
            keyboard.append([
                InlineKeyboardButton(f"{five_pull_count}连 {five_price}{currency_name}", callback_data=f"gacha_pull_{b.get('banner_id')}_{five_pull_count}"),
            ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


async def _build_achievements_menu(uid: str):
    data = await http_get(f"{SERVER_URL}/api/achievements/{uid}", timeout=15)
    if not data.get("success"):
        return "❌ 获取成就失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    achievements = data.get("achievements", [])
    text = "🏅 *成就列表*\n\n"
    keyboard = []
    for ach in achievements[:10]:
        status = "✅" if ach.get("claimed") else ("🎁" if ach.get("completed") else "⬜")
        reward_text = _format_reward_text(ach.get("rewards") or {})
        text += f"{status} {ach.get('name')} ({ach.get('progress')}/{ach.get('goal')})\n"
        if ach.get("desc"):
            text += f"  {ach.get('desc')}\n"
        if reward_text:
            text += f"  奖励: {reward_text}\n"
        if ach.get("completed") and not ach.get("claimed"):
            keyboard.append([InlineKeyboardButton(f"领取#{ach.get('id')}", callback_data=f"ach_claim_{ach.get('id')}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


def _format_reward_text(rewards: dict) -> str:
    if not rewards:
        return ""
    parts = []
    if rewards.get("copper"):
        parts.append(f"{rewards.get('copper')}下品灵石")
    if rewards.get("exp"):
        parts.append(f"{rewards.get('exp')}修为")
    if rewards.get("gold"):
        parts.append(f"{rewards.get('gold')}中品灵石")
    items = rewards.get("items") or []
    if items:
        item_parts = []
        for i in items:
            item_parts.append(f"{_item_display_name(i.get('item_id'))}x{i.get('quantity', 1)}")
        parts.append("物品:" + ", ".join(item_parts))
    return "，".join(parts)


def _item_usage_desc(item_id: str) -> str:
    base = get_item_def(item_id)
    if not base:
        return ""
    item_type = getattr(base.get("type"), "value", base.get("type"))
    if item_type in ("weapon", "armor", "accessory"):
        parts = []
        if base.get("base_attack"):
            parts.append(f"攻+{base.get('base_attack')}")
        if base.get("base_defense"):
            parts.append(f"防+{base.get('base_defense')}")
        if base.get("base_hp"):
            parts.append(f"HP+{base.get('base_hp')}")
        if base.get("base_mp"):
            parts.append(f"MP+{base.get('base_mp')}")
        return "装备: " + (" ".join(parts) if parts else "提升属性")
    if item_type == "pill":
        effect = base.get("effect")
        value = base.get("value", 0)
        value_pct = float(base.get("value_pct", 0) or 0)
        duration = base.get("duration")
        if effect == "exp":
            desc = f"修为+{value}"
        elif effect == "hp":
            desc = f"恢复最大生命的 {int(value_pct * 100)}%"
        elif effect == "mp":
            desc = f"恢复最大法力的 {int(value_pct * 100)}%"
        elif effect == "full_restore":
            desc = "恢复全部生命和法力"
        elif effect == "breakthrough":
            desc = f"突破率+{value}%"
        elif effect == "attack_buff":
            desc = f"攻击+{value}"
        elif effect == "defense_buff":
            desc = f"防御+{value}"
        elif effect == "cultivation_buff":
            desc = f"修炼收益+{value}%"
        elif effect == "cultivation_sprint":
            desc = f"修炼冲刺+{value}%"
        elif effect == "realm_drop_boost":
            desc = f"秘境掉落+{value}%"
        elif effect == "breakthrough_protect":
            desc = f"突破惩罚-{value}%"
        else:
            desc = "效果未知"
        if duration:
            mins = int(duration // 60)
            desc = f"{desc}({mins}分钟)"
        return "丹药: " + desc
    if item_type == "material":
        focus = base.get("focus")
        usage = base.get("usage")
        stage_hint = base.get("stage_hint")
        parts = []
        if focus:
            parts.append(focus)
        if usage:
            parts.append(usage)
        if stage_hint:
            parts.append(f"适用: {stage_hint}")
        return "材料: " + " / ".join(parts) if parts else "材料: 成长资源"
    if item_type == "skill_book":
        return "技能书: 学习技能"
    return ""


def _equipment_affix_text(item: dict) -> str:
    parts = []
    fr = float(item.get("first_round_reduction_pct", 0) or 0)
    if fr > 0:
        parts.append(f"首回合减伤{int(fr * 100)}%")
    ch = float(item.get("crit_heal_pct", 0) or 0)
    if ch > 0:
        parts.append(f"暴击回血{int(ch * 100)}%")
    ed = float(item.get("element_damage_pct", 0) or 0)
    if ed > 0:
        parts.append(f"元素增伤{int(ed * 100)}%")
    lh = float(item.get("low_hp_shield_pct", 0) or 0)
    if lh > 0:
        parts.append(f"残血护盾{int(lh * 100)}%")
    return " / ".join(parts)


_EQUIPMENT_ITEM_TYPES = {"weapon", "armor", "accessory"}
_BAG_ITEMS_PER_PAGE = 8
_EQUIP_ITEMS_PER_PAGE = 6


def _parse_positive_int(value, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_equipment_inventory_item(item: dict) -> bool:
    item_type = str(item.get("item_type") or "").strip().lower()
    return item_type in _EQUIPMENT_ITEM_TYPES


def _split_inventory_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    equipment_items: list[dict] = []
    stackable_map: dict[tuple[str, str, str], dict] = {}

    for raw_item in items or []:
        item = dict(raw_item or {})
        if _is_equipment_inventory_item(item):
            equipment_items.append(item)
            continue

        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("item_name") or item_id or "未知物品")
        item_type = str(item.get("item_type") or "")
        quantity = _parse_positive_int(item.get("quantity", 1), default=1)
        key = (item_id, item_name, item_type)

        if key not in stackable_map:
            item["item_name"] = item_name
            item["quantity"] = quantity
            stackable_map[key] = item
            continue

        stackable_map[key]["quantity"] = int(stackable_map[key].get("quantity", 0) or 0) + quantity

    return equipment_items, list(stackable_map.values())


def _build_bag_panel(items: list[dict], *, page: int = 0) -> tuple[str, list[list[InlineKeyboardButton]]]:
    _, stackable_items = _split_inventory_items(items)
    keyboard: list[list[InlineKeyboardButton]] = []

    total_pages = max(1, (len(stackable_items) + _BAG_ITEMS_PER_PAGE - 1) // _BAG_ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = stackable_items[page * _BAG_ITEMS_PER_PAGE : (page + 1) * _BAG_ITEMS_PER_PAGE]

    text = (
        f"🎒 *我的储物袋* (第{page + 1}/{total_pages}页)\n"
        f"物品种类: {len(stackable_items)}\n\n"
    )

    if not stackable_items:
        text += "当前储物袋为空。"
    else:
        for item in page_items:
            item_name = str(item.get("item_name") or "未知物品")
            quantity = _parse_positive_int(item.get("quantity", 1), default=1)
            usage = _item_usage_desc(str(item.get("item_id") or ""))
            text += f"  {item_name} x{quantity}\n"
            if usage:
                text += f"    用途: {usage}\n"
            if item.get("item_type") == "pill" and item.get("item_id"):
                keyboard.append([InlineKeyboardButton(f"使用 {item_name}", callback_data=f"use_{item['item_id']}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"bag_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"bag_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


def _build_equipment_bag_panel(items: list[dict], *, page: int = 0) -> tuple[str, list[list[InlineKeyboardButton]]]:
    equipment_items, _ = _split_inventory_items(items)
    keyboard: list[list[InlineKeyboardButton]] = []

    total_pages = max(1, (len(equipment_items) + _EQUIP_ITEMS_PER_PAGE - 1) // _EQUIP_ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = equipment_items[page * _EQUIP_ITEMS_PER_PAGE : (page + 1) * _EQUIP_ITEMS_PER_PAGE]

    text = f"👕 *灵装* (第{page + 1}/{total_pages}页)\n灵装数量: {len(equipment_items)}\n\n"
    if not equipment_items:
        text += "当前没有灵装。\n可通过狩猎、锻造等玩法获取灵装。"
    else:
        for item in page_items:
            item_name = str(item.get("item_name") or "未知灵装")
            enhance_level = _parse_positive_int(item.get("enhance_level", 0), default=0)
            quality = item.get("quality_name") or item.get("quality")
            quality_text = f" [{quality}]" if quality else ""
            text += f"  {item_name}{quality_text} +{enhance_level}\n"

            bonuses = []
            if item.get("attack_bonus"):
                bonuses.append(f"攻+{item['attack_bonus']}")
            if item.get("defense_bonus"):
                bonuses.append(f"防+{item['defense_bonus']}")
            if item.get("hp_bonus"):
                bonuses.append(f"HP+{item['hp_bonus']}")
            if item.get("mp_bonus"):
                bonuses.append(f"MP+{item['mp_bonus']}")
            if bonuses:
                text += f"    属性: {', '.join(bonuses)}\n"

            affix_text = _equipment_affix_text(item)
            if affix_text:
                text += f"    词条: {affix_text}\n"

            row_btns = [InlineKeyboardButton(f"装备 {item_name}", callback_data=f"equip_{item['id']}")]
            if enhance_level < 10:
                row_btns.append(InlineKeyboardButton("强化", callback_data=f"enhance_{item['id']}"))
            row_btns.append(InlineKeyboardButton("分解", callback_data=f"decompose_{item['id']}"))
            keyboard.append(row_btns)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"equipbag_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"equipbag_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🎒 储物袋", callback_data="bag")])
    keyboard.append([InlineKeyboardButton("👕 已装备", callback_data="equipped_view")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


def _format_shop_intro(*, rank: int = 1) -> str:
    stage = get_progression_stage_theme(rank)
    return (
        "🏪 *商店*\n\n"
        f"当前阶段：{stage.get('label')} - {stage.get('theme')}\n"
        f"阶段重点：{stage.get('focus')}\n"
        f"货币分工：{get_currency_role('copper')}\n"
        f"中品灵石定位：{get_currency_role('gold')}\n\n"
        f"上品灵石定位：{get_currency_role('spirit_high')}\n\n"
    )


def _shop_currency_name(currency: str) -> str:
    cur = str(currency or "").strip().lower()
    if cur == "gold":
        return "中品灵石"
    if cur == "spirit_high":
        return "上品灵石"
    return "下品灵石"


def _shop_category_label(category: str) -> str:
    return {
        "all": "全部",
        "pill": "丹药",
        "material": "材料",
        "special": "特惠",
    }.get(category, "全部")


def _shop_item_matches_category(item: dict, category: str) -> bool:
    if category == "all":
        return True
    if category == "special":
        return item.get("tag") not in (None, "", "常驻货架")
    item_def = get_item_def(item.get("item_id", "")) or {}
    item_type = item_def.get("type")
    item_type_value = getattr(item_type, "value", item_type)
    return item_type_value == category


def _build_shop_text(items: list[dict], *, rank: int = 1, category: str = "all") -> str:
    text = _format_shop_intro(rank=rank)
    text += f"当前货架：*{_shop_category_label(category)}*（共 {len(items)} 件）\n\n"
    if not items:
        return text + "当前分类暂无商品。"

    for item in items:
        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("name") or _item_display_name(item_id) or item_id or "未知物品")
        price = int(item.get("price", item.get("actual_price", 0)) or 0)
        usage = _item_usage_desc(item_id)
        focus = item.get("focus")
        stage_hint = item.get("stage_hint")
        tag = item.get("tag")
        remaining_limit = item.get("remaining_limit")
        min_rank = int(item.get("min_rank", 1) or 1)
        currency_name = _shop_currency_name(str(item.get("currency") or "copper"))
        text += f"• *{item_name}* - {price} {currency_name}\n"

        # “全部”分类采用精简展示，避免商品较多时消息过长。
        if category == "all":
            if tag and tag != "常驻货架":
                text += f"  货架: {tag}\n"
            if remaining_limit is not None:
                text += f"  限购剩余: {remaining_limit}\n"
            continue

        if tag and tag != "常驻货架":
            text += f"  货架: {tag}\n"
        if usage:
            text += f"  用途: {usage}\n"
        if focus:
            text += f"  路线: {focus}\n"
        if stage_hint:
            text += f"  阶段: {stage_hint}\n"
        if min_rank > 1:
            from core.game.realms import format_realm_display
            text += f"  要求: {format_realm_display(min_rank)}\n"
        if remaining_limit is not None:
            text += f"  限购剩余: {remaining_limit}\n"
    return text


def _build_shop_keyboard(category: str, items: list[dict]):
    keyboard = []
    for item in items:
        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("name") or _item_display_name(item_id) or item_id or "未知物品")
        price = int(item.get("price", item.get("actual_price", 0)) or 0)
        currency = item.get("currency", "copper")
        currency_name = _shop_currency_name(currency)
        keyboard.append([
            InlineKeyboardButton(
                f"购买 {item_name} {price}{currency_name}",
                callback_data=f"buy_{currency}_{item_id}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton("全部", callback_data="shop_all"),
        InlineKeyboardButton("丹药", callback_data="shop_pill"),
        InlineKeyboardButton("材料", callback_data="shop_material"),
        InlineKeyboardButton("特惠", callback_data="shop_special"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return keyboard


async def _load_shop_view(uid: str | None, *, rank: int = 1, category: str = "all") -> tuple[str, list[list[InlineKeyboardButton]]]:
    copper_result = await http_get(
        f"{SERVER_URL}/api/shop",
        params={"currency": "copper", "user_id": uid},
        timeout=15,
    )
    gold_result = await http_get(
        f"{SERVER_URL}/api/shop",
        params={"currency": "gold", "user_id": uid},
        timeout=15,
    )
    high_result = await http_get(
        f"{SERVER_URL}/api/shop",
        params={"currency": "spirit_high", "user_id": uid},
        timeout=15,
    )
    if not copper_result.get("success") and not gold_result.get("success") and not high_result.get("success"):
        return "❌ 获取商店失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    items = (copper_result.get("items", []) if copper_result.get("success") else []) + (
        gold_result.get("items", []) if gold_result.get("success") else []
    ) + (
        high_result.get("items", []) if high_result.get("success") else []
    )
    filtered_items = [item for item in items if _shop_item_matches_category(item, category)]
    if not filtered_items and category != "all":
        category = "all"
        filtered_items = items
    text = _build_shop_text(filtered_items, rank=rank, category=category)
    keyboard = _build_shop_keyboard(category, filtered_items)
    return text, keyboard


def _item_display_name(item_id: str) -> str:
    raw = str(item_id or "").strip()
    aliases = {
        "ironore": "iron_ore",
        "iron-ore": "iron_ore",
        "iron ore": "iron_ore",
    }
    normalized = aliases.get(raw.lower(), raw)
    item = get_item_def(normalized)
    if item:
        return item["name"]
    if raw.lower() in aliases:
        return "铁矿石"
    return normalized


def _format_codex_monsters(rows: list[dict]) -> str:
    if not rows:
        return "暂无怪物记录。"
    lines = []
    for row in rows[:10]:
        monster_id = row.get("monster_id")
        monster = get_monster_by_id(monster_id) if monster_id else None
        name = monster.get("name") if monster else (monster_id or "未知怪物")
        kills = int(row.get("kills", 0) or 0)
        lines.append(f"• {name} ｜ 击杀 {kills}")
    if len(rows) > 10:
        lines.append(f"... 还有 {len(rows) - 10} 个未展示")
    return "\n".join(lines)


def _format_codex_items(rows: list[dict]) -> str:
    if not rows:
        return "暂无物品记录。"
    lines = []
    for row in rows[:10]:
        item_id = row.get("item_id")
        name = _item_display_name(item_id) if item_id else "未知物品"
        obtained = int(row.get("total_obtained", 0) or 0)
        lines.append(f"• {name} ｜ 获得 {obtained} 次")
    if len(rows) > 10:
        lines.append(f"... 还有 {len(rows) - 10} 个未展示")
    return "\n".join(lines)


def _battle_state_text(title: str, payload: dict, *, subtitle: str = "") -> str:
    player = payload.get("player", {}) or {}
    enemy = payload.get("enemy", {}) or {}
    text = f"{title}\n\n"
    if subtitle:
        text += f"{subtitle}\n"
    encounter = payload.get("encounter")
    if isinstance(encounter, dict):
        encounter = encounter.get("label") or encounter.get("type")
    if encounter:
        text += f"遭遇类型: {encounter}\n"
    if payload.get("element_relation"):
        text += f"五行关系: {payload.get('element_relation')}\n"
    text += (
        f"你: {player.get('name', '修士')} ｜ HP {player.get('hp', 0)}/{player.get('max_hp', 0)} ｜ MP {player.get('mp', 0)}/{player.get('max_mp', 0)} ｜ "
        f"ATK {player.get('attack', 0)} DEF {player.get('defense', 0)}\n"
        f"敌: {enemy.get('name', '怪物')} ｜ HP {enemy.get('hp', 0)}/{enemy.get('max_hp', 0)} ｜ "
        f"ATK {enemy.get('attack', 0)} DEF {enemy.get('defense', 0)}\n"
    )
    if payload.get("round"):
        text += f"当前回合: {payload.get('round')}\n"
    round_log = payload.get("round_log") or []
    if round_log:
        text += "\n本回合:\n"
        for entry in round_log[-4:]:
            text += f"• {entry}\n"
    return text


def _battle_action_keyboard(prefix: str, session_id: str, skills: list[dict], *, back_callback: str):
    keyboard = [[InlineKeyboardButton("🗡 普通攻击", callback_data=f"{prefix}_{session_id}_n")]]
    for skill in skills[:2]:
        keyboard.append([
            InlineKeyboardButton(
                f"✨ {skill.get('name')} {skill.get('damage_pct', 100)}% / {skill.get('mp_cost', 0)}蓝",
                callback_data=f"{prefix}_{session_id}_s_{skill.get('id')}"
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


def _secret_choice_keyboard(session_id: str, choices: list[dict], *, back_callback: str):
    keyboard = []
    for choice in choices[:3]:
        choice_id = str(choice.get("id") or "").strip()
        label = str(choice.get("label") or choice_id).strip() or "执行选择"
        if not choice_id:
            continue
        keyboard.append([InlineKeyboardButton(label, callback_data=f"sbc_{session_id}_{choice_id}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


def _format_failure_reasons(reasons: list | None) -> str:
    reasons = reasons or []
    if not reasons:
        return ""
    text = "\n败因:\n"
    for reason in reasons[:3]:
        text += f"• {reason}\n"
    return text


def _skill_line(skill: dict, *, equipped: bool = False) -> str:
    sk_type = "主动" if skill.get("type") == "active" else "被动"
    eq_tag = " [已装备]" if equipped else ""
    elem = str(skill.get("element") or "").strip()
    elem_tag = f"[{elem}] " if elem else ""
    if skill.get("type") == "active":
        mp_cost_text = format_skill_mp_cost(skill)
        return (
            f"  [{sk_type}] {elem_tag}{skill['name']}{eq_tag} - "
            f"{int(round(float((skill.get('effect', {}) or {}).get('attack_multiplier', 1.0) or 1.0) * 100))}%伤害 "
            f"/ {mp_cost_text} - {skill['desc']}\n"
        )
    return f"  [{sk_type}] {elem_tag}{skill['name']} - {skill['desc']}\n"


async def _build_recovery_menu(uid: str, *, return_callback: str = "main_menu"):
    items_r = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)
    stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
    status = (stat_r.get("status") or {}) if stat_r.get("success") else {}
    text = (
        "🩹 *恢复面板*\n\n"
        f"当前 HP: {status.get('hp', 0)}/{status.get('max_hp', 0)}\n"
        f"当前 MP: {status.get('mp', 0)}/{status.get('max_mp', 0)}\n"
        f"自动恢复：{_vitals_regen_desc()}\n\n"
    )
    keyboard = []
    items = items_r.get("items", []) if items_r.get("success") else []
    recovery_ids = {"hp_pill", "mp_pill", "full_restore_pill"}
    recovery_items = [row for row in items if row.get("item_type") == "pill" and row.get("item_id") in recovery_ids]
    if recovery_items:
        text += "可用丹药：\n"
        for row in recovery_items[:6]:
            text += f"• {row.get('item_name')} x{row.get('quantity', 1)}\n"
            keyboard.append([InlineKeyboardButton(
                f"使用 {row.get('item_name')}",
                callback_data=f"recover_use|{row.get('item_id')}|{return_callback}"
            )])
    else:
        text += "当前没有可用的回血/回蓝丹药。\n"
    keyboard.append([InlineKeyboardButton("🛌 自动恢复说明", callback_data=f"recover_auto_{return_callback}")])
    keyboard.append([InlineKeyboardButton("🎒 储物袋", callback_data="bag")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data=return_callback)])
    return text, keyboard


async def _fetch_realm_trial(uid: str) -> dict | None:
    try:
        trial_resp = await http_get(f"{SERVER_URL}/api/realm-trial/{uid}", timeout=15)
    except Exception:
        return None
    if not trial_resp.get("success"):
        return None
    return trial_resp.get("trial") or None


def _format_realm_trial_text(trial: dict | None) -> str:
    if not trial:
        return "当前境界暂未配置试炼任务。"
    hunt_target = max(0, int(trial.get("hunt_target", 0) or 0))
    hunt_progress = max(0, int(trial.get("hunt_progress", 0) or 0))
    secret_target = max(0, int(trial.get("secret_target", 0) or 0))
    secret_progress = max(0, int(trial.get("secret_progress", 0) or 0))
    lines = ["🧪 *境界试炼进度*"]
    if hunt_target > 0:
        lines.append(f"• 狩猎：{hunt_progress}/{hunt_target}（还差 {max(0, hunt_target - hunt_progress)}）")
    if secret_target > 0:
        lines.append(f"• 秘境：{secret_progress}/{secret_target}（还差 {max(0, secret_target - secret_progress)}）")
    if len(lines) == 1:
        lines.append("• 当前境界无额外试炼要求")
    return "\n".join(lines)


def _build_breakthrough_preview(user_data: dict, *, strategy: str = "normal") -> str:
    from core.game.maps import get_map, get_spirit_density

    now_ts = int(time.time())
    current_rank = int(user_data.get("rank", 1) or 1)
    current_realm = get_realm_by_id(current_rank) or {"name": "当前境界"}
    next_realm = get_next_realm(current_rank)
    if not next_realm:
        return "你已站上当前世界的修行尽头。"

    map_id = str(user_data.get("current_map") or "canglan_city")
    map_info = get_map(map_id) or {}
    location_name = str(map_info.get("name") or map_id)
    spirit_density = float(get_spirit_density(map_id) or 1.0)
    location_bonus = max(
        -0.08,
        min(0.12, (spirit_density - 1.0) * _breakthrough_spirit_density_bonus_scale()),
    )
    ally_bonus = max(0.0, _breakthrough_ally_help_bonus())
    is_tribulation = int(current_realm.get("sub_level", 0) or 0) == 4 or "圆满" in str(current_realm.get("name") or "")
    tribulation_flat_penalty = _breakthrough_tribulation_flat_penalty() if is_tribulation else 0.0
    tribulation_rate_multiplier = _breakthrough_tribulation_rate_multiplier() if is_tribulation else 1.0
    tribulation_cost_multiplier = _breakthrough_tribulation_extra_cost_multiplier() if is_tribulation else 1.0
    tribulation_extra_stamina = _breakthrough_tribulation_extra_stamina() if is_tribulation else 0

    strategy = (strategy or "normal").strip().lower()
    if is_tribulation:
        strategy = "steady"
    base_cost = calculate_breakthrough_cost(current_rank)
    protect_need = _breakthrough_protect_material_need(current_rank) if strategy == "protect" else 0
    base_total_cost = int(base_cost + protect_need)
    if is_tribulation:
        cost = int(max(base_total_cost, round(base_total_cost * tribulation_cost_multiplier)))
    else:
        cost = base_total_cost
    tribulation_extra_cost = max(0, int(cost - base_total_cost))
    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    rate_parts = [f"基础成功率 {int(base_rate * 100)}%"]
    shown_rate = base_rate
    fire_bonus = _breakthrough_fire_bonus()
    steady_bonus = _breakthrough_steady_bonus()
    if user_data.get("element") == "火":
        shown_rate = min(1.0, shown_rate + fire_bonus)
        rate_parts.append(f"火灵根 +{int(fire_bonus * 100)}%")
    boost_until = int(user_data.get("breakthrough_boost_until", 0) or 0)
    boost_pct = float(user_data.get("breakthrough_boost_pct", 0) or 0)
    if boost_until > now_ts and boost_pct > 0:
        shown_rate = min(1.0, shown_rate + boost_pct / 100.0)
        rate_parts.append(f"聚灵增益 +{int(boost_pct)}%")
    shown_rate = min(1.0, max(0.0, shown_rate + location_bonus))
    rate_parts.append(f"地脉灵气 {location_bonus * 100:+.1f}%")
    shown_rate = min(1.0, shown_rate + ally_bonus)
    rate_parts.append(f"道友相助 +{int(ally_bonus * 100)}%")
    strategy_name = "渡劫突破" if is_tribulation else {
        "normal": "普通冲关",
        "steady": "稳妥突破",
        "protect": "护脉突破",
        "desperate": "生死突破",
    }.get(strategy, "普通冲关")
    extra_cost_text = "无额外材料"
    if strategy == "steady":
        shown_rate = min(1.0, shown_rate + steady_bonus)
        rate_parts.append(f"突破丹 +{int(steady_bonus * 100)}%")
        extra_cost_text = "额外消耗: 突破丹 x1"
    elif strategy == "protect":
        extra_cost_text = f"额外消耗: 下品灵石 x{protect_need}"
        rate_parts.append("护脉: 失败不进虚弱")
    elif strategy == "desperate":
        extra_cost_text = "额外效果: 成功额外奖励，失败惩罚更重"
    if is_tribulation:
        shown_rate = max(0.0, shown_rate - tribulation_flat_penalty)
        shown_rate = min(1.0, max(0.0, shown_rate * tribulation_rate_multiplier))
        rate_parts.append(f"天雷劫压制 {-tribulation_flat_penalty * 100:+.1f}%")
        rate_parts.append(f"雷劫强度倍率 x{tribulation_rate_multiplier:.2f}")

    title_text = "⛈️ *渡劫突破·天雷劫*" if is_tribulation else "⚡ *突破预告*"
    mode_text = "圆满渡劫（天雷劫）" if is_tribulation else "常规破境"
    tribulation_line = ""
    if is_tribulation:
        tribulation_line = (
            f"雷劫压制: 固定{-tribulation_flat_penalty * 100:+.1f}%，再乘以 {tribulation_rate_multiplier:.2f} 倍\n"
            f"雷劫附加消耗: +{tribulation_extra_cost:,} 下品灵石，+{tribulation_extra_stamina} 点精力\n"
        )
    return (
        f"{title_text}\n"
        f"策略: *{strategy_name}*\n"
        f"关卡类型: *{mode_text}*\n"
        f"你将从 *{current_realm['name']}* 冲击 *{next_realm['name']}*。\n"
        f"所在地: *{location_name}*（灵气×{spirit_density:.2f}，地脉{location_bonus * 100:+.1f}%）\n"
        "今日运势: *平*（0%）\n"
        f"道友相助: *已召集*（+{int(ally_bonus * 100)}%）\n"
        f"{tribulation_line}"
        f"消耗: {cost:,} 下品灵石\n"
        f"额外消耗: {_breakthrough_stamina_cost() + tribulation_extra_stamina} 点精力\n"
        f"{extra_cost_text}\n"
        f"预计成功率: *{int(shown_rate * 100)}%*\n"
        f"加成构成: {' ｜ '.join(rate_parts)}\n"
        "这不是普通操作，而是一次值得期待的破境尝试。\n"
        "说明：突破按实时成功率判定，不再包含保底。"
    )


def _build_breakthrough_strategy_notes(user_data: dict) -> str:
    from core.game.maps import get_spirit_density

    current_rank = int(user_data.get("rank", 1) or 1)
    current_realm = get_realm_by_id(current_rank) or {}
    next_realm = get_next_realm(current_rank) or {}
    is_tribulation = int(current_realm.get("sub_level", 0) or 0) == 4 or "圆满" in str(current_realm.get("name") or "")
    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    fire_bonus = _breakthrough_fire_bonus()
    steady_bonus = _breakthrough_steady_bonus()
    ally_bonus = max(0.0, _breakthrough_ally_help_bonus())
    map_id = str(user_data.get("current_map") or "canglan_city")
    spirit_density = float(get_spirit_density(map_id) or 1.0)
    location_bonus = max(
        -0.08,
        min(0.12, (spirit_density - 1.0) * _breakthrough_spirit_density_bonus_scale()),
    )
    tribulation_flat_penalty = _breakthrough_tribulation_flat_penalty() if is_tribulation else 0.0
    tribulation_rate_multiplier = _breakthrough_tribulation_rate_multiplier() if is_tribulation else 1.0

    def _apply_tribulation(rate: float) -> float:
        value = min(1.0, max(0.0, float(rate or 0.0)))
        if not is_tribulation:
            return value
        value = max(0.0, value - tribulation_flat_penalty)
        return min(1.0, max(0.0, value * tribulation_rate_multiplier))

    if user_data.get("element") == "火":
        base_rate = min(1.0, base_rate + fire_bonus)
    base_rate = min(1.0, max(0.0, base_rate + location_bonus + ally_bonus))
    protect_need = _breakthrough_protect_material_need(current_rank)
    steady_rate = _apply_tribulation(min(1.0, base_rate + steady_bonus))
    protect_rate = _apply_tribulation(base_rate)
    desperate_rate = _apply_tribulation(base_rate)
    if is_tribulation:
        return (
            f"当前为【{current_realm.get('name', '圆满境')}】圆满关口，仅开放 *渡劫突破*。\n"
            f"渡劫突破：消耗下品灵石 + 突破丹 x1，成功率约 *{int(steady_rate * 100)}%*。\n"
            "说明：渡劫成功率由灵根、地脉、道友助阵与天雷劫共同决定，不含保底。"
        )
    return (
        f"稳妥突破：消耗下品灵石 + 突破丹 x1，成功率约 *{int(steady_rate * 100)}%*，失败损失减半\n"
        f"护脉突破：消耗下品灵石（含附加 x{protect_need}），成功率约 *{int(protect_rate * 100)}%*，失败不进虚弱\n"
        f"生死突破：只消耗下品灵石，成功率约 *{int(desperate_rate * 100)}%*，成功有额外奖励，失败惩罚更重"
    )


async def _build_breakthrough_preview_block(uid: str, user_data: dict, *, strategy: str = "steady") -> str:
    """优先使用服务端预览，避免 Bot 本地规则与结算规则漂移。"""
    try:
        data = await http_get(
            f"{SERVER_URL}/api/breakthrough/preview/{uid}",
            params={"user_id": uid, "strategy": strategy, "call_for_help": "true"},
            timeout=15,
        )
        if data.get("success"):
            preview = data.get("preview", {}) or {}
            preview_text = str(preview.get("preview_text", "") or "").strip()
            notes = str(preview.get("strategy_notes", "") or "").strip()
            blocks = [b for b in (preview_text, notes) if b]
            if blocks:
                return "\n\n".join(blocks)
        if data.get("code") in ("UNAUTHORIZED", "FORBIDDEN"):
            return "❌ 突破预告鉴权失败，请关闭面板后重试。"
    except Exception:
        pass
    # fallback: local rendering
    return f"{_build_breakthrough_preview(user_data, strategy=strategy)}\n\n{_build_breakthrough_strategy_notes(user_data)}"


async def _build_events_menu(uid: str | None = None):
    if uid:
        data = await http_get(f"{SERVER_URL}/api/events/status/{uid}", timeout=15)
    else:
        data = await http_get(f"{SERVER_URL}/api/events", timeout=15)
    if not data.get("success"):
        return "❌ 获取活动失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    events = data.get("events", [])
    text = "🎉 *限时活动*\n\n"
    if not events:
        text += "当前没有活动。\n"
    else:
        for e in events[:5]:
            claimed = e.get("claimed_today")
            flag = "✅" if claimed else "🎁"
            reward_text = _format_reward_text(e.get("daily_reward") or {})
            if reward_text:
                text += f"{flag} {e.get('name')} - {e.get('desc')}\n  奖励: {reward_text}\n"
            else:
                text += f"{flag} {e.get('name')} - {e.get('desc')}\n"
    keyboard = []
    if uid:
        for e in events[:5]:
            if not e.get("claimed_today"):
                keyboard.append([InlineKeyboardButton(f"领取 {e.get('name')}", callback_data=f"event_claim_{e.get('id')}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, keyboard


async def _build_worldboss_menu():
    data = await http_get(f"{SERVER_URL}/api/worldboss/status", timeout=15)
    if not data.get("success"):
        return "❌ 获取BOSS状态失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    boss = data.get("boss", {})
    text = (
        "🐲 *世界BOSS*\n\n"
        f"{boss.get('name')} HP: {boss.get('hp')}/{boss.get('max_hp')}\n"
        "每次攻击都会拿到日常下品灵石和修为。\n"
        "金丹后有机会掉妖丹，化神后有机会掉龙鳞或凤羽，击杀额外给中品灵石。"
    )
    keyboard = [
        [InlineKeyboardButton("⚔️ 攻击", callback_data="worldboss_attack")],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, keyboard

# ==================== 回调处理器 ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    callback_request_id = f"tgcb:{query.id}"

    async def _safe_answer(text=None, show_alert=False) -> bool:
        try:
            if text is None:
                await query.answer()
            else:
                await query.answer(text, show_alert=show_alert)
            return True
        except Exception as e:
            # Ignore stale/invalid callback answers to avoid aborting the whole handler.
            logger.warning(f"callback answer skipped: {e}")
            return False

    panel_owner = _get_panel_owner(context, query.message)
    if panel_owner is None:
        # Group/supergroup callbacks must have an explicit owner binding.
        if not _is_private_panel_message(query.message):
            deny_text = "面板已失效，请输入 /xian_start 打开自己的面板"
            answered = await _safe_answer(deny_text, show_alert=True)
            if not answered:
                try:
                    await query.message.reply_text(f"⚠️ {deny_text}")
                except Exception:
                    pass
            return
        panel_owner = user_id
        _bind_panel_owner(context, query.message, panel_owner)
    if panel_owner and panel_owner != user_id:
        deny_text = "这不是你的操作面板"
        answered = await _safe_answer(deny_text, show_alert=True)
        if not answered:
            try:
                await query.message.reply_text(f"⚠️ {deny_text}")
            except Exception:
                pass
        return
    await _safe_answer()

    async def _safe_edit(text: str, reply_markup=None, parse_mode=None):
        """Prefer editing the original message; fall back gracefully if Markdown parsing fails."""
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            if reply_markup is not None:
                _bind_panel_owner(context, query.message, panel_owner or user_id)
        except Exception as first_exc:
            if _is_retry_after_error(first_exc):
                logger.warning(f"callback edit throttled: {first_exc}")
                await _safe_answer("操作过于频繁，请稍后再试。", show_alert=False)
                return
            try:
                await query.edit_message_text(text, reply_markup=reply_markup)
                if reply_markup is not None:
                    _bind_panel_owner(context, query.message, panel_owner or user_id)
            except Exception as edit_exc:
                # Benign case: Telegram rejects idempotent edits.
                if _is_message_not_modified_error(edit_exc):
                    return
                if _is_retry_after_error(edit_exc):
                    logger.warning(f"callback edit throttled: {edit_exc}")
                    await _safe_answer("操作过于频繁，请稍后再试。", show_alert=False)
                    return
                try:
                    sent = await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
                    if reply_markup is not None:
                        _bind_panel_owner(context, sent, user_id)
                except Exception as send_exc:
                    if _is_message_not_modified_error(send_exc):
                        return
                    if _is_retry_after_error(send_exc):
                        logger.warning(f"callback edit fallback throttled: {send_exc}")
                        return
                    try:
                        sent = await query.message.reply_text(text, reply_markup=reply_markup)
                        if reply_markup is not None:
                            _bind_panel_owner(context, sent, user_id)
                    except Exception as plain_exc:
                        if _is_message_not_modified_error(plain_exc):
                            return
                        if _is_retry_after_error(plain_exc):
                            logger.warning(f"callback edit fallback throttled: {plain_exc}")
                            return
                        logger.warning(f"callback edit fallback failed: {plain_exc}")
                        return

    async def _stop_if_cultivating(uid: str, action_text: str) -> bool:
        try:
            status = await http_get(f"{SERVER_URL}/api/cultivate/status/{uid}", timeout=15)
        except Exception:
            return False
        if not status.get("state"):
            return False
        current = int(status.get("current_gain", 0) or 0)
        keyboard = [
            [InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")],
            [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
        ]
        await _safe_edit(
            f"🧘 当前正在修炼中，暂时不能{action_text}。\n已积累修为: {current:,}\n请先结束修炼。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return True

    async def _show_feature_intro_once(feature_key: str, panel_title: str, continue_callback: str) -> bool:
        if _has_seen_feature_intro(context, user_id, feature_key):
            return False
        _mark_feature_intro_seen(context, user_id, feature_key)
        intro_text = _get_feature_intro_text(feature_key)
        text = (
            f"📜 {panel_title} · 初见\n\n"
            f"{intro_text}\n\n"
            "（该剧情仅首次显示）"
        )
        await _safe_edit(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"➡️ 进入{panel_title}", callback_data=continue_callback)],
                [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
            ]),
        )
        return True
    
    data = query.data
    if data == "equipment":
        data = "equipbag_0"

    if data.startswith("admin_test_"):
        if not _is_super_admin_tg(user_id):
            await _safe_edit(
                "❌ 权限不足：仅超管可使用该面板。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]),
            )
            return

        if data == "admin_test_refresh":
            await _show_admin_test_panel(update, context)
            return

        if data == "admin_test_target_self":
            context.user_data["admin_target_token"] = user_id
            context.user_data["admin_target_uid"] = ""
            await _show_admin_test_panel(update, context)
            return

        if data == "admin_test_target_clear":
            context.user_data.pop("admin_target_token", None)
            context.user_data.pop("admin_target_uid", None)
            await _show_admin_test_panel(update, context)
            return

        action_map = {
            "admin_test_action_set": "set",
            "admin_test_action_add": "add",
            "admin_test_action_minus": "minus",
        }
        action = action_map.get(data)
        if action:
            context.user_data["admin_panel_action"] = action
            await _show_admin_test_panel(update, context, selected_action=action)
            return

        if data == "admin_test_manual":
            prompt = await query.message.reply_text(
                "请输入修改内容：\n"
                "1) `字段 数值`（使用当前目标）\n"
                "2) `UID 字段 数值`\n"
                "3) `UID 操作 字段 数值`\n\n"
                "输入 `取消` 退出本次修改。",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(
                context,
                action="admin_modify",
                prompt_message=prompt,
                user_id=user_id,
            )
            await _show_admin_test_panel(update, context)
            return

        if data.startswith("admin_test_quick_"):
            preset_id = str(data[len("admin_test_quick_"):] or "").strip()
            target_token = _admin_current_target_token(context)
            if not target_token:
                await _safe_edit(
                    "❌ 还没有目标玩家，请先回复玩家消息发 /test，或点击“目标自己”。",
                    reply_markup=_admin_panel_keyboard(context),
                )
                return
            guard_key = f"{preset_id}:{target_token}"
            now_ms = int(time.time() * 1000)
            last_key = str(context.user_data.get("admin_quick_guard_key", "") or "")
            last_ts = int(context.user_data.get("admin_quick_guard_ts", 0) or 0)
            if guard_key == last_key and (now_ms - last_ts) < 1800:
                await _safe_answer("操作已受理，请勿连点。", show_alert=False)
                return
            context.user_data["admin_quick_guard_key"] = guard_key
            context.user_data["admin_quick_guard_ts"] = now_ms
            try:
                ok, result_text, resolved_uid = await _admin_apply_preset(
                    operator_tg_id=user_id,
                    target_token=target_token,
                    preset_id=preset_id,
                )
            except Exception as exc:
                logger.error("admin_quick_preset_failed preset=%s error=%s", preset_id, type(exc).__name__)
                await _safe_edit("❌ 预设执行失败，请稍后重试。", reply_markup=_admin_panel_keyboard(context))
                return

            if resolved_uid:
                context.user_data["admin_target_uid"] = resolved_uid
                context.user_data["admin_target_token"] = resolved_uid

            if ok:
                try:
                    await query.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
                await _show_admin_test_panel(update, context)
            else:
                await _safe_edit(result_text, reply_markup=_admin_panel_keyboard(context))
            return

        await _safe_edit("❌ 未知管理员操作。", reply_markup=_admin_panel_keyboard(context))
        return

    intro_targets = {
        "shop_all": ("shop_all", "万宝楼"),
        "cultivate": ("cultivate", "修炼"),
        "sect_menu": ("sect_menu", "宗门"),
        "world_map": ("world_map", "世界地图"),
    }
    intro_conf = intro_targets.get(data)
    if intro_conf:
        feature_key, panel_title = intro_conf
        if await _show_feature_intro_once(feature_key, panel_title, data):
            return

    # 序章剧情翻页
    if data.startswith("prologue_next_"):
        try:
            page = int(data.split("_")[-1])
        except (ValueError, IndexError):
            page = 0
        await _send_prologue_page(update, context, page)
        return

    # ── 剧情系统 ─────────────────────────────────────────
    if data == "story_menu":
        await _handle_story_menu(update, context, user_id, _safe_edit)
        return

    if data.startswith("story_read_"):
        chapter_id = data[len("story_read_"):]
        await _handle_story_read(update, context, user_id, chapter_id, _safe_edit)
        return

    if data.startswith("story_next_"):
        chapter_id = data[len("story_next_"):]
        await _handle_story_read(update, context, user_id, chapter_id, _safe_edit)
        return

    if data.startswith("story_reread_"):
        chapter_id = data[len("story_reread_"):]
        await _handle_story_reread(update, context, user_id, chapter_id, _safe_edit)
        return
    # ── 剧情系统 END ─────────────────────────────────────

    # 主菜单
    if data == "main_menu":
        _clear_pending_action(context)
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if stat_r.get("success"):
                    status = stat_r["status"]
                    equipped_items = await _get_equipped_item_names(uid, status)
                    text = format_status_text(status, "CHS", platform="telegram", equipped_items=equipped_items)
                    await _safe_edit(
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_main_menu_keyboard()
                    )
                    return
            await _safe_edit("选择下方按钮继续：", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"main menu status error: {e}")
            await _safe_edit("选择下方按钮继续：", reply_markup=get_main_menu_keyboard())
        return

    if data == "miniapp_private_hint":
        await _safe_edit(
            "🏯 当前会话不支持直接弹出 MiniApp。\n请先在与机器人私聊中发送 /xian_start，再点击「进入修仙世界」。",
            reply_markup=get_main_menu_keyboard(include_miniapp=False),
        )
        return

    # 大地图
    if data == "world_map":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if stat_r.get("success"):
                    status = stat_r["status"]
                    current_map = status.get("current_map", "canglan_city")
                    rank = int(status.get("rank", 1) or 1)
                    dao_h = float(status.get("dao_heng", 0) or 0)
                    dao_n = float(status.get("dao_ni", 0) or 0)
                    dao_y = float(status.get("dao_yan", 0) or 0)
                    try:
                        from core.game.maps import format_world_map, get_adjacent_maps, get_area_actions
                        map_text = format_world_map(current_map, rank, dao_h, dao_n, dao_y)
                    except Exception as me:
                        logger.error(f"format_world_map error: {me}")
                        map_text = f"🗺️ 当前位置：{current_map}\n（地图渲染失败）"

                    # 构建移动按钮：显示相邻可前往的区域
                    keyboard = []
                    try:
                        from core.game.maps import get_adjacent_maps, check_travel_requirements
                        adj = get_adjacent_maps(current_map)
                        for a in adj:
                            can_enter, _ = check_travel_requirements(a["id"], rank, dao_h, dao_n, dao_y)
                            if can_enter:
                                keyboard.append([InlineKeyboardButton(
                                    f"→ {a['name']}",
                                    callback_data=f"travel_to_{a['id']}"
                                )])
                    except Exception as ae:
                        logger.error(f"adjacent maps error: {ae}")

                    # 当前区域可执行操作
                    try:
                        actions = get_area_actions(current_map)
                        action_row = []
                        for act in actions[:4]:  # 最多显示4个操作按钮
                            action_row.append(InlineKeyboardButton(
                                act["label"],
                                callback_data=f"area_action_{act['action']}"
                            ))
                        if action_row:
                            # 分两行显示
                            keyboard.insert(0, action_row[:2])
                            if len(action_row) > 2:
                                keyboard.insert(1, action_row[2:])
                    except Exception:
                        pass

                    keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                    try:
                        await _safe_edit(map_text, reply_markup=InlineKeyboardMarkup(keyboard))
                    except Exception:
                        await query.message.reply_text(map_text, reply_markup=InlineKeyboardMarkup(keyboard))
                    return
            await _safe_edit("❌ 请先注册角色", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        except Exception as e:
            logger.error(f"world_map error: {e}")
            try:
                await _safe_edit("❌ 地图加载失败，请稍后重试", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
            except Exception:
                await query.message.reply_text("❌ 地图加载失败，请稍后重试")
        return

    # 区域移动
    if data.startswith("travel_to_"):
        to_map_id = data[len("travel_to_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if not r.get("success"):
                await _safe_edit("❌ 请先注册角色")
                return
            uid = r["user_id"]
            travel_r = await http_post(
                f"{SERVER_URL}/api/travel",
                json={"user_id": uid, "to_map": to_map_id},
                timeout=15,
            )
            if travel_r.get("success"):
                to_name = travel_r.get("to_name", to_map_id)
                to_desc = travel_r.get("to_desc", "")
                stamina_cost = travel_r.get("stamina_cost", 1)
                first_visit = travel_r.get("first_visit", False)
                first_visit_text = travel_r.get("first_visit_text") or ""

                text = f"🚶 你启程前往 *{to_name}*……\n\n"
                text += f"_{to_desc}_\n\n"
                text += f"⚡ 消耗精力：{stamina_cost}\n"

                if first_visit and first_visit_text:
                    text += f"\n📍 *首次到达*\n{first_visit_text}\n"

                # 到达后显示该区域可执行操作
                actions = travel_r.get("actions") or []
                keyboard = []
                action_row = []
                for act in actions[:4]:
                    action_row.append(InlineKeyboardButton(
                        act["label"],
                        callback_data=f"area_action_{act['action']}"
                    ))
                if action_row:
                    keyboard.append(action_row[:2])
                    if len(action_row) > 2:
                        keyboard.append(action_row[2:])

                keyboard.append([InlineKeyboardButton("🗺️ 查看地图", callback_data="world_map")])
                keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])

                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN,
                                 reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                msg = travel_r.get("message", "移动失败")
                keyboard = [
                    [InlineKeyboardButton("🗺️ 返回地图", callback_data="world_map")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"❌ {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"travel error: {e}")
            await _safe_edit("❌ 移动失败，请稍后重试", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("area_action_"):
        action = data[len("area_action_"):]
        redirect_map = {
            "shop": "shop_all",
            "auction": "shop_all",
            "trade": "shop_all",
            "quest": "quests",
            "sect_recruit": "sect_menu",
            "sect_daily": "sect_daily_claim",
            "hunt": "hunt",
            "boss": "worldboss_menu",
            "cultivate_bonus": "cultivate",
            "skill_learn": "skills",
            "treasure_hunt": "secret_realms",
            "explore": "secret_realms",
        }
        if action == "npc_talk":
            await _safe_edit(
                "💬 当前区域暂未开放可交谈 NPC 事件，请先进行修炼/历练。",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗺️ 返回地图", callback_data="world_map")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
            return
        target_callback = redirect_map.get(action)
        if target_callback:
            await _safe_edit(
                "✅ 已为你切换到对应功能面板。",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ 继续", callback_data=target_callback)],
                    [InlineKeyboardButton("🗺️ 返回地图", callback_data="world_map")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        else:
            await _safe_edit(
                "❌ 该区域动作暂未开放。",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗺️ 返回地图", callback_data="world_map")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # PVP 菜单
    if data == "social_menu":
        text, keyboard = _build_social_menu_text()
        await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "chat_prompt":
        try:
            prompt = await query.message.reply_text(
                "请输入要论道的玩家名（回复此消息）：\n示例：风霜宗",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="chat_request", prompt_message=prompt, user_id=user_id)
        except Exception as e:
            logger.error(f"chat prompt error: {e}")
            await _safe_edit("❌ 无法发起论道，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("chat_accept_"):
        req_id = data[len("chat_accept_"):]
        try:
            req_id_int = int(req_id)
        except (TypeError, ValueError):
            await _safe_edit("❌ 论道请求无效", reply_markup=get_main_menu_keyboard())
            return
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if not r.get("success"):
                await _safe_edit("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
                return
            uid = r["user_id"]
            result = await http_post(
                f"{SERVER_URL}/api/social/chat/accept",
                json={"user_id": uid, "request_id": req_id_int},
                timeout=15,
            )
            if not result.get("success"):
                await _safe_edit(f"❌ {result.get('message', '处理失败')}", reply_markup=get_main_menu_keyboard())
                return

            to_name = result.get("to_username", "你")
            from_name = result.get("from_username", "道友")
            acceptor_text = "🗣️ 论道成功！\n" + _build_chat_reward_text(
                from_name,
                result.get("to_stamina_gain", 0),
                result.get("to_exp_gain", result.get("exp_gain", 0)),
            )
            initiator_text = "🗣️ 论道成功！\n" + _build_chat_reward_text(
                to_name,
                result.get("from_stamina_gain", 0),
                result.get("from_exp_gain", result.get("exp_gain", 0)),
            )

            await _safe_edit("✅ 已接受论道邀请", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
            try:
                await query.message.reply_text(acceptor_text)
            except Exception:
                pass

            from_tid = result.get("from_telegram_id")
            if from_tid:
                try:
                    await context.bot.send_message(chat_id=from_tid, text=initiator_text)
                except Exception as exc:
                    logger.warning(f"chat notify initiator failed: {exc}")
        except Exception as e:
            logger.error(f"chat accept error: {e}")
            await _safe_edit("❌ 论道处理失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("chat_reject_"):
        req_id = data[len("chat_reject_"):]
        try:
            req_id_int = int(req_id)
        except (TypeError, ValueError):
            await _safe_edit("❌ 论道请求无效", reply_markup=get_main_menu_keyboard())
            return
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if not r.get("success"):
                await _safe_edit("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
                return
            uid = r["user_id"]
            result = await http_post(
                f"{SERVER_URL}/api/social/chat/reject",
                json={"user_id": uid, "request_id": req_id_int},
                timeout=15,
            )
            if not result.get("success"):
                await _safe_edit(f"❌ {result.get('message', '处理失败')}", reply_markup=get_main_menu_keyboard())
                return
            await _safe_edit("已拒绝论道邀请", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
            from_tid = result.get("from_telegram_id")
            to_name = result.get("to_username", "对方")
            if from_tid:
                try:
                    await context.bot.send_message(chat_id=from_tid, text=f"😶 你的论道请求被 {to_name} 拒绝了。")
                except Exception as exc:
                    logger.warning(f"chat reject notify failed: {exc}")
        except Exception as e:
            logger.error(f"chat reject error: {e}")
            await _safe_edit("❌ 论道处理失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "pvp_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "进行 PVP"):
                    return
                text, keyboard = await _build_pvp_menu(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"pvp menu error: {e}")
            await _safe_edit("❌ PVP 面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "pvp_records":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                text, keyboard = await _build_pvp_records(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"pvp records error: {e}")
            await _safe_edit("❌ PVP 记录加载失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ 返回PVP", callback_data="pvp_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "pvp_ranking":
        try:
            text, keyboard = await _build_pvp_ranking()
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"pvp ranking error: {e}")
            await _safe_edit("❌ PVP 排行加载失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ 返回PVP", callback_data="pvp_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("pvp_challenge_"):
        opponent_id = data[len("pvp_challenge_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "挑战 PVP"):
                    return
                request_id = _new_request_id(context)
                result = await http_post(
                    f"{SERVER_URL}/api/pvp/challenge",
                    json={"user_id": uid, "opponent_id": opponent_id, "request_id": request_id},
                    timeout=15,
                )
                if result.get("success"):
                    rewards = result.get("rewards", {})
                    change = result.get("rating_change", {})
                    text = (
                        f"⚔️ *PVP 结果*\n\n"
                        f"{result.get('message', '')}\n"
                        f"回合数: {result.get('rounds', 0)}\n"
                        f"ELO 变化: {change.get('challenger', 0)}\n"
                        f"奖励: {rewards.get('copper', 0)} 下品灵石, {rewards.get('exp', 0)} 修为\n"
                    )
                else:
                    text = f"❌ {result.get('message', '挑战失败')}"
                keyboard = [
                    [InlineKeyboardButton("🎯 再来一局", callback_data="pvp_menu")],
                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                ]
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"pvp challenge error: {e}")
            await _safe_edit("❌ PVP 挑战失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ 返回PVP", callback_data="pvp_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    # 好友/赠礼/论道功能已下线，统一拦截旧按钮与旧回调
    if (
        data in ("gift_menu", "friends_menu", "friends_add_help", "social_chat_menu", "friends_list", "friends_requests")
        or data.startswith("friends_")
        or data.startswith("social_chat_")
    ):
        await _safe_edit(
            "🚫 好友、赠礼、论道功能已下线",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 返回社交", callback_data="social_menu")],
                [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
            ]),
        )
        return

    if data == "sect_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                info = await http_get(f"{SERVER_URL}/api/sect/member/{uid}", timeout=15)
                if info.get("success"):
                    sect = info.get("sect", {})
                    header = "🏛️ *我的宗门*"
                    text = (
                        f"{header}\n\n"
                        f"名称: {sect.get('name')}\n"
                        f"等级: {sect.get('level', 1)}\n"
                        f"阵营: {sect.get('faction', '未知')}\n"
                        f"人数: {sect.get('current_members', 0)}/{sect.get('max_members', 50)}\n"
                        f"修炼加成: +{int(float(sect.get('cultivation_bonus', 10) or 0) * 100)}%\n"
                        f"宗门贡献: {sect.get('contribution', 0)}\n\n"
                        "操作：\n"
                        "• `/sect daily` 领取每日修炼资源\n"
                        "• `/sect quests` 查看宗门任务\n"
                        "• `/sect leave` 退出宗门\n"
                    )
                    keyboard = [
                        [InlineKeyboardButton("📦 领取每日资源", callback_data="sect_daily_claim")],
                        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                    ]
                else:
                    # 未加入宗门：列出预设的12个宗门
                    text = (
                        "🏛️ *宗门系统*\n\n"
                        "你当前未加入任何宗门。\n\n"
                        "各大宗门会不定期开放招聘会，届时全服通知。\n"
                        "满足条件的修士方可申请加入。\n\n"
                        "━━━ 正道五宗 ━━━\n"
                        "🔹 太清宗 ｜ 恒道传承，第一大派\n"
                        "    入门：筑基期（初期）+ 不限灵根\n"
                        "🔹 天剑门 ｜ 剑修圣地\n"
                        "    入门：筑基期（初期）+ 不限灵根\n"
                        "🔹 丹鼎阁 ｜ 丹道至尊\n"
                        "    入门：练气期（后期）+ 不限灵根\n"
                        "🔹 万法宗 ｜ 博采众长\n"
                        "    入门：练气期（中期）+ 不限灵根\n"
                        "🔹 灵兽谷 ｜ 驭兽世家\n"
                        "    入门：练气期（中期）+ 不限灵根\n\n"
                        "━━━ 邪道四宗 ━━━\n"
                        "🔻 逆天殿 ｜ 逆道传承，亦正亦邪\n"
                        "    入门：筑基期（圆满）+ 不限灵根\n"
                        "🔻 血煞宗 ｜ 嗜血修炼\n"
                        "    入门：筑基期（初期）+ 不限灵根\n"
                        "🔻 万鬼门 ｜ 鬼修传承\n"
                        "    入门：筑基期（初期）+ 不限灵根\n"
                        "🔻 天魔教 ｜ 魔道至尊\n"
                        "    入门：金丹期（初期）+ 不限灵根\n\n"
                        "━━━ 中立三方 ━━━\n"
                        "🔸 星辰阁 ｜ 衍道传承，百年开门一次\n"
                        "    入门：金丹期（后期）+ 需「星力觉醒」标签\n"
                        "🔸 乱星海散修联盟 ｜ 散修互助\n"
                        "    入门：练气期（初期）+ 不限\n"
                        "🔸 蛮荒妖族联盟 ｜ 妖修联盟\n"
                        "    入门：练气期（后期）+ 不限灵根\n\n"
                        "💡 提示：等待宗门招聘会开放通知，或达到足够境界后主动申请。"
                    )
                    keyboard = [
                        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                    ]
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"sect menu error: {e}")
            await _safe_edit("❌ 宗门面板加载失败", reply_markup=get_main_menu_keyboard())
        return

    if data == "sect_daily_claim":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                claim_r = await http_post(
                    f"{SERVER_URL}/api/sect/daily_claim",
                    json={"user_id": uid},
                    timeout=15,
                )
                if claim_r.get("success"):
                    rewards = claim_r.get("rewards", {})
                    text = (
                        "📦 *每日宗门资源已领取*\n\n"
                        f"• 下品灵石: +{rewards.get('copper', 0)}\n"
                        f"• 修为: +{rewards.get('exp', 0)}\n"
                    )
                    items = rewards.get("items", [])
                    for item in items:
                        text += f"• {item.get('name', '物品')}: +{item.get('qty', 1)}\n"
                    if rewards.get("mentality_cost"):
                        text += f"\n⚠️ 宗门压榨：心境 -{rewards['mentality_cost']}"
                else:
                    text = f"❌ {claim_r.get('message', '领取失败')}"
            else:
                text = "❌ 未找到账号"
            keyboard = [[InlineKeyboardButton("🔙 宗门", callback_data="sect_menu")]]
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"sect daily claim error: {e}")
            await _safe_edit("❌ 领取失败", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 宗门", callback_data="sect_menu")]]))
        return

    if data == "currency_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                text, keyboard = await _build_currency_menu(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"currency menu error: {e}")
            await _safe_edit("❌ 货币面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("currency_exchange_"):
        parts = data.split("_")
        if len(parts) < 4:
            return
        direction = parts[2]
        amount_text = parts[3]
        try:
            amount = int(amount_text)
        except (TypeError, ValueError):
            await _safe_edit("❌ 兑换参数错误", reply_markup=get_main_menu_keyboard())
            return
        if direction not in ("up", "down"):
            await _safe_edit("❌ 兑换方向无效", reply_markup=get_main_menu_keyboard())
            return
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                from_currency = "copper" if direction == "up" else "gold"
                request_id = _new_request_id(context)
                result = await http_post(
                    f"{SERVER_URL}/api/currency/exchange",
                    json={"user_id": uid, "from_currency": from_currency, "amount": amount, "request_id": request_id},
                    timeout=15,
                )
                msg = f"{'✅' if result.get('success') else '❌'} {result.get('message', '兑换失败')}"
                text, keyboard = await _build_currency_menu(uid)
                await query.message.reply_text(msg)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"currency exchange callback error: {e}")
            await _safe_edit(
                "❌ 货币兑换失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💱 返回货币", callback_data="currency_menu")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data == "convert_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "进行资源转化"):
                    return
                text, keyboard = await _build_convert_menu(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"convert menu error: {e}")
            await _safe_edit("❌ 转化面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("convert_route_"):
        route = data[len("convert_route_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "进行资源转化"):
                    return
                text, keyboard = await _build_convert_route(uid, route)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit(
                    "❌ 未找到账号，请先注册或稍后重试",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]),
                )
        except Exception as e:
            logger.error(f"convert route error: {e}")
            await _safe_edit("❌ 转化路线加载失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 返回转化", callback_data="convert_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("convert_do"):
        route = None
        item_id = None
        qty = None
        if data.startswith("convert_do|"):
            parts = data.split("|")
            if len(parts) < 4:
                await _safe_edit("❌ 转化参数错误", reply_markup=get_main_menu_keyboard())
                return
            route = parts[1]
            qty = parts[-1]
            item_id = "|".join(parts[2:-1]) if len(parts) > 4 else parts[2]
        else:
            payload = data[len("convert_do_"):]
            try:
                route, item_id, qty = payload.rsplit("_", 2)
            except Exception:
                await _safe_edit("❌ 转化参数错误", reply_markup=get_main_menu_keyboard())
                return
        try:
            qty = int(qty)
        except Exception:
            await _safe_edit("❌ 转化参数错误", reply_markup=get_main_menu_keyboard())
            return
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "进行资源转化"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/convert",
                    json={
                        "user_id": uid,
                        "target_item_id": item_id,
                        "quantity": qty,
                        "route": route,
                        "request_id": _new_request_id(context),
                    },
                    timeout=15,
                )
                if result.get("success"):
                    catalyst = result.get("catalyst") or {}
                    catalyst_text = ""
                    if catalyst and catalyst.get("item_id"):
                        catalyst_text = (
                            f"\n专精材料: {catalyst.get('used', 0)} x "
                            f"{_item_display_name(str(catalyst.get('item_id') or ''))}"
                        )
                    text = (
                        f"✅ {result.get('message', result.get('route_name', '转化完成'))}\n"
                        f"目标: {result.get('target_name')} x{result.get('output_quantity', 0)}\n"
                        f"消耗: {result.get('cost_copper', 0)} 下品灵石{catalyst_text}"
                    )
                else:
                    text = f"❌ {result.get('message', '转化失败')}"
                keyboard = [
                    [InlineKeyboardButton("🔁 继续转化", callback_data=f"convert_route_{route}")],
                    [InlineKeyboardButton("🔙 返回", callback_data="convert_menu")],
                ]
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit(
                    "❌ 未找到账号，请先注册或稍后重试",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]),
                )
        except Exception as e:
            logger.error(f"convert do error: {e}")
            await _safe_edit("❌ 转化失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 返回转化", callback_data="convert_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "alchemy_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "炼丹"):
                    return
                text, keyboard = await _build_alchemy_menu(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"alchemy menu error: {e}")
            await _safe_edit("❌ 炼丹面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("alchemy_brew_"):
        recipe_id = data[len("alchemy_brew_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "炼丹"):
                    return
                request_id = _new_request_id(context)
                result = await http_post(
                    f"{SERVER_URL}/api/alchemy/brew",
                    json={"user_id": uid, "recipe_id": recipe_id, "request_id": request_id},
                    timeout=15,
                )
                if result.get("success"):
                    if result.get("brew_success"):
                        prod = result.get("product") or {}
                        text = f"✅ 炼丹成功！获得 {prod.get('item_name', '丹药')} x{prod.get('quantity', 1)}"
                    else:
                        text = "❌ 炼丹失败。材料已消耗。"
                else:
                    text = f"❌ {result.get('message', '炼丹失败')}"
                text2, keyboard = await _build_alchemy_menu(uid)
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _safe_edit(text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"alchemy brew error: {e}")
            await _safe_edit("❌ 炼丹失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧪 返回炼丹", callback_data="alchemy_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "gacha_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            uid = r.get("user_id") if r.get("success") else None
            text, keyboard = await _build_gacha_menu(uid)
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"gacha menu error: {e}")
            await _safe_edit("❌ 抽卡面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "gacha_free_limit":
        try:
            await query.answer("今日免费抽奖次数已用尽", show_alert=False)
        except Exception:
            pass
        return

    if data.startswith("gacha_pull_"):
        parts = data.split("_")
        if len(parts) >= 4:
            banner_id = parts[2]
            draw_mode = parts[3]
        else:
            return
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                force_paid = draw_mode == "paid1"
                if force_paid:
                    count = 1
                else:
                    try:
                        count = int(draw_mode)
                    except (TypeError, ValueError):
                        count = 1
                request_id = _new_request_id(context)
                result = await http_post(
                    f"{SERVER_URL}/api/gacha/pull",
                    json={
                        "user_id": uid,
                        "banner_id": int(banner_id),
                        "count": int(count),
                        "force_paid": force_paid,
                        "request_id": request_id,
                    },
                    timeout=15,
                )
                if result.get("success"):
                    text = "🎲 *抽奖结果*\n\n"
                    for it in result.get("results", [])[:10]:
                        name = it.get("item_name") or _item_display_name(str(it.get("item_id") or ""))
                        text += f"• [{it.get('rarity')}] {name}\n"
                    if result.get("pull_mode") == "free":
                        text += "\n本次为免费抽奖。"
                    else:
                        cost = result.get("cost", {})
                        currency_name = "中品灵石" if cost.get("currency") == "gold" else "下品灵石"
                        text += f"\n消耗：{cost.get('amount', 0)}{currency_name} + {result.get('stamina_cost', 0)}精力"
                else:
                    text = f"❌ {result.get('message', '抽奖失败')}"
                text2, keyboard = await _build_gacha_menu(uid)
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _safe_edit(text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"gacha pull error: {e}")
            await _safe_edit("❌ 抽卡失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎲 返回抽卡", callback_data="gacha_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "achievements_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                text, keyboard = await _build_achievements_menu(uid)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"achievements menu error: {e}")
            await _safe_edit("❌ 成就面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("ach_claim_"):
        ach_id = data[len("ach_claim_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(
                    f"{SERVER_URL}/api/achievements/claim",
                    json={"user_id": uid, "achievement_id": ach_id},
                    timeout=15,
                )
                if result.get("success"):
                    text = f"✅ {result.get('message', '领取成功')}"
                    reward_text = _format_reward_text(result.get("rewards") or {})
                    if reward_text:
                        text += f"\n奖励: {reward_text}"
                else:
                    text = f"❌ {result.get('message', '失败')}"
                text2, keyboard = await _build_achievements_menu(uid)
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _safe_edit(text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"achievement claim error: {e}")
            await _safe_edit("❌ 成就奖励领取失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏅 返回成就", callback_data="achievements_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "events_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            uid = r.get("user_id") if r.get("success") else None
            text, keyboard = await _build_events_menu(uid)
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"events menu error: {e}")
            await _safe_edit("❌ 活动面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "worldboss_menu":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "挑战世界BOSS"):
                    return
            text, keyboard = await _build_worldboss_menu()
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"worldboss menu error: {e}")
            await _safe_edit("❌ 世界BOSS面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "bounty_menu":
        try:
            text, keyboard = await _build_bounty_menu()
            await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"bounty menu error: {e}")
            await _safe_edit("❌ 悬赏会面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("event_claim_"):
        event_id = data[len("event_claim_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "领取活动奖励"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/events/claim",
                    json={"user_id": uid, "event_id": event_id},
                    timeout=15,
                )
                if result.get("success"):
                    reward_text = _format_reward_text(result.get("rewards") or {})
                    text = "✅ 领取成功"
                    if reward_text:
                        text += f"\n🎁 获得: {reward_text}"
                else:
                    text = f"❌ {result.get('message', '失败')}"
                text2, keyboard = await _build_events_menu(uid)
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _safe_edit(text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"event claim error: {e}")
            await _safe_edit("❌ 活动奖励领取失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 返回活动", callback_data="events_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "worldboss_attack":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(
                    f"{SERVER_URL}/api/worldboss/attack",
                    json={"user_id": uid},
                    timeout=15,
                )
                if result.get("success"):
                    text = (
                        f"⚔️ 造成伤害 {result.get('damage')}，BOSS HP {result.get('boss_hp')}\n"
                        f"今日剩余攻击次数：{result.get('attacks_left', 0)}"
                    )
                    reward_text = _format_reward_text(result.get("rewards") or {})
                    if reward_text:
                        text += f"\n🎁 本次收获：{reward_text}"
                    if result.get("defeated"):
                        text += "\n🎉 BOSS 已被击败！奖励已发放。"
                else:
                    text = f"❌ {result.get('message', '攻击失败')}"
                text2, keyboard = await _build_worldboss_menu()
                await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _safe_edit(text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"worldboss attack error: {e}")
            await _safe_edit("❌ 世界BOSS攻击失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🐲 返回BOSS", callback_data="worldboss_menu")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("recover_auto_"):
        return_callback = data[len("recover_auto_"):] or "main_menu"
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                status = (stat_r.get("status") or {}) if stat_r.get("success") else {}
                text = (
                    "🛌 *自动恢复中*\n\n"
                    "你已选择自动恢复。\n"
                    f"{_vitals_regen_desc()}。\n\n"
                    f"当前 HP: {status.get('hp', 0)}/{status.get('max_hp', 0)}\n"
                    f"当前 MP: {status.get('mp', 0)}/{status.get('max_mp', 0)}"
                )
                keyboard = [
                    [InlineKeyboardButton("🩹 恢复面板", callback_data=f"recovery_menu_{return_callback}")],
                    [InlineKeyboardButton("🔙 返回", callback_data=return_callback)],
                ]
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"recover auto error: {e}")
            await _safe_edit("❌ 自动恢复面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("recovery_menu_"):
        return_callback = data[len("recovery_menu_"):] or "main_menu"
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                text, keyboard = await _build_recovery_menu(uid, return_callback=return_callback)
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"recovery menu error: {e}")
            await _safe_edit("❌ 恢复面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data.startswith("recover_use"):
        item_id = None
        return_callback = "main_menu"
        if data.startswith("recover_use|"):
            parts = data.split("|")
            if len(parts) >= 3:
                item_id = parts[1]
                return_callback = parts[2] or "main_menu"
        else:
            item_id = data[len("recover_use_"):]
        if not item_id:
            await _safe_edit("❌ 恢复参数错误", reply_markup=get_main_menu_keyboard())
            return
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/item/use", json={"user_id": uid, "item_id": item_id}, timeout=15)
                text, keyboard = await _build_recovery_menu(uid, return_callback=return_callback or "main_menu")
                prefix = f"{'✅' if result.get('success') else '❌'} {result.get('message', '已处理')}\n\n"
                await _safe_edit(prefix + text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"recover use error: {e}")
            await _safe_edit(
                "❌ 使用恢复道具失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🩹 恢复面板", callback_data=f"recovery_menu_{return_callback or 'main_menu'}")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    # 注册
    if data == "register":
        keyboard = [
            [
                InlineKeyboardButton("金 ⚔️", callback_data="register_gold"),
                InlineKeyboardButton("木 🌿", callback_data="register_wood"),
            ],
            [
                InlineKeyboardButton("水 💧", callback_data="register_water"),
                InlineKeyboardButton("火 🔥", callback_data="register_fire"),
            ],
            [
                InlineKeyboardButton("土 🏔️", callback_data="register_earth"),
            ],
        ]
        await _safe_edit(
            "🌟 选择你的五行属性（选择后无法更改）：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 注册选择五行
    if data.startswith("register_"):
        element_map = {
            "register_gold": "金",
            "register_wood": "木", 
            "register_water": "水",
            "register_fire": "火",
            "register_earth": "土",
        }
        element = element_map.get(data)
        if element:
            try:
                prompt = await query.message.reply_text(
                    "请输入角色名（2-16 位中文/字母/数字）。\n直接发送即可，无需回复这条消息：",
                    reply_markup=ForceReply(selective=True),
                )
                _set_pending_action(context, action="register_name", prompt_message=prompt, user_id=user_id)
                context.user_data["pending_element"] = element
            except Exception as exc:
                logger.error(f"register prompt error: {exc}")
                await _safe_edit("❌ 无法发起注册，请稍后重试", reply_markup=get_main_menu_keyboard())
        return
    
    # 状态
    if data == "status":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if stat_r.get("success"):
                    status = stat_r["status"]
                    equipped_items = await _get_equipped_item_names(uid, status)
                    text = format_status_text(status, "CHS", platform="telegram", equipped_items=equipped_items)
                    await _safe_edit(
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_main_menu_keyboard()
                    )
        except Exception as e:
            logger.error(f"callback status error: {e}")
            await _safe_edit("❌ 状态加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return
    
    # 修炼
    if data == "cultivate":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                status = await http_get(f"{SERVER_URL}/api/cultivate/status/{uid}", timeout=15)
                
                if status.get("state"):
                    # 正在修炼
                    current = status.get("current_gain", 0)
                    text = f"🧘 修炼中，已获得 {current:,} 修为"
                    if status.get("is_capped"):
                        text += "\n修炼经验已满，请及时结算。"
                    keyboard = [[InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")]]
                else:
                    # 未修炼
                    text = "开始修炼？"
                    keyboard = [[InlineKeyboardButton("🧘 开始修炼", callback_data="cultivate_start")]]
                
                keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                await _safe_edit(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"callback cultivate error: {e}")
            await _safe_edit(
                "❌ 修炼面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🧘 修炼", callback_data="cultivate")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    if data == "cultivate_start":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/cultivate/start", json={"user_id": uid}, timeout=15)
                if result.get("success"):
                    keyboard = [
                        [InlineKeyboardButton("📊 查看进度", callback_data="cultivate_stat")],
                        [InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")],
                        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                    ]
                    await _safe_edit(
                        "🧘 开始修炼！",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    keyboard = [[InlineKeyboardButton("🔙 返回修炼面板", callback_data="cultivate")]]
                    await _safe_edit(
                        f"❌ {result.get('message', '开始修炼失败')}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"cultivate start error: {e}")
            await _safe_edit(
                "❌ 开始修炼失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🧘 修炼面板", callback_data="cultivate")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    if data == "cultivate_stat":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                status = await http_get(f"{SERVER_URL}/api/cultivate/status/{uid}", timeout=15)
                if not status.get("success"):
                    await _safe_edit(
                        "❌ 修炼状态获取失败，请稍后重试",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🧘 修炼面板", callback_data="cultivate")],
                            [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                        ]),
                    )
                    return
                current = status.get("current_gain", 0)
                keyboard = [
                    [InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")],
                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                ]
                text = f"🧘 已获得 {current:,} 修为"
                if status.get("is_capped"):
                    text += "\n修炼经验已满，请及时结算。"
                await _safe_edit(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            logger.error(f"cultivate stat error: {e}")
            await _safe_edit(
                "❌ 修炼状态获取失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🧘 修炼面板", callback_data="cultivate")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    if data == "cultivate_end":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/cultivate/end", json={"user_id": uid}, timeout=15)
                if result.get("success"):
                    gain = result.get("gain", 0)
                    await _safe_edit(
                        f"✅ 修炼结束，获得 {gain:,} 修为",
                        reply_markup=get_main_menu_keyboard()
                    )
                else:
                    keyboard = [[InlineKeyboardButton("🔙 返回修炼面板", callback_data="cultivate")]]
                    await _safe_edit(
                        f"❌ {result.get('message', '结束修炼失败')}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"cultivate end error: {e}")
            await _safe_edit(
                "❌ 结束修炼失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🧘 修炼面板", callback_data="cultivate")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    # 狩猎
    if data == "hunt":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "狩猎"):
                    return
                cooldown = await http_get(f"{SERVER_URL}/api/hunt/status/{uid}", timeout=15)
                if cooldown.get("success") and cooldown.get("cooldown_remaining", 0) > 0:
                    remaining = cooldown.get("cooldown_remaining", 0)
                    await _safe_edit(
                        f"⏳ 狩猎冷却中，请等待 {remaining} 秒",
                        reply_markup=get_main_menu_keyboard()
                    )
                    return
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if stat_r.get("success"):
                    user_rank = stat_r["status"].get("rank", 1)
                    monsters = get_available_monsters(user_rank)

                    text = "👹 *选择要挑战的怪物:*\n\n"
                    keyboard = []
                    for m in monsters[:4]:
                        diff = "简单" if m["min_rank"] <= user_rank - 2 else ("普通" if m["min_rank"] <= user_rank else "困难")
                        text += f"▸ *{m['name']}* [{diff}]"
                        if m.get("element"):
                            text += f"  ({m.get('element')})"
                        text += "\n"
                        text += f"  HP:{m['hp']} ATK:{m['attack']} DEF:{m['defense']}\n"
                        text += "  奖励: 依据境界动态结算（含疲劳衰减）\n\n"
                        keyboard.append([
                            InlineKeyboardButton(
                                f"{m['name']} [{diff}]",
                                callback_data=f"hunt_{m['id']}"
                            )
                        ])
                    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])

                    await _safe_edit(
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
        except Exception as e:
            logger.error(f"hunt callback error: {e}")
            await _safe_edit("❌ 狩猎面板出错，请重试", reply_markup=get_main_menu_keyboard())
        return
    
    if data.startswith("hunt_") and not data.startswith("hunt_mode_"):
        monster_id = data[5:]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "狩猎"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/hunt/turn/start",
                    json={"user_id": uid, "monster_id": monster_id},
                    timeout=15,
                )
                if result.get("success"):
                    text = _battle_state_text("⚔️ *狩猎战斗开始*", result, subtitle=f"遭遇: {result.get('monster', {}).get('name', '怪物')}")
                    await _safe_edit(
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=_battle_action_keyboard("hbt", result.get("session_id"), result.get("active_skills", []), back_callback="hunt"),
                    )
                else:
                    await _safe_edit(f"❌ {result.get('message', '无法开始战斗')}", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"hunt mode select error: {e}")
            await _safe_edit("❌ 开始战斗失败，请重新选择怪物", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ 返回狩猎", callback_data="hunt")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("hbt_"):
        parts = data.split("_")
        if len(parts) < 3:
            return
        session_id = parts[1]
        action = "normal"
        selected_skill = None
        if len(parts) >= 4 and parts[2] == "s":
            action = "skill"
            selected_skill = "_".join(parts[3:])
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "狩猎"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/hunt/turn/action",
                    json={
                        "user_id": uid,
                        "session_id": session_id,
                        "action": action,
                        "skill_id": selected_skill,
                        "request_id": callback_request_id,
                    },
                    timeout=15,
                )

                if result.get("success"):
                    if result.get("finished") is False:
                        text = _battle_state_text("⚔️ *狩猎战斗中*", result, subtitle=f"遭遇: {result.get('enemy', {}).get('name', '怪物')}")
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_battle_action_keyboard("hbt", session_id, result.get("active_skills", []), back_callback="hunt"),
                        )
                        return
                    if result.get("victory"):
                        rewards = result.get("rewards", {})
                        drops = result.get("drops", [])
                        log_entries = result.get("battle_log", [])
                        monster = result.get("monster") or {}

                        text = f"⚔️ *战斗胜利！*\n\n{result.get('message', '')}\n"
                        if monster.get("element"):
                            text += f"怪物五行: {monster.get('element')}\n"
                        if result.get("element_relation"):
                            text += f"五行关系: {result.get('element_relation')}\n"
                        text += f"回合数: {result.get('rounds', '?')}\n"
                        if log_entries:
                            for entry in log_entries[-6:]:
                                text += f"  {entry}\n"
                        text += "\n🎁 *奖励:*\n"
                        text += f"• 修为: +{rewards.get('exp', 0):,}\n"
                        text += f"• 下品灵石: +{rewards.get('copper', 0):,}\n"
                        if rewards.get("gold"):
                            text += f"• 中品灵石: +{rewards['gold']}\n"
                        if drops:
                            text += "\n📦 *掉落:*\n"
                            for d in drops[:4]:
                                text += f"• {d.get('item_name', '?')} x{d.get('quantity', 1)}\n"
                        text += _format_post_battle_status(result.get("post_status"))
                    else:
                        text = f"💀 *战斗失败*\n\n{result.get('message', '')}\n回去修炼再来挑战吧！\n"
                        text += _format_failure_reasons(result.get("failure_reasons"))
                        for entry in (result.get("battle_log") or [])[-6:]:
                            text += f"• {entry}\n"
                        text += _format_post_battle_status(result.get("post_status"))
                else:
                    text = f"❌ {result.get('message', '战斗出错')}"

                if result.get("success") and not result.get("victory", True):
                    keyboard = [
                        [InlineKeyboardButton("🛌 自动恢复", callback_data="recover_auto_hunt")],
                        [InlineKeyboardButton("💊 丹药恢复", callback_data="recovery_menu_hunt")],
                        [InlineKeyboardButton("⚔️ 继续狩猎", callback_data="hunt")],
                        [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                    ]
                else:
                    keyboard = [
                        [InlineKeyboardButton("⚔️ 继续狩猎", callback_data="hunt")],
                        [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                    ]
                await _safe_edit(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception as e:
            logger.error(f"hunt monster error: {e}")
            await _safe_edit("❌ 战斗回合处理失败，请重新开始狩猎", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ 返回狩猎", callback_data="hunt")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    # 锻造 / 祭炼（长期 sink）
    if data == "forge":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "锻造"):
                    return
                st = await http_get(f"{SERVER_URL}/api/forge/{uid}", timeout=15)
                if not st.get("success"):
                    return await _safe_edit(st.get("message", "锻造信息获取失败"), reply_markup=get_main_menu_keyboard())
                text, keyboard = await _build_forge_menu(uid)
                return await _safe_edit(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception as e:
            logger.error(f"forge menu error: {e}")
            await _safe_edit(
                "❌ 锻造面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔨 锻造", callback_data="forge")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data == "forge_do":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "锻造"):
                    return
                request_id = _new_request_id(context)
                res = await http_post(
                    f"{SERVER_URL}/api/forge",
                    json={"user_id": uid, "request_id": request_id},
                    timeout=15,
                )
                if res.get("success"):
                    rw = res.get("reward") or {}
                    name = rw.get("item_name") or rw.get("name") or _item_display_name(str(rw.get("item_id") or "")) or "未知奖励"
                    qty = rw.get("quantity", 1)
                    text = f"✅ 锻造成功！\n\n获得: {name} x{qty}"
                else:
                    text = f"❌ {res.get('message','锻造失败')}"
                keyboard = [
                    [InlineKeyboardButton("🔨 再来一次", callback_data="forge_do")],
                    [InlineKeyboardButton("🎯 图鉴锻造", callback_data="forge")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                return await _safe_edit(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"forge do error: {e}")
            await _safe_edit(
                "❌ 锻造失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔨 再来一次", callback_data="forge_do")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("forge_target_"):
        item_id = data[len("forge_target_"):]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "定向锻造"):
                    return
                request_id = _new_request_id(context)
                res = await http_post(
                    f"{SERVER_URL}/api/forge/targeted",
                    json={"user_id": uid, "item_id": item_id, "request_id": request_id},
                    timeout=15,
                )
                prefix = f"🎯 {res.get('message', '定向锻造失败')}"
                text2, keyboard = await _build_forge_menu(uid)
                return await _safe_edit(prefix + "\n\n" + text2, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"forge target error: {e}")
            await _safe_edit(
                "❌ 定向锻造失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔨 返回锻造", callback_data="forge")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

# 突破 - 信息展示
    if data == "breakthrough":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if stat_r.get("success"):
                    user_data = stat_r["status"]
                    can_break = can_breakthrough(user_data.get("exp", 0), user_data.get("rank", 1))
                    progress = format_realm_progress(user_data)
                    if can_break:
                        trial = await _fetch_realm_trial(uid)
                        trial_blocked = bool(trial) and int(trial.get("completed", 0) or 0) != 1
                        if trial_blocked:
                            text = (
                                f"🔥 *突破*\n\n{progress}\n\n"
                                "⚠️ 修为已满足，但境界试炼尚未完成。\n\n"
                                f"{_format_realm_trial_text(trial)}\n\n"
                                "完成试炼后即可突破。"
                            )
                            keyboard = [
                                [
                                    InlineKeyboardButton("⚔️ 去狩猎", callback_data="hunt"),
                                    InlineKeyboardButton("🗺️ 去秘境", callback_data="secret_realms"),
                                ],
                                [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                            ]
                        else:
                            preview_block = await _build_breakthrough_preview_block(uid, user_data, strategy="steady")
                            is_tribulation = _is_consummation_breakthrough_rank(int(user_data.get("rank", 1) or 1))
                            if is_tribulation:
                                text = (
                                    f"🔥 *突破*\n\n{progress}\n\n✨ 可以渡劫！\n"
                                    "当前已至圆满关口，本次突破将直接触发天雷劫。\n\n"
                                    f"请确认准备后执行：\n\n{preview_block}"
                                )
                                keyboard = [
                                    [InlineKeyboardButton("⛈️ 渡劫突破", callback_data="breakthrough_tribulation")],
                                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                                ]
                            else:
                                text = (
                                    f"🔥 *突破*\n\n{progress}\n\n✨ 可以突破！\n"
                                    f"请选择冲关策略：\n\n{preview_block}"
                                )
                                keyboard = [
                                    [
                                        InlineKeyboardButton("🛡️ 稳妥突破", callback_data="breakthrough_steady"),
                                        InlineKeyboardButton("🌿 护脉突破", callback_data="breakthrough_protect"),
                                    ],
                                    [InlineKeyboardButton("⚡ 生死突破", callback_data="breakthrough_desperate")],
                                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                                ]
                    else:
                        text = f"🔥 *突破*\n\n{progress}\n\n修为不足，继续修炼吧！"
                        keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
                    await _safe_edit(
                        text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
        except Exception as e:
            logger.error(f"breakthrough info error: {e}")
            await _safe_edit(
                "❌ 突破面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔥 突破", callback_data="breakthrough")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # 突破 - 执行
    if data in ("breakthrough_start", "breakthrough_pill", "breakthrough_steady", "breakthrough_protect", "breakthrough_desperate", "breakthrough_tribulation"):
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "突破"):
                    return
                strategy = {
                    "breakthrough_start": "normal",
                    "breakthrough_pill": "steady",
                    "breakthrough_steady": "steady",
                    "breakthrough_protect": "protect",
                    "breakthrough_desperate": "desperate",
                    "breakthrough_tribulation": "steady",
                }.get(data, "normal")
                use_pill = strategy == "steady"
                result = await http_post(
                    f"{SERVER_URL}/api/breakthrough",
                    json={
                        "user_id": uid,
                        "use_pill": use_pill,
                        "strategy": strategy,
                        "call_for_help": True,
                    },
                    timeout=15,
                )
                if result.get("success"):
                    realm_name = str(result.get("new_realm", "") or "").strip() or "未知境界"
                    mention = _markdown_safe_tg_mention(getattr(query.from_user, "username", ""))
                    congrats_message = str(result.get("congrats_message", "") or "").strip()
                    if mention:
                        congrats_message = f"灵光一闪！恭喜 {mention} 道友，修为精进，成功突破至【{realm_name}】！"
                    elif not congrats_message:
                        congrats_message = f"灵光一闪！恭喜 道友，修为精进，成功突破至【{realm_name}】！"
                    text = (
                        f"🔥 *突破成功！*\n\n"
                        f"*{congrats_message}*\n\n"
                        f"{result.get('event_title', '你成功破境了')}\n"
                        f"{result.get('event_flavor', '')}\n\n"
                        f"{result.get('message', '')}\n"
                        f"新境界: *{realm_name}*\n"
                        f"{result.get('strategy_cost_text', '')}\n"
                        f"建议: {result.get('next_goal', '继续稳固境界，准备下一阶段修行。')}"
                    )
                else:
                    if result.get("code") == "REALM_TRIAL":
                        text = (
                            "⛔ *暂时无法突破*\n\n"
                            f"{result.get('message', '需完成当前境界试炼后方可突破')}\n\n"
                            f"{_format_realm_trial_text(result.get('trial') or {})}\n\n"
                            "完成试炼后再来冲关。"
                        )
                    else:
                        exp_lost = result.get("exp_lost", 0)
                        weak = result.get("weak_seconds", 0)
                        text = (
                            f"💥 *突破失败*\n\n"
                            f"{result.get('event_title', '这次冲关未能成功')}\n"
                            f"{result.get('event_flavor', '')}\n\n"
                            f"{result.get('message', '')}"
                        )
                        if exp_lost:
                            text += f"\n损失修为: {exp_lost:,}"
                        if weak:
                            text += f"\n虚弱状态: {weak // 60} 分钟"
                        if result.get("strategy_cost_text"):
                            text += f"\n{result.get('strategy_cost_text')}"
                        text += f"\n建议: {result.get('next_goal', '先恢复状态，再准备下一次冲关。')}"
                await _safe_edit(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_main_menu_keyboard()
                )
        except Exception as e:
            logger.error(f"breakthrough callback error: {e}")
            await _safe_edit(
                "❌ 突破结算失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔥 返回突破", callback_data="breakthrough")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    if data == "guide":
        await _safe_edit(
            _build_guide_text(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    # 签到
    if data == "signin":
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(
                    f"{SERVER_URL}/api/signin",
                    json={"user_id": uid},
                    timeout=15,
                )
                if result.get("success"):
                    await _safe_edit(
                        result.get("message", "✅ 签到成功！"),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=get_main_menu_keyboard()
                    )
                else:
                    # 显示签到状态
                    status = await http_get(f"{SERVER_URL}/api/signin/{uid}", timeout=15)
                    if status.get("success"):
                        await _safe_edit(
                            status.get("status_text", "今日已签到"),
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=get_main_menu_keyboard()
                        )
        except Exception as e:
            logger.error(f"signin callback error: {e}")
            await _safe_edit(
                "❌ 签到失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 签到", callback_data="signin")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return
    
    # 商店
    if data.startswith("shop_"):
        shop_parts = data.split("_")
        category = "all"
        if len(shop_parts) >= 2:
            if shop_parts[1] in ("all", "pill", "material", "special"):
                category = shop_parts[1]
            elif len(shop_parts) >= 3 and shop_parts[2] in ("all", "pill", "material", "special"):
                category = shop_parts[2]
        context.user_data["shop_category"] = category
        try:
            stat_r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            rank = 1
            if stat_r.get("success"):
                stat_data = await http_get(f"{SERVER_URL}/api/stat/{stat_r['user_id']}", timeout=15)
                if stat_data.get("success"):
                    rank = int(stat_data.get("status", {}).get("rank", 1) or 1)
            text, keyboard = await _load_shop_view(
                stat_r.get("user_id") if stat_r.get("success") else None,
                rank=rank,
                category=category,
            )
            await _safe_edit(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"shop callback error: {e}")
            await _safe_edit(
                "❌ 商店加载失败，请稍后重试。",
                reply_markup=get_main_menu_keyboard()
            )
        return
    
    # 购买物品
    if data.startswith("buy_"):
        payload = data[4:]
        currency = ""
        item_id = ""
        for cur in ("spirit_high", "copper", "gold"):
            prefix = f"{cur}_"
            if payload.startswith(prefix):
                currency = cur
                item_id = payload[len(prefix):].strip()
                break
        if currency and item_id:
            if currency not in ("copper", "gold", "spirit_high") or not item_id:
                await _safe_edit("❌ 商品参数错误，请重新打开商店", reply_markup=get_main_menu_keyboard())
                return
            context.user_data["pending_shop_buy_currency"] = currency
            context.user_data["pending_shop_buy_item_id"] = item_id
            context.user_data["pending_shop_buy_item_name"] = _item_display_name(item_id)
            category = str(context.user_data.get("shop_category", "all") or "all").strip().lower()
            if category not in ("all", "pill", "material", "special"):
                category = "all"
            context.user_data["pending_shop_buy_category"] = category
            try:
                prompt = await query.message.reply_text(
                    f"🛒 购买 {_item_display_name(item_id)}\n请输入购买数量（1-999），输入“取消”可终止购买。",
                    reply_markup=ForceReply(selective=True),
                )
                _set_pending_action(context, action="shop_buy_qty", prompt_message=prompt, user_id=user_id)
            except Exception as e:
                logger.error(f"buy qty prompt callback error: {e}")
                await _safe_edit("❌ 无法发起购买输入，请稍后重试", reply_markup=get_main_menu_keyboard())
        return
    
    # 储物袋（带分页）
    if data.startswith("bag"):
        page = 0
        if data.startswith("bag_"):
            try:
                page = int(data[4:])
            except ValueError:
                page = 0
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)
                if result.get("success"):
                    items = result.get("items", [])
                    text, keyboard = _build_bag_panel(items, page=page)
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"bag callback error: {e}")
            await _safe_edit("❌ 储物袋面板出错，请重试", reply_markup=get_main_menu_keyboard())
        return

    # 灵装（单独分页）
    if data.startswith("equipbag"):
        page = 0
        if data.startswith("equipbag_"):
            try:
                page = int(data[len("equipbag_"):])
            except ValueError:
                page = 0
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)
                if result.get("success"):
                    items = result.get("items", [])
                    text, keyboard = _build_equipment_bag_panel(items, page=page)
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"equipbag callback error: {e}")
            await _safe_edit("❌ 灵装面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    # 装备物品（从灵装）
    if data.startswith("equip_"):
        item_db_id = data[6:]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/equip", json={"user_id": uid, "item_id": int(item_db_id)}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"equip callback error: {e}")
            await _safe_edit(
                "❌ 装备失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("decompose_"):
        item_db_id = data[10:]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/decompose", json={"user_id": uid, "item_id": int(item_db_id)}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "♻️" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"decompose callback error: {e}")
            await _safe_edit(
                "❌ 分解失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # 使用丹药（从储物袋）
    if data.startswith("use_"):
        item_id = data[4:]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/item/use", json={"user_id": uid, "item_id": item_id}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"use item callback error: {e}")
            await _safe_edit(
                "❌ 使用物品失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # 已装备查看 + 卸下
    if data == "equipped_view":
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                items_r = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)
                all_items = {i["id"]: i for i in items_r.get("items", [])} if items_r.get("success") else {}

                text = "👕 *已佩戴灵装*\n\n"
                keyboard = []
                slot_names = {
                    "equipped_weapon": "⚔️ 武器",
                    "equipped_armor": "🛡️ 护甲",
                    "equipped_accessory1": "💍 饰品1",
                    "equipped_accessory2": "💍 饰品2",
                }
                equipped_data = stat_r.get("status", {}) if stat_r.get("success") else {}

                for slot, label in slot_names.items():
                    db_id = equipped_data.get(slot)
                    try:
                        db_id = int(db_id) if db_id is not None and str(db_id).strip() != "" else None
                    except (TypeError, ValueError):
                        db_id = None
                    if db_id and db_id in all_items:
                        item = all_items[db_id]
                        text += f"{label}: {item['item_name']}"
                        bonuses = []
                        if item.get("attack_bonus"):
                            bonuses.append(f"攻+{item['attack_bonus']}")
                        if item.get("defense_bonus"):
                            bonuses.append(f"防+{item['defense_bonus']}")
                        if item.get("hp_bonus"):
                            bonuses.append(f"HP+{item['hp_bonus']}")
                        if bonuses:
                            text += f" ({', '.join(bonuses)})"
                        text += "\n"
                        affix_text = _equipment_affix_text(item)
                        if affix_text:
                            text += f"  ✨ 词条: {affix_text}\n"
                        keyboard.append([InlineKeyboardButton(f"卸下 {item['item_name']}", callback_data=f"unequip_equipped_{slot}")])
                    else:
                        text += f"{label}: _空_\n"

                keyboard.append([InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")])
                keyboard.append([InlineKeyboardButton("🎒 储物袋", callback_data="bag")])
                keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"equipped view error: {e}")
            await _safe_edit(
                "❌ 已装备面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # 卸下装备
    if data.startswith("unequip_equipped_"):
        slot = data[len("unequip_"):]  # e.g. "equipped_weapon"
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/unequip", json={"user_id": uid, "slot": slot}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("👕 已装备", callback_data="equipped_view")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"unequip callback error: {e}")
            await _safe_edit(
                "❌ 卸下装备失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 已装备", callback_data="equipped_view")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data == "skills":
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_get(f"{SERVER_URL}/api/skills/{uid}", timeout=15)
                if result.get("success"):
                    learned = result.get("learned", [])
                    unlockable = result.get("unlockable", [])
                    learned_ids = {x.get("skill_id") for x in learned}
                    text = "✨ *技能面板*\n\n"
                    keyboard = []
                    if learned:
                        text += "*已学会:*\n"
                        for row in learned[:8]:
                            sk = get_skill(row.get("skill_id"))
                            if not sk:
                                continue
                            text += _skill_line(sk, equipped=bool(row.get("equipped")))
                            if sk.get("type") == "active":
                                if row.get("equipped"):
                                    keyboard.append([InlineKeyboardButton(f"卸下 {sk['name']}", callback_data=f"skill_unequip_{sk['id']}")])
                                else:
                                    keyboard.append([InlineKeyboardButton(f"装备 {sk['name']}", callback_data=f"skill_equip_{sk['id']}")])
                        text += "_被动技能学会后自动生效，无需装备_\n"
                    else:
                        text += "*已学会:*\n  暂无\n"
                    not_learned = [sk for sk in unlockable[:8] if sk['id'] not in learned_ids]
                    if not_learned:
                        text += "\n*可学习:*\n"
                        for sk in not_learned:
                            cost_parts = []
                            if sk['cost_copper']:
                                cost_parts.append(f"{sk['cost_copper']}下灵")
                            if sk['cost_gold']:
                                cost_parts.append(f"{sk['cost_gold']}中灵")
                            text += _skill_line(sk)
                            text = text.rstrip("\n") + f"（{' '.join(cost_parts)}）\n"
                            keyboard.append([InlineKeyboardButton(f"学习 {sk['name']}", callback_data=f"skill_learn_{sk['id']}")])
                    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"skills callback error: {e}")
            await _safe_edit(
                "❌ 技能面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✨ 技能", callback_data="skills")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("skill_learn_"):
        skill_id = data[len("skill_learn_"):]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/skills/learn", json={"user_id": uid, "skill_id": skill_id}, timeout=15)
                await _safe_edit(result.get("message", "已处理"), reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"skill learn callback error: {e}")
            await _safe_edit("❌ 学习技能失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ 技能面板", callback_data="skills")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("skill_equip_"):
        skill_id = data[len("skill_equip_"):]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/skills/equip", json={"user_id": uid, "skill_id": skill_id}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("✨ 技能面板", callback_data="skills")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"skill equip callback error: {e}")
            await _safe_edit("❌ 装备技能失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ 技能面板", callback_data="skills")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("skill_unequip_"):
        skill_id = data[len("skill_unequip_"):]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/skills/unequip", json={"user_id": uid, "skill_id": skill_id}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("✨ 技能面板", callback_data="skills")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"skill unequip callback error: {e}")
            await _safe_edit("❌ 卸下技能失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ 技能面板", callback_data="skills")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data.startswith("leaderboard_"):
        mode = data.split("_", 1)[1]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            uid = r.get("user_id") if r.get("success") else None
            params = {"mode": mode}
            if uid:
                params["user_id"] = uid
            if mode in ("stage", "auto", "recommended"):
                params["stage_only"] = "1"
            result = await http_get(
                f"{SERVER_URL}/api/leaderboard",
                params=params,
                timeout=15,
            )
            if result.get("success"):
                actual_mode = result.get("mode", mode)
                text = _format_leaderboard_text(actual_mode, result.get("entries", []), result.get("stage_goal"))
                keyboard = [
                    [InlineKeyboardButton("本阶段", callback_data="leaderboard_stage"), InlineKeyboardButton("战力", callback_data="leaderboard_power")],
                    [InlineKeyboardButton("修为增长", callback_data="leaderboard_exp_growth"), InlineKeyboardButton("秘境收获", callback_data="leaderboard_realm_loot")],
                    [InlineKeyboardButton("炼丹产出", callback_data="leaderboard_alchemy_output"), InlineKeyboardButton("狩猎", callback_data="leaderboard_hunt")],
                    [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
                ]
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"leaderboard callback error: {e}")
            await _safe_edit("❌ 排行榜加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    # 强化装备
    if data.startswith("enhance_do_"):
        payload = data[len("enhance_do_"):]
        try:
            item_db_id, strategy = payload.rsplit("_", 1)
        except ValueError:
            await _safe_edit(
                "❌ 强化参数错误",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
            return
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "强化装备"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/enhance",
                    json={"user_id": uid, "item_id": int(item_db_id), "strategy": strategy, "request_id": _new_request_id(context)},
                    timeout=15,
                )
                if result.get("success"):
                    bonuses = result.get("bonuses_added", {})
                    bonus_parts = []
                    if bonuses.get("attack"):
                        bonus_parts.append(f"攻+{bonuses['attack']}")
                    if bonuses.get("defense"):
                        bonus_parts.append(f"防+{bonuses['defense']}")
                    if bonuses.get("hp"):
                        bonus_parts.append(f"HP+{bonuses['hp']}")
                    if bonuses.get("mp"):
                        bonus_parts.append(f"MP+{bonuses['mp']}")
                    focus_bonus = result.get("focus_bonus") or {}
                    focus_parts = []
                    if focus_bonus.get("attack"):
                        focus_parts.append(f"攻+{focus_bonus['attack']}")
                    if focus_bonus.get("defense"):
                        focus_parts.append(f"防+{focus_bonus['defense']}")
                    if focus_bonus.get("hp"):
                        focus_parts.append(f"HP+{focus_bonus['hp']}")
                    if focus_bonus.get("mp"):
                        focus_parts.append(f"MP+{focus_bonus['mp']}")
                    icon = "✅" if result.get("enhance_success", True) else "⚠️"
                    text = f"{icon} {result['message']}\n"
                    text += f"消耗: {result.get('cost', 0)} 下品灵石\n"
                    material_item_name = _item_display_name(str((result.get("material", {}) or {}).get("item_id", "") or ""))
                    text += f"材料: {result.get('material', {}).get('used', 0)} x {material_item_name}\n"
                    if bonus_parts:
                        text += f"属性提升: {', '.join(bonus_parts)}"
                    if focus_parts:
                        text += f"\n专精加成: {', '.join(focus_parts)}"
                else:
                    text = f"❌ {result.get('message', '强化失败')}"
                keyboard = [
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"enhance callback error: {e}")
            await _safe_edit(
                "❌ 强化失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👕 灵装", callback_data="equipbag_0")],
                    [InlineKeyboardButton("🎒 储物袋", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("enhance_"):
        item_db_id = data[8:]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "强化装备"):
                    return
        except Exception as exc:
            logger.warning(
                "enhance_precheck_failed telegram_user_id=%s error=%s",
                user_id,
                type(exc).__name__,
            )
            await _safe_edit(
                "⚠️ 服务繁忙，请稍后重试",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]
                ),
            )
            return
        text = (
            "🔧 *选择强化策略*\n\n"
            "保守强化：下品灵石更省，提升较稳，收益较低。\n"
            "冲击强化：提升最高，但有失败风险。\n"
            "材料专精强化：更吃材料，下品灵石更省，适合按路线培养。\n"
        )
        keyboard = [
            [InlineKeyboardButton("🛡️ 保守强化", callback_data=f"enhance_do_{item_db_id}_steady")],
            [InlineKeyboardButton("⚡ 冲击强化", callback_data=f"enhance_do_{item_db_id}_risky")],
            [InlineKeyboardButton("🪨 材料专精强化", callback_data=f"enhance_do_{item_db_id}_focused")],
            [InlineKeyboardButton("👕 返回灵装", callback_data="equipbag_0")],
        ]
        await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 每日任务
    if data == "quests":
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_get(f"{SERVER_URL}/api/quests/{uid}", timeout=15)
                if result.get("success"):
                    quests = result.get("quests", [])
                    defs = result.get("quest_defs", [])
                    def_map = {q["id"]: q for q in defs}

                    text = "📜 *每日任务*\n\n"
                    keyboard = []
                    for row in quests:
                        qdef = def_map.get(row["quest_id"])
                        if not qdef:
                            continue
                        progress = row.get("progress", 0)
                        goal = row.get("goal", 1)
                        claimed = row.get("claimed", 0)
                        reward_parts = []
                        for k, v in qdef["rewards"].items():
                            if k == "copper":
                                reward_parts.append(f"{v}下品灵石")
                            elif k == "exp":
                                reward_parts.append(f"{v}修为")
                            elif k == "gold":
                                reward_parts.append(f"{v}中品灵石")
                        reward_str = " ".join(reward_parts)

                        if claimed:
                            text += f"✅ {qdef['name']} - {qdef['desc']} [{reward_str}]\n"
                        elif progress >= goal:
                            text += f"🎁 {qdef['name']} - {qdef['desc']} [{reward_str}]\n"
                            keyboard.append([InlineKeyboardButton(
                                f"领取 {qdef['name']}", callback_data=f"quest_claim_{row['quest_id']}"
                            )])
                        else:
                            text += f"⬜ {qdef['name']} ({progress}/{goal}) - {qdef['desc']} [{reward_str}]\n"

                    all_claimed = all(r.get("claimed") for r in quests) if quests else False
                    if all_claimed:
                        text += "\n🎉 今日任务全部完成！"

                    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await _safe_edit("❌ 任务面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"quests callback error: {e}")
            await _safe_edit("❌ 任务面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    # 领取任务奖励
    if data.startswith("quest_claim_"):
        quest_id = data[len("quest_claim_"):]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(f"{SERVER_URL}/api/quests/claim", json={"user_id": uid, "quest_id": quest_id, "request_id": _new_request_id(context)}, timeout=15)
                msg = result.get("message", "已处理")
                icon = "✅" if result.get("success") else "❌"
                keyboard = [
                    [InlineKeyboardButton("📜 任务列表", callback_data="quests")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"quest claim callback error: {e}")
            await _safe_edit("❌ 任务奖励领取失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📜 任务列表", callback_data="quests")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    if data == "secret_realms":
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "探索秘境"):
                    return
                result = await http_get(f"{SERVER_URL}/api/secret-realms/{uid}", timeout=15)
                if result.get("success"):
                    attempts = result.get("attempts_left", 0)
                    realms = result.get("realms", [])
                    text = f"🗺️ *秘境探索*\n\n"
                    text += f"📊 今日剩余次数: *{attempts}* / 3\n"
                    if attempts == 0:
                        text += "⏳ 今日次数已用尽，明日再来！\n"
                    text += "\n"
                    keyboard = []
                    if attempts > 0:
                        for realm in realms[:8]:
                            keyboard.append([InlineKeyboardButton(f"🗺️ {realm['name']}", callback_data=f"secret_realm_info_{realm['id']}")])
                    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"secret realms callback error: {e}")
            await _safe_edit("❌ 秘境面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    # 秘境 - 确认进入（显示详情）
    if data.startswith("secret_realm_info_"):
        realm_id = data[len("secret_realm_info_"):]
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "探索秘境"):
                    return
                sr_data = await http_get(f"{SERVER_URL}/api/secret-realms/{uid}", timeout=15)
                if sr_data.get("success"):
                    attempts = sr_data.get("attempts_left", 0)
                    realm = None
                    for rl in sr_data.get("realms", []):
                        if rl["id"] == realm_id:
                            realm = rl
                            break
                    if not realm:
                        realm = get_available_secret_realms(1)  # fallback
                        await _safe_edit("❌ 秘境不存在", reply_markup=get_main_menu_keyboard())
                        return

                    exp_range = realm.get("rewards", {}).get("exp", (0, 0))
                    copper_range = realm.get("rewards", {}).get("copper", (0, 0))
                    drops = realm.get("rewards", {}).get("drops", [])

                    text = f"🗺️ *{realm['name']}*\n\n"
                    text += f"📜 {realm.get('flavor', '')}\n\n"
                    text += f"⚔️ 可能遭遇的敌人:\n"
                    monster_pool = realm.get("monster_pool", [])
                    for mid in monster_pool[:4]:
                        m = get_monster_by_id(mid)
                        if m:
                            text += f"  • {m['name']} (HP:{m['hp']} ATK:{m['attack']})\n"
                    text += f"\n🎁 奖励范围:\n"
                    text += f"  • 修为: {exp_range[0]} - {exp_range[1]}\n"
                    text += f"  • 下品灵石: {copper_range[0]} - {copper_range[1]}\n"
                    if drops:
                        drop_names = []
                        for did in drops:
                            ditem = get_item_def(did)
                            drop_names.append(ditem["name"] if ditem else did)
                        text += f"  • 可能掉落: {', '.join(drop_names)}\n"
                    target_equipment = TARGETED_REALM_DROPS.get(realm_id, [])
                    if target_equipment:
                        target_names = [(_item_display_name(item_id)) for item_id in target_equipment[:3]]
                        text += f"  • 偏向装备: {', '.join(target_names)}\n"
                    text += "\n路线说明:\n"
                    text += "  • 稳妥探索: 低强度怪更多，安全事件更多\n"
                    text += "  • 冒险探索: 精英怪更多，稀有掉落更高\n"
                    text += "  • 寻宝路线: 战斗更少，但会遇到陷阱或守宝怪\n"
                    text += f"\n📊 剩余次数: *{attempts}* / 3"

                    keyboard = []
                    if attempts > 0:
                        keyboard.append([
                            InlineKeyboardButton("🛡️ 稳妥探索", callback_data=f"secret_realm_explore_{realm_id}_safe"),
                            InlineKeyboardButton("⚔️ 冒险探索", callback_data=f"secret_realm_explore_{realm_id}_risky"),
                        ])
                        keyboard.append([InlineKeyboardButton("💰 寻宝路线", callback_data=f"secret_realm_explore_{realm_id}_loot")])
                    keyboard.append([InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")])
                    keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"secret realm info error: {e}")
            await _safe_edit("❌ 秘境详情加载失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
        return

    # 秘境 - 执行探索
    if data.startswith("secret_realm_explore_"):
        rest = data[len("secret_realm_explore_"):]
        if "_" in rest:
            realm_id, path = rest.rsplit("_", 1)
        else:
            realm_id, path = rest, "normal"
        if path not in ("normal", "safe", "risky", "loot"):
            path = "normal"
        try:
            r = await http_get(f"{SERVER_URL}/api/user/lookup", params={"platform": "telegram", "platform_id": user_id}, timeout=15)
            if r.get("success"):
                uid = r["user_id"]
                if await _stop_if_cultivating(uid, "探索秘境"):
                    return
                result = await http_post(
                    f"{SERVER_URL}/api/secret-realms/turn/start",
                    json={"user_id": uid, "realm_id": realm_id, "path": path, "interactive": True},
                    timeout=15,
                )
                if result.get("success"):
                    if result.get("needs_choice"):
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", path), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else str(encounter or "事件")
                        text = f"🗺️ *{realm_name} · 事件抉择*\n路线: {path_name}\n遭遇: {encounter_name}\n\n请选择处理方式：\n"
                        for choice in (result.get("choices") or [])[:3]:
                            text += f"• {choice.get('label', '选项')}"
                            if choice.get("note"):
                                text += f"：{choice.get('note')}"
                            text += "\n"
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_secret_choice_keyboard(
                                result.get("session_id"),
                                result.get("choices", []),
                                back_callback="secret_realms",
                            ),
                        )
                        return
                    if result.get("needs_battle"):
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", path), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else encounter
                        subtitle = f"路线: {path_name}"
                        if encounter_name:
                            subtitle += f" ｜ 遭遇: {encounter_name}"
                        text = _battle_state_text(
                            f"🗺️ *{realm_name} · 遭遇战*",
                            result,
                            subtitle=subtitle,
                        )
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_battle_action_keyboard("sbt", result.get("session_id"), result.get("active_skills", []), back_callback="secret_realms"),
                        )
                        return
                    realm_name = result.get("realm", {}).get("name", "秘境")
                    encounter = result.get("encounter")
                    victory = result.get("victory", True)
                    battle_log = result.get("battle_log", [])
                    event = result.get("event", "")
                    rewards = result.get("rewards", {})
                    drops = rewards.get("drops") or []
                    attempts_left = result.get("attempts_left", 0)
                    used_path = result.get("path", path)

                    path_name = {
                        "safe": "稳妥探索",
                        "risky": "冒险探索",
                        "loot": "寻宝路线",
                        "normal": "常规探索",
                    }.get(used_path, "常规探索")

                    text = f"🗺️ *{realm_name} · 探索报告*\n"
                    text += f"路线: *{path_name}*\n"
                    text += "━━━━━━━━━━━━━━━━━━\n\n"

                    # Encounter section
                    if encounter:
                        icon = "⚔️" if victory else "💀"
                        text += f"{icon} 遭遇: *{encounter}*\n"
                        text += f"结果: {'✅ 击败！' if victory else '❌ 被击败...'}\n"
                        if not victory:
                            text += _format_failure_reasons(result.get("failure_reasons"))
                        if battle_log:
                            for entry in battle_log[:4]:
                                text += f"  ▸ {entry}\n"
                        text += "\n"
                    else:
                        text += "🌫️ 一路平安，未遇强敌。\n\n"
                    if result.get("trap_damage"):
                        text += f"⚠️ 机关余波: -{result.get('trap_damage')} HP\n\n"

                    # Event
                    text += f"📜 {event}\n\n"

                    # Rewards
                    text += "🎁 *探索收获:*\n"
                    text += f"  • 修为: +{rewards.get('exp', 0):,}\n"
                    text += f"  • 下品灵石: +{rewards.get('copper', 0):,}\n"
                    if rewards.get("gold"):
                        text += f"  • 中品灵石: +{rewards['gold']}\n"
                    for drop in drops[:4]:
                        text += f"  • 掉落: *{drop.get('item_name', '未知物品')}*"
                        if drop.get("quantity", 1) > 1:
                            text += f" x{drop['quantity']}"
                        text += "\n"

                    text += f"\n━━━━━━━━━━━━━━━━━━"
                    text += f"\n📊 今日剩余次数: *{attempts_left}* / 3"
                    text += _format_post_battle_status(result.get("post_status"))

                    keyboard = []
                    if not victory and encounter:
                        keyboard.append([InlineKeyboardButton("🛌 自动恢复", callback_data="recover_auto_secret_realms")])
                        keyboard.append([InlineKeyboardButton("💊 丹药恢复", callback_data="recovery_menu_secret_realms")])
                    if attempts_left > 0:
                        keyboard.append([
                            InlineKeyboardButton("🛡️ 稳妥", callback_data=f"secret_realm_explore_{realm_id}_safe"),
                            InlineKeyboardButton("⚔️ 冒险", callback_data=f"secret_realm_explore_{realm_id}_risky"),
                            InlineKeyboardButton("💰 寻宝", callback_data=f"secret_realm_explore_{realm_id}_loot"),
                        ])
                        keyboard.append([InlineKeyboardButton("🗺️ 选择其他秘境", callback_data="secret_realms")])
                    else:
                        keyboard.append([InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")])
                    keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    text = f"❌ {result.get('message', '探索失败')}"
                    keyboard = [
                        [InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")],
                        [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                    ]
                    await _safe_edit(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"secret realm enter error: {e}")
            await _safe_edit(
                "❌ 进入秘境失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("sbc_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        session_id = parts[1]
        choice_id = parts[2]
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(
                    f"{SERVER_URL}/api/secret-realms/turn/action",
                    json={
                        "user_id": uid,
                        "session_id": session_id,
                        "action": "choice",
                        "choice": choice_id,
                        "request_id": callback_request_id,
                    },
                    timeout=15,
                )
                if result.get("success"):
                    if result.get("needs_choice"):
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", "normal"), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else str(encounter or "事件")
                        text = f"🗺️ *{realm_name} · 事件抉择*\n路线: {path_name}\n遭遇: {encounter_name}\n\n请选择处理方式：\n"
                        for choice in (result.get("choices") or [])[:3]:
                            text += f"• {choice.get('label', '选项')}"
                            if choice.get("note"):
                                text += f"：{choice.get('note')}"
                            text += "\n"
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_secret_choice_keyboard(
                                result.get("session_id"),
                                result.get("choices", []),
                                back_callback="secret_realms",
                            ),
                        )
                        return
                    if result.get("needs_battle"):
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", "normal"), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else encounter
                        subtitle = f"路线: {path_name}"
                        if encounter_name:
                            subtitle += f" ｜ 遭遇: {encounter_name}"
                        text = _battle_state_text(
                            f"🗺️ *{realm_name} · 遭遇战*",
                            result,
                            subtitle=subtitle,
                        )
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_battle_action_keyboard("sbt", result.get("session_id"), result.get("active_skills", []), back_callback="secret_realms"),
                        )
                        return
                    rewards = result.get("rewards", {})
                    drops = rewards.get("drops") or []
                    text = f"🗺️ *事件处理完成*\n\n📜 {result.get('event', '本次事件已结算')}\n\n🎁 *探索收获:*\n"
                    text += f"• 修为: +{rewards.get('exp', 0):,}\n"
                    text += f"• 下品灵石: +{rewards.get('copper', 0):,}\n"
                    if rewards.get("gold"):
                        text += f"• 中品灵石: +{rewards['gold']}\n"
                    for drop in drops[:4]:
                        text += f"• 掉落: {drop.get('item_name', '未知物品')}"
                        if drop.get("quantity", 1) > 1:
                            text += f" x{drop['quantity']}"
                        text += "\n"
                    text += _format_post_battle_status(result.get("post_status"))
                    attempts_left = int(result.get("attempts_left", 0) or 0)
                    realm_id = result.get("realm", {}).get("id")
                    keyboard = []
                    if attempts_left > 0 and realm_id:
                        keyboard.append([
                            InlineKeyboardButton("🛡️ 稳妥", callback_data=f"secret_realm_explore_{realm_id}_safe"),
                            InlineKeyboardButton("⚔️ 冒险", callback_data=f"secret_realm_explore_{realm_id}_risky"),
                            InlineKeyboardButton("💰 寻宝", callback_data=f"secret_realm_explore_{realm_id}_loot"),
                        ])
                    keyboard.append([InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")])
                    keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await _safe_edit(
                        f"❌ {result.get('message', '事件处理失败')}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]),
                    )
        except Exception as e:
            logger.error(f"secret realm choice error: {e}")
            await _safe_edit(
                "❌ 事件处理失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    if data.startswith("sbt_"):
        parts = data.split("_")
        if len(parts) < 3:
            return
        session_id = parts[1]
        action = "normal"
        selected_skill = None
        if len(parts) >= 4 and parts[2] == "s":
            action = "skill"
            selected_skill = "_".join(parts[3:])
        try:
            r = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": user_id},
                timeout=15,
            )
            if r.get("success"):
                uid = r["user_id"]
                result = await http_post(
                    f"{SERVER_URL}/api/secret-realms/turn/action",
                    json={
                        "user_id": uid,
                        "session_id": session_id,
                        "action": action,
                        "skill_id": selected_skill,
                        "request_id": callback_request_id,
                    },
                    timeout=15,
                )
                if result.get("success"):
                    if result.get("needs_choice"):
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", "normal"), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else str(encounter or "事件")
                        text = f"🗺️ *{realm_name} · 事件抉择*\n路线: {path_name}\n遭遇: {encounter_name}\n\n请选择处理方式：\n"
                        for choice in (result.get("choices") or [])[:3]:
                            text += f"• {choice.get('label', '选项')}"
                            if choice.get("note"):
                                text += f"：{choice.get('note')}"
                            text += "\n"
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_secret_choice_keyboard(
                                result.get("session_id"),
                                result.get("choices", []),
                                back_callback="secret_realms",
                            ),
                        )
                        return
                    if result.get("finished") is False:
                        realm_name = result.get("realm", {}).get("name", "秘境")
                        path_name = {
                            "safe": "稳妥探索",
                            "risky": "冒险探索",
                            "loot": "寻宝路线",
                            "normal": "常规探索",
                        }.get(result.get("path", "normal"), "常规探索")
                        encounter = result.get("encounter") or {}
                        encounter_name = encounter.get("label") if isinstance(encounter, dict) else encounter
                        subtitle = f"路线: {path_name}"
                        if encounter_name:
                            subtitle += f" ｜ 遭遇: {encounter_name}"
                        text = _battle_state_text(
                            f"🗺️ *{realm_name} · 遭遇战*",
                            result,
                            subtitle=subtitle,
                        )
                        await _safe_edit(
                            text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=_battle_action_keyboard("sbt", session_id, result.get("active_skills", []), back_callback="secret_realms"),
                        )
                        return
                    realm_name = result.get("realm", {}).get("name", "秘境")
                    encounter = result.get("encounter")
                    victory = result.get("victory", True)
                    battle_log = result.get("battle_log", [])
                    event = result.get("event", "")
                    rewards = result.get("rewards", {})
                    drops = rewards.get("drops") or []
                    attempts_left = result.get("attempts_left", 0)
                    used_path = result.get("path", "normal")
                    path_name = {
                        "safe": "稳妥探索",
                        "risky": "冒险探索",
                        "loot": "寻宝路线",
                        "normal": "常规探索",
                    }.get(used_path, "常规探索")
                    text = f"🗺️ *{realm_name} · 探索报告*\n路线: *{path_name}*\n━━━━━━━━━━━━━━━━━━\n\n"
                    if encounter:
                        icon = "⚔️" if victory else "💀"
                        text += f"{icon} 遭遇: *{encounter}*\n结果: {'✅ 击败！' if victory else '❌ 被击败...'}\n"
                        if not victory:
                            text += _format_failure_reasons(result.get("failure_reasons"))
                        for entry in battle_log[-6:]:
                            text += f"  ▸ {entry}\n"
                        text += "\n"
                    else:
                        text += "🌫️ 一路平安，未遇强敌。\n\n"
                    if result.get("trap_damage"):
                        text += f"⚠️ 机关余波: -{result.get('trap_damage')} HP\n\n"
                    text += f"📜 {event}\n\n🎁 *探索收获:*\n"
                    text += f"  • 修为: +{rewards.get('exp', 0):,}\n"
                    text += f"  • 下品灵石: +{rewards.get('copper', 0):,}\n"
                    if rewards.get("gold"):
                        text += f"  • 中品灵石: +{rewards['gold']}\n"
                    for drop in drops[:4]:
                        text += f"  • 掉落: *{drop.get('item_name', '未知物品')}*"
                        if drop.get("quantity", 1) > 1:
                            text += f" x{drop['quantity']}"
                        text += "\n"
                    text += f"\n━━━━━━━━━━━━━━━━━━\n📊 今日剩余次数: *{attempts_left}* / 3"
                    text += _format_post_battle_status(result.get("post_status"))
                    keyboard = []
                    realm_id = result.get("realm", {}).get("id")
                    if not victory and encounter:
                        keyboard.append([InlineKeyboardButton("🛌 自动恢复", callback_data="recover_auto_secret_realms")])
                        keyboard.append([InlineKeyboardButton("💊 丹药恢复", callback_data="recovery_menu_secret_realms")])
                    if attempts_left > 0 and realm_id:
                        keyboard.append([
                            InlineKeyboardButton("🛡️ 稳妥", callback_data=f"secret_realm_explore_{realm_id}_safe"),
                            InlineKeyboardButton("⚔️ 冒险", callback_data=f"secret_realm_explore_{realm_id}_risky"),
                            InlineKeyboardButton("💰 寻宝", callback_data=f"secret_realm_explore_{realm_id}_loot"),
                        ])
                        keyboard.append([InlineKeyboardButton("🗺️ 选择其他秘境", callback_data="secret_realms")])
                    else:
                        keyboard.append([InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")])
                    keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await _safe_edit(
                        f"❌ {result.get('message', '战斗出错')}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]),
                    )
        except Exception as e:
            logger.error(f"secret realm turn error: {e}")
            await _safe_edit(
                "❌ 秘境战斗处理失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗺️ 秘境列表", callback_data="secret_realms")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    await _safe_edit(
        "❌ 这个操作已失效，请重新打开面板",
        reply_markup=get_main_menu_keyboard(),
    )
    return


async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🧘 修仙Bot v{TELEGRAM_VERSION}\nCore: {CORE_VERSION}"
    )


@require_account
async def signin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sign 签到命令"""
    uid = context.user_data["uid"]
    
    try:
        # 执行签到
        data = await http_post(
            f"{SERVER_URL}/api/signin",
            json={"user_id": uid},
            timeout=15,
        )
        
        if data.get("success"):
            await _reply_with_owned_panel(
                update,
                context,
                data.get("message", "✅ 签到成功！"),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
        else:
            # 获取签到状态
            status = await http_get(f"{SERVER_URL}/api/signin/{uid}", timeout=15)
            if status.get("success"):
                await _reply_with_owned_panel(
                    update,
                    context,
                    status.get("status_text", "今日已签到"),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text(f"❌ {data.get('message', '签到失败')}")
                
    except Exception as exc:
        logger.error(f"signin error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/shop 商店命令"""
    category = "all"
    if context.args:
        first = (context.args[0] or "").strip().lower()
        if first in ("all", "pill", "material", "special"):
            category = first
    context.user_data["shop_category"] = category
    
    try:
        status_r = await http_get(f"{SERVER_URL}/api/stat/{context.user_data['uid']}", timeout=15)
        rank = 1
        if status_r.get("success"):
            rank = int(status_r.get("status", {}).get("rank", 1) or 1)
        text, keyboard = await _load_shop_view(
            context.user_data["uid"],
            rank=rank,
            category=category,
        )
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as exc:
        logger.error(f"shop error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def bag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bag 储物袋命令"""
    uid = context.user_data["uid"]

    try:
        data = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)

        if data.get("success"):
            items = data.get("items", [])
            text, keyboard = _build_bag_panel(items, page=0)

            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ 获取储物袋失败")

    except Exception as exc:
        logger.error(f"bag error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def quest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/quest 每日任务命令"""
    uid = context.user_data["uid"]
    try:
        result = await http_get(f"{SERVER_URL}/api/quests/{uid}", timeout=15)
        if result.get("success"):
            quests = result.get("quests", [])
            defs = result.get("quest_defs", [])
            def_map = {q["id"]: q for q in defs}

            text = "📜 *每日任务*\n\n"
            keyboard = []
            for row in quests:
                qdef = def_map.get(row["quest_id"])
                if not qdef:
                    continue
                progress = row.get("progress", 0)
                goal = row.get("goal", 1)
                claimed = row.get("claimed", 0)
                reward_parts = []
                for k, v in qdef["rewards"].items():
                    if k == "copper":
                        reward_parts.append(f"{v}下品灵石")
                    elif k == "exp":
                        reward_parts.append(f"{v}修为")
                    elif k == "gold":
                        reward_parts.append(f"{v}中品灵石")
                reward_str = " ".join(reward_parts)

                if claimed:
                    text += f"✅ {qdef['name']} - {qdef['desc']} [{reward_str}]\n"
                elif progress >= goal:
                    text += f"🎁 {qdef['name']} - {qdef['desc']} [{reward_str}]\n"
                    keyboard.append([InlineKeyboardButton(
                        f"领取 {qdef['name']}", callback_data=f"quest_claim_{row['quest_id']}"
                    )])
                else:
                    text += f"⬜ {qdef['name']} ({progress}/{goal}) - {qdef['desc']} [{reward_str}]\n"

            keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ 获取任务失败")
    except Exception as exc:
        logger.error(f"quest error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def skills_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    try:
        data = await http_get(f"{SERVER_URL}/api/skills/{uid}", timeout=15)
        if not data.get("success"):
            await update.message.reply_text("❌ 获取技能失败")
            return
        learned = data.get("learned", [])
        unlockable = data.get("unlockable", [])
        learned_ids = {x.get("skill_id") for x in learned}
        text = "✨ *技能面板*\n\n"
        keyboard = []
        if learned:
            text += "*已学会:*\n"
            for row in learned[:8]:
                sk = get_skill(row.get("skill_id"))
                if not sk:
                    continue
                text += _skill_line(sk, equipped=bool(row.get("equipped")))
                if sk.get("type") == "active":
                    if row.get("equipped"):
                        keyboard.append([InlineKeyboardButton(f"卸下 {sk['name']}", callback_data=f"skill_unequip_{sk['id']}")])
                    else:
                        keyboard.append([InlineKeyboardButton(f"装备 {sk['name']}", callback_data=f"skill_equip_{sk['id']}")])
            text += "_被动技能学会后自动生效，无需装备_\n"
        else:
            text += "*已学会:*\n  暂无\n"
        not_learned = [sk for sk in unlockable[:8] if sk["id"] not in learned_ids]
        if not_learned:
            text += "\n*可学习:*\n"
            for sk in not_learned:
                cost_parts = []
                if sk['cost_copper']:
                    cost_parts.append(f"{sk['cost_copper']}下灵")
                if sk['cost_gold']:
                    cost_parts.append(f"{sk['cost_gold']}中灵")
                text += _skill_line(sk)
                text = text.rstrip("\n") + f"（{' '.join(cost_parts)}）\n"
                keyboard.append([InlineKeyboardButton(f"学习 {sk['name']}", callback_data=f"skill_learn_{sk['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"skills error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def secret_realms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    try:
        data = await http_get(f"{SERVER_URL}/api/secret-realms/{uid}", timeout=15)
        if not data.get("success"):
            await update.message.reply_text(f"❌ {data.get('message', '获取秘境失败')}")
            return
        realms = data.get("realms", [])
        attempts_left = data.get("attempts_left", 0)
        text = f"🗺️ *秘境探索*\n\n"
        text += f"📊 今日剩余次数: *{attempts_left}* / 3\n"
        if attempts_left == 0:
            text += "⏳ 今日次数已用尽，明日再来！\n"
        text += "\n"
        keyboard = []
        if realms:
            for realm in realms[:8]:
                exp_range = realm.get("rewards", {}).get("exp", (0, 0))
                copper_range = realm.get("rewards", {}).get("copper", (0, 0))
                from core.game.realms import format_realm_display
                text += f"▸ *{realm['name']}* ｜ 需求 {format_realm_display(realm['min_rank'])}\n"
                text += f"  {realm['flavor']}\n"
                text += f"  奖励: {exp_range[0]}-{exp_range[1]}修为 {copper_range[0]}-{copper_range[1]}下品灵石\n\n"
                if attempts_left > 0:
                    keyboard.append([InlineKeyboardButton(f"🗺️ {realm['name']}", callback_data=f"secret_realm_info_{realm['id']}")])
        else:
            text += "你当前境界尚未解锁任何秘境。\n继续提升境界解锁更多秘境！"
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"secret realms error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.args[0] if context.args else "stage"
    uid = context.user_data["uid"]
    try:
        params = {"mode": mode, "user_id": uid}
        if mode in ("stage", "auto", "recommended"):
            params["stage_only"] = "1"
        data = await http_get(f"{SERVER_URL}/api/leaderboard", params=params, timeout=15)
        if not data.get("success"):
            await update.message.reply_text("❌ 获取排行榜失败")
            return
        actual_mode = data.get("mode", mode)
        text = _format_leaderboard_text(actual_mode, data.get("entries", []), data.get("stage_goal"))
        keyboard = [[
            InlineKeyboardButton("本阶段", callback_data="leaderboard_stage"),
            InlineKeyboardButton("战力", callback_data="leaderboard_power"),
        ], [
            InlineKeyboardButton("修为增长", callback_data="leaderboard_exp_growth"),
            InlineKeyboardButton("秘境收获", callback_data="leaderboard_realm_loot"),
        ], [
            InlineKeyboardButton("炼丹产出", callback_data="leaderboard_alchemy_output"),
            InlineKeyboardButton("狩猎", callback_data="leaderboard_hunt"),
        ], [
            InlineKeyboardButton("🔙 返回", callback_data="main_menu"),
        ]]
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"leaderboard error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def pvp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    try:
        text, keyboard = await _build_pvp_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"pvp error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    if not context.args:
        await update.message.reply_text("用法：/chat 玩家名")
        return
    target_name = (context.args[0] or "").strip()
    await _send_chat_request(context, update.message, uid=uid, target_name=target_name)


@require_account
async def sect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    sect_cfg = _sect_text_cfg()

    async def _reply_help(in_sect: bool):
        if in_sect:
            text = (
                "🏛️ *宗门指令*\n\n"
                "• `/sect info` 查看宗门信息\n"
                "• `/sect list [关键字]` 查看宗门列表\n"
                f"• 创建消耗：{int(sect_cfg['create_copper'])} 下品灵石 + {int(sect_cfg['create_gold'])} 中品灵石\n"
                f"• 宗门人数上限：{int(sect_cfg['base_max_members'])} 人\n"
                f"• 宗门Buff：修炼+{int(float(sect_cfg['default_cultivation_buff_pct']))}% ｜ 属性+{int(float(sect_cfg['default_stat_buff_pct']))}% ｜ 战斗收益+{int(float(sect_cfg['default_battle_reward_buff_pct']))}%\n"
                f"• 别院申请：{int(sect_cfg['branch_create_copper'])} 下品灵石 + {int(sect_cfg['branch_create_gold'])} 中品灵石，需宗主同意\n"
                f"• 每个别院最多 {int(sect_cfg['branch_max_members'])} 人\n"
                f"• 别院成员享受宗门 Buff 的 {int(round(float(sect_cfg['branch_buff_rate']) * 100))}%\n"
                "• `/sect donate <下品灵石> [中品灵石]` 捐献\n"
                "• `/sect branch_apply <名称> [描述]`\n"
                "• `/sect branch_join <别院ID>` 加入别院\n"
                "• `/sect branch_approve <申请ID>` 批准别院\n"
                "• `/sect branch_reject <申请ID>` 拒绝别院\n"
                "• `/sect quests` 查看宗门任务\n"
                "• `/sect claim <任务ID>` 领取宗门任务奖励\n"
                "• `/sect war <宗门ID>` 发起宗门战争\n"
                "• `/sect leave` 退出宗门\n"
                "• `/sect promote <UID> <elder|member>` 晋升成员\n"
                "• `/sect kick <UID>` 踢出成员\n"
            )
        else:
            text = (
                "🏛️ *宗门指令*\n\n"
                f"• 创建消耗：{int(sect_cfg['create_copper'])} 下品灵石 + {int(sect_cfg['create_gold'])} 中品灵石\n"
                f"• 宗门人数上限：{int(sect_cfg['base_max_members'])} 人\n"
                f"• 宗门Buff：修炼+{int(float(sect_cfg['default_cultivation_buff_pct']))}% ｜ 属性+{int(float(sect_cfg['default_stat_buff_pct']))}% ｜ 战斗收益+{int(float(sect_cfg['default_battle_reward_buff_pct']))}%\n"
                f"• 每个宗门最多 {int(sect_cfg['branch_max'])} 个附属别院\n"
                f"• 每个别院最多 {int(sect_cfg['branch_max_members'])} 人\n"
                f"• 别院成员享受宗门 Buff 的 {int(round(float(sect_cfg['branch_buff_rate']) * 100))}%\n"
                "• `/sect create <名称> [描述]`\n"
                "• `/sect join <宗门ID>`\n"
                "• `/sect branch_join <别院ID>`\n"
                "• `/sect list [关键字]` 查看宗门列表\n"
            )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    try:
        if not args:
            info = await http_get(f"{SERVER_URL}/api/sect/member/{uid}", timeout=15)
            if info.get("success"):
                sect = info.get("sect", {})
                header = "🏛️ *我的别院*" if sect.get("membership_kind") == "branch" else "🏛️ *我的宗门*"
                buff_rate = float(sect_cfg["branch_buff_rate"]) if sect.get("membership_kind") == "branch" else 1.0
                branch_line = f"归属别院: {(sect.get('branch') or {}).get('display_name')}\n" if sect.get("membership_kind") == "branch" else ""
                text = (
                    f"{header}\n\n"
                    f"名称: {sect.get('name')}\n"
                    f"宗门ID: {sect.get('sect_id')}\n"
                    f"{branch_line}"
                    f"等级: {sect.get('level', 1)}\n"
                    f"人数上限: {int(sect.get('max_members', sect_cfg['base_max_members']) or sect_cfg['base_max_members'])}\n"
                    f"Buff: 修炼+{int(float(sect.get('cultivation_buff_pct', 10) or 0) * buff_rate)}% ｜ 属性+{int(float(sect.get('stat_buff_pct', 5) or 0) * buff_rate)}% ｜ 战斗收益+{int(float(sect.get('battle_reward_buff_pct', 10) or 0) * buff_rate)}%\n"
                    f"附属别院: {len(sect.get('branches', []) or [])}/{int(sect_cfg['branch_max'])}\n"
                    f"资金: {sect.get('fund_copper', 0)}下品灵石 / {sect.get('fund_gold', 0)}中品灵石\n"
                    f"战绩: {sect.get('war_wins', 0)}胜 {sect.get('war_losses', 0)}负\n"
                )
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                await _reply_help(True)
            else:
                await _reply_help(False)
            return

        sub = args[0].lower()
        if sub == "create" and len(args) >= 2:
            name = args[1]
            desc = " ".join(args[2:]) if len(args) > 2 else ""
            data = await http_post(
                f"{SERVER_URL}/api/sect/create",
                json={"user_id": uid, "name": name, "description": desc},
                timeout=15,
            )
            await update.message.reply_text("✅ 创建成功" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "join" and len(args) >= 2:
            sect_id = args[1]
            data = await http_post(
                f"{SERVER_URL}/api/sect/join",
                json={"user_id": uid, "sect_id": sect_id},
                timeout=15,
            )
            await update.message.reply_text("✅ 加入成功" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "branch_join" and len(args) >= 2:
            branch_id = args[1]
            data = await http_post(
                f"{SERVER_URL}/api/sect/branch/join",
                json={"user_id": uid, "branch_id": branch_id},
                timeout=15,
            )
            await update.message.reply_text("✅ 加入别院成功" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "leave":
            data = await http_post(f"{SERVER_URL}/api/sect/leave", json={"user_id": uid}, timeout=15)
            await update.message.reply_text("✅ 已退出" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "list":
            keyword = args[1] if len(args) >= 2 else None
            data = await http_get(f"{SERVER_URL}/api/sect/list", params={"keyword": keyword} if keyword else None, timeout=15)
            if not data.get("success"):
                await update.message.reply_text("❌ 获取列表失败")
                return
            rows = data.get("sects", [])
            text = "🏛️ *宗门列表*\n\n"
            for row in rows[:10]:
                text += (
                    f"• {row.get('name')} ｜ ID {row.get('sect_id')} ｜ 等级{row.get('level',1)} ｜ "
                    f"别院{int(row.get('branch_count', 0) or 0)}/{int(sect_cfg['branch_max'])}\n"
                )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        if sub == "info":
            sect_id = args[1] if len(args) >= 2 else None
            if not sect_id:
                info = await http_get(f"{SERVER_URL}/api/sect/member/{uid}", timeout=15)
                if not info.get("success"):
                    await update.message.reply_text("❌ 你尚未加入宗门")
                    return
                sect_id = info.get("sect", {}).get("sect_id")
            data = await http_get(f"{SERVER_URL}/api/sect/{sect_id}", timeout=15)
            if not data.get("success"):
                await update.message.reply_text("❌ 宗门不存在")
                return
            sect = data.get("sect", {})
            text = (
                f"🏛️ *{sect.get('name')}*\n\n"
                f"ID: {sect.get('sect_id')}\n"
                f"等级: {sect.get('level', 1)}\n"
                f"人数上限: {int(sect.get('max_members', sect_cfg['base_max_members']) or sect_cfg['base_max_members'])}\n"
                f"宗门Buff: 修炼+{int(float(sect.get('cultivation_buff_pct', 10) or 0))}% ｜ 属性+{int(float(sect.get('stat_buff_pct', 5) or 0))}% ｜ 战斗收益+{int(float(sect.get('battle_reward_buff_pct', 10) or 0))}%\n"
                f"附属别院: {len(sect.get('branches', []) or [])}/{int(sect_cfg['branch_max'])}\n"
                f"战绩: {sect.get('war_wins', 0)}胜 {sect.get('war_losses', 0)}负\n"
            )
            branches = sect.get("branches", []) or []
            if branches:
                text += "别院：\n"
                for row in branches[:5]:
                    text += (
                        f"• {row.get('display_name')} ｜ ID {row.get('branch_id')} ｜ "
                        f"{int(row.get('member_count', 0) or 0)}/"
                        f"{int(row.get('max_members', sect_cfg['branch_max_members']) or sect_cfg['branch_max_members'])}人\n"
                    )
            pending = sect.get("pending_branch_requests", []) or []
            if pending:
                text += "待审批：\n"
                for row in pending[:5]:
                    text += (
                        f"• ID {row.get('id')} ｜ {row.get('name')} ｜ "
                        f"{int(row.get('cost_copper', sect_cfg['branch_create_copper']) or sect_cfg['branch_create_copper'])}下品灵石 + "
                        f"{int(row.get('cost_gold', sect_cfg['branch_create_gold']) or sect_cfg['branch_create_gold'])}中品灵石\n"
                    )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        if sub == "donate" and len(args) >= 2:
            try:
                copper = int(args[1])
                gold = int(args[2]) if len(args) >= 3 else 0
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：/sect donate <下品灵石> [中品灵石]，金额必须是整数")
                return
            if copper < 0 or gold < 0:
                await update.message.reply_text("❌ 参数错误：捐献金额不能为负数")
                return
            data = await http_post(
                f"{SERVER_URL}/api/sect/donate",
                json={"user_id": uid, "copper": copper, "gold": gold},
                timeout=15,
            )
            await update.message.reply_text("✅ 捐献成功" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "quests":
            info = await http_get(f"{SERVER_URL}/api/sect/member/{uid}", timeout=15)
            if not info.get("success"):
                await update.message.reply_text("❌ 你尚未加入宗门")
                return
            sect_id = info.get("sect", {}).get("sect_id")
            data = await http_get(f"{SERVER_URL}/api/sect/quests/{sect_id}", timeout=15)
            if not data.get("success"):
                await update.message.reply_text("❌ 获取任务失败")
                return
            text = "📜 *宗门任务*\n\n"
            for row in data.get("quests", [])[:5]:
                text += f"ID {row.get('id')}: 进度 {row.get('progress',0)}/{row.get('target',0)}\n"
            text += "\n使用 `/sect claim <任务ID>` 领取奖励"
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        if sub == "claim" and len(args) >= 2:
            quest_id = args[1]
            data = await http_post(
                f"{SERVER_URL}/api/sect/quests/claim",
                json={"user_id": uid, "quest_id": quest_id},
                timeout=15,
            )
            await update.message.reply_text("✅ 领取成功" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "war" and len(args) >= 2:
            target = args[1]
            data = await http_post(
                f"{SERVER_URL}/api/sect/war/challenge",
                json={"user_id": uid, "target_sect_id": target},
                timeout=15,
            )
            await update.message.reply_text("✅ 战争已结算" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "promote" and len(args) >= 3:
            target = args[1]
            role = args[2]
            data = await http_post(
                f"{SERVER_URL}/api/sect/promote",
                json={"user_id": uid, "target_user_id": target, "role": role},
                timeout=15,
            )
            await update.message.reply_text("✅ 已更新" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "kick" and len(args) >= 2:
            target = args[1]
            data = await http_post(
                f"{SERVER_URL}/api/sect/kick",
                json={"user_id": uid, "target_user_id": target},
                timeout=15,
            )
            await update.message.reply_text("✅ 已踢出" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "branch_apply" and len(args) >= 2:
            name = args[1]
            desc = " ".join(args[2:]) if len(args) > 2 else ""
            data = await http_post(
                f"{SERVER_URL}/api/sect/branch/request",
                json={"user_id": uid, "name": name, "description": desc},
                timeout=15,
            )
            await update.message.reply_text("✅ 已提交别院申请" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "branch_approve" and len(args) >= 2:
            try:
                request_id = int(args[1])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：/sect branch_approve <申请ID>，申请ID必须是整数")
                return
            data = await http_post(
                f"{SERVER_URL}/api/sect/branch/review",
                json={"user_id": uid, "request_id": request_id, "approve": True},
                timeout=15,
            )
            await update.message.reply_text("✅ 已批准别院申请" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return
        if sub == "branch_reject" and len(args) >= 2:
            try:
                request_id = int(args[1])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：/sect branch_reject <申请ID>，申请ID必须是整数")
                return
            data = await http_post(
                f"{SERVER_URL}/api/sect/branch/review",
                json={"user_id": uid, "request_id": request_id, "approve": False},
                timeout=15,
            )
            await update.message.reply_text("✅ 已拒绝别院申请" if data.get("success") else f"❌ {data.get('message', '失败')}")
            return

        info = await http_get(f"{SERVER_URL}/api/sect/member/{uid}", timeout=15)
        await _reply_help(info.get("success"))
    except Exception as exc:
        logger.error(f"sect error: {exc}")
        await update.message.reply_text("❌ 服务器错误")

@require_account
async def alchemy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    try:
        text, keyboard = await _build_alchemy_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"alchemy error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    try:
        if len(args) >= 3:
            from_currency = str(args[0] or "").strip()
            to_currency = str(args[1] or "").strip()
            try:
                amount = int(args[2])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：数量必须是整数")
                return
            result = await http_post(
                f"{SERVER_URL}/api/currency/exchange",
                json={
                    "user_id": uid,
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "amount": amount,
                    "request_id": _new_request_id(context),
                },
                timeout=15,
            )
            await update.message.reply_text(("✅ " if result.get("success") else "❌ ") + result.get("message", "兑换失败"))
            return

        if len(args) >= 2:
            direction = str(args[0] or "").strip().lower()
            from_currency = None
            if direction in ("up", "to_mid", "mid", "上转", "中"):
                from_currency = "copper"
            elif direction in ("down", "to_low", "low", "下转", "下"):
                from_currency = "gold"
            if from_currency is None:
                await update.message.reply_text("❌ 参数错误：/currency <up|down> <数量>")
                return
            try:
                amount = int(args[1])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：数量必须是整数")
                return
            result = await http_post(
                f"{SERVER_URL}/api/currency/exchange",
                json={
                    "user_id": uid,
                    "from_currency": from_currency,
                    "amount": amount,
                    "request_id": _new_request_id(context),
                },
                timeout=15,
            )
            if result.get("success"):
                wallet = result.get("wallet", {})
                text = (
                    f"✅ {result.get('message', '兑换成功')}\n\n"
                    f"下品灵石: {int(wallet.get('spirit_low', 0) or 0):,}\n"
                    f"中品灵石: {int(wallet.get('spirit_mid', 0) or 0):,}"
                )
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"❌ {result.get('message', '兑换失败')}")
            return

        data = await http_get(f"{SERVER_URL}/api/currency/{uid}", timeout=15)
        if not data.get("success"):
            await update.message.reply_text(f"❌ {data.get('message', '获取货币信息失败')}")
            return
        tiers = data.get("tiers", []) or []
        rules = data.get("rules", {})
        rate = int(rules.get("exchange_rate", 1000) or 1000)
        text = "💱 *统一货币*\n\n"
        spirit_rows = [r for r in tiers if str(r.get("group")) == "spirit"]
        immortal_rows = [r for r in tiers if str(r.get("group")) == "immortal"]
        if spirit_rows:
            text += "灵石：\n"
            for row in spirit_rows:
                text += f"• {row.get('label')}: {int(row.get('amount', 0) or 0):,}\n"
            text += "\n"
        if immortal_rows:
            text += "仙石：\n"
            for row in immortal_rows:
                unlocked = bool(row.get("unlocked"))
                amt = f"{int(row.get('amount', 0) or 0):,}" if unlocked else "未开启"
                text += f"• {row.get('label')}: {amt}\n"
            if not all(bool(r.get("unlocked")) for r in immortal_rows):
                text += "提示：你已知晓仙石体系，需个人飞升仙界后开启。\n"
            text += "\n"
        else:
            text += "仙石：当前境界尚未知晓。\n\n"
        text += (
            f"兑换规则：相邻档位 1:{rate}\n"
            "用法：/currency up <下品数量> ｜ /currency down <中品数量>\n"
            "进阶：/currency <源货币ID> <目标货币ID> <数量>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.error(f"currency error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


def _admin_panel_keyboard(context: ContextTypes.DEFAULT_TYPE | None = None) -> InlineKeyboardMarkup:
    current_action = "set"
    if context is not None:
        action = str(context.user_data.get("admin_panel_action", "set") or "set").strip().lower()
        if action in ADMIN_PANEL_ACTION_LABELS:
            current_action = action
    set_label = "✅ 设置" if current_action == "set" else "🛠 设置"
    add_label = "✅ 增加" if current_action == "add" else "➕ 增加"
    minus_label = "✅ 扣减" if current_action == "minus" else "➖ 扣减"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(set_label, callback_data="admin_test_action_set"),
                InlineKeyboardButton(add_label, callback_data="admin_test_action_add"),
                InlineKeyboardButton(minus_label, callback_data="admin_test_action_minus"),
            ],
            [
                InlineKeyboardButton("🎯 目标自己", callback_data="admin_test_target_self"),
                InlineKeyboardButton("🧹 清空目标", callback_data="admin_test_target_clear"),
                InlineKeyboardButton("🔄 刷新", callback_data="admin_test_refresh"),
            ],
            [
                InlineKeyboardButton("下灵+1k", callback_data="admin_test_quick_c1k"),
                InlineKeyboardButton("下灵+1w", callback_data="admin_test_quick_c1w"),
                InlineKeyboardButton("下灵+10w", callback_data="admin_test_quick_c10w"),
            ],
            [
                InlineKeyboardButton("中灵+10", callback_data="admin_test_quick_g10"),
                InlineKeyboardButton("中灵+100", callback_data="admin_test_quick_g100"),
                InlineKeyboardButton("中灵+1000", callback_data="admin_test_quick_g1000"),
            ],
            [
                InlineKeyboardButton("上灵+10", callback_data="admin_test_quick_h10"),
                InlineKeyboardButton("精品+10", callback_data="admin_test_quick_u10"),
                InlineKeyboardButton("极品+10", callback_data="admin_test_quick_x10"),
            ],
            [
                InlineKeyboardButton("修为+1w", callback_data="admin_test_quick_e1w"),
                InlineKeyboardButton("修为+10w", callback_data="admin_test_quick_e10w"),
                InlineKeyboardButton("境界+1", callback_data="admin_test_quick_r1"),
            ],
            [
                InlineKeyboardButton("精力=24", callback_data="admin_test_quick_st24"),
                InlineKeyboardButton("精力+10", callback_data="admin_test_quick_st10"),
                InlineKeyboardButton("破境计数+1", callback_data="admin_test_quick_pity1"),
            ],
            [
                InlineKeyboardButton("满血满蓝", callback_data="admin_test_quick_heal"),
                InlineKeyboardButton("狩猎清零", callback_data="admin_test_quick_hunt0"),
                InlineKeyboardButton("PVP日清", callback_data="admin_test_quick_pvp0"),
            ],
            [InlineKeyboardButton("✍️ 手动输入", callback_data="admin_test_manual")],
            [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
        ]
    )


def _admin_reply_target_token(message) -> str:
    reply_message = getattr(message, "reply_to_message", None) if message else None
    reply_user = getattr(reply_message, "from_user", None) if reply_message else None
    if reply_user and not bool(getattr(reply_user, "is_bot", False)):
        return str(getattr(reply_user, "id", "") or "").strip()
    return ""


async def _admin_modifiable_fields_snapshot() -> tuple[list[dict], dict[str, str]]:
    from core.database import connection as db_conn
    from core.admin.user_management import get_modifiable_fields

    if getattr(db_conn, "_pool", None) is None:
        db_conn.connect_sqlite()
    rows = get_modifiable_fields()
    labels = {str(row.get("field") or ""): str(row.get("label") or row.get("field") or "") for row in rows}
    return rows, labels


def _admin_fields_preview_text(fields: list[dict], *, limit: int = 24) -> str:
    if not fields:
        return "（当前未检测到可编辑字段）"
    groups: dict[str, list[str]] = {}
    for row in fields[: max(1, int(limit))]:
        group_label = str(row.get("group_label") or row.get("group") or "其他")
        groups.setdefault(group_label, []).append(str(row.get("field") or ""))
    lines: list[str] = []
    for group, names in groups.items():
        clean = [n for n in names if n]
        if not clean:
            continue
        lines.append(f"{group}: " + ", ".join(clean))
    return "\n".join(lines) if lines else "（当前未检测到可编辑字段）"


def _admin_current_target_hint(context: ContextTypes.DEFAULT_TYPE) -> str:
    token = str(context.user_data.get("admin_target_token", "") or "").strip()
    uid = str(context.user_data.get("admin_target_uid", "") or "").strip()
    if uid:
        return f"UID {uid}"
    if token:
        return f"TG_ID {token}"
    return "未指定（请回复目标玩家消息，或在输入中携带 UID/TG_ID）"


def _admin_current_target_token(context: ContextTypes.DEFAULT_TYPE) -> str:
    uid = str(context.user_data.get("admin_target_uid", "") or "").strip()
    if uid:
        return uid
    return str(context.user_data.get("admin_target_token", "") or "").strip()


async def _admin_resolve_target_user(target_token: str) -> tuple[str, dict | None]:
    from core.database import connection as db_conn

    token = str(target_token or "").strip()
    if not token:
        return "", None
    if getattr(db_conn, "_pool", None) is None:
        db_conn.connect_sqlite()

    # 数字目标优先按 TG_ID 解析到游戏 UID，避免误命中历史“TG_ID=UID”脏数据。
    if token.isdigit():
        try:
            lookup = await http_get(
                f"{SERVER_URL}/api/user/lookup",
                params={"platform": "telegram", "platform_id": token},
                timeout=15,
            )
        except Exception:
            lookup = {}
        if lookup.get("success"):
            mapped_uid = str(lookup.get("user_id") or "").strip()
            if mapped_uid:
                mapped_user = db_conn.get_user_by_id(mapped_uid)
                if mapped_user:
                    return mapped_uid, mapped_user

    target_uid = token
    target_user = db_conn.get_user_by_id(target_uid)

    return target_uid, target_user


async def _admin_apply_preset(
    *,
    operator_tg_id: str,
    target_token: str,
    preset_id: str,
) -> tuple[bool, str, str]:
    preset = ADMIN_PRESET_MAP.get(str(preset_id or "").strip())
    if not preset:
        return False, "❌ 未知预设操作。", ""

    kind = str(preset.get("kind") or "modify").strip().lower()
    if kind == "modify":
        field = str(preset.get("field") or "").strip()
        action = str(preset.get("action") or "add").strip().lower()
        value = str(preset.get("value") or "0")
        return await _admin_apply_modify(
            operator_tg_id=operator_tg_id,
            target_token=target_token,
            action=action,
            field=field,
            value_raw=value,
        )

    from core.database import connection as db_conn
    from core.services.audit_log_service import write_audit_log

    target_uid, target_user = await _admin_resolve_target_user(target_token)
    if not target_user:
        return False, "❌ 未找到目标玩家，请先指定有效目标。", ""

    if getattr(db_conn, "_pool", None) is None:
        db_conn.connect_sqlite()

    label = str(preset.get("label") or preset_id)
    detail: dict[str, object] = {"preset_id": preset_id, "preset_label": label}
    if kind == "heal_full":
        db_conn.execute(
            "UPDATE users SET hp = GREATEST(COALESCE(max_hp, 0), 0), mp = GREATEST(COALESCE(max_mp, 0), 0) WHERE user_id = %s",
            (target_uid,),
        )
    elif kind == "hunt_reset":
        db_conn.execute(
            "UPDATE users SET dy_times = 0, hunts_today = 0 WHERE user_id = %s",
            (target_uid,),
        )
    elif kind == "pvp_daily_reset":
        db_conn.execute(
            "UPDATE users SET pvp_daily_count = 0 WHERE user_id = %s",
            (target_uid,),
        )
    else:
        return False, "❌ 预设类型不支持。", target_uid

    latest = db_conn.get_user_by_id(target_uid) or target_user
    detail["after"] = {
        "hp": int((latest or {}).get("hp", 0) or 0),
        "mp": int((latest or {}).get("mp", 0) or 0),
        "dy_times": int((latest or {}).get("dy_times", 0) or 0),
        "hunts_today": int((latest or {}).get("hunts_today", 0) or 0),
        "pvp_daily_count": int((latest or {}).get("pvp_daily_count", 0) or 0),
    }
    try:
        write_audit_log(
            module="admin",
            action=f"panel_preset_{preset_id}",
            user_id=operator_tg_id,
            success=True,
            detail={
                "target_uid": target_uid,
                "operator_tg_id": operator_tg_id,
                **detail,
            },
        )
    except Exception:
        pass

    return True, f"✅ 预设执行成功：`{target_uid}` 已应用「{label}」", target_uid


async def _admin_apply_modify(
    *,
    operator_tg_id: str,
    target_token: str,
    action: str,
    field: str,
    value_raw: str,
) -> tuple[bool, str, str]:
    from core.database import connection as db_conn
    from core.admin.user_management import modify_user_field
    from core.services.audit_log_service import write_audit_log

    target_uid, target_user = await _admin_resolve_target_user(target_token)
    if not target_user:
        return False, "❌ 未找到目标玩家，请传入有效游戏UID或TG_ID。", ""

    success, msg = modify_user_field(target_uid, field, action, value_raw)
    if not success:
        return False, f"❌ {msg}", target_uid

    latest = db_conn.get_user_by_id(target_uid) or target_user
    latest_value = (latest or {}).get(field, 0)
    try:
        value_for_audit = float(value_raw)
    except Exception:
        value_for_audit = str(value_raw)

    try:
        write_audit_log(
            module="admin",
            action=f"panel_{action}_{field}",
            user_id=operator_tg_id,
            success=True,
            detail={
                "target_uid": target_uid,
                "field": field,
                "action": action,
                "value": value_for_audit,
                "new_value": latest_value,
                "operator_tg_id": operator_tg_id,
                "target_token": str(target_token or ""),
            },
        )
    except Exception as exc:
        logger.warning("admin_audit_log_failed error=%s", type(exc).__name__)

    action_text = ADMIN_PANEL_ACTION_LABELS.get(action, action)
    return (
        True,
        f"✅ 管理操作成功：{action_text} `{target_uid}` 的 `{field}` = `{latest_value}`",
        target_uid,
    )


async def _show_admin_test_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    selected_action: str | None = None,
) -> None:
    message = update.effective_message
    if message is None:
        return
    operator = str(getattr(update.effective_user, "id", "") or "")
    if not _is_super_admin_tg(operator):
        await message.reply_text("❌ 权限不足：仅超管可使用该命令。")
        return

    if selected_action in ADMIN_PANEL_ACTION_LABELS:
        context.user_data["admin_panel_action"] = selected_action
    current_action = str(context.user_data.get("admin_panel_action", "set") or "set").strip().lower()
    if current_action not in ADMIN_PANEL_ACTION_LABELS:
        current_action = "set"
        context.user_data["admin_panel_action"] = current_action

    reply_target = _admin_reply_target_token(message)
    is_callback_panel = getattr(update, "callback_query", None) is not None
    # 仅在命令入口使用回复目标；按钮回调阶段不能再被历史 reply_to_message 覆盖。
    if reply_target and not is_callback_panel:
        context.user_data["admin_target_token"] = reply_target

    try:
        fields, labels = await _admin_modifiable_fields_snapshot()
    except Exception as exc:
        logger.warning("admin_field_snapshot_failed error=%s", type(exc).__name__)
        fields = []
        labels = {}

    preview = _admin_fields_preview_text(fields, limit=24)
    example_field = next(iter(labels.keys()), "exp")
    example_target = _admin_current_target_token(context) or "<UID|TG_ID>"
    action_label = ADMIN_PANEL_ACTION_LABELS.get(current_action, current_action)
    target_hint = _admin_current_target_hint(context)

    text = (
        "🛡️ 超管管理面板\n\n"
        f"当前操作: {action_label}\n"
        f"当前目标: {target_hint}\n\n"
        "使用方式：\n"
        "1. 回复目标玩家消息后点击上方按钮，或直接输入 UID/TG_ID。\n"
        "2. 直接点下方预设按钮，一键执行。\n"
        "3. 点“手动输入”后可输入：字段 数值（使用当前操作/目标）\n"
        "4. 或直接发：UID 字段 数值 / UID 操作 字段 数值\n\n"
        f"快速示例：\n/test {example_target} add {example_field} 1000\n\n"
        "可编辑字段（节选）：\n"
        f"{preview}"
    )
    await _reply_with_owned_panel(
        update,
        context,
        text,
        reply_markup=_admin_panel_keyboard(context),
    )


async def admin_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    operator = str(getattr(update.effective_user, "id", "") or "")
    if not _is_super_admin_tg(operator):
        await message.reply_text("❌ 权限不足：仅超管可使用该命令。")
        return

    reply_target = _admin_reply_target_token(message)
    if reply_target:
        context.user_data["admin_target_token"] = reply_target

    args = context.args or []
    if not args:
        await _show_admin_test_panel(update, context)
        return

    default_action = str(context.user_data.get("admin_panel_action", "set") or "set").strip().lower()
    if default_action not in ADMIN_PANEL_ACTION_LABELS:
        default_action = "set"

    action = default_action
    target_token = ""
    field = ""
    value_raw = ""

    if len(args) >= 4:
        target_token = str(args[0] or "").strip()
        action = str(args[1] or "").strip().lower()
        field = str(args[2] or "").strip()
        value_raw = str(args[3] or "").strip()
    elif len(args) == 3:
        first = str(args[0] or "").strip()
        if first.lower() in ADMIN_PANEL_ACTION_LABELS:
            action = first.lower()
            field = str(args[1] or "").strip()
            value_raw = str(args[2] or "").strip()
            target_token = reply_target or _admin_current_target_token(context)
        else:
            target_token = first
            field = str(args[1] or "").strip()
            value_raw = str(args[2] or "").strip()
    elif len(args) == 2:
        target_token = reply_target or _admin_current_target_token(context)
        field = str(args[0] or "").strip()
        value_raw = str(args[1] or "").strip()
    else:
        await message.reply_text(
            "❌ 参数不足。\n"
            "用法：/test <UID|TG_ID> <set|add|minus> <field> <value>\n"
            "或：回复目标消息后 /test <set|add|minus> <field> <value>\n"
            "或：/test <UID|TG_ID> <field> <value>"
        )
        return

    if action not in ADMIN_PANEL_ACTION_LABELS:
        await message.reply_text("❌ 操作类型错误，仅支持 set / add / minus。")
        return
    if not target_token:
        await message.reply_text("❌ 未指定目标，请回复目标玩家消息或传入 UID/TG_ID。")
        return
    if not field or not value_raw:
        await message.reply_text("❌ 参数错误，field 和 value 不能为空。")
        return

    try:
        ok, result_text, resolved_uid = await _admin_apply_modify(
            operator_tg_id=operator,
            target_token=target_token,
            action=action,
            field=field,
            value_raw=value_raw,
        )
    except Exception as exc:
        logger.error("admin_test_modify_failed error=%s", type(exc).__name__)
        await message.reply_text("❌ 管理操作失败，请稍后重试。")
        return
    if resolved_uid:
        context.user_data["admin_target_uid"] = resolved_uid
    if target_token:
        context.user_data["admin_target_token"] = target_token
    context.user_data["admin_panel_action"] = action
    await message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
    await _show_admin_test_panel(update, context, selected_action=action)


async def _admin_give_currency_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    tier: str,
) -> None:
    message = update.effective_message
    if message is None:
        return
    operator = str(getattr(update.effective_user, "id", "") or "")
    if not _is_super_admin_tg(operator):
        await message.reply_text("❌ 权限不足：仅超管可使用该命令。")
        return

    field_label = ADMIN_GIVE_CURRENCY_FIELDS.get(tier)
    if not field_label:
        await message.reply_text("❌ 未知发放类型。")
        return
    field, label = field_label

    args = context.args or []
    target_token = ""
    amount = 0
    mode = "explicit"

    if len(args) >= 2:
        target_token = str(args[0] or "").strip()
        try:
            amount = int(args[1])
        except (TypeError, ValueError):
            await message.reply_text("❌ 参数错误：数量必须是整数。")
            return
    elif len(args) == 1:
        try:
            amount = int(args[0])
        except (TypeError, ValueError):
            await message.reply_text("❌ 参数错误：数量必须是整数。")
            return
        reply_message = getattr(message, "reply_to_message", None)
        reply_user = getattr(reply_message, "from_user", None) if reply_message else None
        if reply_user and not bool(getattr(reply_user, "is_bot", False)):
            target_token = str(getattr(reply_user, "id", "") or "").strip()
            mode = "reply"
        else:
            target_token = operator
            mode = "self"
    else:
        await message.reply_text(
            f"用法1：/xian_give_{tier} <数量>（回复他人消息=给对方；不回复=给自己）\n"
            f"用法2：/xian_give_{tier} <游戏UID|TG_ID> <数量>"
        )
        return

    if not target_token:
        await message.reply_text("❌ 参数错误：目标不能为空。")
        return
    if amount <= 0:
        await message.reply_text("❌ 参数错误：数量必须大于 0。")
        return

    from core.database import connection as db_conn
    from core.services.audit_log_service import write_audit_log

    if getattr(db_conn, "_pool", None) is None:
        db_conn.connect_sqlite()

    target_uid, target_user = await _admin_resolve_target_user(target_token)

    if not target_user:
        await message.reply_text("❌ 未找到目标玩家，请传入有效游戏UID或TG_ID。")
        return

    db_conn.execute(
        f"UPDATE users SET {field} = COALESCE({field}, 0) + %s WHERE user_id = %s",
        (amount, target_uid),
    )
    latest = db_conn.get_user_by_id(target_uid) or target_user
    new_amount = int((latest or {}).get(field, 0) or 0)

    write_audit_log(
        module="admin",
        action=f"give_{field}",
        user_id=operator,
        success=True,
        detail={
            "target_uid": target_uid,
            "field": field,
            "amount": amount,
            "new_amount": new_amount,
            "operator_tg_id": operator,
            "mode": mode,
        },
    )

    target_desc = {
        "reply": "回复目标",
        "self": "自己",
        "explicit": "指定目标",
    }.get(mode, "指定目标")
    await message.reply_text(
        f"✅ 发放成功（{target_desc}）：已向 `{target_uid}` 增加 {amount:,} {label}。\n"
        f"当前{label}: {new_amount:,}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def xian_give_low_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_give_currency_cmd(update, context, tier="low")


async def xian_give_mid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_give_currency_cmd(update, context, tier="mid")


async def xian_give_high_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_give_currency_cmd(update, context, tier="high")


async def xian_give_uhigh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_give_currency_cmd(update, context, tier="uhigh")


async def xian_give_xhigh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_give_currency_cmd(update, context, tier="xhigh")


@require_account
async def convert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    try:
        if len(args) >= 2:
            route = args[0].lower()
            target_item_id = args[1]
            if len(args) >= 3:
                try:
                    qty = int(args[2])
                except (TypeError, ValueError):
                    await update.message.reply_text("❌ 参数错误：/convert <路线> <资源ID> [数量]，数量必须是整数")
                    return
            else:
                qty = 1
            if qty <= 0:
                await update.message.reply_text("❌ 参数错误：数量必须大于 0")
                return
            result = await http_post(
                f"{SERVER_URL}/api/convert",
                json={
                    "user_id": uid,
                    "target_item_id": target_item_id,
                    "quantity": qty,
                    "route": route,
                    "request_id": _new_request_id(context),
                },
                timeout=15,
            )
            if result.get("success"):
                catalyst = result.get("catalyst") or {}
                catalyst_text = ""
                if catalyst and catalyst.get("item_id"):
                    catalyst_text = (
                        f"\n专精材料: {catalyst.get('used', 0)} x "
                        f"{_item_display_name(str(catalyst.get('item_id') or ''))}"
                    )
                text = (
                    f"✅ {result.get('message', result.get('route_name', '转化完成'))}\n"
                    f"目标: {result.get('target_name')} x{result.get('output_quantity', 0)}\n"
                    f"消耗: {result.get('cost_copper', 0)} 下品灵石{catalyst_text}"
                )
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"❌ {result.get('message', '转化失败')}")
            return

        text, keyboard = await _build_convert_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"convert error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def gacha_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    try:
        if args:
            try:
                banner_id = int(args[0])
            except (TypeError, ValueError):
                await update.message.reply_text(
                    f"❌ 参数错误：/gacha <卡池ID> [1|{_gacha_five_pull_count()}|paid1]，卡池ID必须是整数"
                )
                return
            draw_arg = (args[1] if len(args) >= 2 else "1").lower()
            force_paid = draw_arg in ("paid1", "paid", "single")
            if force_paid:
                count = 1
            else:
                five_count = _gacha_five_pull_count()
                if draw_arg in ("5", "five", str(five_count)):
                    count = five_count
                else:
                    try:
                        count = int(draw_arg)
                    except (TypeError, ValueError):
                        count = 1
            result = await http_post(
                f"{SERVER_URL}/api/gacha/pull",
                json={
                    "user_id": uid,
                    "banner_id": banner_id,
                    "count": count,
                    "force_paid": force_paid,
                    "request_id": _new_request_id(context),
                },
                timeout=15,
            )
            if result.get("success"):
                text = "🎲 *抽奖结果*\n\n"
                for it in result.get("results", [])[:10]:
                    item_name = it.get("item_name") or _item_display_name(str(it.get("item_id") or ""))
                    line = f"• [{it.get('rarity')}] {item_name}"
                    if it.get("duplicate"):
                        comp = it.get("compensation") or {}
                        comp_name = comp.get("item_name") or _item_display_name(str(comp.get("item_id") or ""))
                        comp_qty = int(comp.get("quantity", 0) or 0)
                        if comp_name and comp_qty > 0:
                            line += f"（重复转化：{comp_name} x{comp_qty}）"
                        else:
                            line += "（重复转化）"
                    text += line + "\n"
                if result.get("pull_mode") == "free":
                    text += "\n\n本次为免费抽奖。"
                else:
                    cost = result.get("cost", {})
                    currency_name = "中品灵石" if cost.get("currency") == "gold" else "下品灵石"
                    text += f"\n\n消耗：{cost.get('amount', 0)}{currency_name} + {result.get('stamina_cost', 0)}精力"
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"❌ {result.get('message', '抽奖失败')}")
            return

        text, keyboard = await _build_gacha_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"gacha error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    try:
        text, keyboard = await _build_achievements_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"achievements error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def codex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    kind_raw = (args[0] if args else "").strip().lower()
    kind_map = {
        "monster": "monsters",
        "monsters": "monsters",
        "m": "monsters",
        "怪物": "monsters",
        "item": "items",
        "items": "items",
        "i": "items",
        "物品": "items",
    }
    kind = kind_map.get(kind_raw, "all")
    try:
        if kind == "monsters":
            data = await http_get(f"{SERVER_URL}/api/codex/{uid}", params={"kind": "monsters"}, timeout=15)
            if not data.get("success"):
                await update.message.reply_text("❌ 获取图鉴失败")
                return
            text = "📖 *图鉴 - 怪物*\n\n" + _format_codex_monsters(data.get("monsters", []))
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return
        if kind == "items":
            data = await http_get(f"{SERVER_URL}/api/codex/{uid}", params={"kind": "items"}, timeout=15)
            if not data.get("success"):
                await update.message.reply_text("❌ 获取图鉴失败")
                return
            text = "📖 *图鉴 - 物品*\n\n" + _format_codex_items(data.get("items", []))
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return

        monsters = await http_get(f"{SERVER_URL}/api/codex/{uid}", params={"kind": "monsters"}, timeout=15)
        items = await http_get(f"{SERVER_URL}/api/codex/{uid}", params={"kind": "items"}, timeout=15)
        if not monsters.get("success") and not items.get("success"):
            await update.message.reply_text("❌ 获取图鉴失败")
            return
        text = "📖 *图鉴概览*\n"
        if monsters.get("success"):
            text += "\n🐾 *怪物*\n" + _format_codex_monsters(monsters.get("monsters", [])) + "\n"
        if items.get("success"):
            text += "\n🎒 *物品*\n" + _format_codex_items(items.get("items", []))
        text += "\n\n提示：/codex monsters 或 /codex items 查看细分。"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.error(f"codex error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        r = await http_get(
            f"{SERVER_URL}/api/user/lookup",
            params={"platform": "telegram", "platform_id": user_id},
            timeout=15,
        )
        uid = r.get("user_id") if r.get("success") else None
        text, keyboard = await _build_events_menu(uid)
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"events error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def worldboss_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text, keyboard = await _build_worldboss_menu()
        await _reply_with_owned_panel(
            update,
            context,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        logger.error(f"worldboss error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


@require_account
async def bounty_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    args = context.args or []
    try:
        if not args:
            text, keyboard = await _build_bounty_menu()
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        action = str(args[0] or "").strip().lower()
        if action == "publish":
            if len(args) < 4:
                await update.message.reply_text("用法：/bounty publish <道具ID> <数量> <奖励下品灵石> [描述]")
                return
            wanted_item_id = str(args[1]).strip()
            try:
                wanted_quantity = int(args[2])
                reward_low = int(args[3])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：数量和奖励必须是整数")
                return
            description = " ".join(args[4:]).strip() if len(args) > 4 else ""
            result = await http_post(
                f"{SERVER_URL}/api/bounty/publish",
                json={
                    "user_id": uid,
                    "wanted_item_id": wanted_item_id,
                    "wanted_quantity": wanted_quantity,
                    "reward_spirit_low": reward_low,
                    "description": description,
                },
                timeout=15,
            )
            await update.message.reply_text(("✅ " if result.get("success") else "❌ ") + result.get("message", "发布失败"))
            return

        if action == "accept":
            if len(args) < 2:
                await update.message.reply_text("用法：/bounty accept <悬赏ID>")
                return
            try:
                bounty_id = int(args[1])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：悬赏ID必须是整数")
                return
            result = await http_post(
                f"{SERVER_URL}/api/bounty/accept",
                json={"user_id": uid, "bounty_id": bounty_id},
                timeout=15,
            )
            await update.message.reply_text(("✅ " if result.get("success") else "❌ ") + result.get("message", "接取失败"))
            return

        if action == "submit":
            if len(args) < 2:
                await update.message.reply_text("用法：/bounty submit <悬赏ID>")
                return
            try:
                bounty_id = int(args[1])
            except (TypeError, ValueError):
                await update.message.reply_text("❌ 参数错误：悬赏ID必须是整数")
                return
            result = await http_post(
                f"{SERVER_URL}/api/bounty/submit",
                json={"user_id": uid, "bounty_id": bounty_id},
                timeout=15,
            )
            await update.message.reply_text(("✅ " if result.get("success") else "❌ ") + result.get("message", "提交失败"))
            return

        if action == "list":
            status = str(args[1] if len(args) >= 2 else "open")
            data = await http_get(f"{SERVER_URL}/api/bounties", params={"status": status, "limit": 15}, timeout=15)
            if not data.get("success"):
                await update.message.reply_text(f"❌ {data.get('message', '获取悬赏失败')}")
                return
            lines = ["📜 悬赏会"]
            for row in (data.get("bounties") or [])[:15]:
                wanted_name = row.get("wanted_item_name") or _item_display_name(str(row.get("wanted_item_id") or ""))
                lines.append(
                    f"#{row.get('id')} {wanted_name} x{row.get('wanted_quantity')} "
                    f"=> {row.get('reward_spirit_low')} 下品灵石 ({row.get('status')})"
                )
            await update.message.reply_text("\n".join(lines))
            return

        await update.message.reply_text("用法：/bounty [list [open|claimed|completed]] | publish | accept | submit")
    except Exception as exc:
        logger.error(f"bounty error: {exc}")
        await update.message.reply_text("❌ 服务器错误")


# ==================== 主函数 ====================

def load_config():
    """兼容函数：使用统一配置模块。"""
    from core.config import config as app_config
    return app_config.raw


def main():
    from core.config import config as app_config

    global SERVER_URL
    SERVER_URL = str(getattr(app_config, "core_server_url", "") or f"http://127.0.0.1:{app_config.core_server_port}").rstrip("/")
    logger.info("Telegram core API target: %s", SERVER_URL)

    if not _acquire_pid_lock():
        return

    token = app_config.telegram_token() if callable(app_config.telegram_token) else app_config.telegram_token
    if not token:
        logger.error("No telegram token configured (set XXBOT_TELEGRAM_TOKEN env var)")
        return

    if os.environ.get("XXBOT_TELEGRAM_TOKEN"):
        token_source = "XXBOT_TELEGRAM_TOKEN"
    elif os.environ.get("TELEGRAM_BOT_TOKEN"):
        token_source = "TELEGRAM_BOT_TOKEN"
    elif os.environ.get("BOT_TOKEN"):
        token_source = "BOT_TOKEN"
    else:
        token_source = "config.json tokens.telegram_token"

    request = _build_telegram_request(for_updates=False)
    get_updates_request = _build_telegram_request(for_updates=True)
    logger.info("Telegram outbound proxy: %s", TELEGRAM_PROXY_URL or "disabled")
    logger.info("Telegram token source: %s (%s)", token_source, _mask_bot_token(token))

    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(_on_app_init)
        .post_shutdown(_on_app_shutdown)
        .build()
    )

    # chat_id 过滤（只响应指定群组）
    _allowed_chat_ids_raw = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
    _allowed_chat_ids = set()
    if _allowed_chat_ids_raw:
        for _cid in _allowed_chat_ids_raw.split(","):
            _cid = _cid.strip()
            if _cid:
                try:
                    _allowed_chat_ids.add(int(_cid))
                except ValueError:
                    pass

    def _make_chat_filter():
        if not _allowed_chat_ids:
            return filters.ALL
        return filters.ChatType.PRIVATE | filters.Chat(chat_id=list(_allowed_chat_ids))

    _chat_filter = _make_chat_filter()
    if _allowed_chat_ids:
        logger.info("Telegram chat filter enabled: private chats + %s", sorted(_allowed_chat_ids))
    else:
        logger.info("Telegram chat filter enabled: all chats")

    # 命令处理器（/xian_ 前缀 + 兼容旧命令）
    app.add_handler(CommandHandler(["xian_start", "start"], start_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_register", "register"], register_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_stat", "xian_status", "stat", "status"], stat_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_cul", "xian_cultivate", "cul", "cultivate"], cultivate_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_hunt", "hunt"], hunt_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_break", "xian_breakthrough", "break", "breakthrough"], breakthrough_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_shop", "shop"], shop_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_bag", "xian_inventory", "bag", "inventory"], bag_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_quest", "xian_quests", "xian_task", "quest", "quests", "task"], quest_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_skills", "xian_skill", "skills", "skill"], skills_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_secret", "xian_mystic", "secret", "mystic"], secret_realms_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_rank", "xian_leaderboard", "rank", "leaderboard"], leaderboard_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_pvp", "pvp"], pvp_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_chat", "xian_dao", "chat", "dao"], chat_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_sect", "sect"], sect_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_alchemy", "alchemy"], alchemy_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_currency", "currency"], currency_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_give_low"], xian_give_low_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_give_mid"], xian_give_mid_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_give_high"], xian_give_high_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_give_uhigh"], xian_give_uhigh_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_give_xhigh"], xian_give_xhigh_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_convert", "convert"], convert_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_achievements", "xian_ach", "achievements", "ach"], achievements_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_codex", "codex"], codex_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_events", "events"], events_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_bounty", "bounty"], bounty_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_worldboss", "xian_boss", "worldboss", "boss"], worldboss_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_guide", "xian_realms", "guide", "realms"], guide_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_version", "version"], version_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["test", "xian_test"], admin_test_cmd, filters=_chat_filter))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & _chat_filter, text_message_handler))

    # 回调处理器
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(global_error_handler)

    # 设置命令菜单（/xian_ 前缀）
    commands = [
        BotCommand("xian_start", "开始游戏"),
        BotCommand("xian_register", "注册账号"),
        BotCommand("xian_stat", "查看状态"),
        BotCommand("xian_cul", "修炼"),
        BotCommand("xian_hunt", "狩猎"),
        BotCommand("xian_break", "突破境界"),
        BotCommand("xian_shop", "商店"),
        BotCommand("xian_bag", "储物袋"),
        BotCommand("xian_quest", "每日任务"),
        BotCommand("xian_skills", "技能面板"),
        BotCommand("xian_secret", "秘境探索"),
        BotCommand("xian_rank", "排行榜"),
        BotCommand("xian_pvp", "PVP 对战"),
        BotCommand("xian_chat", "论道交流"),
        BotCommand("xian_sect", "宗门系统"),
        BotCommand("xian_alchemy", "炼丹系统"),
        BotCommand("xian_currency", "统一货币"),
        BotCommand("xian_convert", "资源转化"),
        BotCommand("xian_achievements", "成就系统"),
        BotCommand("xian_codex", "图鉴"),
        BotCommand("xian_events", "活动"),
        BotCommand("xian_bounty", "全服悬赏"),
        BotCommand("xian_worldboss", "世界BOSS"),
        BotCommand("xian_guide", "玩法说明"),
        BotCommand("xian_version", "版本信息"),
    ]

    for cmd in commands:
        if registry.get(cmd.command):
            continue
        registry.register(
            CommandDef(
                name=cmd.command,
                description=cmd.description,
                category="game",
                handler=lambda *_args, **_kwargs: None,
                aliases=[],
                cooldown=0,
                require_account=False,
                admin_only=False,
            )
        )

    logger.info("Starting XiuXianBot v2.0")
    logger.info("Command registry loaded: %s", len(registry.all_commands))
    app.run_polling(timeout=TELEGRAM_POLL_TIMEOUT)


if __name__ == "__main__":
    main()
