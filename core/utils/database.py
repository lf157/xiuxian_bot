"""
数据库工具函数
"""

import os
import psycopg2.extras
import shutil
import logging
from datetime import datetime, timedelta
from core.database.connection import DatabaseError, fetch_one, execute

logger = logging.getLogger("database.utils")


def generate_universal_uid() -> str:
    """
    生成唯一用户ID（使用 schema_meta 序列计数器）。
    原子操作，线程安全。
    """
    from core.database.connection import get_db

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # 原子递增并返回新值
        cur.execute(
            "UPDATE schema_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'next_uid'"
        )
        cur.execute(
            "SELECT value FROM schema_meta WHERE key = 'next_uid'"
        )
        row = cur.fetchone()
        conn.commit()

        if row:
            return str(row["value"])

        # 如果计数器不存在（首次运行或迁移前），回退到旧逻辑并初始化计数器
        cur.execute(
            "SELECT MAX(CAST(user_id AS INTEGER)) as max_id FROM users WHERE user_id ~ '^[0-9]+'"
        )
        row = cur.fetchone()
        next_uid = (row["max_id"] + 1) if row and row["max_id"] else 1000001
        cur.execute(
            "INSERT INTO schema_meta(key, value) VALUES('next_uid', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(next_uid + 1),),  # 存储的是"下一个要用的"
        )
        conn.commit()
        return str(next_uid)

    except Exception as e:
        conn.rollback()
        logger.error(f"Error generating UID: {e}")
        raise DatabaseError(f"UID generation failed: {e}")
    finally:
        cur.close()


def backup_database_native(backup_path: str = None) -> str:
    """
    使用 pg_dump 进行数据库备份。
    """
    import subprocess
    from core.config import config as app_config

    if backup_path is None:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")
        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backups", date_str, "db"
        )
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"xiuxian_{date_str}_{time_str}.sql")

    dsn = app_config.db_dsn
    try:
        result = subprocess.run(
            ["pg_dump", dsn, "-f", backup_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error(f"pg_dump failed: {result.stderr}")
            raise RuntimeError(f"pg_dump failed: {result.stderr}")
        logger.info(f"Database backup saved to {backup_path}")
    except FileNotFoundError:
        logger.warning("pg_dump not found, falling back to JSON backup")
        return backup_users_data() or backup_path
    except Exception as e:
        logger.error(f"Database backup failed: {e}")
        raise

    return backup_path


def verify_backup(backup_path: str) -> bool:
    """验证备份文件存在且非空"""
    try:
        if not os.path.exists(backup_path):
            logger.error(f"Backup file not found: {backup_path}")
            return False
        size = os.path.getsize(backup_path)
        if size == 0:
            logger.error(f"Backup file is empty: {backup_path}")
            return False
        logger.info(f"Backup verification passed: {backup_path} ({size} bytes)")
        return True
    except Exception as e:
        logger.error(f"Backup verification error: {e}")
        return False


def restore_from_backup(backup_path: str) -> bool:
    """
    从 pg_dump 备份恢复数据库。
    警告：此操作将覆盖当前数据库！
    """
    import subprocess
    from core.config import config as app_config
    from core.database.connection import close_db

    if not os.path.exists(backup_path):
        logger.error(f"Backup file not found: {backup_path}")
        return False

    if not verify_backup(backup_path):
        logger.error("Backup verification failed, aborting restore")
        return False

    close_db()

    dsn = app_config.db_dsn
    try:
        result = subprocess.run(
            ["psql", dsn, "-f", backup_path],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.error(f"psql restore failed: {result.stderr}")
            return False
        logger.info(f"Database restored from {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return False


def cleanup_old_backups(max_days: int = 30) -> int:
    """清理超过 max_days 天的旧备份"""
    backup_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backups"
    )
    if not os.path.exists(backup_root):
        return 0

    cutoff = datetime.now() - timedelta(days=max_days)
    removed = 0

    for entry in os.listdir(backup_root):
        try:
            entry_date = datetime.strptime(entry, "%Y-%m-%d")
            if entry_date < cutoff:
                full_path = os.path.join(backup_root, entry)
                shutil.rmtree(full_path)
                removed += 1
                logger.info(f"Removed old backup: {full_path}")
        except ValueError:
            continue

    return removed


def backup_users_data():
    """备份用户数据（JSON格式）"""
    try:
        import json
        from core.database.connection import fetch_all
        users_data = fetch_all("SELECT * FROM users")

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")
        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backups", date_str, "users"
        )
        os.makedirs(backup_dir, exist_ok=True)

        backup_file = os.path.join(backup_dir, f"{date_str}-{time_str}.json")
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(users_data, f, indent=4, ensure_ascii=False, default=str)

        logger.info(f"Users backup saved to {backup_file}")
        return backup_file
    except Exception as e:
        logger.error(f"Error during backup: {e}")
        return None


def backup_items_data():
    """备份物品数据（JSON格式）"""
    try:
        import json
        from core.database.connection import fetch_all
        items_data = fetch_all("SELECT * FROM items")

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")
        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backups", date_str, "items"
        )
        os.makedirs(backup_dir, exist_ok=True)

        backup_file = os.path.join(backup_dir, f"{date_str}-{time_str}.json")
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(items_data, f, indent=4, ensure_ascii=False, default=str)

        logger.info(f"Items backup saved to {backup_file}")
        return backup_file
    except Exception as e:
        logger.error(f"Error during items backup: {e}")
        return None
