"""修炼 / 突破 / 签到路由。"""

import time

from flask import Blueprint, request, jsonify

from core.routes._helpers import (
    error,
    success,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.config import config
from core.database.connection import (
    get_user_by_id,
    execute,
    fetch_one,
    get_user_skills,
)
from core.game.mechanics import (
    start_cultivation,
    calculate_cultivation_progress,
)
from core.game.realms import can_breakthrough
from core.game.skills import get_skill, scale_skill_effect
from core.game.signin import check_signed_today, format_signin_status
from core.services.settlement_extra import settle_signin, settle_breakthrough
from core.services.quests_service import increment_quest
from core.services.sect_service import get_user_sect_buffs
from core.services.stats_service import recalculate_user_combat_stats
from core.services.metrics_service import log_event, log_economy_ledger
from core.services.realm_trials_service import get_or_create_realm_trial
from core.services.story_service import track_story_action

cultivation_bp = Blueprint("cultivation", __name__)


@cultivation_bp.route("/api/cultivate/start", methods=["POST"])
def cultivate_start():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    log_action("cultivate_start", user_id=user_id)
    if not user_id:
        return error("ERROR", "Missing user_id", 400)

    user = get_user_by_id(user_id)
    if not user:
        log_event("cultivate_start", user_id=user_id, success=False, reason="USER_NOT_FOUND")
        return error("ERROR", "User not found", 404)
    if user.get("state"):
        log_event("cultivate_start", user_id=user_id, success=False, reason="ALREADY")
        return error("ERROR", "Already cultivating", 400)

    # 检查虚弱状态
    weak_until = user.get("weak_until", 0)
    if weak_until > time.time():
        log_event("cultivate_start", user_id=user_id, success=False, reason="WEAK")
        return error(
            "WEAK",
            f"虚弱状态中，还需等待 {int((weak_until - time.time()) / 60)} 分钟",
            400,
        )

    timing = start_cultivation(user_id, user.get("element"))

    # Apply cultivation skill bonus (凝神吐纳)
    learned_skills = get_user_skills(user_id)
    cul_bonus = 0.0
    for row in learned_skills:
        sk = get_skill(row.get("skill_id"))
        if sk:
            sk = scale_skill_effect(sk, int(row.get("skill_level", 1) or 1))
        if sk and sk.get("effect", {}).get("cultivation_bonus_pct"):
            cul_bonus += sk["effect"]["cultivation_bonus_pct"]
    if cul_bonus > 0:
        timing["base_gain"] = int(timing["base_gain"] * (1 + cul_bonus))
    sect_buffs = get_user_sect_buffs(user_id)
    if sect_buffs.get("in_sect"):
        timing["base_gain"] = int(timing["base_gain"] * (1 + float(sect_buffs.get("cultivation_pct", 0.0)) / 100.0))

    # Apply cultivation sprint pill bonus (next session only)
    now = time.time()
    boost_until = int(user.get("cultivation_boost_until", 0) or 0)
    boost_pct = float(user.get("cultivation_boost_pct", 0) or 0)
    boost_applied = False
    boost_mult = 1.0
    if boost_until > now:
        if boost_pct > 0:
            boost_mult = 1.0 + boost_pct / 100.0
        else:
            cfg = config.get_nested("balance", "pill_buffs", "cultivation_sprint", default={}) or {}
            boost_mult = float(cfg.get("exp_mult", 1.35))
        timing["base_gain"] = int(timing["base_gain"] * boost_mult)
        boost_applied = True

    execute(
        "INSERT INTO timings (user_id, start_time, type, base_gain) VALUES (?, ?, ?, ?)",
        (user_id, timing["start_time"], timing["type"], timing["base_gain"]),
    )
    if boost_applied:
        execute(
            "UPDATE users SET state = 1, cultivation_boost_until = 0, cultivation_boost_pct = 0 WHERE user_id = ?",
            (user_id,),
        )
    else:
        execute("UPDATE users SET state = 1 WHERE user_id = ?", (user_id,))

    log_event(
        "cultivate_start",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={
            "base_gain": timing["base_gain"],
            "boost_applied": boost_applied,
            "boost_mult": boost_mult,
        },
    )
    return success(
        start_time=timing["start_time"],
        gain_per_hour=timing["base_gain"],
        sprint_boost_applied=boost_applied,
        sprint_boost_mult=boost_mult,
        sect_cultivation_bonus_pct=float(sect_buffs.get("cultivation_pct", 0.0)),
    )


@cultivation_bp.route("/api/cultivate/end", methods=["POST"])
def cultivate_end():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    log_action("cultivate_end", user_id=user_id)
    if not user_id:
        return error("ERROR", "Missing user_id", 400)

    user = get_user_by_id(user_id)
    if not user or not user.get("state"):
        log_event("cultivate_end", user_id=user_id, success=False, reason="NOT_CULTIVATING")
        return error("ERROR", "Not cultivating", 400)

    timing = fetch_one(
        "SELECT * FROM timings WHERE user_id = ? AND type = 'cultivation'", (user_id,)
    )
    if not timing:
        # recover: timing missing but user is marked cultivating
        execute("UPDATE users SET state = 0 WHERE user_id = ?", (user_id,))
        current_exp = int(user.get("exp", 0) or 0)
        log_event(
            "cultivate_end",
            user_id=user_id,
            success=True,
            rank=int(user.get("rank", 1) or 1),
            meta={"recovered": True, "gain": 0},
        )
        return success(
            gain=0,
            total_exp=current_exp,
            can_breakthrough=can_breakthrough(current_exp, user.get("rank", 1)),
            hours=0,
            efficiency=0,
            tip="修炼记录异常，已重置状态。",
            recovered=True,
        )

    gain_result = calculate_cultivation_progress(timing)
    gain = gain_result["exp"]
    execute("DELETE FROM timings WHERE id = ?", (timing["id"],))
    execute(
        "UPDATE users SET state = 0, exp = exp + ? WHERE user_id = ?", (gain, user_id)
    )

    # Quest progress: cultivate
    increment_quest(user_id, "daily_cultivate")
    story_update = []
    try:
        story_update = track_story_action(user_id, "cultivate_end")
    except Exception:
        story_update = []

    # 检查是否可以突破
    new_exp = user.get("exp", 0) + gain
    can_break = can_breakthrough(new_exp, user.get("rank", 1))

    log_event(
        "cultivate_end",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={
            "gain": gain,
            "hours": gain_result["hours"],
            "efficiency": gain_result["efficiency"],
            "is_capped": gain_result.get("is_capped", False),
        },
    )
    log_economy_ledger(
        user_id=user_id,
        module="cultivation",
        action="cultivate_end",
        delta_exp=int(gain or 0),
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"hours": gain_result["hours"], "efficiency": gain_result["efficiency"]},
    )
    return success(
        gain=gain,
        total_exp=new_exp,
        can_breakthrough=can_break,
        hours=gain_result["hours"],
        efficiency=gain_result["efficiency"],
        tip=gain_result["tip"],
        story_update=story_update,
    )


@cultivation_bp.route("/api/cultivate/status/<user_id>", methods=["GET"])
def cultivate_status(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)

    if not user.get("state"):
        return success(state=False)

    timing = fetch_one(
        "SELECT * FROM timings WHERE user_id = ? AND type = 'cultivation'", (user_id,)
    )
    if not timing:
        # recover: timing missing but user is marked cultivating
        execute("UPDATE users SET state = 0 WHERE user_id = ?", (user_id,))
        return success(state=False, recovered=True)

    gain_result = calculate_cultivation_progress(timing)
    return success(
        state=True,
        start_time=timing["start_time"],
        current_gain=gain_result["exp"],
        hours=gain_result["hours"],
        efficiency=gain_result["efficiency"],
        tip=gain_result["tip"],
        is_capped=gain_result.get("is_capped", False),
        optimal_hours=gain_result.get("optimal_hours"),
    )


@cultivation_bp.route("/api/realm-trial/<user_id>", methods=["GET"])
def realm_trial_status(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    trial = get_or_create_realm_trial(user_id, int(user.get("rank", 1) or 1))
    return success(trial=trial)


@cultivation_bp.route("/api/breakthrough", methods=["POST"])
def breakthrough():
    """突破 API"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    use_pill = data.get("use_pill", False)
    strategy = data.get("strategy", "normal")
    log_action("breakthrough", user_id=user_id, use_pill=bool(use_pill), strategy=strategy)

    if not user_id:
        return error("MISSING_PARAMS", "Missing user_id", 400)

    resp, http_status = settle_breakthrough(user_id=user_id, use_pill=bool(use_pill), strategy=strategy)

    # success: ensure final stats include equipment bonuses (and refill hp/mp)
    if resp.get("success"):
        try:
            recalculate_user_combat_stats(user_id, reset_current=True)
        except Exception:
            pass

    return jsonify(resp), http_status


@cultivation_bp.route("/api/signin/<user_id>", methods=["GET"])
def signin_status(user_id):
    """获取签到状态"""
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)

    status_text = format_signin_status(user)
    signed = check_signed_today(user.get("last_sign_timestamp"))

    return success(
        signed_today=signed,
        consecutive_days=user.get("consecutive_sign_days", 0),
        status_text=status_text,
    )


@cultivation_bp.route("/api/signin", methods=["POST"])
def do_signin_api():
    """执行签到"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    log_action("signin", user_id=user_id)

    if not user_id:
        return error("MISSING_PARAMS", "Missing user_id", 400)

    resp, http_status = settle_signin(user_id=user_id)
    return jsonify(resp), http_status
