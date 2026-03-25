"""
core/database/migrations.py
版本化数据库迁移系统 + 幂等性辅助。
"""

import json
import time
import psycopg2.extras
import logging
from typing import Optional, Dict, Any, List, Tuple

from core.database.connection import get_db, fetch_one, execute

logger = logging.getLogger("Migrations")


# ── 迁移字典：版本号 -> (升级函数, 回滚函数, 描述) ──
MIGRATIONS: Dict[int, Dict[str, Any]] = {
    2: {
        "description": "添加索引和 PRAGMA 优化",
        "up": "_migrate_v2_up",
        "down": "_migrate_v2_down",
    },
    3: {
        "description": "添加数据完整性约束（表重建，需离线执行）",
        "up": "_migrate_v3_up",
        "down": "_migrate_v3_down",
    },
    4: {
        "description": "UID 序列计数器",
        "up": "_migrate_v4_up",
        "down": "_migrate_v4_down",
    },
    5: {
        "description": "技能学习唯一约束与历史去重",
        "up": "_migrate_v5_up",
        "down": "_migrate_v5_down",
    },
    6: {
        "description": "移除 weekly_progress 表（周常系统下线）",
        "up": "_migrate_v6_up",
        "down": "_migrate_v6_down",
    },
}


def get_current_version() -> int:
    """获取当前数据库版本"""
    try:
        row = fetch_one("SELECT value FROM schema_meta WHERE key='schema_version'")
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def set_version(version: int) -> None:
    """设置数据库版本"""
    execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def run_migrations(target_version: int = None) -> List[str]:
    """
    执行迁移到目标版本。
    target_version=None 时迁移到最新版本。
    返回已执行的迁移描述列表。
    """
    current = get_current_version()
    if target_version is None:
        target_version = max(MIGRATIONS.keys()) if MIGRATIONS else current

    executed = []
    conn = get_db()

    if target_version > current:
        # 升级
        for v in sorted(MIGRATIONS.keys()):
            if v <= current:
                continue
            if v > target_version:
                break

            migration = MIGRATIONS[v]
            logger.info(f"Running migration v{v}: {migration['description']}")

            try:
                up_func = globals()[migration["up"]]
                up_func(conn)
                set_version(v)
                executed.append(f"v{current}->v{v}: {migration['description']}")
                logger.info(f"Migration v{v} completed successfully")
            except Exception as e:
                logger.error(f"Migration v{v} failed: {e}")
                conn.rollback()
                raise RuntimeError(f"Migration v{v} failed: {e}")

    elif target_version < current:
        # 回滚
        for v in sorted(MIGRATIONS.keys(), reverse=True):
            if v > current:
                continue
            if v <= target_version:
                break

            migration = MIGRATIONS[v]
            logger.info(f"Rolling back migration v{v}: {migration['description']}")

            try:
                down_func = globals()[migration["down"]]
                down_func(conn)
                set_version(v - 1)
                executed.append(f"v{v}->v{v-1}: rollback {migration['description']}")
                logger.info(f"Rollback v{v} completed")
            except Exception as e:
                logger.error(f"Rollback v{v} failed: {e}")
                conn.rollback()
                raise RuntimeError(f"Rollback v{v} failed: {e}")

    return executed


# ── 迁移函数实现 ──

def _migrate_v2_up(conn: object) -> None:
    """v1->v2: 添加索引"""
    cur = conn.cursor()
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id) WHERE telegram_id IS NOT NULL AND telegram_id != ''",
        "CREATE INDEX IF NOT EXISTS idx_items_user_id ON items(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_user_item_type ON items(user_id, item_id, item_type)",
        "CREATE INDEX IF NOT EXISTS idx_user_skills_user_id ON user_skills(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_quests_user_date ON user_quests(user_id, assigned_date)",
        "CREATE INDEX IF NOT EXISTS idx_timings_user_id ON timings(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_battle_logs_user_ts ON battle_logs(user_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_breakthrough_logs_user_id ON breakthrough_logs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_request_dedup_created ON request_dedup(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_codex_monsters_user ON codex_monsters(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_codex_items_user ON codex_items(user_id)",
    ]
    for sql in indexes:
        cur.execute(sql)
    conn.commit()


def _migrate_v2_down(conn: object) -> None:
    """v2->v1: 移除索引"""
    cur = conn.cursor()
    cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx_%'")
    for row in cur.fetchall():
        cur.execute(f"DROP INDEX IF EXISTS {row[0]}")
    conn.commit()


def _migrate_v3_up(conn: object) -> None:
    """v2->v3: 数据完整性约束（此处仅标记版本，实际表重建需离线执行）"""
    conn.commit()
    logger.warning("v3 migration: table rebuild must be done offline. See FIX_database.md section 9.")


def _migrate_v3_down(conn: object) -> None:
    """v3->v2: 回滚约束（无法自动回滚表结构）"""
    logger.warning("v3 rollback: cannot automatically remove constraints. Manual intervention required.")
    conn.commit()


def _migrate_v4_up(conn: object) -> None:
    """v3->v4: UID 序列计数器"""
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(CAST(user_id AS INTEGER)) as max_id FROM users WHERE user_id ~ '^[0-9]+'"
    )
    row = cur.fetchone()
    max_id = row[0] if row and row[0] else 1000000
    cur.execute(
        "INSERT INTO schema_meta(key, value) VALUES('next_uid', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(max_id + 1),),
    )
    conn.commit()


def _migrate_v4_down(conn: object) -> None:
    """v4->v3: 移除 UID 计数器"""
    cur = conn.cursor()
    cur.execute("DELETE FROM schema_meta WHERE key = 'next_uid'")
    cur.close()
    conn.commit()


def _migrate_v5_up(conn: object) -> None:
    """v4->v5: 为 user_skills 补齐唯一约束并清理重复数据"""
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM user_skills
        WHERE id NOT IN (
            SELECT MIN(id) FROM user_skills GROUP BY user_id, skill_id
        )
        """
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_skills_user_skill ON user_skills(user_id, skill_id)"
    )
    conn.commit()


def _migrate_v5_down(conn: object) -> None:
    """v5->v4: 回滚技能唯一索引"""
    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_user_skills_user_skill")
    conn.commit()


def _migrate_v6_up(conn: object) -> None:
    """v5->v6: 移除周常进度表与索引"""
    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_weekly_progress_user")
    cur.execute("DROP TABLE IF EXISTS user_weekly_progress")
    conn.commit()


def _migrate_v6_down(conn: object) -> None:
    """v6->v5: 恢复周常进度表（若需要回滚）"""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_weekly_progress (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            week_id TEXT,
            points INTEGER DEFAULT 0,
            claimed_3 INTEGER DEFAULT 0,
            claimed_5 INTEGER DEFAULT 0,
            UNIQUE(user_id, week_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_weekly_progress_user ON user_weekly_progress(user_id)")
    conn.commit()


# ── 幂等性辅助（保留原有功能）──

def ensure_idempotency_tables() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'request_dedup'"
    )
    exists = cur.fetchone()
    if exists:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'request_dedup' ORDER BY ordinal_position")
        info = cur.fetchall()
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'request_dedup'::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
        """)
        pk_cols = [r[0] if isinstance(r, (tuple, list)) else r.get("attname", r) for r in cur.fetchall()]
        if pk_cols != ["request_id", "user_id", "action"]:
            cur.execute("ALTER TABLE request_dedup RENAME TO request_dedup_legacy")
            cur.execute(
                """CREATE TABLE request_dedup (
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER,
                    response_json TEXT,
                    PRIMARY KEY(request_id, user_id, action)
                )"""
            )
            cur.execute(
                """
                INSERT INTO request_dedup(request_id, user_id, action, created_at, response_json)
                SELECT
                    request_id,
                    COALESCE(NULLIF(user_id, ''), '__legacy_user_' || rowid),
                    COALESCE(NULLIF(action, ''), '__legacy_action'),
                    created_at,
                    response_json
                FROM request_dedup_legacy
                WHERE request_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )
            cur.execute("DROP TABLE request_dedup_legacy")
            conn.commit()
            return

    cur.execute(
        """CREATE TABLE IF NOT EXISTS request_dedup (
            request_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at INTEGER,
            response_json TEXT,
            PRIMARY KEY(request_id, user_id, action)
        )"""
    )
    conn.commit()


def get_cached_response(
    request_id: str,
    *,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if user_id is None or action is None:
        return None
    row = fetch_one(
        "SELECT response_json FROM request_dedup WHERE request_id = %s AND user_id = %s AND action = %s",
        (request_id, user_id, action),
    )
    if not row:
        return None
    try:
        return json.loads(row["response_json"])
    except Exception:
        return None


def reserve_request(
    request_id: str,
    *,
    user_id: str,
    action: str,
    now: Optional[int] = None,
    stale_after_seconds: int = 30,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Reserve a request_id to prevent duplicate settlement.
    Returns:
      ("cached", response) if completed response exists,
      ("in_progress", None) if another worker is processing,
      ("reserved", None) if caller should proceed.
    """
    now_ts = int(time.time()) if now is None else int(now)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO request_dedup(request_id, user_id, action, created_at, response_json)
           VALUES(%s,%s,%s,%s,NULL)
           ON CONFLICT DO NOTHING""",
        (request_id, user_id, action, now_ts),
    )
    inserted = int(cur.rowcount or 0) == 1
    conn.commit()
    if inserted:
        return "reserved", None

    row = fetch_one(
        "SELECT created_at, response_json FROM request_dedup WHERE request_id = %s AND user_id = %s AND action = %s",
        (request_id, user_id, action),
    )
    if not row:
        return "reserved", None
    payload = row.get("response_json")
    if payload:
        try:
            return "cached", json.loads(payload)
        except Exception:
            return "cached", None
    created_at = int(row.get("created_at", 0) or 0)
    if created_at and now_ts - created_at <= max(5, int(stale_after_seconds or 30)):
        return "in_progress", None
    # stale reservation, take over with CAS guard to avoid double-claim race.
    cur.execute(
        """UPDATE request_dedup
           SET created_at = %s, response_json = NULL
           WHERE request_id = %s AND user_id = %s AND action = %s
             AND created_at = %s
             AND response_json IS NULL""",
        (now_ts, request_id, user_id, action, created_at),
    )
    taken_over = int(cur.rowcount or 0) == 1
    conn.commit()
    if taken_over:
        return "reserved", None

    # Another worker won the race; re-check final state.
    latest = fetch_one(
        "SELECT response_json FROM request_dedup WHERE request_id = %s AND user_id = %s AND action = %s",
        (request_id, user_id, action),
    )
    if not latest:
        return "reserved", None
    latest_payload = latest.get("response_json")
    if latest_payload:
        try:
            return "cached", json.loads(latest_payload)
        except Exception:
            return "cached", None
    return "in_progress", None


def save_response(request_id: str, user_id: str, action: str, response: Dict[str, Any]) -> None:
    payload = json.dumps(response, ensure_ascii=False)
    execute(
        """INSERT INTO request_dedup(request_id, user_id, action, created_at, response_json)
           VALUES(%s,%s,%s,%s,%s)
           ON CONFLICT(request_id, user_id, action)
           DO UPDATE SET created_at = excluded.created_at, response_json = excluded.response_json""",
        (request_id, user_id, action, int(time.time()), payload),
    )


# ── 清理任务 ──

def cleanup_expired_dedup(max_age_days: int = 7) -> int:
    """
    清理超过 max_age_days 天的 request_dedup 记录。
    返回删除的行数。
    """
    cutoff = int(time.time()) - (max_age_days * 86400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM request_dedup WHERE created_at < %s",
        (cutoff,),
    )
    deleted = cur.rowcount
    conn.commit()
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} expired request_dedup records (older than {max_age_days} days)")
    return deleted
