"""
统一配置管理模块 - XiuXianBot

单例模式，全局一次加载。敏感信息从环境变量读取，
业务参数从 config.json 读取。
"""

import os
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger("core.config")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


# ---------- .env 支持 ----------

def _load_dotenv() -> None:
    """尝试从 .env 文件加载环境变量（不依赖 python-dotenv）。"""
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        logger.warning(f"Failed to load .env: {exc}")


# 模块加载时立即尝试读取 .env
_load_dotenv()


# ---------- 配置单例 ----------

class _AppConfig:
    """配置单例类。首次访问时加载，之后缓存。"""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f) or {}
        except FileNotFoundError:
            logger.warning(f"config.json not found at {_CONFIG_PATH}, using defaults")
            self._data = {}
        except Exception as exc:
            logger.error(f"Failed to load config.json: {exc}")
            self._data = {}
        # 兼容：如果 config.json 中仍然存在 tokens 节，也加载它们
        # （向后兼容旧配置格式，优先使用环境变量）
        self._loaded = True

    def reload(self) -> None:
        """强制重新加载配置（运行时热更新用）。"""
        self._loaded = False
        self._ensure_loaded()

    def get(self, key: str, default: Any = None) -> Any:
        """顶层键值获取。"""
        self._ensure_loaded()
        return self._data.get(key, default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """嵌套键值获取，如 get_nested('balance', 'hunt', 'base_exp')。"""
        self._ensure_loaded()
        current = self._data
        for k in keys:
            if isinstance(current, dict):
                current = current.get(k)
            else:
                return default
            if current is None:
                return default
        return current

    @property
    def raw(self) -> Dict[str, Any]:
        """返回完整配置字典（用于兼容旧代码直接访问 dict 的场景）。"""
        self._ensure_loaded()
        return self._data

    # ---------- 便捷属性 ----------

    @property
    def project_root(self) -> str:
        return _PROJECT_ROOT

    @property
    def config_path(self) -> str:
        return _CONFIG_PATH

    # ---- Tokens (环境变量优先，回退到 config.json) ----

    def telegram_token(self) -> str:
        return os.environ.get("XXBOT_TELEGRAM_TOKEN") or \
               os.environ.get("TELEGRAM_BOT_TOKEN") or \
               os.environ.get("BOT_TOKEN") or \
               self.get_nested("tokens", "telegram_token", default="")

    @property
    def internal_api_token(self) -> str:
        """核心服务内部调用令牌。"""
        return os.environ.get("XXBOT_INTERNAL_API_TOKEN") or \
               os.environ.get("INTERNAL_TOKEN") or \
               self.get_nested("core_server", "api_token", default="") or \
               self.telegram_token()

    @property
    def admin_password(self) -> str:
        return os.environ.get("XXBOT_ADMIN_PASSWORD") or \
               self.get_nested("admin_panel", "api_password", default="")

    # ---- 端口 ----

    @property
    def core_server_port(self) -> int:
        return int(self.get_nested("core_server", "port", default=11450))

    @property
    def core_server_host(self) -> str:
        host = os.environ.get("XXBOT_CORE_SERVER_HOST") or \
               os.environ.get("CORE_SERVER_HOST") or \
               self.get_nested("core_server", "host", default="127.0.0.1")
        host = str(host or "").strip()
        return host or "127.0.0.1"

    @property
    def core_server_url(self) -> str:
        raw = os.environ.get("XXBOT_CORE_SERVER_URL") or \
              os.environ.get("CORE_SERVER_URL") or \
              self.get_nested("core_server", "url", default="")
        raw = str(raw or "").strip()
        if raw:
            if not raw.startswith(("http://", "https://")):
                raw = f"http://{raw}"
            parsed = urlparse(raw)
            if parsed.scheme and parsed.netloc:
                return raw.rstrip("/")
        return f"http://{self.core_server_host}:{self.core_server_port}"

    @property
    def miniapp_url(self) -> str:
        raw = os.environ.get("XXBOT_MINIAPP_URL") or \
              self.get_nested("miniapp", "url", default="")
        return str(raw or "").strip()

    @property
    def admin_panel_port(self) -> int:
        return int(self.get_nested("admin_panel", "port", default=11451))

    # ---- 数据库 ----

    @property
    def db_path(self) -> str:
        """兼容旧代码，新代码请使用 db_dsn。"""
        env_path = os.environ.get("XXBOT_DB_PATH")
        if env_path:
            return env_path
        return self.get_nested("db", "sqlite_path", default="data/xiu_xian.db")

    @property
    def db_dsn(self) -> str:
        """PostgreSQL DSN，优先读取环境变量 DATABASE_URL。"""
        url = os.environ.get("DATABASE_URL")
        if url:
            return url
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5432")
        db = os.environ.get("POSTGRES_DB", "xiuxian")
        user = os.environ.get("POSTGRES_USER", "xiuxian")
        password = os.environ.get("POSTGRES_PASSWORD", "xiuxian")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    @property
    def db_pool_min(self) -> int:
        return int(os.environ.get("DB_POOL_MIN", "2"))

    @property
    def db_pool_size(self) -> int:
        return int(os.environ.get("DB_POOL_SIZE", "10"))

    # ---- Redis / FSM ----

    @property
    def redis_enabled(self) -> bool:
        env_value = os.environ.get("XXBOT_REDIS_ENABLED")
        if env_value is not None:
            return _as_bool(env_value, default=True)
        return _as_bool(self.get_nested("redis", "enabled", default=True), default=True)

    @property
    def redis_host(self) -> str:
        env_value = os.environ.get("XXBOT_REDIS_HOST")
        if env_value:
            return str(env_value).strip()
        return str(self.get_nested("redis", "host", default="127.0.0.1") or "127.0.0.1").strip()

    @property
    def redis_port(self) -> int:
        env_value = os.environ.get("XXBOT_REDIS_PORT")
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                pass
        try:
            return int(self.get_nested("redis", "port", default=6379))
        except (TypeError, ValueError):
            return 6379

    @property
    def redis_db(self) -> int:
        env_value = os.environ.get("XXBOT_REDIS_DB")
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                pass
        try:
            return int(self.get_nested("redis", "db", default=0))
        except (TypeError, ValueError):
            return 0

    @property
    def redis_password(self) -> str:
        return str(
            os.environ.get("XXBOT_REDIS_PASSWORD")
            or self.get_nested("redis", "password", default="")
            or ""
        ).strip()

    @property
    def redis_url(self) -> str:
        raw = (
            os.environ.get("XXBOT_REDIS_URL")
            or os.environ.get("REDIS_URL")
            or self.get_nested("redis", "url", default="")
            or ""
        )
        raw = str(raw).strip()
        if raw:
            return raw
        password = self.redis_password
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def redis_fsm_key_prefix(self) -> str:
        env_value = os.environ.get("XXBOT_REDIS_FSM_KEY_PREFIX")
        raw = str(env_value or self.get_nested("redis", "fsm_key_prefix", default="xxbot:fsm:v2:") or "").strip()
        return raw or "xxbot:fsm:v2:"

    @property
    def redis_purge_legacy_fsm_prefixes(self) -> bool:
        env_value = os.environ.get("XXBOT_REDIS_PURGE_LEGACY_FSM_PREFIXES")
        if env_value is not None:
            return _as_bool(env_value, default=False)
        return _as_bool(self.get_nested("redis", "purge_legacy_fsm_prefixes", default=False), default=False)

    # ---- 冷却时间 ----

    @property
    def hunt_cooldown(self) -> int:
        return int(self.get_nested("cooldowns", "hunt", default=30))

    @property
    def secret_realm_cooldown(self) -> int:
        return int(self.get_nested("cooldowns", "secret_realm", default=20))

    @property
    def quest_claim_cooldown(self) -> int:
        return int(self.get_nested("cooldowns", "quest_claim", default=3))

    @property
    def enhance_cooldown(self) -> int:
        return int(self.get_nested("cooldowns", "enhance", default=2))

    # ---- 适配器开关 ----

    def is_adapter_enabled(self, name: str) -> bool:
        return bool(self.get_nested("adapters", name, default=False))

    # ---- Balance ----

    @property
    def balance(self) -> Dict[str, Any]:
        return self.get("balance", {}) or {}


# 全局单例
config = _AppConfig()
