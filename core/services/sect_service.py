"""Sect (guild) service layer."""

from __future__ import annotations

import psycopg2.errors
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.config import config
from core.database.connection import fetch_one, fetch_all, db_transaction, get_user_by_id
from core.game.leaderboards import calculate_power
from core.services.metrics_service import log_event, log_economy_ledger
from core.utils.timeutil import today_local, midnight_timestamp


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("sect", key, default=default))
    except Exception:
        return int(default)


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(config.get_nested("sect", key, default=default))
    except Exception:
        return float(default)


SECT_CREATE_COST_COPPER = _cfg_int("create_copper", 5000)
SECT_CREATE_COST_GOLD = _cfg_int("create_gold", 10)
SECT_BASE_MAX_MEMBERS = max(1, _cfg_int("base_max_members", 10))
SECT_LEVEL_EXP_BASE = _cfg_int("level_exp_base", 1000)
SECT_WAR_COOLDOWN = _cfg_int("war_cooldown_seconds", 3600)
SECT_BRANCH_MAX = max(1, _cfg_int("branch_max", 5))
SECT_BRANCH_CREATE_COST_COPPER = max(0, _cfg_int("branch_create_copper", 3000))
SECT_BRANCH_CREATE_COST_GOLD = max(0, _cfg_int("branch_create_gold", 3))
SECT_BRANCH_BUFF_RATE = max(0.0, _cfg_float("branch_buff_rate", 0.9))
SECT_BRANCH_MAX_MEMBERS = max(1, _cfg_int("branch_max_members", 5))
WEAK_DEBUFF_PCT = max(0, min(100, _cfg_int("weak_debuff_pct", 30)))
WEAK_DEBUFF_MULT = 1.0 - WEAK_DEBUFF_PCT / 100.0
SECT_DONATE_EXP_PER_COPPER_DIV = max(1, _cfg_int("donate_exp_per_copper_div", 10))
SECT_DONATE_EXP_PER_GOLD = max(0, _cfg_int("donate_exp_per_gold", 20))
SECT_DONATE_CONTRIBUTION_PER_GOLD = max(0, _cfg_int("donate_contribution_per_gold", 100))
SECT_LEVEL_UP_MEMBER_INCREASE = max(0, _cfg_int("level_up_member_increase", 2))
SECT_MAX_MEMBERS_CAP = max(1, _cfg_int("max_members_cap", SECT_BASE_MAX_MEMBERS))

def _load_quest_defs() -> Dict[str, Dict[str, Any]]:
    defaults: Dict[str, Dict[str, Any]] = {
        "donate": {"target": 5000, "reward_copper": 1000, "reward_exp": 500},
        "hunt": {"target": 12, "reward_copper": 600, "reward_exp": 300},
        "secret_realm": {"target": 6, "reward_copper": 800, "reward_exp": 400},
    }
    raw = config.get_nested("sect", "quest_defs", default={}) or {}
    if not isinstance(raw, dict):
        return defaults
    merged: Dict[str, Dict[str, Any]] = {}
    for key, base in defaults.items():
        row = raw.get(key) if isinstance(raw.get(key), dict) else {}
        merged[key] = {
            "target": max(1, int(row.get("target", base["target"]) or base["target"])),
            "reward_copper": max(0, int(row.get("reward_copper", base["reward_copper"]) or base["reward_copper"])),
            "reward_exp": max(0, int(row.get("reward_exp", base["reward_exp"]) or base["reward_exp"])),
        }
    return merged


SECT_QUEST_DEFS: Dict[str, Dict[str, Any]] = _load_quest_defs()


def _gen_sect_id() -> str:
    return f"S{uuid.uuid4().hex[:16]}"


def _gen_branch_id() -> str:
    return f"B{uuid.uuid4().hex[:16]}"


def _get_sect_quest_def(quest_type: str) -> Optional[Dict[str, Any]]:
    return SECT_QUEST_DEFS.get(quest_type)


def _ensure_sect_quest_row(
    cur: object,
    *,
    sect_id: str,
    quest_type: str,
    assigned_date: str,
) -> Optional[int]:
    qdef = _get_sect_quest_def(quest_type)
    if not qdef:
        return None
    cur.execute(
        """INSERT INTO sect_quests
           (sect_id, quest_type, target, progress, reward_copper, reward_exp, assigned_date, completed, claimed)
           VALUES (%s, %s, %s, 0, %s, %s, %s, 0, 0)
           ON CONFLICT(sect_id, quest_type, assigned_date) DO NOTHING""",
        (
            sect_id,
            quest_type,
            int(qdef.get("target", 0) or 0),
            int(qdef.get("reward_copper", 0) or 0),
            int(qdef.get("reward_exp", 0) or 0),
            assigned_date,
        ),
    )
    cur.execute(
        "SELECT id FROM sect_quests WHERE sect_id = %s AND quest_type = %s AND assigned_date = %s",
        (sect_id, quest_type, assigned_date),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def _sect_member_count_tx(cur: object, sect_id: str) -> int:
    cur.execute("SELECT COUNT(1) AS c FROM sect_members WHERE sect_id = %s", (sect_id,))
    direct = cur.fetchone()
    cur.execute(
        """SELECT COUNT(1) AS c
           FROM sect_branch_members bm
           JOIN sect_branches b ON bm.branch_id = b.branch_id
           WHERE b.parent_sect_id = %s""",
        (sect_id,),
    )
    branch = cur.fetchone()
    direct_count = int(direct["c"] if direct else 0) or 0
    branch_count = int(branch["c"] if branch else 0) or 0
    return direct_count + branch_count


def _increment_sect_quest_progress_tx(
    cur: object,
    *,
    sect_id: str,
    user_id: str,
    quest_type: str,
    amount: int,
    assigned_date: str,
    now: int,
) -> None:
    quest_id = _ensure_sect_quest_row(cur, sect_id=sect_id, quest_type=quest_type, assigned_date=assigned_date)
    if not quest_id:
        return
    amt = int(amount or 0)
    if amt <= 0:
        return
    cur.execute(
        """UPDATE sect_quests
           SET progress = progress + %s
           WHERE id = %s""",
        (amt, quest_id),
    )
    cur.execute(
        """UPDATE sect_quests
           SET completed = CASE WHEN progress >= target THEN 1 ELSE completed END
           WHERE id = %s""",
        (quest_id,),
    )
    cur.execute(
        """INSERT INTO sect_quest_claims (quest_id, sect_id, user_id, progress, claimed, claimed_at, updated_at)
           VALUES (%s, %s, %s, %s, 0, 0, %s)
           ON CONFLICT(quest_id, user_id) DO UPDATE SET
               progress = sect_quest_claims.progress + excluded.progress,
               updated_at = excluded.updated_at""",
        (quest_id, sect_id, user_id, amt, now),
    )


def increment_sect_quest_progress(user_id: str, quest_type: str, amount: int = 1) -> None:
    sect = get_user_sect(user_id)
    if not sect:
        return
    now = int(time.time())
    today = today_local()
    with db_transaction() as cur:
        _increment_sect_quest_progress_tx(
            cur,
            sect_id=sect["sect_id"],
            user_id=user_id,
            quest_type=quest_type,
            amount=amount,
            assigned_date=today,
            now=now,
        )


def _get_member_role(sect_id: str, user_id: str) -> Optional[str]:
    row = fetch_one(
        "SELECT role FROM sect_members WHERE sect_id = %s AND user_id = %s",
        (sect_id, user_id),
    )
    return row.get("role") if row else None


def _get_branch_member(branch_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM sect_branch_members WHERE branch_id = %s AND user_id = %s",
        (branch_id, user_id),
    )


def _get_user_branch(user_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """SELECT b.*, m.role AS branch_role, m.joined_at AS branch_joined_at
           FROM sect_branch_members m
           JOIN sect_branches b ON m.branch_id = b.branch_id
           WHERE m.user_id = %s""",
        (user_id,),
    )


def _branch_member_count(branch_id: str) -> int:
    row = fetch_one("SELECT COUNT(1) AS c FROM sect_branch_members WHERE branch_id = %s", (branch_id,))
    return int((row or {}).get("c", 0) or 0)


def _has_any_membership_tx(cur: object, user_id: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM sect_members WHERE user_id = %s
        UNION
        SELECT 1 FROM sect_branch_members WHERE user_id = %s
        LIMIT 1
        """,
        (user_id, user_id),
    )
    row = cur.fetchone()
    return row is not None


def _sect_total_power_tx(cur: object, sect_id: str) -> int:
    cur.execute(
        """
        SELECT DISTINCT u.*
        FROM users u
        WHERE u.user_id IN (
            SELECT user_id FROM sect_members WHERE sect_id = %s
        )
           OR u.user_id IN (
            SELECT bm.user_id
            FROM sect_branch_members bm
            JOIN sect_branches b ON b.branch_id = bm.branch_id
            WHERE b.parent_sect_id = %s
        )
        """,
        (sect_id, sect_id),
    )
    rows = cur.fetchall()
    return int(sum(calculate_power(dict(row)) for row in rows))


def get_user_sect(user_id: str) -> Optional[Dict[str, Any]]:
    row = fetch_one(
        """SELECT s.* FROM sect_members m
           JOIN sects s ON m.sect_id = s.sect_id
           WHERE m.user_id = %s""",
        (user_id,),
    )
    branch = _get_user_branch(user_id)
    if not row:
        if not branch:
            return None
        row = fetch_one("SELECT * FROM sects WHERE sect_id = %s", (branch["parent_sect_id"],))
        if not row:
            return None
    branches = fetch_all(
        "SELECT branch_id, name, display_name, leader_user_id, max_members, created_at FROM sect_branches WHERE parent_sect_id = %s ORDER BY created_at ASC",
        (row["sect_id"],),
    )
    for entry in branches:
        entry["member_count"] = _branch_member_count(entry["branch_id"])
    pending_branch_requests = []
    direct_role = _get_member_role(row["sect_id"], user_id)
    if direct_role in ("leader", "deputy"):
        pending_branch_requests = fetch_all(
            "SELECT id, applicant_user_id, name, cost_copper, cost_gold, created_at FROM sect_branch_requests WHERE parent_sect_id = %s AND status = 'pending' ORDER BY created_at ASC",
            (row["sect_id"],),
        )
    return {
        **row,
        "branches": branches,
        "pending_branch_requests": pending_branch_requests,
        "membership_kind": "branch" if branch else "sect",
        "branch": branch,
        "role": direct_role if direct_role else (branch.get("branch_role") if branch else None),
    }


def get_user_sect_buffs(user_id: str) -> Dict[str, Any]:
    sect = get_user_sect(user_id)
    if not sect:
        return {
            "in_sect": False,
            "sect_name": None,
            "membership_kind": None,
            "branch_name": None,
            "cultivation_pct": 0.0,
            "stat_pct": 0.0,
            "battle_reward_pct": 0.0,
        }
    rate = SECT_BRANCH_BUFF_RATE if sect.get("membership_kind") == "branch" else 1.0
    return {
        "in_sect": True,
        "sect_name": sect.get("name"),
        "membership_kind": sect.get("membership_kind"),
        "branch_name": ((sect.get("branch") or {}).get("display_name") if sect.get("branch") else None),
        "cultivation_pct": float(sect.get("cultivation_buff_pct", 10) or 0.0) * rate,
        "stat_pct": float(sect.get("stat_buff_pct", 5) or 0.0) * rate,
        "battle_reward_pct": float(sect.get("battle_reward_buff_pct", 10) or 0.0) * rate,
    }


def apply_sect_stat_buffs(user: Dict[str, Any]) -> Dict[str, Any]:
    if not user:
        return user
    buffs = get_user_sect_buffs(user.get("user_id"))
    enriched = dict(user)
    if buffs.get("in_sect"):
        mult = 1.0 + float(buffs.get("stat_pct", 0.0)) / 100.0
        enriched["max_hp"] = max(1, int(round(float(user.get("max_hp", 100) or 100) * mult)))
        enriched["hp"] = min(enriched["max_hp"], max(1, int(user.get("hp", enriched["max_hp"]) or enriched["max_hp"])))
        enriched["attack"] = max(1, int(round(float(user.get("attack", 10) or 10) * mult)))
        enriched["defense"] = max(0, int(round(float(user.get("defense", 5) or 5) * mult)))
    now = int(time.time())
    atk_until = int(enriched.get("attack_buff_until", 0) or 0)
    atk_val = int(enriched.get("attack_buff_value", 0) or 0)
    if atk_val > 0 and atk_until > now:
        enriched["attack"] = max(1, int(enriched["attack"]) + atk_val)
    def_until = int(enriched.get("defense_buff_until", 0) or 0)
    def_val = int(enriched.get("defense_buff_value", 0) or 0)
    if def_val > 0 and def_until > now:
        enriched["defense"] = max(0, int(enriched["defense"]) + def_val)

    weak_until = int(enriched.get("weak_until", 0) or 0)
    weak_remaining_seconds = max(0, weak_until - now)
    is_weak = weak_remaining_seconds > 0
    if is_weak:
        # Breakthrough failure debuff: temporary reduction of core combat attributes.
        max_hp = int(enriched.get("max_hp", user.get("max_hp", 100)) or user.get("max_hp", 100) or 100)
        max_mp = int(enriched.get("max_mp", user.get("max_mp", 50)) or user.get("max_mp", 50) or 50)
        hp = int(enriched.get("hp", max_hp) or max_hp)
        mp = int(enriched.get("mp", max_mp) or max_mp)
        attack = int(enriched.get("attack", user.get("attack", 10)) or user.get("attack", 10) or 10)
        defense = int(enriched.get("defense", user.get("defense", 5)) or user.get("defense", 5) or 5)
        crit_rate = float(enriched.get("crit_rate", user.get("crit_rate", 0.05)) or user.get("crit_rate", 0.05) or 0.05)

        debuffed_max_hp = max(1, int(round(max_hp * WEAK_DEBUFF_MULT)))
        debuffed_max_mp = max(1, int(round(max_mp * WEAK_DEBUFF_MULT)))
        debuffed_attack = max(1, int(round(attack * WEAK_DEBUFF_MULT)))
        debuffed_defense = max(0, int(round(defense * WEAK_DEBUFF_MULT)))

        enriched["max_hp"] = debuffed_max_hp
        enriched["max_mp"] = debuffed_max_mp
        enriched["hp"] = min(debuffed_max_hp, max(0, hp))
        enriched["mp"] = min(debuffed_max_mp, max(0, mp))
        enriched["attack"] = debuffed_attack
        enriched["defense"] = debuffed_defense
        enriched["crit_rate"] = max(0.0, crit_rate * WEAK_DEBUFF_MULT)
    enriched["sect_buffs"] = buffs
    enriched["is_weak"] = is_weak
    enriched["weak_remaining_seconds"] = weak_remaining_seconds
    enriched["weak_debuff_pct"] = WEAK_DEBUFF_PCT
    enriched["_weak_debuff_applied"] = is_weak
    enriched["_combat_stats_precomputed"] = True
    if is_weak:
        enriched["weak_effects"] = [
            "不能开始修炼",
            f"HP/MP/攻击/防御/暴击率 -{WEAK_DEBUFF_PCT}%",
        ]
    else:
        enriched["weak_effects"] = []
    return enriched


def list_sects(limit: int = 20, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))
    if keyword:
        rows = fetch_all(
            "SELECT * FROM sects WHERE name LIKE %s ORDER BY level DESC, exp DESC LIMIT %s",
            (f"%{keyword}%", limit),
        )
    else:
        rows = fetch_all("SELECT * FROM sects ORDER BY level DESC, exp DESC LIMIT %s", (limit,))
    results = []
    for row in rows:
        branch_count = fetch_one("SELECT COUNT(1) AS c FROM sect_branches WHERE parent_sect_id = %s", (row["sect_id"],))
        results.append({**row, "branch_count": int((branch_count or {}).get("c", 0) or 0)})
    return results


def get_sect_detail(sect_id: str) -> Optional[Dict[str, Any]]:
    sect = fetch_one("SELECT * FROM sects WHERE sect_id = %s", (sect_id,))
    if not sect:
        return None
    members = fetch_all(
        "SELECT user_id, role, contribution, joined_at FROM sect_members WHERE sect_id = %s ORDER BY contribution DESC",
        (sect_id,),
    )
    branches = fetch_all(
        "SELECT branch_id, name, display_name, leader_user_id, max_members, created_at FROM sect_branches WHERE parent_sect_id = %s ORDER BY created_at ASC",
        (sect_id,),
    )
    for entry in branches:
        entry["member_count"] = _branch_member_count(entry["branch_id"])
    pending_branch_requests = fetch_all(
        "SELECT id, applicant_user_id, name, cost_copper, cost_gold, created_at FROM sect_branch_requests WHERE parent_sect_id = %s AND status = 'pending' ORDER BY created_at ASC",
        (sect_id,),
    )
    return {**sect, "members": members, "branches": branches, "pending_branch_requests": pending_branch_requests}


def create_sect(user_id: str, name: str, description: str = "") -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        log_event("sect_create", user_id=user_id, success=False, reason="USER_NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    if get_user_sect(user_id):
        log_event("sect_create", user_id=user_id, success=False, reason="ALREADY")
        return {"success": False, "code": "ALREADY", "message": "你已加入宗门"}, 400
    if not name or len(name) > 16:
        log_event("sect_create", user_id=user_id, success=False, reason="INVALID_NAME")
        return {"success": False, "code": "INVALID", "message": "宗门名称无效"}, 400

    now = int(time.time())
    for _ in range(3):
        sect_id = _gen_sect_id()
        try:
            with db_transaction() as cur:
                if _has_any_membership_tx(cur, user_id):
                    raise ValueError("ALREADY")
                cur.execute(
                    """UPDATE users
                       SET copper = copper - %s, gold = gold - %s
                       WHERE user_id = %s AND copper >= %s AND gold >= %s""",
                    (
                        SECT_CREATE_COST_COPPER,
                        SECT_CREATE_COST_GOLD,
                        user_id,
                        SECT_CREATE_COST_COPPER,
                        SECT_CREATE_COST_GOLD,
                    ),
                )
                if int(cur.rowcount or 0) == 0:
                    raise ValueError("INSUFFICIENT")
                cur.execute(
                    """INSERT INTO sects
                       (sect_id, name, description, leader_id, level, exp, fund_copper, fund_gold, max_members,
                       war_wins, war_losses, last_war_time, created_at)
                       VALUES (%s, %s, %s, %s, 1, 0, 0, 0, %s, 0, 0, 0, %s)""",
                    (sect_id, name, description or "", user_id, SECT_BASE_MAX_MEMBERS, now),
                )
                cur.execute(
                    "INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at) VALUES (%s, %s, 'leader', 0, %s)",
                    (sect_id, user_id, now),
                )
            log_event(
                "sect_create",
                user_id=user_id,
                success=True,
                rank=int(user.get("rank", 1) or 1),
                meta={"sect_id": sect_id},
            )
            log_economy_ledger(
                user_id=user_id,
                module="sect",
                action="sect_create",
                delta_copper=-SECT_CREATE_COST_COPPER,
                delta_gold=-SECT_CREATE_COST_GOLD,
                success=True,
                rank=int(user.get("rank", 1) or 1),
                meta={"sect_id": sect_id},
            )
            return {"success": True, "sect_id": sect_id, "message": "宗门创建成功"}, 200
        except ValueError as exc:
            reason = str(exc)
            if reason == "ALREADY":
                log_event("sect_create", user_id=user_id, success=False, reason="ALREADY")
                return {"success": False, "code": "ALREADY", "message": "你已加入宗门"}, 400
            log_event("sect_create", user_id=user_id, success=False, reason="INSUFFICIENT")
            return {"success": False, "code": "INSUFFICIENT", "message": "资源不足，无法创建宗门"}, 400
        except psycopg2.errors.UniqueViolation:
            continue
    log_event("sect_create", user_id=user_id, success=False, reason="CONFLICT")
    return {"success": False, "code": "CONFLICT", "message": "宗门创建冲突，请重试"}, 409


def join_sect(user_id: str, sect_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    if get_user_sect(user_id):
        return {"success": False, "code": "ALREADY", "message": "你已加入宗门"}, 400
    sect = fetch_one("SELECT * FROM sects WHERE sect_id = %s", (sect_id,))
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "宗门不存在"}, 404

    member_limit = min(
        int(sect.get("max_members", SECT_BASE_MAX_MEMBERS) or SECT_BASE_MAX_MEMBERS),
        SECT_MAX_MEMBERS_CAP,
    )

    now = int(time.time())
    try:
        with db_transaction() as cur:
            cur.execute(
                """INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at)
                   SELECT %s, %s, 'member', 0, %s
                   WHERE NOT EXISTS (SELECT 1 FROM sect_members WHERE user_id = %s)
                     AND NOT EXISTS (SELECT 1 FROM sect_branch_members WHERE user_id = %s)
                     AND (SELECT COUNT(1) FROM sect_members WHERE sect_id = %s) < %s""",
                (sect_id, user_id, now, user_id, user_id, sect_id, member_limit),
            )
            if int(cur.rowcount or 0) == 0:
                if _has_any_membership_tx(cur, user_id):
                    raise ValueError("ALREADY")
                cur.execute("SELECT COUNT(1) AS c FROM sect_members WHERE sect_id = %s", (sect_id,))
                row = cur.fetchone()
                if row and int(row["c"] or 0) >= member_limit:
                    raise ValueError("FULL")
                raise ValueError("CONFLICT")
    except psycopg2.errors.UniqueViolation:
        return {"success": False, "code": "ALREADY", "message": "你已加入宗门或别院"}, 400
    except ValueError as exc:
        reason = str(exc)
        if reason == "ALREADY":
            return {"success": False, "code": "ALREADY", "message": "你已加入宗门或别院"}, 400
        if reason == "FULL":
            return {"success": False, "code": "FULL", "message": "宗门已满员"}, 400
        return {"success": False, "code": "CONFLICT", "message": "加入请求冲突，请重试"}, 409
    return {"success": True, "message": "加入宗门成功"}, 200


def create_branch_request(user_id: str, name: str, description: str = "") -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if sect.get("membership_kind") != "sect":
        return {"success": False, "code": "FORBIDDEN", "message": "仅主宗直系成员可申请创建别院"}, 403
    if _get_member_role(sect["sect_id"], user_id) is None:
        return {"success": False, "code": "FORBIDDEN", "message": "仅主宗直系成员可申请创建别院"}, 403
    if not name or len(name) > 12:
        return {"success": False, "code": "INVALID", "message": "别院名称无效"}, 400
    base_name = name[:-2] if name.endswith("别院") else name
    branch_name = f"{base_name}别院"
    now = int(time.time())
    try:
        with db_transaction() as cur:
            cur.execute(
                "SELECT COUNT(1) AS c FROM sect_branches WHERE parent_sect_id = %s",
                (sect["sect_id"],),
            )
            current_branch_count = cur.fetchone()
            if int((current_branch_count["c"] if current_branch_count else 0) or 0) >= SECT_BRANCH_MAX:
                raise ValueError("FULL")
            cur.execute(
                "SELECT 1 FROM sect_branches WHERE parent_sect_id = %s AND leader_user_id = %s LIMIT 1",
                (sect["sect_id"], user_id),
            )
            if cur.fetchone():
                raise ValueError("ALREADY")
            cur.execute(
                "SELECT 1 FROM sect_branches WHERE parent_sect_id = %s AND name = %s LIMIT 1",
                (sect["sect_id"], branch_name),
            )
            if cur.fetchone():
                raise ValueError("DUPLICATE")
            cur.execute(
                "SELECT copper, gold FROM users WHERE user_id = %s",
                (user_id,),
            )
            user_row = cur.fetchone()
            if not user_row:
                raise ValueError("NOT_FOUND")
            if int(user_row["copper"] or 0) < SECT_BRANCH_CREATE_COST_COPPER or int(user_row["gold"] or 0) < SECT_BRANCH_CREATE_COST_GOLD:
                raise ValueError("INSUFFICIENT")
            cur.execute(
                """INSERT INTO sect_branch_requests
                   (parent_sect_id, applicant_user_id, name, description, cost_copper, cost_gold, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)""",
                (sect["sect_id"], user_id, branch_name, description or "", SECT_BRANCH_CREATE_COST_COPPER, SECT_BRANCH_CREATE_COST_GOLD, now),
            )
    except psycopg2.errors.UniqueViolation:
        return {"success": False, "code": "PENDING", "message": "你已有待审批的别院申请"}, 400
    except ValueError as exc:
        reason = str(exc)
        if reason == "FULL":
            return {"success": False, "code": "FULL", "message": "本宗附属别院已达上限"}, 400
        if reason == "ALREADY":
            return {"success": False, "code": "ALREADY", "message": "你已担任一个附属别院的院主"}, 400
        if reason == "DUPLICATE":
            return {"success": False, "code": "DUPLICATE", "message": "该别院名称已存在"}, 400
        if reason == "INSUFFICIENT":
            return {"success": False, "code": "INSUFFICIENT", "message": "资源不足，无法申请创建别院"}, 400
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    return {
        "success": True,
        "message": f"已提交 {branch_name} 的创建申请，等待宗主同意",
        "branch_name": branch_name,
        "costs": {"copper": SECT_BRANCH_CREATE_COST_COPPER, "gold": SECT_BRANCH_CREATE_COST_GOLD},
    }, 200


def _leader_sect_or_none(user_id: str) -> Optional[Dict[str, Any]]:
    sect = get_user_sect(user_id)
    if not sect:
        return None
    return sect if _get_member_role(sect["sect_id"], user_id) == "leader" else None


def _manager_sect_or_none(user_id: str) -> Optional[Dict[str, Any]]:
    sect = get_user_sect(user_id)
    if not sect:
        return None
    role = _get_member_role(sect["sect_id"], user_id)
    return sect if role in ("leader", "deputy") else None


def review_branch_request(user_id: str, request_id: int, approve: bool) -> Tuple[Dict[str, Any], int]:
    sect = _manager_sect_or_none(user_id)
    if not sect:
        return {"success": False, "code": "FORBIDDEN", "message": "仅宗主/副宗主可审批附属别院"}, 403
    now = int(time.time())
    if not approve:
        with db_transaction() as cur:
            cur.execute(
                """UPDATE sect_branch_requests
                   SET status = 'rejected', decided_at = %s, decided_by = %s
                   WHERE id = %s AND parent_sect_id = %s AND status = 'pending'""",
                (now, user_id, request_id, sect["sect_id"]),
            )
            if int(cur.rowcount or 0) == 0:
                cur.execute(
                    "SELECT id FROM sect_branch_requests WHERE id = %s AND parent_sect_id = %s",
                    (request_id, sect["sect_id"]),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "code": "NOT_FOUND", "message": "申请不存在"}, 404
                return {"success": False, "code": "DONE", "message": "该申请已处理"}, 400
        return {"success": True, "message": "已拒绝别院申请"}, 200

    req = fetch_one("SELECT * FROM sect_branch_requests WHERE id = %s AND parent_sect_id = %s", (request_id, sect["sect_id"]))
    if not req:
        return {"success": False, "code": "NOT_FOUND", "message": "申请不存在"}, 404
    display_name = f"{req['name']} [{sect['name']}附属]"

    for _ in range(3):
        branch_id = _gen_branch_id()
        try:
            with db_transaction() as cur:
                cur.execute(
                    "SELECT * FROM sect_branch_requests WHERE id = %s AND parent_sect_id = %s",
                    (request_id, sect["sect_id"]),
                )
                req_row = cur.fetchone()
                if not req_row:
                    raise ValueError("NOT_FOUND")
                req_data = dict(req_row)
                if str(req_data["status"]) != "pending":
                    raise ValueError("DONE")

                cur.execute(
                    "SELECT COUNT(1) AS c FROM sect_branches WHERE parent_sect_id = %s",
                    (sect["sect_id"],),
                )
                branch_count = cur.fetchone()
                if int((branch_count["c"] if branch_count else 0) or 0) >= SECT_BRANCH_MAX:
                    raise ValueError("FULL")

                applicant_id = str(req_data["applicant_user_id"])
                cur.execute(
                    "SELECT 1 FROM sect_members WHERE sect_id = %s AND user_id = %s LIMIT 1",
                    (sect["sect_id"], applicant_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("INVALID")
                cur.execute(
                    "SELECT 1 FROM sect_branch_members WHERE user_id = %s LIMIT 1",
                    (applicant_id,),
                )
                if cur.fetchone():
                    raise ValueError("INVALID")
                cur.execute(
                    "SELECT 1 FROM sect_branches WHERE parent_sect_id = %s AND name = %s LIMIT 1",
                    (sect["sect_id"], req_data["name"]),
                )
                if cur.fetchone():
                    raise ValueError("DUPLICATE")

                cost_copper = int(req_data["cost_copper"] or 0)
                cost_gold = int(req_data["cost_gold"] or 0)
                cur.execute(
                    """UPDATE users
                       SET copper = copper - %s, gold = gold - %s
                       WHERE user_id = %s AND copper >= %s AND gold >= %s""",
                    (cost_copper, cost_gold, applicant_id, cost_copper, cost_gold),
                )
                if int(cur.rowcount or 0) == 0:
                    raise ValueError("INSUFFICIENT")

                cur.execute(
                    """INSERT INTO sect_branches
                       (branch_id, parent_sect_id, name, display_name, leader_user_id, max_members, description, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        branch_id,
                        sect["sect_id"],
                        req_data["name"],
                        display_name,
                        applicant_id,
                        SECT_BRANCH_MAX_MEMBERS,
                        req_data.get("description", "") or "",
                        now,
                    ),
                )
                cur.execute(
                    """INSERT INTO sect_branch_members
                       (branch_id, user_id, role, contribution, joined_at)
                       VALUES (%s, %s, 'leader', 0, %s)""",
                    (branch_id, applicant_id, now),
                )
                # 申请人转入别院，避免同时保留在主宗成员表。
                cur.execute(
                    "DELETE FROM sect_members WHERE sect_id = %s AND user_id = %s",
                    (sect["sect_id"], applicant_id),
                )
                cur.execute(
                    """UPDATE sect_branch_requests
                       SET status = 'approved', decided_at = %s, decided_by = %s
                       WHERE id = %s AND parent_sect_id = %s AND status = 'pending'""",
                    (now, user_id, request_id, sect["sect_id"]),
                )
                if int(cur.rowcount or 0) == 0:
                    raise ValueError("DONE")
            log_event(
                "sect_branch_create",
                user_id=applicant_id,
                success=True,
                rank=int((get_user_by_id(applicant_id) or {}).get("rank", 1) or 1),
                meta={"branch_id": branch_id, "sect_id": sect["sect_id"], "approved_by": user_id},
            )
            log_economy_ledger(
                user_id=applicant_id,
                module="sect",
                action="sect_branch_create",
                delta_copper=-cost_copper,
                delta_gold=-cost_gold,
                success=True,
                rank=int((get_user_by_id(applicant_id) or {}).get("rank", 1) or 1),
                meta={"branch_id": branch_id, "sect_id": sect["sect_id"]},
            )
            return {"success": True, "message": f"已批准创建 {display_name}", "branch_id": branch_id, "display_name": display_name}, 200
        except psycopg2.errors.UniqueViolation:
            continue
        except ValueError as exc:
            reason = str(exc)
            if reason == "NOT_FOUND":
                return {"success": False, "code": "NOT_FOUND", "message": "申请不存在"}, 404
            if reason == "DONE":
                return {"success": False, "code": "DONE", "message": "该申请已处理"}, 400
            if reason == "FULL":
                return {"success": False, "code": "FULL", "message": "本宗附属别院已达上限"}, 400
            if reason == "INVALID":
                return {"success": False, "code": "INVALID", "message": "申请人已不在本宗"}, 400
            if reason == "INSUFFICIENT":
                return {"success": False, "code": "INSUFFICIENT", "message": "申请人资源不足，无法批准"}, 400
            if reason == "DUPLICATE":
                return {"success": False, "code": "DUPLICATE", "message": "该别院名称已存在"}, 400
            return {"success": False, "code": "CONFLICT", "message": "别院创建冲突，请重试"}, 409
    return {"success": False, "code": "CONFLICT", "message": "别院创建冲突，请重试"}, 409


def join_branch(user_id: str, branch_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    if get_user_sect(user_id):
        return {"success": False, "code": "ALREADY", "message": "你已加入宗门或别院"}, 400
    branch = fetch_one("SELECT * FROM sect_branches WHERE branch_id = %s", (branch_id,))
    if not branch:
        return {"success": False, "code": "NOT_FOUND", "message": "别院不存在"}, 404
    now = int(time.time())
    max_members = int(branch.get("max_members", SECT_BRANCH_MAX_MEMBERS) or SECT_BRANCH_MAX_MEMBERS)
    try:
        with db_transaction() as cur:
            cur.execute(
                """INSERT INTO sect_branch_members (branch_id, user_id, role, contribution, joined_at)
                   SELECT %s, %s, 'member', 0, %s
                   WHERE NOT EXISTS (SELECT 1 FROM sect_members WHERE user_id = %s)
                     AND NOT EXISTS (SELECT 1 FROM sect_branch_members WHERE user_id = %s)
                     AND (SELECT COUNT(1) FROM sect_branch_members WHERE branch_id = %s) < %s""",
                (branch_id, user_id, now, user_id, user_id, branch_id, max_members),
            )
            if int(cur.rowcount or 0) == 0:
                if _has_any_membership_tx(cur, user_id):
                    raise ValueError("ALREADY")
                cur.execute(
                    "SELECT COUNT(1) AS c FROM sect_branch_members WHERE branch_id = %s",
                    (branch_id,),
                )
                row = cur.fetchone()
                if row and int(row["c"] or 0) >= max_members:
                    raise ValueError("FULL")
                raise ValueError("CONFLICT")
    except psycopg2.errors.UniqueViolation:
        return {"success": False, "code": "ALREADY", "message": "你已加入宗门或别院"}, 400
    except ValueError as exc:
        reason = str(exc)
        if reason == "ALREADY":
            return {"success": False, "code": "ALREADY", "message": "你已加入宗门或别院"}, 400
        if reason == "FULL":
            return {"success": False, "code": "FULL", "message": "别院已满员"}, 400
        return {"success": False, "code": "CONFLICT", "message": "加入请求冲突，请重试"}, 409
    return {"success": True, "message": "加入别院成功"}, 200


def leave_sect(user_id: str) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if sect.get("membership_kind") == "branch":
        branch = sect.get("branch") or {}
        if branch.get("branch_role") == "leader":
            members = _branch_member_count(branch["branch_id"])
            if members > 1:
                return {"success": False, "code": "LEADER", "message": "院主需先转让或清空别院成员"}, 400
            with db_transaction() as cur:
                cur.execute("DELETE FROM sect_branch_members WHERE branch_id = %s", (branch["branch_id"],))
                cur.execute("DELETE FROM sect_branches WHERE branch_id = %s", (branch["branch_id"],))
            return {"success": True, "message": "别院已解散"}, 200
        with db_transaction() as cur:
            cur.execute("DELETE FROM sect_branch_members WHERE branch_id = %s AND user_id = %s", (branch["branch_id"], user_id))
        return {"success": True, "message": "已退出别院"}, 200
    role = _get_member_role(sect["sect_id"], user_id)
    if role == "leader":
        members = fetch_one(
            "SELECT COUNT(1) AS c FROM sect_members WHERE sect_id = %s",
            (sect["sect_id"],),
        )
        if members and int(members.get("c", 0) or 0) > 1:
            return {"success": False, "code": "LEADER", "message": "宗主需先转让或解散宗门"}, 400
        # disband
        with db_transaction() as cur:
            branch_rows = fetch_all("SELECT branch_id FROM sect_branches WHERE parent_sect_id = %s", (sect["sect_id"],))
            for row in branch_rows:
                cur.execute("DELETE FROM sect_branch_members WHERE branch_id = %s", (row["branch_id"],))
            cur.execute("DELETE FROM sect_branch_requests WHERE parent_sect_id = %s", (sect["sect_id"],))
            cur.execute("DELETE FROM sect_branches WHERE parent_sect_id = %s", (sect["sect_id"],))
            cur.execute("DELETE FROM sect_members WHERE sect_id = %s", (sect["sect_id"],))
            cur.execute("DELETE FROM sects WHERE sect_id = %s", (sect["sect_id"],))
        return {"success": True, "message": "宗门已解散"}, 200

    with db_transaction() as cur:
        cur.execute("DELETE FROM sect_members WHERE sect_id = %s AND user_id = %s", (sect["sect_id"], user_id))
    return {"success": True, "message": "已退出宗门"}, 200


def promote_member(user_id: str, target_user_id: str, role: str) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if _get_member_role(sect["sect_id"], user_id) != "leader":
        return {"success": False, "code": "FORBIDDEN", "message": "仅宗主可操作"}, 403
    if role not in ("elder", "deputy", "member"):
        return {"success": False, "code": "INVALID", "message": "无效角色"}, 400
    with db_transaction() as cur:
        cur.execute(
            "UPDATE sect_members SET role = %s WHERE sect_id = %s AND user_id = %s",
            (role, sect["sect_id"], target_user_id),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "NOT_FOUND", "message": "目标成员不存在"}, 404
    return {"success": True, "message": "已更新成员职务"}, 200


def transfer_leadership(user_id: str, target_user_id: str) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if _get_member_role(sect["sect_id"], user_id) != "leader":
        return {"success": False, "code": "FORBIDDEN", "message": "仅宗主可移交"}, 403
    if not target_user_id or target_user_id == user_id:
        return {"success": False, "code": "INVALID", "message": "无效的接任人"}, 400
    target_role = _get_member_role(sect["sect_id"], target_user_id)
    if not target_role:
        return {"success": False, "code": "NOT_FOUND", "message": "接任人不在宗门成员中"}, 404

    with db_transaction() as cur:
        cur.execute(
            "UPDATE sect_members SET role = 'leader' WHERE sect_id = %s AND user_id = %s",
            (sect["sect_id"], target_user_id),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "NOT_FOUND", "message": "接任人不在宗门成员中"}, 404
        cur.execute(
            "UPDATE sect_members SET role = 'elder' WHERE sect_id = %s AND user_id = %s",
            (sect["sect_id"], user_id),
        )
        cur.execute(
            "UPDATE sects SET leader_id = %s WHERE sect_id = %s",
            (target_user_id, sect["sect_id"]),
        )
    return {"success": True, "message": "宗主移交成功"}, 200


def kick_member(user_id: str, target_user_id: str) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if _get_member_role(sect["sect_id"], user_id) != "leader":
        return {"success": False, "code": "FORBIDDEN", "message": "仅宗主可操作"}, 403
    if target_user_id == user_id:
        return {"success": False, "code": "INVALID", "message": "不能踢出自己"}, 400
    with db_transaction() as cur:
        cur.execute(
            "DELETE FROM sect_members WHERE sect_id = %s AND user_id = %s",
            (sect["sect_id"], target_user_id),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "NOT_FOUND", "message": "目标成员不存在"}, 404
    return {"success": True, "message": "已踢出成员"}, 200


def _level_up_if_needed(sect_id: str) -> None:
    sect = fetch_one("SELECT level, exp, max_members FROM sects WHERE sect_id = %s", (sect_id,))
    if not sect:
        return
    level = int(sect.get("level", 1) or 1)
    exp = int(sect.get("exp", 0) or 0)
    need = SECT_LEVEL_EXP_BASE * level
    if exp < need:
        return
    new_level = level + 1
    new_max = min(
        int(sect.get("max_members", SECT_BASE_MAX_MEMBERS) or SECT_BASE_MAX_MEMBERS) + SECT_LEVEL_UP_MEMBER_INCREASE,
        SECT_MAX_MEMBERS_CAP,
    )
    with db_transaction() as cur:
        cur.execute(
            "UPDATE sects SET level = %s, exp = %s, max_members = %s WHERE sect_id = %s",
            (new_level, exp - need, new_max, sect_id),
        )


def donate(user_id: str, copper: int = 0, gold: int = 0) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        log_event("sect_donate", user_id=user_id, success=False, reason="NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    try:
        copper = max(0, int(copper or 0))
        gold = max(0, int(gold or 0))
    except (TypeError, ValueError):
        log_event("sect_donate", user_id=user_id, success=False, reason="INVALID_AMOUNT")
        return {"success": False, "code": "INVALID_AMOUNT", "message": "捐献数量必须是整数"}, 400
    if copper <= 0 and gold <= 0:
        log_event("sect_donate", user_id=user_id, success=False, reason="INVALID_AMOUNT")
        return {"success": False, "code": "INVALID", "message": "请输入捐献数量"}, 400

    exp_gain = copper // SECT_DONATE_EXP_PER_COPPER_DIV + gold * SECT_DONATE_EXP_PER_GOLD
    today = today_local()
    now = int(time.time())

    try:
        with db_transaction() as cur:
            cur.execute(
                """UPDATE users
                   SET copper = copper - %s, gold = gold - %s
                   WHERE user_id = %s AND copper >= %s AND gold >= %s""",
                (copper, gold, user_id, copper, gold),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("INSUFFICIENT")

            cur.execute(
                "UPDATE sects SET fund_copper = fund_copper + %s, fund_gold = fund_gold + %s, exp = exp + %s WHERE sect_id = %s",
                (copper, gold, exp_gain, sect["sect_id"]),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("NOT_FOUND")

            contribution = copper + gold * SECT_DONATE_CONTRIBUTION_PER_GOLD
            if sect.get("membership_kind") == "branch":
                cur.execute(
                    "UPDATE sect_branch_members SET contribution = contribution + %s WHERE branch_id = %s AND user_id = %s",
                    (contribution, sect.get("branch", {}).get("branch_id"), user_id),
                )
            else:
                cur.execute(
                    "UPDATE sect_members SET contribution = contribution + %s WHERE sect_id = %s AND user_id = %s",
                    (contribution, sect["sect_id"], user_id),
                )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("NOT_FOUND")

            _increment_sect_quest_progress_tx(
                cur,
                sect_id=sect["sect_id"],
                user_id=user_id,
                quest_type="donate",
                amount=contribution,
                assigned_date=today,
                now=now,
            )
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT":
            log_event("sect_donate", user_id=user_id, success=False, reason="INSUFFICIENT")
            return {"success": False, "code": "INSUFFICIENT", "message": "资源不足"}, 400
        log_event("sect_donate", user_id=user_id, success=False, reason="NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404

    _level_up_if_needed(sect["sect_id"])

    log_event(
        "sect_donate",
        user_id=user_id,
        success=True,
        rank=int((get_user_by_id(user_id) or {}).get("rank", 1) or 1),
        meta={"sect_id": sect["sect_id"], "copper": copper, "gold": gold, "exp_gain": exp_gain},
    )
    log_economy_ledger(
        user_id=user_id,
        module="sect",
        action="sect_donate",
        delta_copper=-copper,
        delta_gold=-gold,
        success=True,
        rank=int((get_user_by_id(user_id) or {}).get("rank", 1) or 1),
        meta={"sect_id": sect["sect_id"], "exp_gain": exp_gain},
    )
    return {"success": True, "message": "捐献成功", "exp_gain": exp_gain}, 200


def get_quests(sect_id: str, user_id: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    sect = fetch_one("SELECT * FROM sects WHERE sect_id = %s", (sect_id,))
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "宗门不存在"}, 404
    today = today_local()
    with db_transaction() as cur:
        for quest_type in SECT_QUEST_DEFS:
            _ensure_sect_quest_row(cur, sect_id=sect_id, quest_type=quest_type, assigned_date=today)
    quests = fetch_all(
        "SELECT * FROM sect_quests WHERE sect_id = %s AND assigned_date = %s",
        (sect_id, today),
    )
    quest_rows = [dict(row) for row in quests]
    for row in quest_rows:
        row["completed"] = 1 if int(row.get("progress", 0) or 0) >= int(row.get("target", 0) or 0) else int(row.get("completed", 0) or 0)
    if user_id and quest_rows:
        quest_ids = [int(row["id"]) for row in quest_rows]
        placeholders = ",".join(["%s"] * len(quest_ids))
        claim_rows = fetch_all(
            f"SELECT quest_id, claimed, progress FROM sect_quest_claims WHERE user_id = %s AND quest_id IN ({placeholders})",
            (user_id, *quest_ids),
        )
        claim_map = {int(r["quest_id"]): r for r in claim_rows}
        for row in quest_rows:
            claim = claim_map.get(int(row["id"]))
            row["claimed_by_me"] = bool(int((claim or {}).get("claimed", 0) or 0))
            row["my_progress"] = int((claim or {}).get("progress", 0) or 0)
    return {"success": True, "quests": quest_rows}, 200


def claim_quest(user_id: str, quest_id: int) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        log_event("sect_quest_claim", user_id=user_id, success=False, reason="NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    row = fetch_one(
        "SELECT * FROM sect_quests WHERE id = %s AND sect_id = %s",
        (quest_id, sect["sect_id"]),
    )
    if not row:
        log_event("sect_quest_claim", user_id=user_id, success=False, reason="NOT_FOUND", meta={"quest_id": quest_id})
        return {"success": False, "code": "NOT_FOUND", "message": "任务不存在"}, 404
    if int(row.get("progress", 0) or 0) < int(row.get("target", 0) or 0):
        log_event("sect_quest_claim", user_id=user_id, success=False, reason="NOT_DONE", meta={"quest_id": quest_id})
        return {"success": False, "code": "NOT_DONE", "message": "任务未完成"}, 400

    reward_copper = int(row.get("reward_copper", 0) or 0)
    reward_exp = int(row.get("reward_exp", 0) or 0)
    now = int(time.time())
    try:
        with db_transaction() as cur:
            member_count = max(1, _sect_member_count_tx(cur, sect["sect_id"]))
            reward_copper = int(round(reward_copper / member_count)) if reward_copper > 0 else 0
            reward_exp = int(round(reward_exp / member_count)) if reward_exp > 0 else 0
            cur.execute(
                """INSERT INTO sect_quest_claims (quest_id, sect_id, user_id, progress, claimed, claimed_at, updated_at)
                   VALUES (%s, %s, %s, 0, 1, %s, %s)
                   ON CONFLICT(quest_id, user_id) DO UPDATE SET
                       claimed = 1,
                       claimed_at = excluded.claimed_at,
                       updated_at = excluded.updated_at
                   WHERE sect_quest_claims.claimed = 0""",
                (quest_id, sect["sect_id"], user_id, now, now),
            )
            if cur.rowcount == 0:
                raise ValueError("ALREADY_OR_NOT_DONE")
            cur.execute(
                "UPDATE users SET copper = copper + %s, exp = exp + %s WHERE user_id = %s",
                (reward_copper, reward_exp, user_id),
            )
    except ValueError:
        latest = fetch_one(
            "SELECT claimed FROM sect_quest_claims WHERE quest_id = %s AND user_id = %s",
            (quest_id, user_id),
        )
        if latest and int(latest.get("claimed", 0) or 0) == 1:
            log_event("sect_quest_claim", user_id=user_id, success=False, reason="CLAIMED", meta={"quest_id": quest_id})
            return {"success": False, "code": "CLAIMED", "message": "已领取过奖励"}, 400
        log_event("sect_quest_claim", user_id=user_id, success=False, reason="NOT_DONE", meta={"quest_id": quest_id})
        return {"success": False, "code": "NOT_DONE", "message": "任务未完成"}, 400
    log_event(
        "sect_quest_claim",
        user_id=user_id,
        success=True,
        rank=int((get_user_by_id(user_id) or {}).get("rank", 1) or 1),
        meta={"quest_id": quest_id, "sect_id": sect["sect_id"]},
    )
    log_economy_ledger(
        user_id=user_id,
        module="sect",
        action="sect_quest_claim",
        delta_copper=reward_copper,
        delta_exp=reward_exp,
        success=True,
        rank=int((get_user_by_id(user_id) or {}).get("rank", 1) or 1),
        meta={"quest_id": quest_id, "sect_id": sect["sect_id"]},
    )
    return {
        "success": True,
        "message": "领取成功",
        "rewards": {"copper": reward_copper, "exp": reward_exp},
    }, 200


def challenge_war(user_id: str, target_sect_id: str) -> Tuple[Dict[str, Any], int]:
    sect = get_user_sect(user_id)
    if not sect:
        return {"success": False, "code": "NOT_FOUND", "message": "你尚未加入宗门"}, 404
    if _get_member_role(sect["sect_id"], user_id) not in ("leader", "deputy", "elder"):
        return {"success": False, "code": "FORBIDDEN", "message": "仅宗主/副宗主/长老可发起战争"}, 403
    if sect["sect_id"] == target_sect_id:
        return {"success": False, "code": "INVALID", "message": "不能挑战自己宗门"}, 400

    target = fetch_one("SELECT * FROM sects WHERE sect_id = %s", (target_sect_id,))
    if not target:
        return {"success": False, "code": "NOT_FOUND", "message": "目标宗门不存在"}, 404

    now = int(time.time())
    attacker_id = sect["sect_id"]
    try:
        with db_transaction() as cur:
            cur.execute(
                """UPDATE sects
                   SET last_war_time = %s
                   WHERE sect_id = %s AND (%s - last_war_time) >= %s""",
                (now, attacker_id, now, SECT_WAR_COOLDOWN),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("COOLDOWN")
            cur.execute(
                """UPDATE sects
                   SET last_war_time = %s
                   WHERE sect_id = %s AND (%s - last_war_time) >= %s""",
                (now, target_sect_id, now, SECT_WAR_COOLDOWN),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("TARGET_COOLDOWN")

            power_a = _sect_total_power_tx(cur, attacker_id)
            power_b = _sect_total_power_tx(cur, target_sect_id)
            winner_id = attacker_id if power_a >= power_b else target_sect_id

            cur.execute(
                "INSERT INTO sect_wars (attacker_sect_id, defender_sect_id, winner_sect_id, power_a, power_b, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (attacker_id, target_sect_id, winner_id, power_a, power_b, now),
            )
            cur.execute(
                "UPDATE sects SET war_wins = war_wins + %s, war_losses = war_losses + %s WHERE sect_id = %s",
                (1 if winner_id == attacker_id else 0, 1 if winner_id != attacker_id else 0, attacker_id),
            )
            cur.execute(
                "UPDATE sects SET war_wins = war_wins + %s, war_losses = war_losses + %s WHERE sect_id = %s",
                (1 if winner_id == target_sect_id else 0, 1 if winner_id != target_sect_id else 0, target_sect_id),
            )
    except ValueError as exc:
        reason = str(exc)
        if reason == "COOLDOWN":
            return {"success": False, "code": "COOLDOWN", "message": "宗门战争冷却中"}, 400
        return {"success": False, "code": "COOLDOWN", "message": "目标宗门处于战争保护期"}, 400

    return {
        "success": True,
        "winner_sect_id": winner_id,
        "power_a": power_a,
        "power_b": power_b,
        "message": "宗门战争结束",
    }, 200
