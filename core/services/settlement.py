"""Settlement / reward application services.

Goal: keep route handlers thin and make settlement consistent.
Each function returns (response_dict, http_status).
"""

from __future__ import annotations

import logging
import time
import random
from typing import Any, Dict, Tuple, Optional

from core.database.connection import (
    get_user_by_id,
    update_user,
    execute,
    add_item,
    get_user_skills,
    log_battle,
    fetch_one,
    fetch_all,
    get_item_by_db_id,
    db_transaction,
    spend_user_stamina,
    spend_user_stamina_tx,
    refresh_user_stamina,
    refresh_user_vitals,
)
from core.database.migrations import save_response, reserve_request

from core.game.combat import hunt_monster, get_monster_by_id
from core.game.quests import get_quest_def, get_all_quest_defs
from core.game.secret_realms import (
    apply_secret_realm_modifiers,
    build_secret_realm_node_chain,
    can_explore_secret_realm,
    get_secret_realm_attempts_left,
    get_secret_realm_by_id,
    DAILY_SECRET_REALM_LIMIT,
    roll_secret_realm_encounter,
    roll_secret_realm_rewards,
    scale_secret_realm_monster,
)
from core.game.items import calculate_drop_rewards
from core.game.items import (
    get_item_by_id,
    Quality,
    generate_material,
    generate_pill,
    generate_equipment,
)
from core.services.codex_service import ensure_monster, ensure_item
from core.services.balance_service import hunt_rewards, fatigue_multiplier, exp_fatigue_multiplier
from core.services.drop_pity_service import roll_targeted_drop_with_pity
from core.services.quests_service import increment_quest
from core.services.sect_service import apply_sect_stat_buffs, get_user_sect_buffs, increment_sect_quest_progress
from core.services.metrics_service import log_event, log_economy_ledger
from core.services.story_service import track_story_action
from core.services.skills_service import gain_skill_mastery
from core.services.realm_trials_service import increment_realm_trial
from core.utils.timeutil import midnight_timestamp, local_day_key
from core.utils.reward_scaling import rank_scale
from core.utils.number import format_stamina_value
from core.config import config


def _battle_post_status(user: Dict[str, Any], battle_result: Dict[str, Any]) -> Dict[str, Any]:
    user = apply_sect_stat_buffs(user)
    max_hp = int(user.get("max_hp", 100) or 100)
    hp_after = int(battle_result.get("attacker_remaining_hp", max_hp) or 0)
    hp_after = max(0, min(max_hp, hp_after))
    return {
        "hp": hp_after,
        "max_hp": max_hp,
        "mp": int(user.get("mp", 50) or 50),
        "max_mp": int(user.get("max_mp", 50) or 50),
        "stamina": format_stamina_value(user.get("stamina", 24)),
        "max_stamina": 24,
        "attack": int(user.get("attack", 10) or 10),
        "defense": int(user.get("defense", 5) or 5),
        "rank": int(user.get("rank", 1) or 1),
        "exp": int(user.get("exp", 0) or 0),
        "copper": int(user.get("copper", 0) or 0),
        "gold": int(user.get("gold", 0) or 0),
    }


def _enhance_pct_for_level(level: int) -> float:
    # REVIEW_balance recommendation: 1-3:+6%, 4-6:+4%, 7-10:+2%
    lvl = int(level or 0)
    if lvl <= 3:
        return 0.06
    if lvl <= 6:
        return 0.04
    return 0.02


APP_CONFIG = config.raw
logger = logging.getLogger("Settlement")


def _element_relation_label(user: Dict[str, Any], monster: Optional[Dict[str, Any]]) -> Optional[str]:
    if not monster:
        return None
    try:
        from core.game.elements import get_element_relationship
        ue = user.get("element")
        me = (monster or {}).get("element")
        if ue and me:
            rel = get_element_relationship(ue, me)
            if rel == "mutual":
                return "相生(+25%)/敌-25%"
            if rel == "restrained":
                return "被克(-25%)/敌+25%"
            return "无"
    except Exception:
        return None
    return None


def _hunt_defeat_weak_seconds() -> int:
    configured = config.get_nested("balance", "hunt", "defeat_weak_seconds", default=1800)
    try:
        return max(0, int(configured or 0))
    except (TypeError, ValueError):
        return 1800


def _format_weak_duration(seconds: int) -> str:
    total = max(0, int(seconds or 0))
    if total <= 0:
        return "0秒"
    if total % 60 == 0:
        return f"{total // 60}分钟"
    return f"{total}秒"


def _stamina_block_response(user: Dict[str, Any], *, action_label: str) -> Tuple[Dict[str, Any], int]:
    current = format_stamina_value((user or {}).get("stamina", 0))
    return {
        "success": False,
        "code": "INSUFFICIENT_STAMINA",
        "message": f"精力不足，{action_label}需要 1 点精力",
        "stamina": current,
        "stamina_cost": 1,
    }, 400


def _reset_secret_realm_attempts_if_needed(user_id: str, user: Dict[str, Any], now: int) -> Dict[str, Any]:
    last_reset = int((user or {}).get("secret_realm_last_reset", 0) or 0)
    if last_reset >= midnight_timestamp():
        return user
    update_user(
        user_id,
        {
            "secret_realm_attempts": 0,
            "secret_realm_last_reset": int(now),
        },
    )
    return get_user_by_id(user_id) or user


def settle_hunt(
    *,
    user_id: str,
    monster_id: str,
    request_id: Optional[str],
    hunt_cooldown_seconds: int,
    now: Optional[int] = None,
    use_active: bool = True,
    active_skill_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="hunt")
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
            save_response(request_id, user_id, "hunt", resp)
        return resp, http_status

    refresh_user_vitals(user_id)
    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)
    sect_buffs = get_user_sect_buffs(user_id)

    if user.get("state"):
        return _dedup_return({"success": False, "code": "IN_CULTIVATION", "message": "请先结束修炼"}, 400)

    now = int(time.time()) if now is None else int(now)
    refresh_user_stamina(user_id, now=now)
    user = get_user_by_id(user_id) or user
    last_hunt = int(user.get("last_hunt_time", 0) or 0)
    remaining = hunt_cooldown_seconds - (now - last_hunt)
    if remaining > 0:
        return _dedup_return({
            "success": False,
            "code": "COOLDOWN",
            "message": f"狩猎冷却中，请等待 {remaining} 秒",
            "cooldown_remaining": remaining,
        }, 400)
    monster = get_monster_by_id(monster_id)
    if not monster:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "怪物不存在"}, 404)

    hcfg = (APP_CONFIG.get("balance", {}) or {}).get("hunt", {}) or {}
    rank = int(user.get("rank", 1) or 1)
    if int(monster.get("min_rank", 1) or 1) > rank:
        return _dedup_return({
            "success": False,
            "code": "RANK_TOO_LOW",
            "message": f"需要达到 {monster['min_rank']} 级才能挑战此怪物",
        }, 400)
    day = local_day_key(now)
    daily_limit = int(hcfg.get("daily_limit", 50))

    # atomic stamina + hunt counter + cooldown guard
    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, 1, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            cur.execute(
                """UPDATE users
                   SET hunts_today = CASE WHEN hunts_today_reset = ? THEN hunts_today + 1 ELSE 1 END,
                       hunts_today_reset = ?,
                       last_hunt_time = ?
                   WHERE user_id = ?
                     AND (? - last_hunt_time) >= ?
                     AND (hunts_today_reset != ? OR hunts_today < ?)""",
                (day, day, now, user_id, now, hunt_cooldown_seconds, day, daily_limit),
            )
            if cur.rowcount == 0:
                raise ValueError("HUNT_BLOCKED")
    except ValueError as exc:
        reason = str(exc)
        latest = get_user_by_id(user_id) or user
        if reason == "INSUFFICIENT_STAMINA":
            return _dedup_return(_stamina_block_response(latest, action_label="狩猎")[0], 400)
        # recompute blockers
        last_hunt = int(latest.get("last_hunt_time", 0) or 0)
        remaining = hunt_cooldown_seconds - (now - last_hunt)
        if remaining > 0:
            return _dedup_return({
                "success": False,
                "code": "COOLDOWN",
                "message": f"狩猎冷却中，请等待 {remaining} 秒",
                "cooldown_remaining": remaining,
            }, 400)
        if int(latest.get("hunts_today_reset", 0) or 0) == day and int(latest.get("hunts_today", 0) or 0) >= daily_limit:
            return _dedup_return({
                "success": False,
                "code": "DAILY_LIMIT",
                "message": f"今日狩猎次数已达上限 ({daily_limit}次)",
            }, 400)
        return _dedup_return({"success": False, "code": "CONFLICT", "message": "操作过快，请重试"}, 409)

    user = get_user_by_id(user_id) or user

    battle_user = apply_sect_stat_buffs(user)
    learned_skills = get_user_skills(user_id)
    result = hunt_monster(
        battle_user,
        monster_id,
        learned_skills,
        use_active=bool(use_active),
        active_skill_id=active_skill_id,
    )

    # REVIEW_balance: rank baseline + monster exp floor (stronger monsters can reward more)
    if result.get("victory"):
        base_rw = hunt_rewards(rank, hcfg, monster=result.get("monster"))
        hunts_today = int((user or {}).get("hunts_today", 1) or 1)
        mult = fatigue_multiplier(hunts_today, hcfg)
        exp_mult = exp_fatigue_multiplier(hunts_today, hcfg)
        result.setdefault("rewards", {})
        result["rewards"]["exp"] = int(base_rw["exp"] * exp_mult)
        result["rewards"]["copper"] = int(round(base_rw["copper"] * mult))
        reward_mult = 1.0 + float(sect_buffs.get("battle_reward_pct", 0.0)) / 100.0
        result["rewards"]["exp"] = int(round(result["rewards"]["exp"] * reward_mult))
        result["rewards"]["copper"] = int(round(result["rewards"]["copper"] * reward_mult))

    element_relation = _element_relation_label(user, result.get("monster"))
    drop_pity_meta: Optional[Dict[str, Any]] = None
    story_update: List[Dict[str, Any]] = []

    if result.get("success") and result.get("victory"):
        try:
            ensure_monster(user_id, monster_id)
        except Exception:
            pass
        drops = calculate_drop_rewards(monster_id, user.get("rank", 1), include_targeted=False)
        targeted_drop, drop_pity_meta = roll_targeted_drop_with_pity(
            user_id=user_id,
            source_kind="monster",
            source_id=monster_id,
            user_rank=int(user.get("rank", 1) or 1),
            boosted=False,
        )
        if targeted_drop:
            drops.append(targeted_drop)
        rewards = result.get("rewards", {})
        hp_after_battle = max(1, int(result.get("attacker_remaining_hp", user.get("max_hp", 100)) or 1))

        # ---- 单事务原子结算 ----
        with db_transaction() as cur:
            # 1. 批量插入掉落物品
            for drop in drops:
                cur.execute(
                    """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                       quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                       first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, drop.get("item_id"), drop.get("item_name"),
                     drop.get("item_type"), drop.get("quality", "common"),
                     drop.get("quantity", 1), drop.get("level", 1),
                     drop.get("attack_bonus", 0), drop.get("defense_bonus", 0),
                     drop.get("hp_bonus", 0), drop.get("mp_bonus", 0),
                     drop.get("first_round_reduction_pct", 0), drop.get("crit_heal_pct", 0),
                     drop.get("element_damage_pct", 0), drop.get("low_hp_shield_pct", 0)),
                )

            # 2. 原子更新用户数值（SQL加法，非读-改-写）
            cur.execute(
                """UPDATE users SET
                   exp = exp + ?, copper = copper + ?, gold = gold + ?, hp = ?, mp = ?, vitals_updated_at = ?,
                   dy_times = dy_times + 1, last_hunt_time = ?
                   WHERE user_id = ?""",
                (rewards.get("exp", 0), rewards.get("copper", 0),
                 rewards.get("gold", 0), hp_after_battle,
                 int(result.get("attacker_remaining_mp", user.get("mp", user.get("max_mp", 50))) or 0),
                 now, now, user_id),
            )

            # 3. 记录战斗日志
            cur.execute(
                """INSERT INTO battle_logs
                   (user_id, monster_id, victory, rounds, exp_gained, copper_gained, gold_gained, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, monster_id, 1, result.get("rounds", 0),
                 rewards.get("exp", 0), rewards.get("copper", 0),
                 rewards.get("gold", 0), now),
            )

        # 读取更新后的用户数据用于响应
        from core.services.codex_service import ensure_item
        for drop in drops:
            ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
        updated = get_user_by_id(user_id)
        result["total_exp"] = updated.get("exp", 0) if updated else 0
        result["total_copper"] = updated.get("copper", 0) if updated else 0
        result["drops"] = drops
        increment_quest(user_id, "daily_hunt")
        increment_sect_quest_progress(user_id, "hunt", 1)
        try:
            increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "hunt", 1)
        except Exception:
            logger.error(
                "realm_trial_increment_failed user_id=%s rank=%s kind=hunt",
                user_id,
                int(user.get("rank", 1) or 1),
                exc_info=True,
            )
            try:
                log_event(
                    "realm_trial_increment",
                    user_id=user_id,
                    success=False,
                    rank=int(user.get("rank", 1) or 1),
                    reason="EXCEPTION",
                    meta={"kind": "hunt"},
                )
            except Exception:
                pass
        try:
            story_update = track_story_action(user_id, "hunt_victory")
        except Exception:
            story_update = []
    elif result.get("success"):
        weak_seconds = _hunt_defeat_weak_seconds()
        weak_until = max(int(user.get("weak_until", 0) or 0), now + weak_seconds) if weak_seconds > 0 else int(user.get("weak_until", 0) or 0)
        update_user(
            user_id,
            {
                "hp": 1,
                "mp": int(result.get("attacker_remaining_mp", user.get("mp", user.get("max_mp", 50))) or 0),
                "weak_until": weak_until,
                "vitals_updated_at": now,
            },
        )
        if weak_seconds > 0:
            base_msg = str(result.get("message") or "战斗失败")
            result["message"] = f"{base_msg}，进入虚弱状态 {_format_weak_duration(weak_seconds)}"
        result["weak_seconds"] = weak_seconds
        result["weak_until"] = weak_until
        updated = get_user_by_id(user_id)
    else:
        updated = get_user_by_id(user_id) or user

    try:
        skill_id = result.get("active_skill_id")
        skill_uses = int(result.get("skill_uses", 0) or 0)
        if skill_id and skill_uses > 0:
            gain_skill_mastery(user_id, skill_id, skill_uses, now=now)
    except Exception:
        pass

    event_point_grants: list[Dict[str, Any]] = []
    if result.get("success") and result.get("victory"):
        try:
            from core.services.events_service import grant_event_points_for_action
            event_point_grants = grant_event_points_for_action(
                user_id,
                "hunt_victory",
                meta={"monster_id": monster_id},
            )
        except Exception:
            event_point_grants = []

    resp: Dict[str, Any] = dict(result)
    if not result.get("victory"):
        result.setdefault("rewards", {"exp": 0, "copper": 0, "gold": 0})
    status_source = updated if 'updated' in locals() and updated else get_user_by_id(user_id)
    if status_source:
        resp["post_status"] = _battle_post_status(status_source, result)
    if element_relation:
        resp["element_relation"] = element_relation
    if event_point_grants:
        resp["event_points"] = event_point_grants
    if story_update:
        resp["story_update"] = story_update
    resp["combat_modifiers"] = {
        "element_relation": element_relation,
        "elite_affixes": result.get("elite_affixes", []),
        "source_kind": "hunt",
        "drop_pity": drop_pity_meta if result.get("victory") else None,
    }
    if result.get("success"):
        log_event(
            "hunt",
            user_id=user_id,
            success=True,
            request_id=request_id,
            rank=int(user.get("rank", 1) or 1),
            meta={
                "victory": bool(result.get("victory")),
                "monster_id": monster_id,
                "rounds": int(result.get("rounds", 0) or 0),
            },
        )
        if result.get("victory"):
            rewards = result.get("rewards", {}) or {}
            log_economy_ledger(
                user_id=user_id,
                module="hunt",
                action="hunt",
                delta_copper=int(rewards.get("copper", 0) or 0),
                delta_gold=int(rewards.get("gold", 0) or 0),
                delta_exp=int(rewards.get("exp", 0) or 0),
                delta_stamina=-1,
                success=True,
                request_id=request_id,
                rank=int(user.get("rank", 1) or 1),
                meta={"monster_id": monster_id},
            )
    return _dedup_return(resp, 200)


def settle_secret_realm_explore(
    *,
    user_id: str,
    realm_id: str,
    path: str,
    request_id: Optional[str],
    secret_cooldown_seconds: int,
    now: Optional[int] = None,
    multi_step: bool = False,
    multi_step_nodes: Optional[int] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="secret_realm")
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
            save_response(request_id, user_id, "secret_realm", resp)
        return resp, http_status

    refresh_user_vitals(user_id)
    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)
    sect_buffs = get_user_sect_buffs(user_id)

    now = int(time.time()) if now is None else int(now)
    refresh_user_stamina(user_id, now=now)
    user = _reset_secret_realm_attempts_if_needed(user_id, user, now)
    last_secret = int(user.get("last_secret_time", 0) or 0)
    remaining = secret_cooldown_seconds - (now - last_secret)
    if remaining > 0:
        return _dedup_return({
            "success": False,
            "code": "COOLDOWN",
            "message": f"秘境冷却中，请等待 {remaining} 秒",
            "cooldown_remaining": remaining,
        }, 400)

    ok, msg = can_explore_secret_realm(user, realm_id)
    if not ok:
        return _dedup_return({"success": False, "code": "FORBIDDEN", "message": msg}, 400)

    realm = get_secret_realm_by_id(realm_id)
    if not realm:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "秘境不存在"}, 404)

    path = (path or "normal").strip()
    if path not in ("normal", "safe", "risky", "loot"):
        path = "normal"
    multi_step_cfg = config.get_nested("battle", "secret_realm_multi_step", default={}) or {}
    multi_step_enabled = bool(multi_step_cfg.get("enabled", True))
    default_nodes = int(multi_step_cfg.get("default_nodes", 3) or 3)
    requested_nodes = default_nodes if multi_step_nodes is None else int(multi_step_nodes or default_nodes)
    run_multi_step = bool(multi_step and multi_step_enabled)
    node_count = max(2, min(5, requested_nodes)) if run_multi_step else 1

    encounters = (
        build_secret_realm_node_chain(realm, path=path, steps=node_count)
        if run_multi_step
        else [roll_secret_realm_encounter(realm, path=path)]
    )
    encounter = encounters[0]
    encounter_monster = None
    for node in encounters:
        monster_id = node.get("monster_id")
        if not monster_id:
            continue
        monster_def = get_monster_by_id(monster_id)
        if not monster_def:
            return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "秘境遭遇失效，请重新进入"}, 404)
        if node is encounter:
            encounter_monster = monster_def

    day_reset = midnight_timestamp()
    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, 1, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            cur.execute(
                """UPDATE users
                   SET secret_realm_attempts = CASE WHEN secret_realm_last_reset >= ? THEN secret_realm_attempts + 1 ELSE 1 END,
                       secret_realm_last_reset = CASE WHEN secret_realm_last_reset >= ? THEN secret_realm_last_reset ELSE ? END,
                       last_secret_time = ?
                   WHERE user_id = ?
                     AND (? - last_secret_time) >= ?
                     AND (secret_realm_last_reset < ? OR secret_realm_attempts < ?)""",
                (day_reset, day_reset, now, now, user_id, now, secret_cooldown_seconds, day_reset, int(DAILY_SECRET_REALM_LIMIT)),
            )
            if cur.rowcount == 0:
                raise ValueError("REALM_BLOCKED")
    except ValueError as exc:
        reason = str(exc)
        latest = get_user_by_id(user_id) or user
        if reason == "INSUFFICIENT_STAMINA":
            return _dedup_return(_stamina_block_response(latest, action_label="探索秘境")[0], 400)
        last_secret = int(latest.get("last_secret_time", 0) or 0)
        remaining = secret_cooldown_seconds - (now - last_secret)
        if remaining > 0:
            return _dedup_return({
                "success": False,
                "code": "COOLDOWN",
                "message": f"秘境冷却中，请等待 {remaining} 秒",
                "cooldown_remaining": remaining,
            }, 400)
        if get_secret_realm_attempts_left(latest) <= 0:
            return _dedup_return({"success": False, "code": "FORBIDDEN", "message": "今日秘境次数已用尽"}, 400)
        return _dedup_return({"success": False, "code": "CONFLICT", "message": "操作过快，请重试"}, 409)

    user = get_user_by_id(user_id) or user
    if run_multi_step:
        battle_user = apply_sect_stat_buffs(user)
        learned_skills = get_user_skills(user_id)
        current_hp = int(user.get("hp", battle_user.get("max_hp", user.get("max_hp", 100))) or battle_user.get("max_hp", user.get("max_hp", 100)))
        current_mp = int(user.get("mp", battle_user.get("max_mp", user.get("max_mp", 50))) or battle_user.get("max_mp", user.get("max_mp", 50)))
        total_reward = {"exp": 0, "copper": 0, "gold": 0}
        all_drops: list[Dict[str, Any]] = []
        all_logs: list[str] = []
        node_results: list[Dict[str, Any]] = []
        overall_victory = True
        reward_mult = 1.0 + float(sect_buffs.get("battle_reward_pct", 0.0)) / 100.0
        drop_buff_used = False
        drop_boost_until = int(user.get("realm_drop_boost_until", 0) or 0)
        final_combat_modifiers: Dict[str, Any] = {
            "element_relation": None,
            "elite_affixes": [],
            "source_kind": "secret",
            "path": path,
            "danger_scale": 1.0,
        }

        for idx, node in enumerate(encounters, start=1):
            node_path = node.get("node_path", path) or path
            node_type = node.get("type", "monster")
            node_monster = get_monster_by_id(node.get("monster_id")) if node.get("monster_id") else None
            node_scaled_monster = scale_secret_realm_monster(node_monster, node) if node_monster else None

            node_user = dict(battle_user)
            node_user["hp"] = max(1, int(current_hp or 1))
            node_user["mp"] = max(0, int(current_mp or 0))
            node_user["max_hp"] = int(battle_user.get("max_hp", user.get("max_hp", 100)) or user.get("max_hp", 100))
            node_user["max_mp"] = int(battle_user.get("max_mp", user.get("max_mp", 50)) or user.get("max_mp", 50))

            if node_monster:
                battle_result = hunt_monster(
                    node_user,
                    node["monster_id"],
                    learned_skills,
                    source_kind="secret",
                    ignore_min_rank=True,
                    monster_override=node_scaled_monster,
                )
            else:
                trap_damage = 0
                if node_type == "trap":
                    max_hp = int(node_user.get("max_hp", 100) or 100)
                    trap_damage = max(3, int(round(max_hp * 0.08)))
                    node_user["hp"] = max(1, int(node_user.get("hp", max_hp) or max_hp) - trap_damage)
                battle_result = {
                    "success": True,
                    "victory": True,
                    "message": node.get("event_text", "秘境中没有遇到强敌"),
                    "attacker_remaining_hp": int(node_user.get("hp", node_user.get("max_hp", 100)) or 1),
                    "attacker_remaining_mp": int(node_user.get("mp", 0) or 0),
                    "log": [],
                    "trap_damage": trap_damage,
                }
            if not battle_result.get("success"):
                return _dedup_return(battle_result, 400)

            try:
                skill_id = battle_result.get("active_skill_id")
                skill_uses = int(battle_result.get("skill_uses", 0) or 0)
                if skill_id and skill_uses > 0:
                    gain_skill_mastery(user_id, skill_id, skill_uses, now=now)
            except Exception:
                pass

            mods = apply_secret_realm_modifiers(node_path)
            if drop_boost_until > now:
                buff_cfg = (APP_CONFIG.get("balance", {}) or {}).get("pill_buffs", {}) or {}
                realm_cfg = buff_cfg.get("realm_drop", {}) or {}
                drop_mul = float(realm_cfg.get("drop_mul", 1.35))
                mods["drop_mul"] = float(mods.get("drop_mul", 1.0)) * drop_mul
                drop_buff_used = True

            reward = roll_secret_realm_rewards(
                realm,
                victory=battle_result.get("victory", False),
                user_rank=int(user.get("rank", 1) or 1),
                path=node_path,
                encounter_type=node_type,
                **mods,
            )
            node_drops = []
            for reward_item_id in reward.get("drop_item_ids", []) or []:
                base_item = get_item_by_id(reward_item_id)
                if not base_item:
                    continue
                item_type = base_item.get("type")
                item_enum_value = getattr(item_type, "value", item_type)
                if item_enum_value == "material":
                    drop = generate_material(reward_item_id, 1)
                elif item_enum_value == "pill":
                    drop = generate_pill(reward_item_id, 1)
                else:
                    drop = generate_equipment(base_item, Quality.COMMON, max(1, int(user.get("rank", 1) or 1) // 3))
                if drop:
                    node_drops.append(drop)
            node_drop_pity = None
            if battle_result.get("victory", False) and node_path == "risky":
                targeted_drop, node_drop_pity = roll_targeted_drop_with_pity(
                    user_id=user_id,
                    source_kind="realm",
                    source_id=realm_id,
                    user_rank=int(user.get("rank", 1) or 1),
                    boosted=True,
                )
                if targeted_drop:
                    node_drops.append(targeted_drop)

            battle_gold = int((battle_result.get("rewards", {}) or {}).get("gold", 0) or 0)
            node_exp = int(round(float(reward.get("exp", 0) or 0) * reward_mult))
            node_copper = int(round(float(reward.get("copper", 0) or 0) * reward_mult))
            node_gold = int(round(float(reward.get("gold", 0) or 0) + battle_gold) * reward_mult)
            total_reward["exp"] += node_exp
            total_reward["copper"] += node_copper
            total_reward["gold"] += node_gold
            all_drops.extend(node_drops)

            current_hp = max(1, int(battle_result.get("attacker_remaining_hp", current_hp) or current_hp))
            current_mp = max(0, int(battle_result.get("attacker_remaining_mp", current_mp) or current_mp))
            if node_monster and not battle_result.get("victory", True):
                current_hp = 1
                overall_victory = False
            element_relation = _element_relation_label(user, battle_result.get("monster"))
            final_combat_modifiers = {
                "element_relation": element_relation,
                "elite_affixes": battle_result.get("elite_affixes", []),
                "source_kind": "secret",
                "path": node_path,
                "danger_scale": float(node.get("danger_scale", 1.0) or 1.0),
            }

            node_results.append(
                {
                    "node_index": idx,
                    "path": node_path,
                    "encounter": node.get("label") or (battle_result.get("monster", {}) or {}).get("name"),
                    "encounter_type": node_type,
                    "victory": bool(battle_result.get("victory", True)),
                    "rounds": int(battle_result.get("rounds", 0) or 0),
                    "battle_message": battle_result.get("message"),
                    "trap_damage": int(battle_result.get("trap_damage", 0) or 0),
                    "event": reward.get("event"),
                    "rewards": {"exp": node_exp, "copper": node_copper, "gold": node_gold, "drops": node_drops},
                    "combat_modifiers": final_combat_modifiers,
                    "drop_pity": node_drop_pity,
                }
            )

            round_logs = battle_result.get("log", []) or []
            if round_logs:
                all_logs.append(f"【节点{idx}】")
                all_logs.extend(round_logs)
            if not overall_victory:
                break

        loot_score = int(
            total_reward["exp"]
            + total_reward["copper"]
            + total_reward["gold"] * 300
            + sum((d.get("quantity", 1) or 1) * 120 for d in all_drops)
        )
        with db_transaction() as cur:
            for drop in all_drops:
                cur.execute(
                    """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                       quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                       first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        drop.get("item_id"),
                        drop.get("item_name"),
                        drop.get("item_type"),
                        drop.get("quality", "common"),
                        drop.get("quantity", 1),
                        drop.get("level", 1),
                        drop.get("attack_bonus", 0),
                        drop.get("defense_bonus", 0),
                        drop.get("hp_bonus", 0),
                        drop.get("mp_bonus", 0),
                        drop.get("first_round_reduction_pct", 0),
                        drop.get("crit_heal_pct", 0),
                        drop.get("element_damage_pct", 0),
                        drop.get("low_hp_shield_pct", 0),
                    ),
                )
            cur.execute(
                "UPDATE users SET exp = exp + ?, copper = copper + ?, gold = gold + ?, hp = ?, mp = ?, secret_loot_score = secret_loot_score + ?, vitals_updated_at = ? WHERE user_id = ?",
                (
                    int(total_reward["exp"]),
                    int(total_reward["copper"]),
                    int(total_reward["gold"]),
                    max(1, int(current_hp)),
                    max(0, int(current_mp)),
                    loot_score,
                    now,
                    user_id,
                ),
            )

        updated_user = get_user_by_id(user_id)
        for drop in all_drops:
            try:
                ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
            except Exception:
                pass
        increment_quest(user_id, "daily_secret_realm")
        increment_sect_quest_progress(user_id, "secret_realm", 1)
        try:
            if overall_victory:
                increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "secret", 1)
        except Exception:
            logger.error(
                "realm_trial_increment_failed user_id=%s rank=%s kind=secret multi_step=true",
                user_id,
                int(user.get("rank", 1) or 1),
                exc_info=True,
            )
            try:
                log_event(
                    "realm_trial_increment",
                    user_id=user_id,
                    success=False,
                    rank=int(user.get("rank", 1) or 1),
                    reason="EXCEPTION",
                    meta={"kind": "secret", "multi_step": True},
                )
            except Exception:
                pass
        story_update: List[Dict[str, Any]] = []
        if overall_victory:
            try:
                story_update = track_story_action(user_id, "secret_realm_victory")
            except Exception:
                story_update = []

        resp = {
            "success": True,
            "realm": realm,
            "path": path,
            "multi_step": True,
            "total_nodes": int(node_count),
            "completed_nodes": len(node_results),
            "node_results": node_results,
            "encounter": (node_results[-1].get("encounter") if node_results else encounter.get("label")),
            "encounter_type": (node_results[-1].get("encounter_type") if node_results else encounter.get("type")),
            "victory": bool(overall_victory),
            "battle_message": f"完成 {len(node_results)}/{node_count} 个节点" if overall_victory else f"在第 {len(node_results)} 个节点受挫",
            "battle_log": all_logs,
            "event": (node_results[-1].get("event") if node_results else ""),
            "rewards": {
                "exp": int(total_reward["exp"]),
                "copper": int(total_reward["copper"]),
                "gold": int(total_reward["gold"]),
                "drops": all_drops,
            },
            "attempts_left": get_secret_realm_attempts_left(updated_user),
            "combat_modifiers": final_combat_modifiers,
        }
        if final_combat_modifiers.get("element_relation"):
            resp["element_relation"] = final_combat_modifiers["element_relation"]
        if updated_user:
            resp["post_status"] = _battle_post_status(updated_user, {"attacker_remaining_hp": max(1, int(current_hp))})
        if drop_buff_used:
            resp["buff_used"] = "realm_drop_boost"
        if story_update:
            resp["story_update"] = story_update
        if overall_victory:
            try:
                from core.services.events_service import grant_event_points_for_action
                grants = grant_event_points_for_action(
                    user_id,
                    "secret_victory",
                    meta={"realm_id": realm_id, "path": path, "multi_step": True},
                )
                if grants:
                    resp["event_points"] = grants
            except Exception:
                pass
        log_event(
            "secret_realm_explore",
            user_id=user_id,
            success=True,
            request_id=request_id,
            rank=int(user.get("rank", 1) or 1),
            meta={
                "realm_id": realm_id,
                "path": path,
                "multi_step": True,
                "node_count": int(node_count),
                "completed_nodes": len(node_results),
                "victory": bool(overall_victory),
            },
        )
        log_economy_ledger(
            user_id=user_id,
            module="secret_realm",
            action="secret_realm_explore_multi_step",
            delta_copper=int(total_reward.get("copper", 0) or 0),
            delta_gold=int(total_reward.get("gold", 0) or 0),
            delta_exp=int(total_reward.get("exp", 0) or 0),
            delta_stamina=-1,
            success=True,
            request_id=request_id,
            rank=int(user.get("rank", 1) or 1),
            meta={"realm_id": realm_id, "path": path, "nodes": int(node_count), "completed_nodes": len(node_results)},
        )
        return _dedup_return(resp, 200)

    battle_user = apply_sect_stat_buffs(user)
    learned_skills = get_user_skills(user_id)
    scaled_monster = None
    if encounter.get("monster_id"):
        if encounter_monster:
            scaled_monster = scale_secret_realm_monster(encounter_monster, encounter)
        battle_result = hunt_monster(
            battle_user,
            encounter["monster_id"],
            learned_skills,
            source_kind="secret",
            ignore_min_rank=True,
            monster_override=scaled_monster,
        )
    else:
        trap_damage = 0
        if encounter.get("type") == "trap":
            max_hp = int(user.get("max_hp", 100) or 100)
            raw_hp = user.get("hp")
            current_hp = max_hp if raw_hp is None else int(raw_hp)
            trap_damage = max(3, int(round(max_hp * 0.08)))
            current_hp = max(1, current_hp - trap_damage)
            user["hp"] = current_hp
            user["vitals_updated_at"] = now
        battle_result = {
            "success": True,
            "victory": True,
            "message": encounter.get("event_text", "秘境中没有遇到强敌"),
            "attacker_remaining_hp": int(user.get("hp", user.get("max_hp", 100)) or 1),
            "log": [],
            "trap_damage": trap_damage,
        }
    if not battle_result.get("success"):
        return _dedup_return(battle_result, 400)

    try:
        skill_id = battle_result.get("active_skill_id")
        skill_uses = int(battle_result.get("skill_uses", 0) or 0)
        if skill_id and skill_uses > 0:
            gain_skill_mastery(user_id, skill_id, skill_uses, now=now)
    except Exception:
        pass

    mods = apply_secret_realm_modifiers(path)
    drop_buff_used = False
    drop_boost_until = int(user.get("realm_drop_boost_until", 0) or 0)
    if drop_boost_until > now:
        buff_cfg = (APP_CONFIG.get("balance", {}) or {}).get("pill_buffs", {}) or {}
        realm_cfg = buff_cfg.get("realm_drop", {}) or {}
        drop_mul = float(realm_cfg.get("drop_mul", 1.35))
        mods["drop_mul"] = float(mods.get("drop_mul", 1.0)) * drop_mul
        drop_buff_used = True
    reward = roll_secret_realm_rewards(
        realm,
        victory=battle_result.get("victory", False),
        user_rank=int(user.get("rank", 1) or 1),
        path=path,
        encounter_type=encounter.get("type", "monster"),
        **mods,
    )

    drops = []
    for reward_item_id in reward.get("drop_item_ids", []) or []:
        base_item = get_item_by_id(reward_item_id)
        if not base_item:
            continue
        item_type = base_item.get("type")
        item_enum_value = getattr(item_type, "value", item_type)
        if item_enum_value == "material":
            drop = generate_material(reward_item_id, 1)
        elif item_enum_value == "pill":
            drop = generate_pill(reward_item_id, 1)
        else:
            drop = generate_equipment(base_item, Quality.COMMON, max(1, user.get("rank", 1) // 3))
        if drop:
            drops.append(drop)

    targeted_drop = None
    secret_drop_pity = None
    if battle_result.get("victory", False) and path == "risky":
        targeted_drop, secret_drop_pity = roll_targeted_drop_with_pity(
            user_id=user_id,
            source_kind="realm",
            source_id=realm_id,
            user_rank=int(user.get("rank", 1) or 1),
            boosted=True,
        )
        if targeted_drop:
            drops.append(targeted_drop)

    # 中品灵石 = 战斗掉落 + 秘境自身掉落
    battle_gold = battle_result.get("rewards", {}).get("gold", 0)
    realm_gold = reward.get("gold", 0)
    reward_mult = 1.0 + float(sect_buffs.get("battle_reward_pct", 0.0)) / 100.0
    reward["exp"] = int(round(float(reward.get("exp", 0) or 0) * reward_mult))
    reward["copper"] = int(round(float(reward.get("copper", 0) or 0) * reward_mult))
    total_gold = int(round((battle_gold + realm_gold) * reward_mult))
    loot_score = int(reward["exp"] + reward["copper"] + total_gold * 300 + sum((d.get("quantity", 1) or 1) * 120 for d in drops))
    hp_after_battle = max(1, int(battle_result.get("attacker_remaining_hp", user.get("max_hp", 100)) or 1))
    if encounter.get("monster_id") and not battle_result.get("victory", True):
        hp_after_battle = 1
    mp_after_battle = int(battle_result.get("attacker_remaining_mp", user.get("mp", user.get("max_mp", 50))) or 0)

    with db_transaction() as cur:
        for drop in drops:
            cur.execute(
                """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                   quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                   first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, drop.get("item_id"), drop.get("item_name"),
                 drop.get("item_type"), drop.get("quality", "common"),
                 drop.get("quantity", 1), drop.get("level", 1),
                 drop.get("attack_bonus", 0), drop.get("defense_bonus", 0),
                 drop.get("hp_bonus", 0), drop.get("mp_bonus", 0),
                 drop.get("first_round_reduction_pct", 0), drop.get("crit_heal_pct", 0),
                 drop.get("element_damage_pct", 0), drop.get("low_hp_shield_pct", 0)),
            )
        cur.execute(
            "UPDATE users SET exp = exp + ?, copper = copper + ?, gold = gold + ?, hp = ?, mp = ?, secret_loot_score = secret_loot_score + ?, vitals_updated_at = ? WHERE user_id = ?",
            (reward["exp"], reward["copper"], total_gold, hp_after_battle, mp_after_battle, loot_score, now, user_id),
        )

    updated_user = get_user_by_id(user_id)
    for drop in drops:
        try:
            ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
        except Exception:
            pass
    increment_quest(user_id, "daily_secret_realm")
    increment_sect_quest_progress(user_id, "secret_realm", 1)
    try:
        if battle_result.get("victory", True):
            increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "secret", 1)
    except Exception:
        logger.error(
            "realm_trial_increment_failed user_id=%s rank=%s kind=secret multi_step=false",
            user_id,
            int(user.get("rank", 1) or 1),
            exc_info=True,
        )
        try:
            log_event(
                "realm_trial_increment",
                user_id=user_id,
                success=False,
                rank=int(user.get("rank", 1) or 1),
                reason="EXCEPTION",
                meta={"kind": "secret", "multi_step": False},
            )
        except Exception:
            pass
    story_update: List[Dict[str, Any]] = []
    if battle_result.get("victory", True):
        try:
            story_update = track_story_action(user_id, "secret_realm_victory")
        except Exception:
            story_update = []

    combat_modifiers = {
        "element_relation": _element_relation_label(user, battle_result.get("monster")),
        "elite_affixes": battle_result.get("elite_affixes", []),
        "source_kind": "secret",
        "path": path,
        "danger_scale": float(encounter.get("danger_scale", 1.0) or 1.0),
        "drop_pity": secret_drop_pity,
    }
    resp = {
        "success": True,
        "realm": realm,
        "path": path,
        "encounter": encounter.get("label") or (battle_result.get("monster", {}).get("name") if battle_result.get("monster") else None),
        "encounter_type": encounter.get("type"),
        "victory": battle_result.get("victory", True),
        "battle_message": battle_result.get("message"),
        "battle_log": battle_result.get("log", []),
        "event": reward["event"],
        "rewards": {
            "exp": reward["exp"],
            "copper": reward["copper"],
            "gold": total_gold,
            "drops": drops,
        },
        "attempts_left": get_secret_realm_attempts_left(updated_user),
        "combat_modifiers": combat_modifiers,
    }
    if combat_modifiers.get("element_relation"):
        resp["element_relation"] = combat_modifiers["element_relation"]
    if updated_user:
        resp["post_status"] = _battle_post_status(updated_user, battle_result)
    if drop_buff_used:
        resp["buff_used"] = "realm_drop_boost"
    if story_update:
        resp["story_update"] = story_update
    if battle_result.get("victory", True):
        try:
            from core.services.events_service import grant_event_points_for_action
            grants = grant_event_points_for_action(
                user_id,
                "secret_victory",
                meta={"realm_id": realm_id, "path": path},
            )
            if grants:
                resp["event_points"] = grants
        except Exception:
            pass
    log_event(
        "secret_realm_explore",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={
            "realm_id": realm_id,
            "path": path,
            "victory": bool(battle_result.get("victory", True)),
            "encounter_type": encounter.get("type"),
            "danger_scale": float(encounter.get("danger_scale", 1.0) or 1.0),
        },
    )
    log_economy_ledger(
        user_id=user_id,
        module="secret_realm",
        action="secret_realm_explore",
        delta_copper=int(reward.get("copper", 0) or 0),
        delta_gold=int(total_gold or 0),
        delta_exp=int(reward.get("exp", 0) or 0),
        delta_stamina=-1,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"realm_id": realm_id, "path": path, "victory": bool(battle_result.get("victory", True))},
    )
    return _dedup_return(resp, 200)


def settle_quest_claim(
    *,
    user_id: str,
    quest_id: str,
    request_id: Optional[str],
    claim_cooldown_seconds: int,
    today: str,
    now: Optional[int] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="quest_claim")
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
            save_response(request_id, user_id, "quest_claim", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)

    row = fetch_one(
        "SELECT * FROM user_quests WHERE user_id = ? AND quest_id = ? AND assigned_date = ?",
        (user_id, quest_id, today),
    )
    if not row:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "任务不存在"}, 404)
    if row["claimed"]:
        return _dedup_return({"success": False, "code": "ALREADY_CLAIMED", "message": "已领取过奖励"}, 400)
    if row["progress"] < row["goal"]:
        return _dedup_return({"success": False, "code": "NOT_COMPLETED", "message": "任务未完成"}, 400)

    qdef = get_quest_def(quest_id)
    if not qdef:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "任务定义不存在"}, 404)

    now = int(time.time()) if now is None else int(now)
    last_claim = int(user.get("last_quest_claim_time", 0) or 0)
    remaining = claim_cooldown_seconds - (now - last_claim)
    if remaining > 0:
        return _dedup_return({
            "success": False,
            "code": "COOLDOWN",
            "message": f"操作太快，请等待 {remaining} 秒再领取",
            "cooldown_remaining": remaining,
        }, 400)

    rewards = qdef["rewards"]
    scale = APP_CONFIG.get("balance", {}).get("quest_reward_scale", {}) or {}
    sc_c = float(scale.get("copper", 1.0))
    sc_e = float(scale.get("exp", 1.0))
    sc_g = float(scale.get("gold", 1.0))
    rank_mult = rank_scale(int(user.get("rank", 1) or 1))
    rewards_scaled = {
        "copper": int(round(rewards.get("copper", 0) * sc_c * rank_mult)),
        "exp": int(round(rewards.get("exp", 0) * sc_e * rank_mult)),
        "gold": int(round(rewards.get("gold", 0) * sc_g)),
    }
    try:
        with db_transaction() as cur:
            cur.execute(
                """UPDATE users
                   SET copper = copper + ?, exp = exp + ?, gold = gold + ?, last_quest_claim_time = ?
                   WHERE user_id = ? AND (? - last_quest_claim_time) >= ?""",
                (
                    rewards_scaled.get("copper", 0),
                    rewards_scaled.get("exp", 0),
                    rewards_scaled.get("gold", 0),
                    now,
                    user_id,
                    now,
                    claim_cooldown_seconds,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError("COOLDOWN")

            cur.execute(
                """UPDATE user_quests
                   SET claimed = 1
                   WHERE id = ? AND user_id = ? AND claimed = 0 AND progress >= goal""",
                (row["id"], user_id),
            )
            if cur.rowcount == 0:
                raise ValueError("ALREADY_CLAIMED")
    except ValueError as exc:
        reason = str(exc)
        if reason == "COOLDOWN":
            updated_user = get_user_by_id(user_id) or user
            updated_last = int(updated_user.get("last_quest_claim_time", 0) or 0)
            current_now = int(time.time()) if now is None else int(now)
            cooldown_remaining = max(0, claim_cooldown_seconds - (current_now - updated_last))
            return _dedup_return({
                "success": False,
                "code": "COOLDOWN",
                "message": f"操作太快，请等待 {cooldown_remaining} 秒再领取",
                "cooldown_remaining": cooldown_remaining,
            }, 400)
        return _dedup_return({"success": False, "code": "ALREADY_CLAIMED", "message": "已领取过奖励"}, 400)

    resp = {
        "success": True,
        "message": f"领取成功！获得 {rewards_scaled.get('copper', 0)} 下品灵石，{rewards_scaled.get('exp', 0)} 修为",
        "rewards": rewards_scaled,
    }
    log_event(
        "quest_claim",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"quest_id": quest_id},
    )
    log_economy_ledger(
        user_id=user_id,
        module="quest",
        action="quest_claim",
        delta_copper=int(rewards_scaled.get("copper", 0) or 0),
        delta_gold=int(rewards_scaled.get("gold", 0) or 0),
        delta_exp=int(rewards_scaled.get("exp", 0) or 0),
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"quest_id": quest_id},
    )
    return _dedup_return(resp, 200)


def settle_quest_claim_all(
    *,
    user_id: str,
    request_id: Optional[str],
    claim_cooldown_seconds: int,
    today: str,
    now: Optional[int] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="quest_claim_all")
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
            save_response(request_id, user_id, "quest_claim_all", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)

    rows = fetch_all(
        """SELECT id, quest_id, progress, goal, claimed
           FROM user_quests
           WHERE user_id = ? AND assigned_date = ? AND claimed = 0 AND progress >= goal""",
        (user_id, today),
    )
    if not rows:
        resp = {
            "success": True,
            "message": "暂无可领取的任务奖励",
            "claimed_count": 0,
            "claimed_quests": [],
            "rewards": {"copper": 0, "exp": 0, "gold": 0},
        }
        return _dedup_return(resp, 200)

    now = int(time.time()) if now is None else int(now)
    last_claim = int(user.get("last_quest_claim_time", 0) or 0)
    remaining = claim_cooldown_seconds - (now - last_claim)
    if remaining > 0:
        return _dedup_return({
            "success": False,
            "code": "COOLDOWN",
            "message": f"操作太快，请等待 {remaining} 秒再领取",
            "cooldown_remaining": remaining,
        }, 400)

    qdef_map = {qdef["id"]: qdef for qdef in get_all_quest_defs()}
    scale = APP_CONFIG.get("balance", {}).get("quest_reward_scale", {}) or {}
    sc_c = float(scale.get("copper", 1.0))
    sc_e = float(scale.get("exp", 1.0))
    sc_g = float(scale.get("gold", 1.0))
    rank_mult = rank_scale(int(user.get("rank", 1) or 1))

    total_rewards = {"copper": 0, "exp": 0, "gold": 0}
    claimed_quests: list[Dict[str, Any]] = []

    try:
        with db_transaction() as cur:
            for row in rows:
                qdef = qdef_map.get(row.get("quest_id"))
                if not qdef:
                    continue
                cur.execute(
                    """UPDATE user_quests
                       SET claimed = 1
                       WHERE id = ? AND user_id = ? AND claimed = 0 AND progress >= goal""",
                    (row["id"], user_id),
                )
                if cur.rowcount == 0:
                    continue
                rewards = qdef.get("rewards", {}) or {}
                rewards_scaled = {
                    "copper": int(round(rewards.get("copper", 0) * sc_c * rank_mult)),
                    "exp": int(round(rewards.get("exp", 0) * sc_e * rank_mult)),
                    "gold": int(round(rewards.get("gold", 0) * sc_g)),
                }
                total_rewards["copper"] += rewards_scaled["copper"]
                total_rewards["exp"] += rewards_scaled["exp"]
                total_rewards["gold"] += rewards_scaled["gold"]
                claimed_quests.append({
                    "quest_id": row.get("quest_id"),
                    "rewards": rewards_scaled,
                })
            if claimed_quests:
                cur.execute(
                    """UPDATE users
                       SET copper = copper + ?, exp = exp + ?, gold = gold + ?, last_quest_claim_time = ?
                       WHERE user_id = ? AND (? - last_quest_claim_time) >= ?""",
                    (
                        total_rewards["copper"],
                        total_rewards["exp"],
                        total_rewards["gold"],
                        now,
                        user_id,
                        now,
                        claim_cooldown_seconds,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("COOLDOWN")
    except ValueError as exc:
        if str(exc) == "COOLDOWN":
            updated_user = get_user_by_id(user_id) or user
            updated_last = int(updated_user.get("last_quest_claim_time", 0) or 0)
            cooldown_remaining = max(0, claim_cooldown_seconds - (now - updated_last))
            return _dedup_return({
                "success": False,
                "code": "COOLDOWN",
                "message": f"操作太快，请等待 {cooldown_remaining} 秒再领取",
                "cooldown_remaining": cooldown_remaining,
            }, 400)
        return _dedup_return({"success": False, "code": "ALREADY_CLAIMED", "message": "已领取过奖励"}, 400)

    if not claimed_quests:
        resp = {
            "success": True,
            "message": "暂无可领取的任务奖励",
            "claimed_count": 0,
            "claimed_quests": [],
            "rewards": {"copper": 0, "exp": 0, "gold": 0},
        }
        return _dedup_return(resp, 200)

    resp = {
        "success": True,
        "message": f"领取成功！共领取 {len(claimed_quests)} 个任务奖励",
        "claimed_count": len(claimed_quests),
        "claimed_quests": claimed_quests,
        "rewards": total_rewards,
    }
    log_event(
        "quest_claim_all",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"claimed_count": len(claimed_quests)},
    )
    log_economy_ledger(
        user_id=user_id,
        module="quest",
        action="quest_claim_all",
        delta_copper=int(total_rewards.get("copper", 0) or 0),
        delta_gold=int(total_rewards.get("gold", 0) or 0),
        delta_exp=int(total_rewards.get("exp", 0) or 0),
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"claimed_count": len(claimed_quests)},
    )
    return _dedup_return(resp, 200)


def settle_enhance(
    *,
    user_id: str,
    item_db_id: int,
    request_id: Optional[str],
    enhance_cooldown_seconds: int,
    strategy: str = "steady",
    now: Optional[int] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="enhance")
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
            save_response(request_id, user_id, "enhance", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        log_event(
            "enhance",
            user_id=user_id,
            success=False,
            request_id=request_id,
            reason="USER_NOT_FOUND",
        )
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)
    rank = int(user.get("rank", 1) or 1)

    def _log_enhance(success: bool, *, reason: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        log_event(
            "enhance",
            user_id=user_id,
            success=success,
            request_id=request_id,
            rank=rank,
            reason=reason,
            meta=meta,
        )

    item = get_item_by_db_id(item_db_id)
    if not item or item["user_id"] != user_id:
        _log_enhance(False, reason="NOT_FOUND", meta={"item_db_id": item_db_id})
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "物品不存在"}, 404)
    if item["item_type"] not in ("weapon", "armor", "accessory"):
        _log_enhance(False, reason="INVALID", meta={"item_db_id": item_db_id})
        return _dedup_return({"success": False, "code": "INVALID", "message": "只能强化装备"}, 400)
    strategy = (strategy or "steady").strip().lower()
    if strategy not in ("steady", "risky", "focused"):
        strategy = "steady"

    now = int(time.time()) if now is None else int(now)
    last_enhance = int(user.get("last_enhance_time", 0) or 0)
    remaining = enhance_cooldown_seconds - (now - last_enhance)
    if remaining > 0:
        _log_enhance(False, reason="COOLDOWN", meta={"item_db_id": item_db_id, "strategy": strategy})
        return _dedup_return({
            "success": False,
            "code": "COOLDOWN",
            "message": f"操作太快，请等待 {remaining} 秒再强化",
            "cooldown_remaining": remaining,
        }, 400)

    current_level = item.get("enhance_level", 0) or 0
    max_level = int(APP_CONFIG.get("balance", {}).get("enhance", {}).get("max_level", 10))
    if current_level >= max_level:
        _log_enhance(False, reason="MAX_LEVEL", meta={"item_db_id": item_db_id, "strategy": strategy})
        return _dedup_return({"success": False, "code": "MAX_LEVEL", "message": f"已达最大强化等级 (+{max_level})"}, 400)

    # REVIEW_balance: cost = 120 * (level+1)^2
    cost_base = int(APP_CONFIG.get("balance", {}).get("enhance_curve", {}).get("cost_base", 120))
    new_level = current_level + 1
    cost = int(cost_base * (new_level ** 2))

    # REVIEW_balance: material cost (use iron_ore as default enhancement stone)
    mat_id = str(APP_CONFIG.get("balance", {}).get("enhance_curve", {}).get("material_item_id", "iron_ore"))
    mat_need = int(APP_CONFIG.get("balance", {}).get("enhance_curve", {}).get("material_per_level_base", 1))
    # simple scaling: need = base + floor((new_level-1)/3)
    mat_need = max(1, mat_need + (new_level - 1) // 3)
    pct_mult = 1.0
    success_rate = 1.0
    fail_cost = cost
    strategy_name = "保守强化"
    if strategy == "steady":
        cost = max(1, int(round(cost * 0.85)))
        pct_mult = 0.85
    elif strategy == "risky":
        cost = max(1, int(round(cost * 1.1)))
        fail_cost = max(1, int(round(cost * 0.6)))
        pct_mult = 1.5
        success_rate = 0.68
        strategy_name = "冲击强化"
    elif strategy == "focused":
        cost = max(1, int(round(cost * 0.75)))
        pct_mult = 1.2
        strategy_name = "材料专精强化"
        item_type = item["item_type"]
        if item_type == "weapon":
            mat_id = "iron_ore"
        elif item_type == "armor":
            mat_id = "spirit_stone"
        else:
            mat_id = "spirit_herb"
        mat_need = mat_need + 1

    if user.get("copper", 0) < cost:
        _log_enhance(False, reason="INSUFFICIENT", meta={"item_db_id": item_db_id, "strategy": strategy, "cost": cost})
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {cost} 下品灵石"}, 400)
    mat_row = fetch_one(
        "SELECT id, quantity FROM items WHERE user_id = ? AND item_id = ? AND item_type = 'material' ORDER BY id ASC LIMIT 1",
        (user_id, mat_id),
    )
    if not mat_row or int(mat_row.get("quantity", 0) or 0) < mat_need:
        _log_enhance(False, reason="INSUFFICIENT_MATERIAL", meta={"item_db_id": item_db_id, "strategy": strategy, "material_id": mat_id, "material_need": mat_need})
        return _dedup_return({
            "success": False,
            "code": "INSUFFICIENT_MATERIAL",
            "message": f"材料不足，需要 {mat_need} 个 {mat_id}",
            "material": {"item_id": mat_id, "need": mat_need, "have": int(mat_row.get('quantity',0) or 0) if mat_row else 0},
        }, 400)
    ok_stamina, stamina_user = spend_user_stamina(user_id, 1, now=now)
    if not ok_stamina:
        _log_enhance(False, reason="INSUFFICIENT_STAMINA", meta={"item_db_id": item_db_id, "strategy": strategy})
        payload, status = _stamina_block_response(stamina_user or user, action_label="强化装备")
        return _dedup_return(payload, status)

    pct = _enhance_pct_for_level(new_level) * pct_mult

    # Apply percentage increase to base bonuses (min 1 if bonus>0)
    atk_add = int(max(0, item.get("attack_bonus", 0)) * pct)
    dfn_add = int(max(0, item.get("defense_bonus", 0)) * pct)
    hp_add = int(max(0, item.get("hp_bonus", 0)) * pct)
    mp_add = int(max(0, item.get("mp_bonus", 0)) * pct)
    if item.get("attack_bonus", 0) > 0:
        atk_add = max(atk_add, 1)
    if item.get("defense_bonus", 0) > 0:
        dfn_add = max(dfn_add, 1)
    if item.get("hp_bonus", 0) > 0:
        hp_add = max(hp_add, 1)
    if item.get("mp_bonus", 0) > 0:
        mp_add = max(mp_add, 1)

    focus_bonus = {"attack": 0, "defense": 0, "hp": 0, "mp": 0}
    if strategy == "focused":
        item_type = item.get("item_type")
        if item_type == "weapon" and item.get("attack_bonus", 0) > 0:
            focus_bonus["attack"] = max(1, int(item.get("attack_bonus", 0) * 0.05))
            atk_add += focus_bonus["attack"]
        elif item_type == "armor" and item.get("defense_bonus", 0) > 0:
            focus_bonus["defense"] = max(1, int(item.get("defense_bonus", 0) * 0.05))
            dfn_add += focus_bonus["defense"]
        else:
            base_hp = int(item.get("hp_bonus", 0) or 0)
            base_mp = int(item.get("mp_bonus", 0) or 0)
            if base_hp >= base_mp and base_hp > 0:
                focus_bonus["hp"] = max(1, int(base_hp * 0.05))
                hp_add += focus_bonus["hp"]
            elif base_mp > 0:
                focus_bonus["mp"] = max(1, int(base_mp * 0.05))
                mp_add += focus_bonus["mp"]

    enhance_success = random.random() <= success_rate
    copper_spent = cost if enhance_success else fail_cost

    try:
        # ---- 单事务原子强化 ----
        with db_transaction() as cur:
            # 1. 消耗材料（带条件，防并发下重复扣减）
            if int(mat_row.get("quantity", 0) or 0) == mat_need:
                cur.execute(
                    "DELETE FROM items WHERE id = ? AND user_id = ? AND quantity = ?",
                    (mat_row["id"], user_id, mat_need),
                )
            else:
                cur.execute(
                    "UPDATE items SET quantity = quantity - ? WHERE id = ? AND user_id = ? AND quantity >= ?",
                    (mat_need, mat_row["id"], user_id, mat_need),
                )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT_MATERIAL")

            if enhance_success:
                cur.execute(
                    """UPDATE items SET enhance_level = ?,
                       attack_bonus = attack_bonus + ?,
                       defense_bonus = defense_bonus + ?,
                       hp_bonus = hp_bonus + ?,
                       mp_bonus = mp_bonus + ?,
                       item_name = ? WHERE id = ? AND user_id = ?""",
                    (
                        new_level,
                        atk_add,
                        dfn_add,
                        hp_add,
                        mp_add,
                        f"{item['item_name'].split(' +')[0]} +{new_level}",
                        item_db_id,
                        user_id,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("ITEM_NOT_FOUND")

            cur.execute(
                "UPDATE users SET copper = copper - ?, last_enhance_time = ? WHERE user_id = ? AND copper >= ?",
                (copper_spent, now, user_id, copper_spent),
            )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_MATERIAL":
            _log_enhance(False, reason="INSUFFICIENT_MATERIAL", meta={"item_db_id": item_db_id, "strategy": strategy, "material_id": mat_id, "material_need": mat_need})
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_MATERIAL",
                "message": f"材料不足，需要 {mat_need} 个 {mat_id}",
            }, 400)
        if reason == "ITEM_NOT_FOUND":
            _log_enhance(False, reason="NOT_FOUND", meta={"item_db_id": item_db_id, "strategy": strategy})
            return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "物品不存在"}, 404)
        _log_enhance(False, reason="INSUFFICIENT", meta={"item_db_id": item_db_id, "strategy": strategy, "cost": copper_spent})
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {copper_spent} 下品灵石"}, 400)

    if enhance_success:
        message = f"{strategy_name}成功！{item['item_name'].split(' +')[0]} +{new_level}"
    else:
        message = f"{strategy_name}失败，本次未提升等级，但消耗了部分资源"
    resp = {
        "success": True,
        "enhance_success": enhance_success,
        "message": message,
        "strategy": strategy,
        "strategy_name": strategy_name,
        "new_level": new_level if enhance_success else current_level,
        "cost": cost if enhance_success else fail_cost,
        "material": {"item_id": mat_id, "used": mat_need},
        "bonuses_added": {"attack": atk_add if enhance_success else 0, "defense": dfn_add if enhance_success else 0, "hp": hp_add if enhance_success else 0, "mp": mp_add if enhance_success else 0},
        "focus_bonus": focus_bonus if strategy == "focused" and enhance_success else None,
    }
    _log_enhance(
        True,
        meta={
            "item_db_id": item_db_id,
            "item_id": item.get("item_id"),
            "strategy": strategy,
            "enhance_success": enhance_success,
            "new_level": new_level if enhance_success else current_level,
            "material_id": mat_id,
            "material_need": mat_need,
        },
    )
    log_economy_ledger(
        user_id=user_id,
        module="enhance",
        action="enhance",
        delta_copper=-copper_spent,
        delta_stamina=-1,
        item_id=mat_id,
        qty=mat_need,
        success=True,
        request_id=request_id,
        rank=rank,
        meta={
            "item_db_id": item_db_id,
            "item_id": item.get("item_id"),
            "strategy": strategy,
            "enhance_success": enhance_success,
            "new_level": new_level if enhance_success else current_level,
        },
    )
    return _dedup_return(resp, 200)
