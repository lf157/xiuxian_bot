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
    BotCommand("xian_sign", "Daily sign-in"),
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
    BotCommand("xian_gacha", "Gacha"),
    BotCommand("xian_achievements", "Achievements"),
    BotCommand("xian_codex", "Codex"),
    BotCommand("xian_events", "Events"),
    BotCommand("xian_bounty", "Bounty"),
    BotCommand("xian_worldboss", "World boss"),
    BotCommand("xian_guide", "Guide"),
    BotCommand("xian_version", "Version"),
]


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
SERVER_URL = f"http://127.0.0.1:{DEFAULT_SERVER_PORT}"
INTERNAL_API_TOKEN = (config.internal_api_token or "").strip()
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


def _breakthrough_protect_material_need(rank: int) -> int:
    base = max(0, _cfg_int("balance", "breakthrough", "protect_material_base", default=2))
    per_10 = max(0, _cfg_int("balance", "breakthrough", "protect_material_per_10_rank", default=1))
    return base + max(0, int(rank or 1) // 10) * per_10


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


def _bind_panel_owner(context: ContextTypes.DEFAULT_TYPE, message, owner_id: str) -> None:
    if not message:
        return
    owners = context.application.bot_data.setdefault("panel_owners", {})
    owners[_panel_key(message.chat_id, message.message_id)] = str(owner_id)
    if len(owners) > 5000:
        owners.pop(next(iter(owners)))


def _get_panel_owner(context: ContextTypes.DEFAULT_TYPE, message) -> str | None:
    if not message:
        return None
    owners = context.application.bot_data.get("panel_owners", {})
    return owners.get(_panel_key(message.chat_id, message.message_id))


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
    for key in ("pending_action", "pending_prompt_id", "pending_chat_id", "pending_user_id", "pending_element"):
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
    return await message.reply_text(text, **kwargs)


async def _on_app_init(_app: Application) -> None:
    install_asyncio_exception_logging("telegram")
    await _get_http_session()
    try:
        await _app.bot.set_my_commands([
            BotCommand("xian_start", "修仙世界入口"),
            BotCommand("xian_register", "踏入修仙之路"),
            BotCommand("xian_stat", "查看修仙状态"),
            BotCommand("xian_cul", "打坐修炼"),
            BotCommand("xian_hunt", "外出历练"),
            BotCommand("xian_break", "尝试突破境界"),
            BotCommand("xian_sign", "每日签到"),
            BotCommand("xian_shop", "灵石商铺"),
            BotCommand("xian_bag", "储物袋"),
            BotCommand("xian_quest", "任务面板"),
            BotCommand("xian_sect", "宗门系统"),
            BotCommand("xian_currency", "统一货币"),
            BotCommand("xian_gacha", "天机阁抽签"),
            BotCommand("xian_bounty", "全服悬赏会"),
            BotCommand("xian_pvp", "切磋挑战"),
            BotCommand("xian_rank", "修仙排行榜"),
            BotCommand("xian_guide", "修仙指南"),
        ])
        await _app.bot.set_my_commands(_MENU_COMMANDS)
        await _app.bot.set_my_commands(_MENU_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await _app.bot.set_my_commands(_MENU_COMMANDS, scope=BotCommandScopeAllGroupChats())
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

def get_main_menu_keyboard():
    """获取主菜单键盘"""
    keyboard = [
        [
            InlineKeyboardButton("📊 状态", callback_data="status"),
            InlineKeyboardButton("🧘 修炼", callback_data="cultivate"),
        ],
        [
            InlineKeyboardButton("⚔️ 狩猎", callback_data="hunt"),
            InlineKeyboardButton("🔥 突破", callback_data="breakthrough"),
        ],
        [
            InlineKeyboardButton("📅 签到", callback_data="signin"),
            InlineKeyboardButton("🏪 商店", callback_data="shop_all"),
        ],
        [
            InlineKeyboardButton("🎒 背包", callback_data="bag"),
            InlineKeyboardButton("✨ 技能", callback_data="skills"),
        ],
        [
            InlineKeyboardButton("🗺️ 秘境", callback_data="secret_realms"),
            InlineKeyboardButton("🏆 排行", callback_data="leaderboard_stage"),
        ],
        [
            InlineKeyboardButton("👥 社交", callback_data="social_menu"),
            InlineKeyboardButton("🏛️ 宗门", callback_data="sect_menu"),
        ],
        [
            InlineKeyboardButton("💱 货币", callback_data="currency_menu"),
            InlineKeyboardButton("🔁 转化", callback_data="convert_menu"),
            InlineKeyboardButton("🧪 炼丹", callback_data="alchemy_menu"),
        ],
        [
            InlineKeyboardButton("🎲 抽卡", callback_data="gacha_menu"),
            InlineKeyboardButton("🏅 成就", callback_data="achievements_menu"),
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
    ]
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
            try:
                story_r = await http_get(f"{SERVER_URL}/api/story/{r['user_id']}", timeout=15)
                pending = ((story_r or {}).get("story") or {}).get("pending_claims") or []
                if pending:
                    first_chapter = pending[0]
                    title = str(first_chapter.get("title") or "主线")
                    detail = str(first_chapter.get("narrative") or first_chapter.get("summary") or "")
                    story_hint = f"\n📜 *{title}*\n{detail}\n"
            except Exception:
                story_hint = ""
            text = f"""
👋 欢迎回来，*{r.get('username', user_name)}*！

🕯️ 修仙之路漫漫，吾将上下而求索。

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
            story_intro = data.get("story_intro") or {}
            intro_title = str(story_intro.get("title") or "")
            intro_text = str(story_intro.get("narrative") or story_intro.get("summary") or "")
            text = f"""
✅ *注册成功！*

👤 账号: {username}
🆔 UID: `{data['user_id']}`
🌟 五行: {element or '未选择'}

开始你的修仙之旅吧！
"""
            if intro_title or intro_text:
                text += f"\n📜 *{intro_title or '序章'}*\n{intro_text}\n"
            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
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
                keyboard = [[InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")]]
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
                preview_block = await _build_breakthrough_preview_block(uid, user_data, strategy="steady")
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
        f"• 境界: Lv.{status.get('rank', 1)}\n"
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
        text += f"• {name} ｜ Lv.{op.get('rank', 1)} ｜ ELO {op.get('pvp_rating', 1000)}\n"
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
        extra = f"Lv.{row['rank']} ｜ 战力{row['power']} ｜ 修为{row['exp']} ｜ 狩猎{row['dy_times']}"
        if mode == "realm_loot":
            extra = f"Lv.{row['rank']} ｜ 秘境分 {row.get('realm_loot', 0)} ｜ 战力{row['power']}"
        elif mode == "alchemy_output":
            extra = f"Lv.{row['rank']} ｜ 炼丹分 {row.get('alchemy_output', 0)} ｜ 修为{row['exp']}"
        elif mode in ("exp", "exp_growth"):
            extra = f"Lv.{row['rank']} ｜ 修为{row['exp']} ｜ 战力{row['power']} ｜ 秘境{row.get('realm_loot', 0)}"
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
        text += f"你当前境界为 Lv.{rank}，达到 Lv.{int(next_unlock_rank)} 后可解锁转化目标。\n"
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
            text += f"你当前境界为 Lv.{rank}，达到 Lv.{int(next_unlock_rank)} 后可解锁目标。\n"
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
            text += f"• {row.get('name')} ｜ Lv.{row.get('min_rank', 1)} ｜ 已获得 {row.get('obtained', 0)} 次\n"
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
        text += f"{status} {ach.get('name')} ({ach.get('progress')}/{ach.get('goal')})\n"
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


def _format_shop_intro(*, rank: int = 1) -> str:
    stage = get_progression_stage_theme(rank)
    return (
        "🏪 *商店*\n\n"
        f"当前阶段：{stage.get('label')} - {stage.get('theme')}\n"
        f"阶段重点：{stage.get('focus')}\n"
        f"货币分工：{get_currency_role('copper')}\n"
        f"中品灵石定位：{get_currency_role('gold')}\n\n"
    )


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
    text += f"当前货架：*{_shop_category_label(category)}*\n\n"
    if not items:
        return text + "当前分类暂无商品。"

    for item in items[:10]:
        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("name") or _item_display_name(item_id) or item_id or "未知物品")
        price = int(item.get("price", item.get("actual_price", 0)) or 0)
        usage = _item_usage_desc(item_id)
        focus = item.get("focus")
        stage_hint = item.get("stage_hint")
        tag = item.get("tag")
        remaining_limit = item.get("remaining_limit")
        min_rank = int(item.get("min_rank", 1) or 1)
        currency_name = "下品灵石" if item.get("currency") == "copper" else "中品灵石"
        text += f"• *{item_name}* - {price} {currency_name}\n"
        if tag and tag != "常驻货架":
            text += f"  货架: {tag}\n"
        if usage:
            text += f"  用途: {usage}\n"
        if focus:
            text += f"  路线: {focus}\n"
        if stage_hint:
            text += f"  阶段: {stage_hint}\n"
        if min_rank > 1:
            text += f"  要求: Lv.{min_rank}\n"
        if remaining_limit is not None:
            text += f"  限购剩余: {remaining_limit}\n"
    return text


def _build_shop_keyboard(category: str, items: list[dict]):
    keyboard = []
    for item in items[:10]:
        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("name") or _item_display_name(item_id) or item_id or "未知物品")
        price = int(item.get("price", item.get("actual_price", 0)) or 0)
        currency = item.get("currency", "copper")
        currency_name = "下品灵石" if currency == "copper" else "中品灵石"
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
    if not copper_result.get("success") and not gold_result.get("success"):
        return "❌ 获取商店失败", [[InlineKeyboardButton("🔙 返回", callback_data="main_menu")]]
    items = (copper_result.get("items", []) if copper_result.get("success") else []) + (
        gold_result.get("items", []) if gold_result.get("success") else []
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
    keyboard.append([InlineKeyboardButton("🎒 背包", callback_data="bag")])
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
    from core.services.breakthrough_pity import bonus as pity_bonus, get_hard_pity_threshold

    current_rank = int(user_data.get("rank", 1) or 1)
    current_realm = get_realm_by_id(current_rank) or {"name": "当前境界"}
    next_realm = get_next_realm(current_rank)
    if not next_realm:
        return "你已站上当前世界的修行尽头。"

    cost = calculate_breakthrough_cost(current_rank)
    pity = int(user_data.get("breakthrough_pity", 0) or 0)
    threshold = get_hard_pity_threshold(current_rank)
    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    rate_parts = [f"基础成功率 {int(base_rate * 100)}%"]
    shown_rate = base_rate
    fire_bonus = _breakthrough_fire_bonus()
    steady_bonus = _breakthrough_steady_bonus()
    if user_data.get("element") == "火":
        shown_rate = min(1.0, shown_rate + fire_bonus)
        rate_parts.append(f"火灵根 +{int(fire_bonus * 100)}%")
    strategy = (strategy or "normal").strip().lower()
    strategy_name = {
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
        need = _breakthrough_protect_material_need(current_rank)
        extra_cost_text = f"额外消耗: 灵石 x{need}"
        rate_parts.append("护脉: 失败不进虚弱")
    elif strategy == "desperate":
        extra_cost_text = "额外效果: 成功额外奖励，失败惩罚更重"
    pity_rate = pity_bonus(pity)
    if pity_rate > 0:
        shown_rate = min(1.0, shown_rate + pity_rate)
        rate_parts.append(f"心魔值加成 +{int(pity_rate * 100)}%")

    return (
        f"⚡ *渡劫预告*\n"
        f"策略: *{strategy_name}*\n"
        f"你将从 *{current_realm['name']}* 冲击 *{next_realm['name']}*。\n"
        f"消耗: {cost:,} 下品灵石\n"
        f"额外消耗: {_breakthrough_stamina_cost()} 点精力\n"
        f"{extra_cost_text}\n"
        f"预计成功率: *{int(shown_rate * 100)}%*\n"
        f"保底进度: {pity}/{threshold}\n"
        f"加成构成: {' ｜ '.join(rate_parts)}\n"
        "这不是普通操作，而是一次值得期待的破境尝试。"
    )


def _build_breakthrough_strategy_notes(user_data: dict) -> str:
    from core.services.breakthrough_pity import bonus as pity_bonus

    current_rank = int(user_data.get("rank", 1) or 1)
    next_realm = get_next_realm(current_rank) or {}
    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    fire_bonus = _breakthrough_fire_bonus()
    steady_bonus = _breakthrough_steady_bonus()
    if user_data.get("element") == "火":
        base_rate = min(1.0, base_rate + fire_bonus)
    pity = int(user_data.get("breakthrough_pity", 0) or 0)
    base_rate = min(1.0, base_rate + pity_bonus(pity))
    protect_need = _breakthrough_protect_material_need(current_rank)
    steady_rate = min(1.0, base_rate + steady_bonus)
    protect_rate = base_rate
    desperate_rate = base_rate
    return (
        f"稳妥突破：消耗下品灵石 + 突破丹 x1，成功率约 *{int(steady_rate * 100)}%*，失败损失减半\n"
        f"护脉突破：消耗下品灵石 + 灵石 x{protect_need}，成功率约 *{int(protect_rate * 100)}%*，失败不进虚弱\n"
        f"生死突破：只消耗下品灵石，成功率约 *{int(desperate_rate * 100)}%*，成功有额外奖励，失败惩罚更重"
    )


async def _build_breakthrough_preview_block(uid: str, user_data: dict, *, strategy: str = "steady") -> str:
    """优先使用服务端预览，避免 Bot 本地规则与结算规则漂移。"""
    try:
        data = await http_get(
            f"{SERVER_URL}/api/breakthrough/preview/{uid}",
            params={"strategy": strategy},
            timeout=15,
        )
        if data.get("success"):
            preview = data.get("preview", {}) or {}
            preview_text = str(preview.get("preview_text", "") or "").strip()
            notes = str(preview.get("strategy_notes", "") or "").strip()
            blocks = [b for b in (preview_text, notes) if b]
            if blocks:
                return "\n\n".join(blocks)
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

    async def _safe_answer(text=None, show_alert=False):
        try:
            if text is None:
                await query.answer()
            else:
                await query.answer(text, show_alert=show_alert)
        except Exception as e:
            # Ignore stale/invalid callback answers to avoid aborting the whole handler.
            logger.warning(f"callback answer skipped: {e}")

    panel_owner = _get_panel_owner(context, query.message)
    if panel_owner and panel_owner != user_id:
        await _safe_answer("这不是你的操作面板", show_alert=True)
        return
    await _safe_answer()

    async def _safe_edit(text: str, reply_markup=None, parse_mode=None):
        """Prefer editing the original message; fall back gracefully if Markdown parsing fails."""
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            try:
                await query.edit_message_text(text, reply_markup=reply_markup)
            except Exception:
                try:
                    sent = await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
                    if reply_markup is not None:
                        _bind_panel_owner(context, sent, user_id)
                except Exception:
                    sent = await query.message.reply_text(text, reply_markup=reply_markup)
                    if reply_markup is not None:
                        _bind_panel_owner(context, sent, user_id)

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
    
    data = query.data
    
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
                sect_cfg = _sect_text_cfg()
                if info.get("success"):
                    sect = info.get("sect", {})
                    header = "🏛️ *我的别院*" if sect.get("membership_kind") == "branch" else "🏛️ *我的宗门*"
                    buff_rate = float(sect_cfg["branch_buff_rate"]) if sect.get("membership_kind") == "branch" else 1.0
                    branch_line = f"归属别院: {(sect.get('branch') or {}).get('display_name')}\n" if sect.get("membership_kind") == "branch" else ""
                    text = (
                        f"{header}\n\n"
                        f"名称: {sect.get('name')}\n"
                        f"{branch_line}"
                        f"等级: {sect.get('level', 1)}\n"
                        f"人数上限: {int(sect.get('max_members', sect_cfg['base_max_members']) or sect_cfg['base_max_members'])}\n"
                        f"Buff: 修炼+{int(float(sect.get('cultivation_buff_pct', 10) or 0) * buff_rate)}% ｜ 属性+{int(float(sect.get('stat_buff_pct', 5) or 0) * buff_rate)}% ｜ 战斗收益+{int(float(sect.get('battle_reward_buff_pct', 10) or 0) * buff_rate)}%\n"
                        f"附属别院: {len(sect.get('branches', []) or [])}/{int(sect_cfg['branch_max'])}\n"
                        f"资金: {sect.get('fund_copper', 0)}下品灵石 / {sect.get('fund_gold', 0)}中品灵石\n"
                        f"战绩: {sect.get('war_wins', 0)}胜 {sect.get('war_losses', 0)}负\n\n"
                        "使用命令：\n"
                        f"• 创建消耗：{int(sect_cfg['create_copper'])} 下品灵石 + {int(sect_cfg['create_gold'])} 中品灵石\n"
                        f"• 别院申请：{int(sect_cfg['branch_create_copper'])} 下品灵石 + {int(sect_cfg['branch_create_gold'])} 中品灵石，需宗主同意\n"
                        f"• 每个别院最多 {int(sect_cfg['branch_max_members'])} 人\n"
                        "• `/sect list` 查看宗门列表\n"
                        "• `/sect branch_join <别院ID>`\n"
                        "• `/sect donate <下品灵石> [中品灵石]` 捐献\n"
                        "• `/sect branch_apply <名称> [描述]`\n"
                        "• `/sect branch_approve <申请ID>`\n"
                        "• `/sect branch_reject <申请ID>`\n"
                        "• `/sect quests` 查看宗门任务\n"
                        "• `/sect war <宗门ID>` 发起战争\n"
                        "• `/sect leave` 退出宗门\n"
                    )
                else:
                    text = (
                        "🏛️ *宗门系统*\n\n"
                        "你当前未加入宗门。\n\n"
                        "使用命令：\n"
                        f"• 创建消耗：{int(sect_cfg['create_copper'])} 下品灵石 + {int(sect_cfg['create_gold'])} 中品灵石\n"
                        f"• 宗门人数上限：{int(sect_cfg['base_max_members'])} 人\n"
                        f"• 宗门Buff：修炼+{int(float(sect_cfg['default_cultivation_buff_pct']))}% ｜ 属性+{int(float(sect_cfg['default_stat_buff_pct']))}% ｜ 战斗收益+{int(float(sect_cfg['default_battle_reward_buff_pct']))}%\n"
                        f"• 每个宗门最多 {int(sect_cfg['branch_max'])} 个附属别院\n"
                        f"• 每个别院最多 {int(sect_cfg['branch_max_members'])} 人\n"
                        f"• 别院成员享受宗门 Buff 的 {int(round(float(sect_cfg['branch_buff_rate']) * 100))}%\n"
                        "• `/sect create <名称> [描述]`\n"
                        "• `/sect join <宗门ID>`\n"
                        "• `/sect list` 查看宗门列表\n"
                    )
                keyboard = []
                if not info.get("success"):
                    keyboard.append([InlineKeyboardButton("🏛️ 创建宗门", callback_data="sect_create_prompt")])
                keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await _safe_edit("❌ 未找到账号，请先注册或稍后重试", reply_markup=get_main_menu_keyboard())
        except Exception as e:
            logger.error(f"sect menu error: {e}")
            await _safe_edit("❌ 宗门面板加载失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return

    if data == "sect_create_prompt":
        try:
            prompt = await query.message.reply_text(
                "请输入宗门名称与描述（回复此消息）：\n示例：风霜宗 坚许风霜罢了",
                reply_markup=ForceReply(selective=True),
            )
            _set_pending_action(context, action="sect_create", prompt_message=prompt, user_id=user_id)
        except Exception as e:
            logger.error(f"sect create prompt error: {e}")
            await _safe_edit("❌ 无法发起创建流程，请稍后重试", reply_markup=get_main_menu_keyboard())
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
                text = "✅ 领取成功" if result.get("success") else f"❌ {result.get('message', '失败')}"
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
                keyboard = [[InlineKeyboardButton("⏹️ 结束修炼", callback_data="cultivate_end")]]
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
    if data in ("breakthrough_start", "breakthrough_pill", "breakthrough_steady", "breakthrough_protect", "breakthrough_desperate"):
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
                }.get(data, "normal")
                use_pill = strategy == "steady"
                result = await http_post(
                    f"{SERVER_URL}/api/breakthrough",
                    json={"user_id": uid, "use_pill": use_pill, "strategy": strategy},
                    timeout=15,
                )
                if result.get("success"):
                    text = (
                        f"🔥 *突破成功！*\n\n"
                        f"{result.get('event_title', '你成功破境了')}\n"
                        f"{result.get('event_flavor', '')}\n\n"
                        f"{result.get('message', '')}\n"
                        f"新境界: *{result.get('new_realm', '')}*\n"
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
        parts = data.split("_")
        if len(parts) >= 3:
            currency = parts[1]
            item_id = "_".join(parts[2:])
            try:
                r = await http_get(
                    f"{SERVER_URL}/api/user/lookup",
                    params={"platform": "telegram", "platform_id": user_id},
                    timeout=15,
                )
                if r.get("success"):
                    uid = r["user_id"]
                    result = await http_post(
                        f"{SERVER_URL}/api/shop/buy",
                        json={"user_id": uid, "item_id": item_id, "quantity": 1, "currency": currency},
                        timeout=15,
                    )
                    if result.get("success"):
                        await _safe_edit(
                            f"✅ {result.get('message', '购买成功！')}",
                            reply_markup=get_main_menu_keyboard()
                        )
                    else:
                        await _safe_edit(
                            f"❌ {result.get('message', '购买失败')}",
                            reply_markup=get_main_menu_keyboard()
                        )
            except Exception as e:
                logger.error(f"buy callback error: {e}")
                await _safe_edit("❌ 购买失败，请稍后重试", reply_markup=get_main_menu_keyboard())
        return
    
    # 背包（带分页）
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
                stat_r = await http_get(f"{SERVER_URL}/api/stat/{uid}", timeout=15)
                if result.get("success"):
                    items = result.get("items", [])
                    if not items:
                        await _safe_edit(
                            "🎒 背包空空如也~",
                            reply_markup=get_main_menu_keyboard()
                        )
                        return

                    ITEMS_PER_PAGE = 8
                    total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
                    page = max(0, min(page, total_pages - 1))
                    page_items = items[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

                    text = f"🎒 *我的背包* (第{page + 1}/{total_pages}页)\n\n"
                    keyboard = []

                    for i in page_items:
                        itype = i.get("item_type", "")
                        if itype in ("weapon", "armor", "accessory"):
                            enhance = i.get("enhance_level", 0) or 0
                            bonuses = []
                            if i.get("attack_bonus"):
                                bonuses.append(f"攻+{i['attack_bonus']}")
                            if i.get("defense_bonus"):
                                bonuses.append(f"防+{i['defense_bonus']}")
                            if i.get("hp_bonus"):
                                bonuses.append(f"HP+{i['hp_bonus']}")
                            bonus_str = f" ({', '.join(bonuses)})" if bonuses else ""
                            text += f"  {i['item_name']}{bonus_str}\n"
                            affix_text = _equipment_affix_text(i)
                            if affix_text:
                                text += f"    词条: {affix_text}\n"
                            row_btns = [InlineKeyboardButton(f"装备 {i['item_name']}", callback_data=f"equip_{i['id']}")]
                            if enhance < 10:
                                row_btns.append(InlineKeyboardButton(f"强化", callback_data=f"enhance_{i['id']}"))
                            row_btns.append(InlineKeyboardButton("分解", callback_data=f"decompose_{i['id']}"))
                            keyboard.append(row_btns)
                        elif itype == "pill":
                            text += f"  {i['item_name']} x{i.get('quantity', 1)}\n"
                            keyboard.append([InlineKeyboardButton(
                                f"使用 {i['item_name']}", callback_data=f"use_{i['item_id']}"
                            )])
                        else:
                            text += f"  {i['item_name']} x{i.get('quantity', 1)}\n"

                    # Pagination buttons
                    nav = []
                    if page > 0:
                        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"bag_{page - 1}"))
                    if page < total_pages - 1:
                        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"bag_{page + 1}"))
                    if nav:
                        keyboard.append(nav)

                    keyboard.append([InlineKeyboardButton("👕 已装备", callback_data="equipped_view")])
                    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
                    await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"bag callback error: {e}")
            await _safe_edit("❌ 背包面板出错，请重试", reply_markup=get_main_menu_keyboard())
        return

    # 装备物品（从背包）
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
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"equip callback error: {e}")
            await _safe_edit(
                "❌ 装备失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
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
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"decompose callback error: {e}")
            await _safe_edit(
                "❌ 分解失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]),
            )
        return

    # 使用丹药（从背包）
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
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(f"{icon} {msg}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"use item callback error: {e}")
            await _safe_edit(
                "❌ 使用物品失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
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

                text = "👕 *已装备*\n\n"
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

                keyboard.append([InlineKeyboardButton("🎒 背包", callback_data="bag")])
                keyboard.append([InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")])
                await _safe_edit(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"equipped view error: {e}")
            await _safe_edit(
                "❌ 已装备面板加载失败，请稍后重试",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
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
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
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
                    [InlineKeyboardButton("🎒 背包", callback_data="bag")],
                    [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")],
                ]
                await _safe_edit(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"enhance callback error: {e}")
            await _safe_edit("❌ 强化失败，请稍后重试", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎒 背包", callback_data="bag")], [InlineKeyboardButton("🔙 主菜单", callback_data="main_menu")]]))
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
            [InlineKeyboardButton("🎒 返回背包", callback_data="bag")],
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
    """/bag 背包命令"""
    uid = context.user_data["uid"]

    try:
        data = await http_get(f"{SERVER_URL}/api/items/{uid}", timeout=15)

        if data.get("success"):
            items = data.get("items", [])

            if not items:
                await update.message.reply_text(
                    "🎒 背包空空如也~",
                    reply_markup=get_main_menu_keyboard()
                )
                return

            ITEMS_PER_PAGE = 8
            total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
            page_items = items[:ITEMS_PER_PAGE]

            text = f"🎒 *我的背包* (第1/{total_pages}页)\n\n"
            keyboard = []

            for i in page_items:
                itype = i.get("item_type", "")
                if itype in ("weapon", "armor", "accessory"):
                    bonuses = []
                    if i.get("attack_bonus"):
                        bonuses.append(f"攻+{i['attack_bonus']}")
                    if i.get("defense_bonus"):
                        bonuses.append(f"防+{i['defense_bonus']}")
                    if i.get("hp_bonus"):
                        bonuses.append(f"HP+{i['hp_bonus']}")
                    bonus_str = f" ({', '.join(bonuses)})" if bonuses else ""
                    text += f"  {i['item_name']}{bonus_str}\n"
                    affix_text = _equipment_affix_text(i)
                    if affix_text:
                        text += f"    词条: {affix_text}\n"
                    row_btns = [InlineKeyboardButton(f"装备 {i['item_name']}", callback_data=f"equip_{i['id']}")]
                    enhance = i.get("enhance_level", 0) or 0
                    if enhance < 10:
                        row_btns.append(InlineKeyboardButton(f"强化", callback_data=f"enhance_{i['id']}"))
                    row_btns.append(InlineKeyboardButton("分解", callback_data=f"decompose_{i['id']}"))
                    keyboard.append(row_btns)
                elif itype == "pill":
                    usage = _item_usage_desc(i.get("item_id"))
                    text += f"  {i['item_name']} x{i.get('quantity', 1)}\n"
                    if usage:
                        text += f"    用途: {usage}\n"
                    keyboard.append([InlineKeyboardButton(
                        f"使用 {i['item_name']}", callback_data=f"use_{i['item_id']}"
                    )])
                else:
                    usage = _item_usage_desc(i.get("item_id"))
                    text += f"  {i['item_name']} x{i.get('quantity', 1)}\n"
                    if usage:
                        text += f"    用途: {usage}\n"

            if total_pages > 1:
                keyboard.append([InlineKeyboardButton("➡️ 下一页", callback_data="bag_1")])
            keyboard.append([InlineKeyboardButton("👕 已装备", callback_data="equipped_view")])
            keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])

            await _reply_with_owned_panel(
                update,
                context,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ 获取背包失败")

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
                text += f"▸ *{realm['name']}* ｜ 需求 Lv.{realm['min_rank']}\n"
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
                    f"• {row.get('name')} ｜ ID {row.get('sect_id')} ｜ Lv.{row.get('level',1)} ｜ "
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
    port = app_config.core_server_port
    SERVER_URL = f"http://127.0.0.1:{port}"

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
    app.add_handler(CommandHandler(["xian_sign", "xian_signin", "sign", "signin"], signin_cmd, filters=_chat_filter))
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
    app.add_handler(CommandHandler(["xian_convert", "convert"], convert_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_gacha", "gacha"], gacha_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_achievements", "xian_ach", "achievements", "ach"], achievements_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_codex", "codex"], codex_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_events", "events"], events_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_bounty", "bounty"], bounty_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_worldboss", "xian_boss", "worldboss", "boss"], worldboss_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_guide", "xian_realms", "guide", "realms"], guide_cmd, filters=_chat_filter))
    app.add_handler(CommandHandler(["xian_version", "version"], version_cmd, filters=_chat_filter))
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
        BotCommand("xian_sign", "每日签到"),
        BotCommand("xian_shop", "商店"),
        BotCommand("xian_bag", "背包"),
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
        BotCommand("xian_gacha", "抽奖"),
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

