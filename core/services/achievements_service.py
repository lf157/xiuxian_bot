"""Achievements service."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

from core.database.connection import fetch_one, fetch_all, db_transaction, get_user_by_id, get_db
from core.game.achievements import list_achievements, get_achievement, list_achievements_by_stage, get_current_stage_achievements
from core.services.metrics_service import log_event, log_economy_ledger

_ACH_TABLES_READY = False
_ACH_TABLES_LOCK = threading.Lock()


def _ensure_achievement_tables() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_achievements (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            achievement_id TEXT NOT NULL,
            claimed INTEGER DEFAULT 0,
            completed_at INTEGER DEFAULT 0,
            UNIQUE(user_id, achievement_id)
        )
        """
    )
    conn.commit()


def _ensure_achievement_tables_once() -> None:
    global _ACH_TABLES_READY
    if _ACH_TABLES_READY:
        return
    with _ACH_TABLES_LOCK:
        if _ACH_TABLES_READY:
            return
        _ensure_achievement_tables()
        _ACH_TABLES_READY = True


def _progress(user: Dict[str, Any], ach: Dict[str, Any]) -> int:
    goal = int(ach.get("goal", 0) or 0)
    t = ach.get("type")
    if t == "hunt_count":
        return min(int(user.get("dy_times", 0) or 0), goal)
    if t == "rank_reach":
        return min(int(user.get("rank", 1) or 1), goal)
    if t == "signin_streak":
        historical = int(user.get("max_signin_days", 0) or 0)
        current = int(user.get("consecutive_sign_days", 0) or 0)
        return min(max(historical, current), goal)
    if t == "pvp_wins":
        return min(int(user.get("pvp_wins", 0) or 0), goal)
    if t == "skill_count":
        row = fetch_one("SELECT COUNT(1) AS c FROM user_skills WHERE user_id = %s", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    if t == "skill_level":
        row = fetch_one("SELECT MAX(skill_level) AS lvl FROM user_skills WHERE user_id = %s", (user.get("user_id"),))
        return min(int(row.get("lvl", 0) or 0), goal) if row else 0
    if t == "quest_complete":
        row = fetch_one("SELECT COUNT(1) AS c FROM user_quests WHERE user_id = %s AND claimed = 1", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    if t == "breakthrough_success":
        row = fetch_one("SELECT COUNT(1) AS c FROM breakthrough_logs WHERE user_id = %s AND success = 1", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    if t == "secret_realm_count":
        row = fetch_one("SELECT secret_realm_count AS c FROM user_story_counters WHERE user_id = %s", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    if t == "cultivate_count":
        row = fetch_one("SELECT cultivate_count AS c FROM user_story_counters WHERE user_id = %s", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    if t == "forge_count":
        row = fetch_one("SELECT COUNT(1) AS c FROM alchemy_logs WHERE user_id = %s", (user.get("user_id"),))
        return min(int(row.get("c", 0) or 0), goal) if row else 0
    return 0


def _is_claimed(user_id: str, ach_id: str) -> bool:
    row = fetch_one(
        "SELECT 1 AS ok FROM user_achievements WHERE user_id = %s AND achievement_id = %s AND claimed = 1",
        (user_id, ach_id),
    )
    return row is not None


def get_achievements(user_id: str) -> Tuple[Dict[str, Any], int]:
    _ensure_achievement_tables_once()
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    claimed_rows = fetch_all(
        "SELECT achievement_id FROM user_achievements WHERE user_id = %s AND claimed = 1",
        (user_id,),
    )
    claimed_set = {str(row.get("achievement_id") or "") for row in (claimed_rows or [])}
    achievements = []
    for ach in list_achievements():
        prog = _progress(user, ach)
        goal = int(ach.get("goal", 0) or 0)
        achievements.append({
            **ach,
            "progress": prog,
            "completed": prog >= goal,
            "claimed": str(ach.get("id") or "") in claimed_set,
        })
    return {"success": True, "achievements": achievements}, 200


def claim_achievement(user_id: str, achievement_id: str) -> Tuple[Dict[str, Any], int]:
    _ensure_achievement_tables_once()
    user = get_user_by_id(user_id)
    if not user:
        log_event("achievement_claim", user_id=user_id, success=False, reason="USER_NOT_FOUND", meta={"achievement_id": achievement_id})
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    ach = get_achievement(achievement_id)
    if not ach:
        log_event("achievement_claim", user_id=user_id, success=False, reason="INVALID", meta={"achievement_id": achievement_id})
        return {"success": False, "code": "INVALID", "message": "成就不存在"}, 404
    prog = _progress(user, ach)
    if prog < int(ach.get("goal", 0) or 0):
        log_event("achievement_claim", user_id=user_id, success=False, reason="NOT_DONE", meta={"achievement_id": achievement_id})
        return {"success": False, "code": "NOT_DONE", "message": "成就未完成"}, 400
    rewards = ach.get("rewards", {})
    now = int(time.time())
    already_claimed = False
    try:
        with db_transaction() as cur:
            cur.execute(
                """
                INSERT INTO user_achievements (user_id, achievement_id, claimed, completed_at)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT(user_id, achievement_id) DO UPDATE SET
                    claimed = 1,
                    completed_at = excluded.completed_at
                WHERE user_achievements.claimed = 0
                """,
                (user_id, achievement_id, now),
            )
            if int(cur.rowcount or 0) == 0:
                already_claimed = True
            else:
                cur.execute(
                    "UPDATE users SET copper = copper + %s, exp = exp + %s, gold = gold + %s WHERE user_id = %s",
                    (
                        int(rewards.get("copper", 0) or 0),
                        int(rewards.get("exp", 0) or 0),
                        int(rewards.get("gold", 0) or 0),
                        user_id,
                    ),
                )
                if int(cur.rowcount or 0) == 0:
                    raise ValueError("USER_NOT_FOUND")
    except ValueError:
        log_event("achievement_claim", user_id=user_id, success=False, reason="USER_NOT_FOUND", meta={"achievement_id": achievement_id})
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404

    if already_claimed:
        log_event("achievement_claim", user_id=user_id, success=True, reason="CLAIMED", meta={"achievement_id": achievement_id})
        return {
            "success": True,
            "already_claimed": True,
            "message": "已领取过奖励",
            "rewards": rewards,
        }, 200
    log_event(
        "achievement_claim",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"achievement_id": achievement_id},
    )
    log_economy_ledger(
        user_id=user_id,
        module="achievement",
        action="achievement_claim",
        delta_copper=int(rewards.get("copper", 0) or 0),
        delta_gold=int(rewards.get("gold", 0) or 0),
        delta_exp=int(rewards.get("exp", 0) or 0),
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"achievement_id": achievement_id},
    )
    return {"success": True, "message": "领取成功", "rewards": rewards}, 200


def get_achievements_by_stage(user_id: str, stage: str) -> Tuple[Dict[str, Any], int]:
    """Return achievements for a specific cultivation stage, with player progress."""
    _ensure_achievement_tables_once()
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    claimed_rows = fetch_all(
        "SELECT achievement_id FROM user_achievements WHERE user_id = %s AND claimed = 1",
        (user_id,),
    )
    claimed_set = {str(row.get("achievement_id") or "") for row in (claimed_rows or [])}
    achievements = []
    for ach in list_achievements_by_stage(stage):
        prog = _progress(user, ach)
        goal = int(ach.get("goal", 0) or 0)
        achievements.append({
            **ach,
            "progress": prog,
            "completed": prog >= goal,
            "claimed": str(ach.get("id") or "") in claimed_set,
        })
    return {"success": True, "stage": stage, "achievements": achievements}, 200


def get_achievements_current_stage(user_id: str) -> Tuple[Dict[str, Any], int]:
    """Return achievements matching the player's current rank stage."""
    _ensure_achievement_tables_once()
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    rank = int(user.get("rank", 1) or 1)
    claimed_rows = fetch_all(
        "SELECT achievement_id FROM user_achievements WHERE user_id = %s AND claimed = 1",
        (user_id,),
    )
    claimed_set = {str(row.get("achievement_id") or "") for row in (claimed_rows or [])}
    achievements = []
    for ach in get_current_stage_achievements(rank):
        prog = _progress(user, ach)
        goal = int(ach.get("goal", 0) or 0)
        achievements.append({
            **ach,
            "progress": prog,
            "completed": prog >= goal,
            "claimed": str(ach.get("id") or "") in claimed_set,
        })
    stage_name = achievements[0].get("stage_name", "") if achievements else ""
    stage = achievements[0].get("stage", "") if achievements else ""
    return {
        "success": True,
        "rank": rank,
        "stage": stage,
        "stage_name": stage_name,
        "achievements": achievements,
    }, 200
