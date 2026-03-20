"""Events and world boss services."""

from __future__ import annotations

import random
import time
import math
import json
import datetime
from typing import Any, Dict, List, Tuple

from core.config import config
from core.database.connection import (
    fetch_one,
    db_transaction,
    get_user_by_id,
    refresh_user_stamina,
    spend_user_stamina_tx,
)
from core.utils.number import format_stamina_value
from core.game.events import list_events, get_world_boss_config
from core.services.sect_service import apply_sect_stat_buffs, get_user_sect_buffs
from core.services.metrics_service import log_event, log_economy_ledger
from core.services.worldboss_fsm import WorldBossFSM, WorldBossSnapshot
from core.utils.timeutil import midnight_timestamp, local_day_key


def _wb_cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("events", "world_boss", key, default=default))
    except (TypeError, ValueError):
        return int(default)


def _wb_cfg_float(key: str, default: float) -> float:
    try:
        return float(config.get_nested("events", "world_boss", key, default=default))
    except (TypeError, ValueError):
        return float(default)


BOSS_DAILY_LIMIT = max(1, _wb_cfg_int("daily_attack_limit", 5))
BOSS_STAMINA_COST = max(1, _wb_cfg_int("stamina_cost", 1))
BOSS_DAMAGE_VAR_MIN = _wb_cfg_float("damage_variance_min", 0.8)
BOSS_DAMAGE_VAR_MAX = _wb_cfg_float("damage_variance_max", 1.2)
BOSS_REWARD_BASE_COPPER = _wb_cfg_int("attack_reward_base_copper", 50)
BOSS_REWARD_PER_RANK_COPPER = _wb_cfg_int("attack_reward_per_rank_copper", 8)
BOSS_REWARD_MIN_COPPER = _wb_cfg_int("attack_reward_min_copper", 80)
BOSS_REWARD_MAX_COPPER = _wb_cfg_int("attack_reward_max_copper", 260)
BOSS_REWARD_BASE_EXP = _wb_cfg_int("attack_reward_base_exp", 40)
BOSS_REWARD_PER_RANK_EXP = _wb_cfg_int("attack_reward_per_rank_exp", 6)
BOSS_REWARD_MIN_EXP = _wb_cfg_int("attack_reward_min_exp", 60)
BOSS_REWARD_MAX_EXP = _wb_cfg_int("attack_reward_max_exp", 220)
BOSS_RARE_DROP_MIN_RANK = _wb_cfg_int("rare_drop_min_rank", 20)
BOSS_RARE_DROP_CHANCE = max(0.0, min(1.0, _wb_cfg_float("rare_drop_chance", 0.18)))
BOSS_MID_DROP_MIN_RANK = _wb_cfg_int("mid_drop_min_rank", 10)
BOSS_MID_DROP_CHANCE = max(0.0, min(1.0, _wb_cfg_float("mid_drop_chance", 0.28)))
BOSS_DEFEAT_BONUS_RARE_MIN_RANK = _wb_cfg_int("defeat_bonus_rare_min_rank", 20)
BOSS_DEFEAT_BONUS_RARE_ITEM_ID = str(config.get_nested("events", "world_boss", "defeat_bonus_rare_item_id", default="dragon_scale") or "dragon_scale")
BOSS_DEFEAT_BONUS_COMMON_ITEM_ID = str(config.get_nested("events", "world_boss", "defeat_bonus_common_item_id", default="demon_core") or "demon_core")


def _world_boss() -> Dict[str, Any]:
    return get_world_boss_config()


def _ensure_event_point_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_points (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            points_total INTEGER DEFAULT 0,
            points_spent INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(user_id, event_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_point_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            delta_points INTEGER NOT NULL,
            source TEXT NOT NULL,
            meta_json TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_exchange_claims (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            exchange_id TEXT NOT NULL,
            period_key TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            UNIQUE(user_id, event_id, exchange_id, period_key)
        )
        """
    )


def _event_period_key(now_ts: int, period: str) -> str:
    period = str(period or "event")
    if period == "day":
        return f"day:{local_day_key(now_ts)}"
    if period == "week":
        ts = int(now_ts or time.time())
        local_tz = datetime.timezone(datetime.timedelta(hours=8))
        local_dt = datetime.datetime.fromtimestamp(ts, tz=local_tz)
        iso = local_dt.isocalendar()
        return f"week:{int(iso.year)}-{int(iso.week):02d}"
    return "event:all"


def _grant_event_points(cur, user_id: str, event_id: str, points: int, source: str, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    pts = int(points or 0)
    if pts <= 0:
        cur.execute(
            "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s",
            (user_id, event_id),
        )
        row = cur.fetchone()
        total = int((row["points_total"] if row else 0) or 0)
        spent = int((row["points_spent"] if row else 0) or 0)
        return {"points_total": total, "points_spent": spent, "points_balance": max(0, total - spent)}
    now = int(time.time())
    _ensure_event_point_tables(cur)
    cur.execute(
        """
        INSERT INTO event_points(user_id, event_id, points_total, points_spent, updated_at)
        VALUES(%s, %s, %s, 0, %s)
        ON CONFLICT(user_id, event_id)
        DO UPDATE SET
            points_total = event_points.points_total + excluded.points_total,
            updated_at = excluded.updated_at
        """,
        (user_id, event_id, pts, now),
    )
    cur.execute(
        "INSERT INTO event_point_logs(user_id, event_id, delta_points, source, meta_json, created_at) VALUES(%s,%s,%s,%s,%s,%s)",
        (user_id, event_id, pts, source, json.dumps(meta or {}, ensure_ascii=False), now),
    )
    cur.execute(
        "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s",
        (user_id, event_id),
    )
    row = cur.fetchone()
    total = int((row["points_total"] if row else 0) or 0)
    spent = int((row["points_spent"] if row else 0) or 0)
    return {"points_total": total, "points_spent": spent, "points_balance": max(0, total - spent)}


def _apply_action_points(cur, user_id: str, action_key: str, *, now_ts: int, meta: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    action = str(action_key or "").strip()
    if not action:
        return []
    events = []
    for event in get_active_events():
        rules = event.get("point_rules", {}) or {}
        points = int(rules.get(action, 0) or 0)
        if points <= 0:
            continue
        balance = _grant_event_points(cur, user_id, event["id"], points, action, meta=meta)
        events.append({"event_id": event["id"], "granted_points": points, **balance})
    return events


def grant_event_points_for_action(user_id: str, action_key: str, *, meta: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    now = int(time.time())
    with db_transaction() as cur:
        _ensure_event_point_tables(cur)
        return _apply_action_points(cur, user_id, action_key, now_ts=now, meta=meta)


def _grant_generated_item(cur, user_id: str, item_id: str, quantity: int) -> Dict[str, Any] | None:
    from core.game.items import get_item_by_id, generate_material, generate_pill, generate_equipment, Quality

    base = get_item_by_id(item_id)
    if not base:
        return None
    item_type = getattr(base.get("type"), "value", base.get("type"))
    qty = int(quantity or 1)
    if item_type == "pill":
        generated = generate_pill(item_id, qty)
    elif item_type == "material":
        generated = generate_material(item_id, qty)
    else:
        generated = generate_equipment(base, Quality.COMMON, 1)
        generated["quantity"] = qty
    cur.execute(
        """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
           quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
           first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            user_id,
            generated.get("item_id"),
            generated.get("item_name"),
            generated.get("item_type"),
            generated.get("quality", "common"),
            generated.get("quantity", 1),
            generated.get("level", 1),
            generated.get("attack_bonus", 0),
            generated.get("defense_bonus", 0),
            generated.get("hp_bonus", 0),
            generated.get("mp_bonus", 0),
            generated.get("first_round_reduction_pct", 0),
            generated.get("crit_heal_pct", 0),
            generated.get("element_damage_pct", 0),
            generated.get("low_hp_shield_pct", 0),
        ),
    )
    return generated


def get_active_events() -> List[Dict[str, Any]]:
    now = int(time.time())
    active = []
    for e in list_events():
        if int(e.get("start_ts", 0) or 0) <= now <= int(e.get("end_ts", 0) or 0):
            active.append(e)
    return active


def get_event_status(user_id: str) -> Dict[str, Any]:
    active = get_active_events()
    items = []
    now = int(time.time())
    next_refresh = midnight_timestamp() + 86400
    with db_transaction() as cur:
        _ensure_event_point_tables(cur)
    for e in active:
        row = fetch_one(
            "SELECT last_claim FROM event_claims WHERE user_id = %s AND event_id = %s",
            (user_id, e["id"]),
        )
        last_claim = int(row.get("last_claim", 0) or 0) if row else 0
        claimed_today = last_claim >= midnight_timestamp()
        end_ts = int(e.get("end_ts", 0) or 0)
        remaining_days = 0
        if end_ts > 0:
            remaining_days = max(0, int(math.ceil((end_ts - now) / 86400)))
        points_row = fetch_one(
            "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s",
            (user_id, e["id"]),
        )
        points_total = int((points_row or {}).get("points_total", 0) or 0)
        points_spent = int((points_row or {}).get("points_spent", 0) or 0)
        points_balance = max(0, points_total - points_spent)
        exchange_status = []
        for exchange in e.get("exchange_shop", []) or []:
            period = exchange.get("period", "event")
            period_key = _event_period_key(now, period)
            claim_row = fetch_one(
                """
                SELECT quantity FROM event_exchange_claims
                WHERE user_id = %s AND event_id = %s AND exchange_id = %s AND period_key = %s
                """,
                (user_id, e["id"], exchange.get("id"), period_key),
            )
            claimed_qty = int((claim_row or {}).get("quantity", 0) or 0)
            limit = int(exchange.get("limit", 0) or 0)
            remaining = max(0, limit - claimed_qty) if limit > 0 else None
            exchange_status.append(
                {
                    **exchange,
                    "claimed": claimed_qty,
                    "remaining": remaining,
                    "period_key": period_key,
                    "can_exchange": points_balance >= int(exchange.get("cost_points", 0) or 0) and (remaining is None or remaining > 0),
                }
            )
        items.append(
            {
                **e,
                "claimed_today": claimed_today,
                "points_total": points_total,
                "points_spent": points_spent,
                "points_balance": points_balance,
                "exchange_status": exchange_status,
            }
        )
        items[-1]["end_ts"] = end_ts
        items[-1]["remaining_days"] = remaining_days
        items[-1]["next_refresh_ts"] = min(next_refresh, end_ts) if end_ts > 0 else next_refresh
    return {"success": True, "events": items}


def claim_event_reward(user_id: str, event_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        log_event("event_claim", user_id=user_id, success=False, reason="USER_NOT_FOUND", meta={"event_id": event_id})
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    active = {e["id"]: e for e in get_active_events()}
    if event_id not in active:
        log_event("event_claim", user_id=user_id, success=False, reason="INVALID", meta={"event_id": event_id})
        return {"success": False, "code": "INVALID", "message": "活动不存在或未开启"}, 400
    e = active[event_id]
    now = int(time.time())

    rewards = e.get("daily_reward", {}) or {}
    points_granted = int((e.get("point_rules", {}) or {}).get("claim_daily", 0) or 0)
    points_info = {"points_total": 0, "points_spent": 0, "points_balance": 0}
    items = rewards.get("items", []) or []
    today_start = midnight_timestamp()
    with db_transaction() as cur:
        _ensure_event_point_tables(cur)
        cur.execute(
            """
            UPDATE event_claims
            SET last_claim = %s, claims = claims + 1
            WHERE user_id = %s AND event_id = %s AND last_claim < %s
            """,
            (now, user_id, event_id, today_start),
        )
        claimed = int(cur.rowcount or 0) > 0
        if not claimed:
            cur.execute(
                "INSERT INTO event_claims (user_id, event_id, last_claim, claims) VALUES (%s, %s, %s, 1)",
                (user_id, event_id, now),
            )
            claimed = int(cur.rowcount or 0) > 0
        if not claimed:
            log_event("event_claim", user_id=user_id, success=False, reason="ALREADY", meta={"event_id": event_id})
            return {"success": False, "code": "ALREADY", "message": "今日已领取"}, 400

        cur.execute(
            "UPDATE users SET copper = copper + %s, exp = exp + %s, gold = gold + %s WHERE user_id = %s",
            (int(rewards.get("copper", 0) or 0), int(rewards.get("exp", 0) or 0), int(rewards.get("gold", 0) or 0), user_id),
        )
        for it in items:
            from core.game.items import get_item_by_id, generate_material, generate_pill, generate_equipment, Quality
            base = get_item_by_id(it.get("item_id"))
            if not base:
                continue
            item_type = getattr(base.get("type"), "value", base.get("type"))
            qty = int(it.get("quantity", 1) or 1)
            if item_type == "pill":
                generated = generate_pill(it.get("item_id"), qty)
            elif item_type == "material":
                generated = generate_material(it.get("item_id"), qty)
            else:
                generated = generate_equipment(base, Quality.COMMON, 1)
                generated["quantity"] = qty
            cur.execute(
                """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                   quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                   first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id,
                    generated.get("item_id"),
                    generated.get("item_name"),
                    generated.get("item_type"),
                    generated.get("quality", "common"),
                    generated.get("quantity", 1),
                    generated.get("level", 1),
                    generated.get("attack_bonus", 0),
                    generated.get("defense_bonus", 0),
                    generated.get("hp_bonus", 0),
                    generated.get("mp_bonus", 0),
                    generated.get("first_round_reduction_pct", 0),
                    generated.get("crit_heal_pct", 0),
                    generated.get("element_damage_pct", 0),
                    generated.get("low_hp_shield_pct", 0),
                ),
            )
        points_info = _grant_event_points(
            cur,
            user_id,
            event_id,
            points_granted,
            "claim_daily",
            meta={"event_id": event_id},
        )

    log_event(
        "event_claim",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"event_id": event_id, "items": rewards.get("items", [])},
    )
    log_economy_ledger(
        user_id=user_id,
        module="event",
        action="event_claim",
        delta_copper=int(rewards.get("copper", 0) or 0),
        delta_gold=int(rewards.get("gold", 0) or 0),
        delta_exp=int(rewards.get("exp", 0) or 0),
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"event_id": event_id},
    )
    return {
        "success": True,
        "message": "领取成功",
        "rewards": rewards,
        "points_granted": points_granted,
        "points_balance": int(points_info.get("points_balance", 0) or 0),
        "points_total": int(points_info.get("points_total", 0) or 0),
        "points_spent": int(points_info.get("points_spent", 0) or 0),
    }, 200


def _ensure_boss_state(cur, *, for_update: bool = False):
    boss = _world_boss()
    lock_clause = " FOR UPDATE" if for_update else ""
    cur.execute(f"SELECT * FROM world_boss_state WHERE boss_id = %s{lock_clause}", (boss["id"],))
    row = cur.fetchone()
    if row:
        # Heal legacy/corrupted rows and keep runtime state aligned with config changes.
        row_data = dict(row)
        cfg_max_hp = int(boss.get("max_hp", 1) or 1)
        max_hp = int(row_data.get("max_hp", 0) or 0)
        hp = int(row_data.get("hp", 0) or 0)
        if max_hp <= 0:
            fixed_max_hp = cfg_max_hp
            fixed_hp = fixed_max_hp if hp <= 0 else min(max(0, hp), fixed_max_hp)
            cur.execute(
                "UPDATE world_boss_state SET hp = %s, max_hp = %s WHERE boss_id = %s",
                (fixed_hp, fixed_max_hp, boss["id"]),
            )
            cur.execute(f"SELECT * FROM world_boss_state WHERE boss_id = %s{lock_clause}", (boss["id"],))
            return cur.fetchone()
        if max_hp != cfg_max_hp:
            fixed_hp = 0 if hp <= 0 else min(max(0, hp), cfg_max_hp)
            cur.execute(
                "UPDATE world_boss_state SET hp = %s, max_hp = %s WHERE boss_id = %s",
                (fixed_hp, cfg_max_hp, boss["id"]),
            )
            cur.execute(f"SELECT * FROM world_boss_state WHERE boss_id = %s{lock_clause}", (boss["id"],))
            return cur.fetchone()
        return row
    cur.execute(
        """INSERT INTO world_boss_state (boss_id, hp, max_hp, last_reset, last_defeated)
           VALUES (%s, %s, %s, %s, 0)
           ON CONFLICT (boss_id) DO NOTHING""",
        (boss["id"], boss["max_hp"], boss["max_hp"], int(time.time())),
    )
    cur.execute(f"SELECT * FROM world_boss_state WHERE boss_id = %s{lock_clause}", (boss["id"],))
    return cur.fetchone()


def _table_columns(cur, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    rows = cur.fetchall() or []
    names: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            name = row.get("column_name")
        elif isinstance(row, (list, tuple)):
            name = row[0] if row else None
        else:
            try:
                name = row["column_name"]
            except Exception:
                name = None
        if name:
            names.add(str(name))
    return names


def _ensure_unique_index(cur, *, table: str, index_name: str, columns_sql: str) -> None:
    cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns_sql})")


def _cleanup_worldboss_duplicates(cur) -> None:
    # Clear invalid legacy rows first so unique indexes can be applied safely.
    cur.execute("DELETE FROM world_boss_state WHERE boss_id IS NULL OR boss_id = ''")
    cur.execute("DELETE FROM world_boss_attacks WHERE user_id IS NULL OR user_id = ''")

    # Keep the newest row (max id) per key, remove stale duplicates from legacy schemas.
    cur.execute(
        """
        DELETE FROM world_boss_state
        WHERE boss_id IS NOT NULL AND boss_id <> ''
          AND id NOT IN (
              SELECT MAX(id) FROM world_boss_state
              WHERE boss_id IS NOT NULL AND boss_id <> ''
              GROUP BY boss_id
          )
        """
    )
    cur.execute(
        """
        DELETE FROM world_boss_attacks
        WHERE user_id IS NOT NULL AND user_id <> ''
          AND id NOT IN (
              SELECT MAX(id) FROM world_boss_attacks
              WHERE user_id IS NOT NULL AND user_id <> ''
              GROUP BY user_id
          )
        """
    )


def _ensure_worldboss_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS world_boss_state (
            id SERIAL PRIMARY KEY,
            boss_id TEXT UNIQUE NOT NULL,
            hp INTEGER DEFAULT 0,
            max_hp INTEGER DEFAULT 0,
            last_reset INTEGER DEFAULT 0,
            last_defeated INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS world_boss_attacks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            last_attack_day INTEGER DEFAULT 0,
            attacks_today INTEGER DEFAULT 0,
            UNIQUE(user_id)
        )
        """
    )
    # Backward compatibility for legacy schemas: old tables may miss columns.
    state_cols = _table_columns(cur, "world_boss_state")
    for col, ddl in [
        ("boss_id", "TEXT"),
        ("hp", "INTEGER DEFAULT 0"),
        ("max_hp", "INTEGER DEFAULT 0"),
        ("last_reset", "INTEGER DEFAULT 0"),
        ("last_defeated", "INTEGER DEFAULT 0"),
    ]:
        if col not in state_cols:
            cur.execute(f"ALTER TABLE world_boss_state ADD COLUMN {col} {ddl}")

    attack_cols = _table_columns(cur, "world_boss_attacks")
    for col, ddl in [
        ("user_id", "TEXT"),
        ("last_attack_day", "INTEGER DEFAULT 0"),
        ("attacks_today", "INTEGER DEFAULT 0"),
    ]:
        if col not in attack_cols:
            cur.execute(f"ALTER TABLE world_boss_attacks ADD COLUMN {col} {ddl}")

    _cleanup_worldboss_duplicates(cur)
    _ensure_unique_index(
        cur,
        table="world_boss_state",
        index_name="ux_world_boss_state_boss_id",
        columns_sql="boss_id",
    )
    _ensure_unique_index(
        cur,
        table="world_boss_attacks",
        index_name="ux_world_boss_attacks_user_id",
        columns_sql="user_id",
    )


def get_world_boss_status() -> Dict[str, Any]:
    boss_cfg = _world_boss()
    now = int(time.time())
    day_start = midnight_timestamp()
    with db_transaction() as cur:
        _ensure_worldboss_tables(cur)
        row = _ensure_boss_state(cur, for_update=True)
        if row is None:
            return {"success": False, "message": "Boss not found"}
        row_data = dict(row)
        snapshot = WorldBossSnapshot.from_row(row_data, now_ts=now, day_start_ts=day_start)
        fsm = WorldBossFSM(snapshot)
        if fsm.should_daily_reset():
            fsm.apply_daily_reset()
            cur.execute(
                "UPDATE world_boss_state SET hp = %s, last_reset = %s WHERE boss_id = %s",
                (snapshot.hp, snapshot.last_reset, boss_cfg["id"]),
            )
            row = _ensure_boss_state(cur, for_update=True)
            row_data = dict(row) if row is not None else {}
            snapshot = WorldBossSnapshot.from_row(row_data, now_ts=now, day_start_ts=day_start)
            fsm = WorldBossFSM(snapshot)
    return {
        "success": True,
        "boss": {
            "id": boss_cfg["id"],
            "name": boss_cfg["name"],
            "hp": int(row_data.get("hp", 0) or 0),
            "max_hp": int(row_data.get("max_hp", 0) or 0),
            "state": fsm.state.value,
        },
    }


def attack_world_boss(user_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        log_event("world_boss_attack", user_id=user_id, success=False, reason="USER_NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    sect_buffs = get_user_sect_buffs(user_id)
    boss_cfg = _world_boss()

    now = int(time.time())
    stamina_user = refresh_user_stamina(user_id, now=now)
    event_point_grants: List[Dict[str, Any]] = []
    attacks_used_today = 0
    with db_transaction() as cur:
        _ensure_worldboss_tables(cur)
        _ensure_event_point_tables(cur)
        day = local_day_key(now)
        cur.execute(
            """
            INSERT INTO world_boss_attacks (user_id, last_attack_day, attacks_today)
            VALUES (%s, %s, 0)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, day),
        )
        cur.execute(
            "SELECT last_attack_day, attacks_today FROM world_boss_attacks WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        attack_row = cur.fetchone()
        if not attack_row:
            return {"success": False, "code": "CONFLICT", "message": "攻击记录锁定失败，请重试"}, 409
        last_day = int(attack_row["last_attack_day"] or 0)
        attacks_today = int(attack_row["attacks_today"] or 0)
        if last_day != day:
            attacks_today = 0
        if attacks_today >= BOSS_DAILY_LIMIT:
            log_event(
                "world_boss_attack",
                user_id=user_id,
                success=False,
                rank=int(user.get("rank", 1) or 1),
                reason="LIMIT",
            )
            return {"success": False, "code": "LIMIT", "message": "今日攻击次数已用完"}, 400

        row = _ensure_boss_state(cur, for_update=True)
        snapshot = WorldBossSnapshot.from_row(row, now_ts=now, day_start_ts=midnight_timestamp())
        fsm = WorldBossFSM(snapshot)
        if fsm.should_daily_reset():
            fsm.apply_daily_reset()
            cur.execute(
                "UPDATE world_boss_state SET hp = %s, last_reset = %s WHERE boss_id = %s",
                (snapshot.hp, snapshot.last_reset, boss_cfg["id"]),
            )
            row = _ensure_boss_state(cur, for_update=True)
            snapshot = WorldBossSnapshot.from_row(row, now_ts=now, day_start_ts=midnight_timestamp())
            fsm = WorldBossFSM(snapshot)

        if not fsm.can_attack():
            log_event(
                "world_boss_attack",
                user_id=user_id,
                success=False,
                rank=int(user.get("rank", 1) or 1),
                reason="DEFEATED",
            )
            return {"success": False, "code": "DEFEATED", "message": "世界BOSS已被击败，请等待刷新"}, 400
        hp = int(snapshot.hp or 0)
        if not spend_user_stamina_tx(cur, user_id, BOSS_STAMINA_COST, now=now):
            current = get_user_by_id(user_id) or stamina_user or user
            log_event(
                "world_boss_attack",
                user_id=user_id,
                success=False,
                rank=int(user.get("rank", 1) or 1),
                reason="INSUFFICIENT_STAMINA",
            )
            return {
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": f"精力不足，攻击世界BOSS需要 {BOSS_STAMINA_COST} 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": BOSS_STAMINA_COST,
            }, 400
        user = get_user_by_id(user_id) or user

        battle_user = apply_sect_stat_buffs(user)
        base = int(battle_user.get("attack", 10) or 10)
        var_min = min(BOSS_DAMAGE_VAR_MIN, BOSS_DAMAGE_VAR_MAX)
        var_max = max(BOSS_DAMAGE_VAR_MIN, BOSS_DAMAGE_VAR_MAX)
        damage = max(1, int(base * random.uniform(var_min, var_max)))
        new_hp = max(0, hp - damage)

        cur.execute(
            "UPDATE world_boss_state SET hp = %s WHERE boss_id = %s",
            (new_hp, boss_cfg["id"]),
        )

        defeated = new_hp == 0
        rank = int(user.get("rank", 1) or 1)
        rewards = {
            "copper": max(
                BOSS_REWARD_MIN_COPPER,
                min(BOSS_REWARD_MAX_COPPER, BOSS_REWARD_BASE_COPPER + rank * BOSS_REWARD_PER_RANK_COPPER),
            ),
            "exp": max(
                BOSS_REWARD_MIN_EXP,
                min(BOSS_REWARD_MAX_EXP, BOSS_REWARD_BASE_EXP + rank * BOSS_REWARD_PER_RANK_EXP),
            ),
            "gold": 0,
            "items": [],
        }
        reward_mult = 1.0 + float(sect_buffs.get("battle_reward_pct", 0.0)) / 100.0
        rewards["copper"] = int(round(rewards["copper"] * reward_mult))
        rewards["exp"] = int(round(rewards["exp"] * reward_mult))

        if rank >= BOSS_RARE_DROP_MIN_RANK and random.random() < BOSS_RARE_DROP_CHANCE:
            rare_id = "dragon_scale" if random.random() < 0.5 else "phoenix_feather"
            generated = _grant_generated_item(cur, user_id, rare_id, 1)
            if generated:
                rewards["items"].append({"item_id": generated["item_id"], "quantity": generated.get("quantity", 1)})
        elif rank >= BOSS_MID_DROP_MIN_RANK and random.random() < BOSS_MID_DROP_CHANCE:
            generated = _grant_generated_item(cur, user_id, "demon_core", 1)
            if generated:
                rewards["items"].append({"item_id": generated["item_id"], "quantity": generated.get("quantity", 1)})

        cur.execute(
            "UPDATE users SET copper = copper + %s, exp = exp + %s WHERE user_id = %s",
            (rewards["copper"], rewards["exp"], user_id),
        )

        if defeated:
            rewards["copper"] += boss_cfg["reward_copper"]
            rewards["exp"] += boss_cfg["reward_exp"]
            rewards["gold"] += int(boss_cfg.get("reward_gold", 0) or 0)
            cur.execute(
                "UPDATE users SET copper = copper + %s, exp = exp + %s, gold = gold + %s WHERE user_id = %s",
                (boss_cfg["reward_copper"], boss_cfg["reward_exp"], rewards["gold"], user_id),
            )
            boss_bonus_id = (
                BOSS_DEFEAT_BONUS_RARE_ITEM_ID
                if rank >= BOSS_DEFEAT_BONUS_RARE_MIN_RANK
                else BOSS_DEFEAT_BONUS_COMMON_ITEM_ID
            )
            generated = _grant_generated_item(cur, user_id, boss_bonus_id, 1)
            if generated:
                rewards["items"].append({"item_id": generated["item_id"], "quantity": generated.get("quantity", 1)})
            cur.execute(
                "UPDATE world_boss_state SET last_defeated = %s WHERE boss_id = %s",
                (now, boss_cfg["id"]),
            )

        attacks_used_today = attacks_today + 1
        cur.execute(
            "UPDATE world_boss_attacks SET last_attack_day = %s, attacks_today = %s WHERE user_id = %s",
            (day, attacks_used_today, user_id),
        )
        event_point_grants = _apply_action_points(
            cur,
            user_id,
            "world_boss_attack",
            now_ts=now,
            meta={"damage": damage, "defeated": defeated},
        )

    log_event(
        "world_boss_attack",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"damage": damage, "defeated": defeated},
    )
    log_economy_ledger(
        user_id=user_id,
        module="world_boss",
        action="world_boss_attack",
        delta_copper=int(rewards.get("copper", 0) or 0),
        delta_gold=int(rewards.get("gold", 0) or 0),
        delta_exp=int(rewards.get("exp", 0) or 0),
        delta_stamina=-BOSS_STAMINA_COST,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"defeated": defeated, "damage": damage},
    )
    return {
        "success": True,
        "damage": damage,
        "boss_hp": new_hp,
        "defeated": defeated,
        "rewards": rewards,
        "attacks_left": max(0, BOSS_DAILY_LIMIT - attacks_used_today),
        "event_points": event_point_grants,
    }, 200


def exchange_event_points(user_id: str, event_id: str, exchange_id: str, *, quantity: int = 1) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return {"success": False, "code": "INVALID_PARAMS", "message": "兑换数量必须是整数且大于 0"}, 400
    if qty <= 0:
        return {"success": False, "code": "INVALID_PARAMS", "message": "兑换数量必须是整数且大于 0"}, 400
    active_map = {e["id"]: e for e in get_active_events()}
    event = active_map.get(event_id)
    if not event:
        return {"success": False, "code": "INVALID", "message": "活动不存在或未开启"}, 400
    exchange = next((x for x in (event.get("exchange_shop", []) or []) if x.get("id") == exchange_id), None)
    if not exchange:
        return {"success": False, "code": "INVALID", "message": "兑换项不存在"}, 400
    cost_points = max(1, int(exchange.get("cost_points", 0) or 0))
    total_cost = cost_points * qty
    now = int(time.time())
    period = str(exchange.get("period") or "event")
    period_key = _event_period_key(now, period)
    reward_spec = exchange.get("rewards", {}) or {}
    awarded_items: List[Dict[str, Any]] = []
    total = 0
    spent = 0
    balance = 0
    points_balance_for_error = 0
    remaining_for_error = 0

    try:
        with db_transaction() as cur:
            _ensure_event_point_tables(cur)
            cur.execute(
                """
                INSERT INTO event_points(user_id, event_id, points_total, points_spent, updated_at)
                VALUES(%s, %s, 0, 0, %s)
                ON CONFLICT(user_id, event_id) DO NOTHING
                """,
                (user_id, event_id, now),
            )
            cur.execute(
                "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s FOR UPDATE",
                (user_id, event_id),
            )
            row = cur.fetchone()
            points_total = int((row["points_total"] if row else 0) or 0)
            points_spent = int((row["points_spent"] if row else 0) or 0)
            points_balance = max(0, points_total - points_spent)
            points_balance_for_error = points_balance
            if points_balance < total_cost:
                raise ValueError("INSUFFICIENT_POINTS")

            limit = int(exchange.get("limit", 0) or 0)
            cur.execute(
                """
                INSERT INTO event_exchange_claims(user_id, event_id, exchange_id, period_key, quantity)
                VALUES(%s,%s,%s,%s,0)
                ON CONFLICT(user_id, event_id, exchange_id, period_key) DO NOTHING
                """,
                (user_id, event_id, exchange_id, period_key),
            )
            cur.execute(
                """
                SELECT quantity FROM event_exchange_claims
                WHERE user_id = %s AND event_id = %s AND exchange_id = %s AND period_key = %s
                FOR UPDATE
                """,
                (user_id, event_id, exchange_id, period_key),
            )
            claim_row = cur.fetchone()
            claimed = int((claim_row["quantity"] if claim_row else 0) or 0)
            if limit > 0 and claimed + qty > limit:
                remaining_for_error = max(0, limit - claimed)
                raise ValueError("LIMIT")

            cur.execute(
                """
                UPDATE event_points
                SET points_spent = points_spent + %s, updated_at = %s
                WHERE user_id = %s AND event_id = %s
                  AND points_total - points_spent >= %s
                """,
                (total_cost, now, user_id, event_id, total_cost),
            )
            if int(cur.rowcount or 0) == 0:
                cur.execute(
                    "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s",
                    (user_id, event_id),
                )
                latest_row = cur.fetchone()
                latest_total = int((latest_row["points_total"] if latest_row else 0) or 0)
                latest_spent = int((latest_row["points_spent"] if latest_row else 0) or 0)
                points_balance_for_error = max(0, latest_total - latest_spent)
                raise ValueError("INSUFFICIENT_POINTS")

            if limit > 0:
                cur.execute(
                    """
                    UPDATE event_exchange_claims
                    SET quantity = quantity + %s
                    WHERE user_id = %s AND event_id = %s AND exchange_id = %s AND period_key = %s
                      AND quantity + %s <= %s
                    """,
                    (qty, user_id, event_id, exchange_id, period_key, qty, limit),
                )
            else:
                cur.execute(
                    """
                    UPDATE event_exchange_claims
                    SET quantity = quantity + %s
                    WHERE user_id = %s AND event_id = %s AND exchange_id = %s AND period_key = %s
                    """,
                    (qty, user_id, event_id, exchange_id, period_key),
                )
            if int(cur.rowcount or 0) == 0:
                cur.execute(
                    """
                    SELECT quantity FROM event_exchange_claims
                    WHERE user_id = %s AND event_id = %s AND exchange_id = %s AND period_key = %s
                    """,
                    (user_id, event_id, exchange_id, period_key),
                )
                latest_claim = cur.fetchone()
                latest_claimed = int((latest_claim["quantity"] if latest_claim else 0) or 0)
                remaining_for_error = max(0, limit - latest_claimed) if limit > 0 else 0
                raise ValueError("LIMIT")

            cur.execute(
                "INSERT INTO event_point_logs(user_id, event_id, delta_points, source, meta_json, created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    user_id,
                    event_id,
                    -total_cost,
                    "exchange",
                    json.dumps({"exchange_id": exchange_id, "quantity": qty}, ensure_ascii=False),
                    now,
                ),
            )
            cur.execute(
                "UPDATE users SET copper = copper + %s, exp = exp + %s, gold = gold + %s WHERE user_id = %s",
                (
                    int(reward_spec.get("copper", 0) or 0) * qty,
                    int(reward_spec.get("exp", 0) or 0) * qty,
                    int(reward_spec.get("gold", 0) or 0) * qty,
                    user_id,
                ),
            )
            for item in reward_spec.get("items", []) or []:
                generated = _grant_generated_item(
                    cur,
                    user_id,
                    item.get("item_id"),
                    int(item.get("quantity", 1) or 1) * qty,
                )
                if generated:
                    awarded_items.append({"item_id": generated.get("item_id"), "quantity": int(generated.get("quantity", 1) or 1)})
            cur.execute(
                "SELECT points_total, points_spent FROM event_points WHERE user_id = %s AND event_id = %s",
                (user_id, event_id),
            )
            final_row = cur.fetchone()
            total = int((final_row["points_total"] if final_row else 0) or 0)
            spent = int((final_row["points_spent"] if final_row else 0) or 0)
            balance = max(0, total - spent)
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_POINTS":
            return {
                "success": False,
                "code": "INSUFFICIENT_POINTS",
                "message": f"积分不足，需要 {total_cost}，当前 {points_balance_for_error}",
                "points_balance": points_balance_for_error,
            }, 400
        return {
            "success": False,
            "code": "LIMIT",
            "message": f"兑换次数不足，本周期剩余 {remaining_for_error} 次",
            "remaining": remaining_for_error,
        }, 400

    return {
        "success": True,
        "message": "兑换成功",
        "event_id": event_id,
        "exchange_id": exchange_id,
        "quantity": qty,
        "cost_points": total_cost,
        "points_total": total,
        "points_spent": spent,
        "points_balance": balance,
        "rewards": {
            "copper": int(reward_spec.get("copper", 0) or 0) * qty,
            "exp": int(reward_spec.get("exp", 0) or 0) * qty,
            "gold": int(reward_spec.get("gold", 0) or 0) * qty,
            "items": awarded_items,
        },
    }, 200
