"""
数据库连接和操作

线程安全：使用 psycopg2.pool.ThreadedConnectionPool + threading.local 缓存。
事务管理：db_transaction() 上下文管理器确保原子性。
"""

import logging
import threading
import functools
import time
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras
import psycopg2.errors

logger = logging.getLogger("Database")

from core.constants import (
    DEFAULT_STAMINA_MAX,
    DEFAULT_STAMINA_REGEN_SECONDS,
    DEFAULT_VITALS_REGEN_SECONDS,
    DEFAULT_VITALS_REGEN_PCT,
)

# ── 连接池 + 线程本地缓存 ──
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_local = threading.local()


class DatabaseError(Exception):
    pass


def _normalize_query_placeholders(query: str) -> str:
    """
    Backward compatibility for legacy SQLite-style placeholders.
    Convert qmark placeholders (`?`) to psycopg2 style (`%s`) outside quoted strings.
    """
    if "?" not in query:
        return query

    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    size = len(query)

    while i < size:
        ch = query[i]
        if ch == "'" and not in_double:
            result.append(ch)
            if in_single and i + 1 < size and query[i + 1] == "'":
                # Escaped single quote inside string literal: ''.
                result.append("'")
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            result.append("%s")
        else:
            result.append(ch)
        i += 1

    return "".join(result)


class _CompatCursor:
    def __init__(self, cursor: "psycopg2.extensions.cursor"):
        self._cursor = cursor

    def execute(self, query: str, params: tuple | list | None = None):
        if params is None:
            params = ()
        return self._cursor.execute(_normalize_query_placeholders(query), params)

    def executemany(self, query: str, seq_of_params):
        return self._cursor.executemany(_normalize_query_placeholders(query), seq_of_params)

    def __getattr__(self, item):
        return getattr(self._cursor, item)


def connect_db() -> object:
    """
    初始化 PostgreSQL 连接池（仅在启动时调用一次）。
    """
    global _pool
    from core.config import config
    dsn = config.db_dsn
    min_conn = config.db_pool_min
    max_conn = config.db_pool_size
    _pool = psycopg2.pool.ThreadedConnectionPool(min_conn, max_conn, dsn)
    conn = _pool.getconn()
    conn.autocommit = False
    _local.conn = conn
    logger.info(f"Connected to PostgreSQL (pool {min_conn}-{max_conn})")
    return conn


def get_db() -> object:
    """
    获取当前线程的数据库连接（从连接池）。
    每个线程首次调用时自动获取。
    包含连接健康检查。
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except psycopg2.Error:
            logger.warning("Connection health check failed, reconnecting...")
            try:
                _pool.putconn(conn, close=True)
            except Exception:
                pass
            _local.conn = None

    conn = _pool.getconn()
    conn.autocommit = False
    _local.conn = conn
    logger.debug(f"New PG connection for thread: {threading.current_thread().name}")
    return conn


def close_db() -> None:
    """归还当前线程的连接到池（用于线程清理）"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            _pool.putconn(conn)
        except Exception:
            pass
        _local.conn = None


# ── 事务管理 ──

@contextmanager
def db_transaction(conn=None):
    """
    事务上下文管理器。
    用法:
        with db_transaction() as cur:
            cur.execute("UPDATE ...", (...))
            cur.execute("INSERT ...", (...))
        # 正常退出自动 commit，异常自动 rollback
    """
    if conn is None:
        conn = get_db()
    cur = _CompatCursor(conn.cursor(cursor_factory=psycopg2.extras.DictCursor))
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def transactional(func):
    """
    装饰器方式的事务管理。
    被装饰函数将接收额外的 cur 关键字参数。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with db_transaction() as cur:
            kwargs["cur"] = cur
            return func(*args, **kwargs)
    return wrapper


def execute_in_transaction(queries: list) -> None:
    """
    在单个事务中执行多条 SQL。
    queries: [(sql, params), ...]
    """
    with db_transaction() as cur:
        for sql, params in queries:
            cur.execute(sql, params)


# ── 基础查询接口（保持向后兼容）──

def fetch_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_normalize_query_placeholders(query), params)
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_all(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_normalize_query_placeholders(query), params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def execute(query: str, params: tuple = ()) -> int:
    """执行单条 SQL 并立即 commit。如有 RETURNING 子句则返回首列值。"""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(_normalize_query_placeholders(query), params)
        row_id = 0
        if cur.description is not None:
            row = cur.fetchone()
            if row:
                row_id = row[0]
    conn.commit()
    return row_id or 0


def execute_query(query: str, params: tuple = ()) -> int:
    """Legacy alias kept for routes/services that still import execute_query."""
    return execute(query, params)




# ── 兼容重导出：从拆分后的模块导入，确保旧的 import 路径继续工作 ──

from core.database.schema import (  # noqa: F401
    _ensure_user_platform_columns,
    _ensure_request_dedup_schema,
    ensure_battle_session_table,
    _dedupe_users_for_unique_username,
    _dedupe_users_for_unique_telegram_id,
    _create_indexes,
    create_tables,
)

from core.database.user_repository import (  # noqa: F401
    fetch_schema_version,
    VALID_PLATFORMS,
    get_user_by_platform,
    get_user_by_id,
    get_user_by_username,
    VALID_USER_COLUMNS,
    update_user,
    refresh_user_stamina,
    spend_user_stamina,
    spend_user_stamina_tx,
    refresh_user_vitals,
)

from core.database.game_repository import (  # noqa: F401
    add_item,
    get_user_items,
    get_user_skills,
    learn_skill,
    set_equipped_skill,
    unequip_all_skills,
    unequip_skill,
    has_skill,
    get_item_by_db_id,
    log_battle,
    log_breakthrough,
    get_user_quests,
    upsert_quest,
    claim_quest,
)
