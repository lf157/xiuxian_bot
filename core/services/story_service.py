"""Mainline story progression service."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from core.database.connection import db_transaction, fetch_all, fetch_one, get_user_by_id
from core.game.story import list_chapters
from core.services.metrics_service import log_economy_ledger, log_event

_ACTION_COUNTER_COLUMN: Dict[str, str] = {
    "signin": "signin_count",
    "cultivate_end": "cultivate_count",
    "hunt_victory": "hunt_victory_count",
    "secret_realm_victory": "secret_realm_count",
    "breakthrough_success": "breakthrough_success_count",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_json_loads(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ensure_story_tables_tx(cur: object) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_state (
            user_id TEXT PRIMARY KEY,
            next_chapter_order INTEGER DEFAULT 1,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_counters (
            user_id TEXT PRIMARY KEY,
            signin_count INTEGER DEFAULT 0,
            cultivate_count INTEGER DEFAULT 0,
            hunt_victory_count INTEGER DEFAULT 0,
            secret_realm_count INTEGER DEFAULT 0,
            breakthrough_success_count INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_unlocks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            narrative TEXT DEFAULT '',
            reward_json TEXT DEFAULT '{}',
            trigger_event TEXT,
            unlocked_at INTEGER NOT NULL,
            claimed INTEGER DEFAULT 0,
            claimed_at INTEGER DEFAULT 0,
            UNIQUE(user_id, chapter_id)
        )
        """
    )


def _ensure_story_rows_tx(cur: object, user_id: str, now: int) -> None:
    cur.execute(
        """
        INSERT INTO user_story_state (user_id, next_chapter_order, updated_at)
        VALUES (%s, 1, %s)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, now),
    )
    cur.execute(
        """
        INSERT INTO user_story_counters (
            user_id, signin_count, cultivate_count, hunt_victory_count, secret_realm_count, breakthrough_success_count, updated_at
        )
        VALUES (%s, 0, 0, 0, 0, 0, %s)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, now),
    )


def _chapter_ready(chapter: Dict[str, Any], counters: Dict[str, Any], user: Dict[str, Any]) -> bool:
    req = chapter.get("requirements", {}) or {}
    rank_need = _safe_int(req.get("rank", 0), 0)
    if rank_need > 0 and _safe_int(user.get("rank", 1), 1) < rank_need:
        return False
    for key in (
        "signin_count",
        "cultivate_count",
        "hunt_victory_count",
        "secret_realm_count",
        "breakthrough_success_count",
    ):
        need = _safe_int(req.get(key, 0), 0)
        if need > 0 and _safe_int(counters.get(key, 0), 0) < need:
            return False
    return True


def _compose_unlock_narrative(
    chapter: Dict[str, Any],
    *,
    action: str,
    user: Dict[str, Any],
    counters: Dict[str, Any],
) -> str:
    chapter_id = str(chapter.get("id") or "")
    if chapter_id.startswith("prologue"):
        return (
            "天地初分，三界并立。恒道守序，逆道破局，衍道化生。"
            "你将以凡人之身踏入修途，在宗门、秘境与天劫之间走出自己的道。"
        )
    return (
        f"{chapter.get('title', 'New Chapter')}: {chapter.get('summary', '')} "
        f"(trigger={action}, rank={_safe_int(user.get('rank', 1), 1)}, "
        f"cultivate={_safe_int(counters.get('cultivate_count', 0), 0)}, "
        f"hunt={_safe_int(counters.get('hunt_victory_count', 0), 0)})"
    )


def _ensure_story_rows(user_id: str) -> None:
    now = int(time.time())
    with db_transaction() as cur:
        _ensure_story_tables_tx(cur)
        _ensure_story_rows_tx(cur, user_id, now)


def track_story_action(
    user_id: str,
    action: str,
    *,
    amount: int = 1,
) -> List[Dict[str, Any]]:
    user = get_user_by_id(user_id)
    if not user:
        return []

    now = int(time.time())
    chapters = list_chapters()
    counter_col = _ACTION_COUNTER_COLUMN.get(str(action or "").strip())
    unlocked_updates: List[Dict[str, Any]] = []

    with db_transaction() as cur:
        _ensure_story_tables_tx(cur)
        _ensure_story_rows_tx(cur, user_id, now)

        delta = _safe_int(amount, 1)
        if counter_col and delta > 0:
            cur.execute(
                f"UPDATE user_story_counters SET {counter_col} = {counter_col} + %s, updated_at = %s WHERE user_id = %s",
                (delta, now, user_id),
            )

        cur.execute("SELECT * FROM user_story_counters WHERE user_id = %s", (user_id,))
        counters_row = cur.fetchone()
        counters = dict(counters_row) if counters_row else {}

        cur.execute("SELECT next_chapter_order FROM user_story_state WHERE user_id = %s", (user_id,))
        state_row = cur.fetchone()
        next_order = _safe_int((state_row or {}).get("next_chapter_order", 1), 1)

        while 1 <= next_order <= len(chapters):
            chapter = chapters[next_order - 1]
            if not _chapter_ready(chapter, counters, user):
                break

            narrative = _compose_unlock_narrative(
                chapter,
                action=action,
                user=user,
                counters=counters,
            )
            rewards = chapter.get("rewards", {}) or {}
            cur.execute(
                """
                INSERT INTO user_story_unlocks (
                    user_id, chapter_id, chapter_order, title, summary, narrative,
                    reward_json, trigger_event, unlocked_at, claimed, claimed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0)
                ON CONFLICT(user_id, chapter_id) DO NOTHING
                """,
                (
                    user_id,
                    chapter.get("id"),
                    _safe_int(chapter.get("order", next_order), next_order),
                    chapter.get("title", ""),
                    chapter.get("summary", ""),
                    narrative,
                    json.dumps(rewards, ensure_ascii=False),
                    action,
                    now,
                ),
            )
            inserted = int(cur.rowcount or 0) > 0

            next_order += 1
            cur.execute(
                "UPDATE user_story_state SET next_chapter_order = %s, updated_at = %s WHERE user_id = %s",
                (next_order, now, user_id),
            )

            if inserted:
                unlocked_updates.append(
                    {
                        "chapter_id": chapter.get("id"),
                        "chapter_order": _safe_int(chapter.get("order", 0), 0),
                        "title": chapter.get("title", ""),
                        "summary": chapter.get("summary", ""),
                        "rewards": rewards,
                    }
                )

    if unlocked_updates:
        log_event(
            "story_unlock",
            user_id=user_id,
            success=True,
            rank=_safe_int(user.get("rank", 1), 1),
            meta={"action": action, "chapters": [u.get("chapter_id") for u in unlocked_updates]},
        )
    return unlocked_updates


def get_story_status(user_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "User not found"}, 404

    track_story_action(user_id, "status_check", amount=0)
    _ensure_story_rows(user_id)
    chapters = list_chapters()
    state = fetch_one(
        "SELECT next_chapter_order, updated_at FROM user_story_state WHERE user_id = %s",
        (user_id,),
    ) or {}
    counters = fetch_one("SELECT * FROM user_story_counters WHERE user_id = %s", (user_id,)) or {}
    unlock_rows = fetch_all(
        """
        SELECT chapter_id, chapter_order, title, summary, narrative, reward_json, trigger_event,
               unlocked_at, claimed, claimed_at
        FROM user_story_unlocks
        WHERE user_id = %s
        ORDER BY chapter_order ASC
        """,
        (user_id,),
    )
    unlock_map = {str(row.get("chapter_id")): row for row in unlock_rows}

    chapter_views: List[Dict[str, Any]] = []
    claimed_count = 0
    unlocked_count = 0
    for chapter in chapters:
        cid = str(chapter.get("id"))
        unlocked = unlock_map.get(cid)
        is_unlocked = unlocked is not None
        is_claimed = bool(_safe_int((unlocked or {}).get("claimed", 0), 0))
        if is_unlocked:
            unlocked_count += 1
        if is_claimed:
            claimed_count += 1
        chapter_views.append(
            {
                "id": cid,
                "order": _safe_int(chapter.get("order", 0), 0),
                "title": chapter.get("title", ""),
                "summary": chapter.get("summary", ""),
                "requirements": chapter.get("requirements", {}) or {},
                "rewards": chapter.get("rewards", {}) or {},
                "unlocked": is_unlocked,
                "claimed": is_claimed,
                "unlocked_at": _safe_int((unlocked or {}).get("unlocked_at", 0), 0),
                "claimed_at": _safe_int((unlocked or {}).get("claimed_at", 0), 0),
                "trigger_event": (unlocked or {}).get("trigger_event"),
                "narrative": (unlocked or {}).get("narrative"),
            }
        )

    pending_claims = [
        {
            "chapter_id": row.get("chapter_id"),
            "chapter_order": _safe_int(row.get("chapter_order", 0), 0),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "narrative": row.get("narrative"),
            "rewards": _safe_json_loads(row.get("reward_json")),
            "unlocked_at": _safe_int(row.get("unlocked_at", 0), 0),
        }
        for row in unlock_rows
        if _safe_int(row.get("claimed", 0), 0) == 0
    ]

    return {
        "success": True,
        "story": {
            "next_chapter_order": _safe_int(state.get("next_chapter_order", 1), 1),
            "updated_at": _safe_int(state.get("updated_at", 0), 0),
            "unlocked_count": unlocked_count,
            "claimed_count": claimed_count,
            "total_chapters": len(chapters),
            "pending_claim_count": len(pending_claims),
            "counters": {
                "signin_count": _safe_int(counters.get("signin_count", 0), 0),
                "cultivate_count": _safe_int(counters.get("cultivate_count", 0), 0),
                "hunt_victory_count": _safe_int(counters.get("hunt_victory_count", 0), 0),
                "secret_realm_count": _safe_int(counters.get("secret_realm_count", 0), 0),
                "breakthrough_success_count": _safe_int(counters.get("breakthrough_success_count", 0), 0),
            },
            "pending_claims": pending_claims,
            "chapters": chapter_views,
        },
    }, 200


def claim_story_chapter(user_id: str, chapter_id: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "User not found"}, 404

    _ensure_story_rows(user_id)
    now = int(time.time())

    claimed_row: Dict[str, Any] | None = None
    rewards: Dict[str, Any] = {}
    with db_transaction() as cur:
        if chapter_id:
            cur.execute(
                """
                SELECT id, chapter_id, chapter_order, title, summary, narrative, reward_json, claimed
                FROM user_story_unlocks
                WHERE user_id = %s AND chapter_id = %s
                LIMIT 1
                """,
                (user_id, chapter_id),
            )
        else:
            cur.execute(
                """
                SELECT id, chapter_id, chapter_order, title, summary, narrative, reward_json, claimed
                FROM user_story_unlocks
                WHERE user_id = %s AND claimed = 0
                ORDER BY chapter_order ASC
                LIMIT 1
                """,
                (user_id,),
            )
        row = cur.fetchone()
        if not row:
            return {"success": False, "code": "NO_PENDING", "message": "No claimable story chapter"}, 404
        claimed_row = dict(row)
        if _safe_int(claimed_row.get("claimed", 0), 0) == 1:
            return {"success": False, "code": "ALREADY", "message": "Story chapter already claimed"}, 400

        cur.execute(
            "UPDATE user_story_unlocks SET claimed = 1, claimed_at = %s WHERE id = %s AND claimed = 0",
            (now, _safe_int(claimed_row.get("id", 0), 0)),
        )
        if int(cur.rowcount or 0) == 0:
            return {"success": False, "code": "ALREADY", "message": "Story chapter already claimed"}, 400

        rewards = _safe_json_loads(claimed_row.get("reward_json"))
        delta_copper = _safe_int(rewards.get("copper", 0), 0)
        delta_exp = _safe_int(rewards.get("exp", 0), 0)
        delta_gold = _safe_int(rewards.get("gold", 0), 0)
        cur.execute(
            "UPDATE users SET copper = copper + %s, exp = exp + %s, gold = gold + %s WHERE user_id = %s",
            (delta_copper, delta_exp, delta_gold, user_id),
        )

    delta_copper = _safe_int(rewards.get("copper", 0), 0)
    delta_exp = _safe_int(rewards.get("exp", 0), 0)
    delta_gold = _safe_int(rewards.get("gold", 0), 0)
    log_event(
        "story_claim",
        user_id=user_id,
        success=True,
        rank=_safe_int(user.get("rank", 1), 1),
        meta={"chapter_id": claimed_row.get("chapter_id")},
    )
    log_economy_ledger(
        user_id=user_id,
        module="story",
        action="story_claim",
        delta_copper=delta_copper,
        delta_gold=delta_gold,
        delta_exp=delta_exp,
        success=True,
        rank=_safe_int(user.get("rank", 1), 1),
        meta={"chapter_id": claimed_row.get("chapter_id")},
    )
    return {
        "success": True,
        "message": "Story chapter reward claimed",
        "chapter": {
            "chapter_id": claimed_row.get("chapter_id"),
            "chapter_order": _safe_int(claimed_row.get("chapter_order", 0), 0),
            "title": claimed_row.get("title"),
            "summary": claimed_row.get("summary"),
            "narrative": claimed_row.get("narrative"),
        },
        "rewards": rewards,
    }, 200
