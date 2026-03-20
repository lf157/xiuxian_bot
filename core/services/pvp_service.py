"""PVP service layer.

Handles matchmaking, battle settlement, ratings, and records.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import config
from core.database.connection import (
    get_user_by_id,
    fetch_all,
    fetch_one,
    db_transaction,
    refresh_user_stamina,
)
from core.database.migrations import reserve_request, save_response
from core.database.connection import get_user_skills
from core.game.combat import pvp_battle
from core.game.leaderboards import calculate_power
from core.services.metrics_service import log_event, log_economy_ledger
from core.utils.timeutil import midnight_timestamp
from core.utils.number import format_stamina_value


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("pvp", key, default=default))
    except Exception:
        return int(default)


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(config.get_nested("pvp", key, default=default))
    except Exception:
        return float(default)


def _cfg_pair(path: tuple[str, str], default_low: int, default_high: int) -> tuple[int, int]:
    raw = config.get_nested("pvp", "reward_ranges", path[0], path[1], default=[default_low, default_high])
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            low = int(raw[0])
            high = int(raw[1])
            if low > high:
                low, high = high, low
            return low, high
        except (TypeError, ValueError):
            pass
    return default_low, default_high


PVP_DAILY_LIMIT = _cfg_int("daily_limit", 10)
PVP_K_FACTOR = _cfg_int("k_factor", 32)
PVP_RATING_RANGE = _cfg_int("rating_range", 200)
PVP_RANK_RANGE = _cfg_int("rank_range", 5)
PVP_RECENT_BLOCK_HOURS = _cfg_int("recent_block_hours", 24)
PVP_DEFENSE_DAILY_LIMIT = _cfg_int("defense_daily_limit", 12)
PVP_DEFENSE_REWARD_COPPER = _cfg_int("defense_reward_copper", 20)
PVP_DEFENSE_REWARD_EXP = _cfg_int("defense_reward_exp", 10)
PVP_POWER_RATIO_MIN = _cfg_float("power_ratio_min", 0.75)
PVP_POWER_RATIO_MAX = _cfg_float("power_ratio_max", 1.33)
PVP_POWER_OUTSIDE_MODE = str(config.get_nested("pvp", "power_outside_mode", default="friendly") or "friendly").lower()
PVP_REWARD_WIN_COPPER_RANGE = _cfg_pair(("win", "copper"), 50, 200)
PVP_REWARD_WIN_EXP_RANGE = _cfg_pair(("win", "exp"), 30, 150)
PVP_REWARD_LOSS_COPPER_RANGE = _cfg_pair(("loss", "copper"), 10, 30)
PVP_REWARD_LOSS_EXP_RANGE = _cfg_pair(("loss", "exp"), 10, 30)
PVP_REWARD_DRAW_COPPER_RANGE = _cfg_pair(("draw", "copper"), 20, 50)
PVP_REWARD_DRAW_EXP_RANGE = _cfg_pair(("draw", "exp"), 15, 50)
PVP_EXPECT_WEIGHT_RATING = _cfg_float("expected_win_weight_rating", 0.6)
PVP_EXPECT_WEIGHT_POWER = _cfg_float("expected_win_weight_power", 0.4)
PVP_EXPECT_MIN = _cfg_float("expected_win_rate_min", 0.05)
PVP_EXPECT_MAX = _cfg_float("expected_win_rate_max", 0.95)
PVP_RISK_ADVANTAGE_THRESHOLD = _cfg_float("risk_label_advantage_threshold", 0.6)
PVP_RISK_BALANCE_THRESHOLD = _cfg_float("risk_label_balance_threshold", 0.45)
PVP_STAMINA_COST = max(1, _cfg_int("stamina_cost", 1))


def calculate_elo_change(winner_rating: int, loser_rating: int, k: int = PVP_K_FACTOR) -> Tuple[int, int]:
    """Return (winner_change, loser_change)."""
    expected_winner = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    expected_loser = 1 - expected_winner
    winner_change = int(k * (1 - expected_winner))
    loser_change = int(k * (0 - expected_loser))
    # ensure minimum impact
    winner_change = max(5, winner_change)
    loser_change = min(-5, loser_change)
    return winner_change, loser_change


def calculate_draw_change(rating_a: int, rating_b: int, k: int = PVP_K_FACTOR) -> Tuple[int, int]:
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    change_a = int(round(k * (0.5 - expected_a)))
    change_b = -change_a
    return change_a, change_b


def _scale_by_rank(rank: int) -> float:
    base = max(1, min(int(rank or 1), 32))
    return 1.0 + (base - 1) / 20.0


def _reward_for_result(rank: int, outcome: str) -> Dict[str, int]:
    scale = _scale_by_rank(rank)
    if outcome == "win":
        copper = int(random.randint(*PVP_REWARD_WIN_COPPER_RANGE) * scale)
        exp = int(random.randint(*PVP_REWARD_WIN_EXP_RANGE) * scale)
    elif outcome == "loss":
        copper = int(random.randint(*PVP_REWARD_LOSS_COPPER_RANGE) * scale)
        exp = int(random.randint(*PVP_REWARD_LOSS_EXP_RANGE) * scale)
    else:  # draw
        copper = int(random.randint(*PVP_REWARD_DRAW_COPPER_RANGE) * scale)
        exp = int(random.randint(*PVP_REWARD_DRAW_EXP_RANGE) * scale)
    return {"copper": max(0, copper), "exp": max(0, exp)}


def _defense_reward(rank: int) -> Dict[str, int]:
    scale = _scale_by_rank(rank)
    return {
        "copper": max(0, int(PVP_DEFENSE_REWARD_COPPER * scale)),
        "exp": max(0, int(PVP_DEFENSE_REWARD_EXP * scale)),
    }


def _power_ratio(a: int, b: int) -> float:
    denom = max(1, int(b or 0))
    return float(int(a or 0)) / float(denom)


def _expected_win_rate(
    my_power: int,
    opp_power: int,
    my_rating: int,
    opp_rating: int,
) -> float:
    rating_expect = 1 / (1 + 10 ** ((opp_rating - my_rating) / 400))
    ratio = _power_ratio(my_power, opp_power)
    power_expect = ratio / (1 + ratio) if ratio > 0 else 0.0
    expected = rating_expect * PVP_EXPECT_WEIGHT_RATING + power_expect * PVP_EXPECT_WEIGHT_POWER
    lo = min(PVP_EXPECT_MIN, PVP_EXPECT_MAX)
    hi = max(PVP_EXPECT_MIN, PVP_EXPECT_MAX)
    return max(lo, min(hi, float(expected)))


def _risk_label(expected: float) -> str:
    if expected >= PVP_RISK_ADVANTAGE_THRESHOLD:
        return "优势"
    if expected >= PVP_RISK_BALANCE_THRESHOLD:
        return "均势"
    return "劣势"


def _recent_opponent_ids(user_id: str, since_ts: int) -> set[str]:
    rows = fetch_all(
        """SELECT challenger_id, defender_id FROM pvp_records
           WHERE (challenger_id = ? OR defender_id = ?) AND timestamp >= ?""",
        (user_id, user_id, since_ts),
    )
    blocked: set[str] = set()
    for row in rows:
        other = row["defender_id"] if row["challenger_id"] == user_id else row["challenger_id"]
        if other:
            blocked.add(other)
    return blocked


def _defender_daily_count(user_id: str, since_ts: int) -> int:
    row = fetch_one(
        "SELECT COUNT(1) AS c FROM pvp_records WHERE defender_id = ? AND timestamp >= ?",
        (user_id, since_ts),
    )
    return int(row.get("c", 0) or 0) if row else 0


def get_opponents(user_id: str, limit: int = 3) -> Optional[List[Dict[str, Any]]]:
    user = get_user_by_id(user_id)
    if not user:
        return None

    rating = int(user.get("pvp_rating", 1000) or 1000)
    rank = int(user.get("rank", 1) or 1)
    my_power = calculate_power(user)
    limit = max(1, min(int(limit or 3), 10))

    since_ts = int(time.time()) - (PVP_RECENT_BLOCK_HOURS * 3600)
    recent_blocked = _recent_opponent_ids(user_id, since_ts)

    candidates = fetch_all(
        """SELECT user_id, in_game_username, rank, pvp_rating, element, attack, defense, max_hp, max_mp
           FROM users
           WHERE user_id != ? AND rank BETWEEN ? AND ?
           AND pvp_rating BETWEEN ? AND ?
           ORDER BY ABS(pvp_rating - ?) ASC
           LIMIT ?""",
        (
            user_id,
            max(1, rank - PVP_RANK_RANGE),
            rank + PVP_RANK_RANGE,
            max(0, rating - PVP_RATING_RANGE),
            rating + PVP_RATING_RANGE,
            rating,
            limit * 4,
        ),
    )

    filtered = [c for c in candidates if c["user_id"] not in recent_blocked]
    if len(filtered) < limit:
        fallback = fetch_all(
            "SELECT user_id, in_game_username, rank, pvp_rating, element, attack, defense, max_hp, max_mp FROM users WHERE user_id != ? ORDER BY RANDOM() LIMIT ?",
            (user_id, limit * 2),
        )
        for row in fallback:
            if row["user_id"] == user_id or row["user_id"] in recent_blocked:
                continue
            filtered.append(row)
            if len(filtered) >= limit:
                break

    seen: set[str] = set()
    unique_rows = []
    for row in filtered:
        uid = row.get("user_id")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        unique_rows.append(row)

    enriched = []
    for row in unique_rows:
        entry = dict(row)
        entry_power = calculate_power(entry)
        opp_rating = int(entry.get("pvp_rating", 1000) or 1000)
        ratio = _power_ratio(my_power, entry_power)
        expected = _expected_win_rate(my_power, entry_power, rating, opp_rating)
        within_range = PVP_POWER_RATIO_MIN <= ratio <= PVP_POWER_RATIO_MAX
        enriched.append({
            "user_id": entry["user_id"],
            "username": entry.get("in_game_username", "未知修士"),
            "rank": entry.get("rank", 1),
            "power": entry_power,
            "power_ratio": round(ratio, 3),
            "within_power_range": within_range,
            "expected_win_rate": round(expected, 3),
            "risk_label": _risk_label(expected),
            "pvp_rating": opp_rating,
            "element": entry.get("element"),
        })

    in_range = [entry for entry in enriched if entry["within_power_range"]]
    out_range = [entry for entry in enriched if not entry["within_power_range"]]
    selected = in_range[:limit]
    if len(selected) < limit:
        selected.extend(out_range[: max(0, limit - len(selected))])
    return selected


def do_pvp_challenge(user_id: str, opponent_id: str, request_id: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="pvp_challenge")
        if status == "cached" and cached:
            return cached, 200
        if status == "in_progress":
            return {
                "success": False,
                "code": "REQUEST_IN_PROGRESS",
                "message": "请求处理中，请稍后重试",
            }, 409

    def _dedup_return(resp: Dict[str, Any], http_status: int) -> Tuple[Dict[str, Any], int]:
        if request_id:
            save_response(request_id, user_id, "pvp_challenge", resp)
        return resp, http_status

    if not user_id or not opponent_id or user_id == opponent_id:
        return _dedup_return({"success": False, "code": "INVALID", "message": "Invalid opponent"}, 400)

    challenger = get_user_by_id(user_id)
    defender = get_user_by_id(opponent_id)
    if not challenger or not defender:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)
    if challenger.get("state"):
        return _dedup_return({"success": False, "code": "IN_CULTIVATION", "message": "请先结束修炼"}, 400)
    if defender.get("state"):
        return _dedup_return({"success": False, "code": "DEFENDER_BUSY", "message": "对方正在修炼"}, 400)

    now = int(time.time())
    today_midnight = midnight_timestamp()
    challenger = refresh_user_stamina(user_id, now=now) or challenger

    recent_blocked = _recent_opponent_ids(user_id, now - PVP_RECENT_BLOCK_HOURS * 3600)
    if opponent_id in recent_blocked:
        log_event(
            "pvp_challenge",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=int(challenger.get("rank", 1) or 1),
            reason="RECENT_OPPONENT",
            meta={"opponent_id": opponent_id},
        )
        return _dedup_return({"success": False, "code": "RECENT_OPPONENT", "message": "近期已挑战过该对手，请稍后再试"}, 400)

    defender_today = _defender_daily_count(opponent_id, today_midnight)
    if defender_today >= PVP_DEFENSE_DAILY_LIMIT:
        log_event(
            "pvp_challenge",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=int(challenger.get("rank", 1) or 1),
            reason="DEFENDER_PROTECTED",
            meta={"opponent_id": opponent_id},
        )
        return _dedup_return({"success": False, "code": "DEFENDER_PROTECTED", "message": "对方今日被挑战次数已达上限"}, 400)

    daily_reset = int(challenger.get("pvp_daily_reset", 0) or 0)
    daily_count = int(challenger.get("pvp_daily_count", 0) or 0)
    effective_daily_count = 0 if daily_reset < today_midnight else daily_count
    if effective_daily_count >= PVP_DAILY_LIMIT:
        log_event(
            "pvp_challenge",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=int(challenger.get("rank", 1) or 1),
            reason="LIMIT",
        )
        return _dedup_return({"success": False, "code": "LIMIT", "message": "今日PVP次数已用完"}, 400)
    try:
        current_stamina = float(challenger.get("stamina", 0) or 0)
    except (TypeError, ValueError):
        current_stamina = 0.0
    if current_stamina < PVP_STAMINA_COST:
        log_event(
            "pvp_challenge",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=int(challenger.get("rank", 1) or 1),
            reason="INSUFFICIENT_STAMINA",
        )
        return _dedup_return({
            "success": False,
            "code": "INSUFFICIENT_STAMINA",
            "message": f"精力不足，PVP 挑战需要 {PVP_STAMINA_COST} 点精力",
            "stamina": format_stamina_value(challenger.get("stamina", 0)),
            "stamina_cost": PVP_STAMINA_COST,
        }, 400)

    c_skills = get_user_skills(user_id)
    d_skills = get_user_skills(opponent_id)
    result = pvp_battle(challenger, defender, c_skills, d_skills)

    c_rating = int(challenger.get("pvp_rating", 1000) or 1000)
    d_rating = int(defender.get("pvp_rating", 1000) or 1000)
    c_power = calculate_power(challenger)
    d_power = calculate_power(defender)
    power_ratio = _power_ratio(c_power, d_power)
    expected_rate = _expected_win_rate(c_power, d_power, c_rating, d_rating)
    risk_label = _risk_label(expected_rate)
    within_power_range = PVP_POWER_RATIO_MIN <= power_ratio <= PVP_POWER_RATIO_MAX
    friendly_mode = False
    if not within_power_range:
        if PVP_POWER_OUTSIDE_MODE == "block":
            return _dedup_return({
                "success": False,
                "code": "POWER_GAP",
                "message": "对手战力差距过大，仅支持友谊战",
                "power_ratio": round(power_ratio, 3),
                "expected_win_rate": round(expected_rate, 3),
                "risk_label": risk_label,
            }, 400)
        friendly_mode = True

    outcome = "draw" if result.get("draw") else ("win" if result.get("winner_id") == user_id else "loss")
    if friendly_mode:
        c_change, d_change = 0, 0
        rewards = {"copper": 0, "exp": 0, "friendly": True, "reason": "FRIENDLY"}
        defense_reward = {"copper": 0, "exp": 0}
    else:
        if outcome == "draw":
            c_change, d_change = calculate_draw_change(c_rating, d_rating)
        elif outcome == "win":
            c_change, d_change = calculate_elo_change(c_rating, d_rating)
        else:
            d_change, c_change = calculate_elo_change(d_rating, c_rating)
        rewards = _reward_for_result(int(challenger.get("rank", 1) or 1), outcome)
        rewards["reason"] = "NORMAL"
        defense_reward = _defense_reward(int(defender.get("rank", 1) or 1))
    new_daily_count = effective_daily_count + 1

    try:
        with db_transaction() as cur:
            if friendly_mode:
                cur.execute(
                    """UPDATE users SET
                       stamina = stamina - ?, stamina_updated_at = ?,
                       pvp_daily_count = (CASE WHEN pvp_daily_reset < ? THEN 0 ELSE pvp_daily_count END) + 1,
                       pvp_daily_reset = CASE WHEN pvp_daily_reset < ? THEN ? ELSE pvp_daily_reset END
                       WHERE user_id = ?
                         AND (CASE WHEN pvp_daily_reset < ? THEN 0 ELSE pvp_daily_count END) < ?
                         AND stamina >= ?""",
                    (
                        PVP_STAMINA_COST,
                        now,
                        today_midnight,
                        today_midnight,
                        now,
                        user_id,
                        today_midnight,
                        PVP_DAILY_LIMIT,
                        PVP_STAMINA_COST,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("LIMIT_OR_STAMINA")
                cur.execute(
                    """INSERT INTO pvp_records
                       (challenger_id, defender_id, winner_id, rounds,
                        challenger_rating_before, defender_rating_before,
                        challenger_rating_after, defender_rating_after,
                        rewards_json, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        opponent_id,
                        result.get("winner_id"),
                        int(result.get("rounds", 0) or 0),
                        c_rating,
                        d_rating,
                        c_rating,
                        d_rating,
                        json.dumps(rewards, ensure_ascii=False),
                        now,
                    ),
                )
            else:
                cur.execute(
                    """UPDATE users SET
                       pvp_rating = ?, pvp_wins = pvp_wins + ?, pvp_losses = pvp_losses + ?, pvp_draws = pvp_draws + ?,
                       copper = copper + ?, exp = exp + ?,
                       stamina = stamina - ?, stamina_updated_at = ?,
                       pvp_daily_count = (CASE WHEN pvp_daily_reset < ? THEN 0 ELSE pvp_daily_count END) + 1,
                       pvp_daily_reset = CASE WHEN pvp_daily_reset < ? THEN ? ELSE pvp_daily_reset END
                       WHERE user_id = ?
                         AND (CASE WHEN pvp_daily_reset < ? THEN 0 ELSE pvp_daily_count END) < ?
                         AND stamina >= ?""",
                    (
                        max(0, c_rating + c_change),
                        1 if outcome == "win" else 0,
                        1 if outcome == "loss" else 0,
                        1 if outcome == "draw" else 0,
                        rewards.get("copper", 0),
                        rewards.get("exp", 0),
                        PVP_STAMINA_COST,
                        now,
                        today_midnight,
                        today_midnight,
                        now,
                        user_id,
                        today_midnight,
                        PVP_DAILY_LIMIT,
                        PVP_STAMINA_COST,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("LIMIT_OR_STAMINA")

                cur.execute(
                    """UPDATE users SET
                       pvp_rating = ?, pvp_wins = pvp_wins + ?, pvp_losses = pvp_losses + ?, pvp_draws = pvp_draws + ?,
                       copper = copper + ?, exp = exp + ?
                       WHERE user_id = ?""",
                    (
                        max(0, d_rating + d_change),
                        1 if outcome == "loss" else 0,
                        1 if outcome == "win" else 0,
                        1 if outcome == "draw" else 0,
                        defense_reward.get("copper", 0),
                        defense_reward.get("exp", 0),
                        opponent_id,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("NOT_FOUND")

                cur.execute(
                    """INSERT INTO pvp_records
                       (challenger_id, defender_id, winner_id, rounds,
                        challenger_rating_before, defender_rating_before,
                        challenger_rating_after, defender_rating_after,
                        rewards_json, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        opponent_id,
                        result.get("winner_id"),
                        int(result.get("rounds", 0) or 0),
                        c_rating,
                        d_rating,
                        max(0, c_rating + c_change),
                        max(0, d_rating + d_change),
                        json.dumps(rewards, ensure_ascii=False),
                        now,
                    ),
                )
    except ValueError as exc:
        reason = str(exc)
        if reason == "NOT_FOUND":
            return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)
        fresh = get_user_by_id(user_id)
        if not fresh:
            return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)
        fresh_daily_count = int(fresh.get("pvp_daily_count", 0) or 0)
        fresh_daily_reset = int(fresh.get("pvp_daily_reset", 0) or 0)
        fresh_effective = 0 if fresh_daily_reset < today_midnight else fresh_daily_count
        if fresh_effective >= PVP_DAILY_LIMIT:
            return _dedup_return({"success": False, "code": "LIMIT", "message": "今日PVP次数已用完"}, 400)
        return _dedup_return({
            "success": False,
            "code": "INSUFFICIENT_STAMINA",
            "message": f"精力不足，PVP 挑战需要 {PVP_STAMINA_COST} 点精力",
            "stamina": format_stamina_value(fresh.get("stamina", 0)),
            "stamina_cost": PVP_STAMINA_COST,
        }, 400)

    result["rating_change"] = {"challenger": c_change, "defender": d_change}
    result["rewards"] = rewards
    result["new_rating"] = max(0, c_rating + c_change)
    result["daily_count"] = new_daily_count
    result["defense_reward"] = defense_reward
    result["friendly"] = friendly_mode
    result["power_ratio"] = round(power_ratio, 3)
    result["expected_win_rate"] = round(expected_rate, 3)
    result["risk_label"] = risk_label
    result["within_power_range"] = within_power_range

    log_event(
        "pvp_challenge",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(challenger.get("rank", 1) or 1),
        meta={
            "opponent_id": opponent_id,
            "outcome": outcome,
            "friendly": friendly_mode,
            "reward_reason": rewards.get("reason"),
            "power_ratio": round(power_ratio, 3),
            "expected_win_rate": round(expected_rate, 3),
            "risk_label": risk_label,
        },
    )
    if not friendly_mode:
        log_economy_ledger(
            user_id=user_id,
            module="pvp",
            action="pvp_challenge",
            delta_copper=int(rewards.get("copper", 0) or 0),
            delta_exp=int(rewards.get("exp", 0) or 0),
            delta_stamina=-PVP_STAMINA_COST,
            success=True,
            request_id=request_id,
            rank=int(challenger.get("rank", 1) or 1),
            meta={"opponent_id": opponent_id, "outcome": outcome},
        )
        log_economy_ledger(
            user_id=opponent_id,
            module="pvp",
            action="pvp_defense",
            delta_copper=int(defense_reward.get("copper", 0) or 0),
            delta_exp=int(defense_reward.get("exp", 0) or 0),
            success=True,
            request_id=request_id,
            rank=int(defender.get("rank", 1) or 1),
            meta={"challenger_id": user_id},
        )

    return _dedup_return(result, 200)


def get_pvp_records(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))
    rows = fetch_all(
        """SELECT r.*, cu.in_game_username AS challenger_name, du.in_game_username AS defender_name
           FROM pvp_records r
           LEFT JOIN users cu ON r.challenger_id = cu.user_id
           LEFT JOIN users du ON r.defender_id = du.user_id
           WHERE r.challenger_id = ? OR r.defender_id = ?
           ORDER BY r.timestamp DESC
           LIMIT ?""",
        (user_id, user_id, limit),
    )
    results: List[Dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        try:
            entry["rewards"] = json.loads(entry.get("rewards_json") or "{}")
        except Exception:
            entry["rewards"] = {}
        results.append(entry)
    return results


def get_pvp_ranking(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))
    rows = fetch_all(
        """SELECT user_id, in_game_username, rank, element, pvp_rating, pvp_wins, pvp_losses, pvp_draws,
                  attack, defense, max_hp, max_mp, exp
           FROM users
           ORDER BY pvp_rating DESC, pvp_wins DESC
           LIMIT ?""",
        (limit,),
    )
    ranking = []
    for row in rows:
        entry = dict(row)
        entry["power"] = calculate_power(entry)
        ranking.append({
            "user_id": entry["user_id"],
            "username": entry.get("in_game_username", "未知修士"),
            "rank": entry.get("rank", 1),
            "element": entry.get("element"),
            "pvp_rating": int(entry.get("pvp_rating", 1000) or 1000),
            "pvp_wins": int(entry.get("pvp_wins", 0) or 0),
            "pvp_losses": int(entry.get("pvp_losses", 0) or 0),
            "pvp_draws": int(entry.get("pvp_draws", 0) or 0),
            "power": entry["power"],
        })
    return ranking
