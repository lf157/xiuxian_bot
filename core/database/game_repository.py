"""
游戏数据仓库 - 物品、技能、战斗日志、任务 CRUD

从 connection.py 中拆分而来。
"""

import logging
import time
from typing import Optional, Dict, Any, List

import psycopg2.extras

from core.database.connection import (
    get_db, fetch_one, fetch_all, execute, db_transaction,
)

logger = logging.getLogger("Database")


def add_item(user_id: str, item: Dict[str, Any]) -> int:
    """添加物品"""
    row_id = execute(
        """
        INSERT INTO items (user_id, item_id, item_name, item_type, quality, quantity, level,
                          attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                          first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            item.get("item_id"),
            item.get("item_name"),
            item.get("item_type"),
            item.get("quality", "common"),
            item.get("quantity", 1),
            item.get("level", 1),
            item.get("attack_bonus", 0),
            item.get("defense_bonus", 0),
            item.get("hp_bonus", 0),
            item.get("mp_bonus", 0),
            item.get("first_round_reduction_pct", 0),
            item.get("crit_heal_pct", 0),
            item.get("element_damage_pct", 0),
            item.get("low_hp_shield_pct", 0),
        )
    )
    try:
        from core.services.codex_service import ensure_item
        ensure_item(user_id, item.get("item_id"), item.get("quantity", 1))
    except Exception:
        pass
    return row_id


def get_user_items(user_id: str) -> List[Dict[str, Any]]:
    """获取用户所有物品，合并同类道具数量，关联物品定义补充中文名和描述"""
    rows = fetch_all("SELECT * FROM items WHERE user_id = %s ORDER BY id DESC", (user_id,))
    try:
        from core.game.items import get_item_by_id
        for row in rows:
            defn = get_item_by_id(row.get("item_id", ""))
            if defn:
                row.setdefault("name", defn.get("name", ""))
                row.setdefault("desc", defn.get("desc", defn.get("description", "")))
                row.setdefault("rarity", defn.get("rarity", "common"))
                row.setdefault("item_type", defn.get("type", ""))
    except Exception:
        pass
    # 合并同 item_id + item_type 的条目，数量求和，保留第一条的元数据
    merged: dict = {}
    for row in rows:
        key = f"{row.get('item_id', '')}:{row.get('item_type', '')}"
        if key in merged:
            merged[key]["quantity"] = int(merged[key].get("quantity", 0) or 0) + int(row.get("quantity", 0) or 0)
        else:
            merged[key] = dict(row)
    return list(merged.values())


def get_user_skills(user_id: str) -> List[Dict[str, Any]]:
    return fetch_all("SELECT * FROM user_skills WHERE user_id = %s ORDER BY id ASC", (user_id,))


def learn_skill(user_id: str, skill_id: str, equipped: int = 0) -> int:
    import time
    return execute(
        "INSERT INTO user_skills (user_id, skill_id, equipped, learned_at, skill_level, mastery_exp, last_used_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user_id, skill_id, equipped, int(time.time()), 1, 0, 0),
    )


def set_equipped_skill(user_id: str, skill_id: str, *, max_active_equipped: int = 2) -> None:
    """装备技能。主动技能最多装备指定数量，被动技能不受该限制。"""
    with db_transaction() as cur:
        cur.execute("SELECT skill_id FROM user_skills WHERE user_id = %s AND skill_id = %s", (user_id, skill_id))
        row = cur.fetchone()
        if not row:
            return
        try:
            from core.game.skills import get_skill
            skill = get_skill(skill_id)
        except Exception:
            skill = None
        if skill and skill.get("type") == "active":
            cur.execute(
                "SELECT id, skill_id FROM user_skills WHERE user_id = %s AND equipped = 1 ORDER BY learned_at ASC, id ASC",
                (user_id,),
            )
            equipped_rows = cur.fetchall()
            active_equipped = []
            for eq in equipped_rows:
                sk = get_skill(eq["skill_id"])
                if sk and sk.get("type") == "active":
                    active_equipped.append(eq)
            if len(active_equipped) >= max_active_equipped and all(eq["skill_id"] != skill_id for eq in active_equipped):
                oldest = active_equipped[0]
                cur.execute("UPDATE user_skills SET equipped = 0 WHERE id = %s", (oldest["id"],))
            cur.execute("UPDATE user_skills SET equipped = 1 WHERE user_id = %s AND skill_id = %s", (user_id, skill_id))
            return
        cur.execute("UPDATE user_skills SET equipped = 1 WHERE user_id = %s AND skill_id = %s", (user_id, skill_id))


def unequip_all_skills(user_id: str) -> None:
    execute("UPDATE user_skills SET equipped = 0 WHERE user_id = %s", (user_id,))


def unequip_skill(user_id: str, skill_id: str) -> None:
    execute("UPDATE user_skills SET equipped = 0 WHERE user_id = %s AND skill_id = %s", (user_id, skill_id))


def has_skill(user_id: str, skill_id: str) -> bool:
    row = fetch_one("SELECT 1 AS ok FROM user_skills WHERE user_id = %s AND skill_id = %s", (user_id, skill_id))
    return row is not None


def get_item_by_db_id(item_db_id) -> Optional[Dict[str, Any]]:
    """Get item by its database row id"""
    return fetch_one("SELECT * FROM items WHERE id = %s", (item_db_id,))


def log_battle(user_id: str, result: Dict[str, Any]) -> int:
    """记录战斗"""
    import time
    return execute(
        """
        INSERT INTO battle_logs (user_id, monster_id, victory, rounds, exp_gained, copper_gained, gold_gained, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            result.get("monster_id"),
            1 if result.get("victory") else 0,
            result.get("rounds", 0),
            result.get("exp", 0),
            result.get("copper", 0),
            result.get("gold", 0),
            int(time.time()),
        )
    )


def log_breakthrough(user_id: str, from_rank: int, to_rank: int, success: bool, exp_lost: int = 0) -> int:
    """记录突破"""
    import time
    return execute(
        """
        INSERT INTO breakthrough_logs (user_id, from_rank, to_rank, success, exp_lost, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, from_rank, to_rank, 1 if success else 0, exp_lost, int(time.time()))
    )


def get_user_quests(user_id: str, date_str: str) -> List[Dict[str, Any]]:
    """Get quests assigned to user for a given date."""
    return fetch_all(
        "SELECT * FROM user_quests WHERE user_id = %s AND assigned_date = %s",
        (user_id, date_str),
    )


def upsert_quest(user_id: str, quest_id: str, date_str: str, progress: int, goal: int) -> int:
    """Insert or update a quest row for today.

    Keep existing progress/claimed state while allowing goal updates and missing-row backfill.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_quests (user_id, quest_id, progress, goal, claimed, assigned_date)
        VALUES (%s, %s, %s, %s, 0, %s)
        ON CONFLICT(user_id, quest_id, assigned_date) DO UPDATE SET
            goal = excluded.goal,
            progress = GREATEST(user_quests.progress, excluded.progress)
        """,
        (user_id, quest_id, int(progress or 0), int(goal or 1), date_str),
    )
    conn.commit()
    cur_tmp = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur_tmp.execute(
        "SELECT id FROM user_quests WHERE user_id = %s AND quest_id = %s AND assigned_date = %s",
        (user_id, quest_id, date_str),
    )
    row = cur_tmp.fetchone()
    cur_tmp.close()
    return int(row["id"]) if row else 0


def claim_quest(quest_row_id: int) -> None:
    execute("UPDATE user_quests SET claimed = 1 WHERE id = %s", (quest_row_id,))
