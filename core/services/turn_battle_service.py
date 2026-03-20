"""Turn-based battle sessions for Telegram-driven PvE fights."""

from __future__ import annotations

import logging
import random
import time
import json
import threading
import copy
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.config import config
from core.database.connection import (
    db_transaction,
    get_user_by_id,
    get_user_skills,
    spend_user_stamina_tx,
    refresh_user_stamina,
    execute,
    refresh_user_vitals,
    fetch_one,
    fetch_all,
    ensure_battle_session_table,
)
from core.database.migrations import reserve_request, save_response
from core.game.combat import get_monster_by_id, create_combatant_from_user, create_combatant_from_monster
from core.game.combat_kernel import (
    apply_counter_bonus,
    apply_critical,
    apply_defensive_affixes as apply_defensive_kernel,
    apply_poison_thorns as apply_poison_thorns_kernel,
    calc_base_damage,
    maybe_apply_enrage as maybe_apply_enrage_kernel,
)
from core.game.elements import get_element_relationship
from core.game.items import calculate_drop_rewards, generate_equipment, generate_material, generate_pill, get_item_by_id, Quality
from core.game.secret_realms import (
    apply_secret_realm_modifiers,
    can_explore_secret_realm,
    get_secret_realm_attempts_left,
    get_secret_realm_by_id,
    DAILY_SECRET_REALM_LIMIT,
    roll_secret_realm_encounter,
    roll_secret_realm_rewards,
    scale_secret_realm_monster,
)
from core.game.skills import compute_skill_mp_cost, format_skill_mp_cost, get_skill, scale_skill_effect
from core.game.elite_affixes import roll_elite_affixes, apply_elite_affixes
from core.services.balance_service import exp_fatigue_multiplier, fatigue_multiplier, hunt_rewards
from core.services.codex_service import ensure_item, ensure_monster
from core.services.drop_pity_service import roll_targeted_drop_with_pity
from core.services.quests_service import increment_quest
from core.services.sect_service import apply_sect_stat_buffs, get_user_sect_buffs, increment_sect_quest_progress
from core.services.skills_service import gain_skill_mastery
from core.services.realm_trials_service import increment_realm_trial
from core.utils.timeutil import midnight_timestamp, local_day_key
from core.utils.number import format_stamina_value

logger = logging.getLogger("core.turn_battle")
_BATTLE_SESSIONS: dict[str, dict[str, Any]] = {}
_BATTLE_SESSIONS_LOCK = threading.RLock()
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_LOCK = threading.Lock()


def _session_ttl_seconds() -> int:
    configured = config.get_nested("battle", "session_ttl_seconds", default=900)
    try:
        return max(60, int(configured or 900))
    except (TypeError, ValueError):
        return 900


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


def _compensate_expired_secret_session(session: Dict[str, Any], now: int) -> None:
    if session.get("kind") not in ("secret", "secret_event"):
        return
    user_id = session.get("user_id")
    if not user_id:
        return
    day_reset = midnight_timestamp()
    cooldown = int(config.secret_realm_cooldown)
    cooldown_cutoff = max(0, now - cooldown)
    try:
        execute(
            """UPDATE users
               SET secret_realm_attempts = CASE
                    WHEN secret_realm_last_reset >= ? THEN GREATEST(secret_realm_attempts - 1, 0)
                    ELSE secret_realm_attempts
               END,
                   last_secret_time = CASE
                    WHEN last_secret_time > ? THEN ?
                    ELSE last_secret_time
               END
               WHERE user_id = ?""",
            (day_reset, cooldown_cutoff, cooldown_cutoff, user_id),
        )
    except Exception as exc:
        logger.warning(
            "secret_realm_timeout_compensation_failed user_id=%s error=%s",
            user_id,
            type(exc).__name__,
        )


def _cleanup_expired_sessions(now_ts: Optional[int] = None) -> int:
    now = int(time.time()) if now_ts is None else int(now_ts)
    ttl_seconds = _session_ttl_seconds()
    expire_before = now - ttl_seconds
    stale_ids = []
    for sid, sess in _session_items_snapshot():
        last_active = int(sess.get("last_active_at", sess.get("started_at", 0)) or 0)
        if last_active <= expire_before:
            stale_ids.append(sid)
    for sid in stale_ids:
        sess = _session_pop(sid)
        if sess:
            _compensate_expired_secret_session(sess, now)
        _delete_session(sid)
    try:
        rows = fetch_all(
            "SELECT session_id, data_json FROM battle_sessions WHERE expires_at <= ?",
            (now,),
        )
        for row in rows or []:
            sid = row.get("session_id")
            if sid in stale_ids:
                continue
            try:
                payload = json.loads(row.get("data_json") or "{}")
            except Exception:
                payload = {}
            if payload:
                _compensate_expired_secret_session(payload, now)
        execute("DELETE FROM battle_sessions WHERE expires_at <= ?", (now,))
    except Exception:
        pass
    if stale_ids:
        logger.warning(
            "cleaned_expired_battle_sessions count=%s ttl=%s",
            len(stale_ids),
            ttl_seconds,
        )
    return len(stale_ids)


def _session_id() -> str:
    return f"B{uuid.uuid4().hex}"


def _session_items_snapshot() -> List[Tuple[str, Dict[str, Any]]]:
    with _BATTLE_SESSIONS_LOCK:
        return list(_BATTLE_SESSIONS.items())


def _session_values_snapshot() -> List[Dict[str, Any]]:
    with _BATTLE_SESSIONS_LOCK:
        return list(_BATTLE_SESSIONS.values())


def _session_get(session_id: str) -> Optional[Dict[str, Any]]:
    with _BATTLE_SESSIONS_LOCK:
        return _BATTLE_SESSIONS.get(session_id)


def _session_put(session: Dict[str, Any]) -> None:
    sid = str(session.get("id") or "")
    if not sid:
        return
    with _BATTLE_SESSIONS_LOCK:
        _BATTLE_SESSIONS[sid] = session


def _session_pop(session_id: str) -> Optional[Dict[str, Any]]:
    with _BATTLE_SESSIONS_LOCK:
        return _BATTLE_SESSIONS.pop(session_id, None)


def _session_exists(session_id: str) -> bool:
    with _BATTLE_SESSIONS_LOCK:
        return session_id in _BATTLE_SESSIONS


def _session_exists_in_store(session_id: str) -> bool:
    ensure_battle_session_table()
    row = fetch_one("SELECT 1 AS c FROM battle_sessions WHERE session_id = ? LIMIT 1", (session_id,))
    return bool(row)


def _allocate_session_id(max_attempts: int = 6) -> str:
    for _ in range(max(1, int(max_attempts or 6))):
        sid = _session_id()
        if _session_exists(sid):
            continue
        if _session_exists_in_store(sid):
            continue
        return sid
    raise RuntimeError("unable to allocate unique battle session id")


def _get_session_lock(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_LOCK:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _SESSION_LOCKS[session_id] = lock
        return lock


def _persist_session(session: Dict[str, Any]) -> None:
    ensure_battle_session_table()
    now = int(time.time())
    ttl = _session_ttl_seconds()
    try:
        payload = json.dumps(session, ensure_ascii=False)
    except Exception:
        payload = json.dumps(session, ensure_ascii=False, default=str)
    execute(
        """INSERT INTO battle_sessions(session_id, user_id, kind, data_json, created_at, updated_at, expires_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(session_id)
           DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at, expires_at = excluded.expires_at""",
        (
            session.get("id"),
            session.get("user_id"),
            session.get("kind"),
            payload,
            int(session.get("started_at", now) or now),
            now,
            now + ttl,
        ),
    )


def _load_session(session_id: str) -> Optional[Dict[str, Any]]:
    ensure_battle_session_table()
    row = fetch_one(
        "SELECT data_json, expires_at FROM battle_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not row:
        return None
    now = int(time.time())
    if int(row.get("expires_at", 0) or 0) <= now:
        execute("DELETE FROM battle_sessions WHERE session_id = ?", (session_id,))
        return None
    try:
        return json.loads(row.get("data_json") or "{}")
    except Exception:
        return None


def _delete_session(session_id: str) -> None:
    execute("DELETE FROM battle_sessions WHERE session_id = ?", (session_id,))
    with _SESSION_LOCKS_LOCK:
        _SESSION_LOCKS.pop(session_id, None)


def _find_active_session_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    now = int(time.time())
    ttl = _session_ttl_seconds()
    expire_before = now - ttl
    for sess in _session_values_snapshot():
        if sess.get("user_id") != user_id:
            continue
        last_active = int(sess.get("last_active_at", sess.get("started_at", 0)) or 0)
        if last_active > expire_before:
            return sess
    ensure_battle_session_table()
    row = fetch_one(
        "SELECT session_id FROM battle_sessions WHERE user_id = ? AND expires_at > ? ORDER BY updated_at DESC LIMIT 1",
        (user_id, now),
    )
    if not row:
        return None
    session = _load_session(row.get("session_id"))
    if session:
        _session_put(session)
    return session


def _reset_secret_realm_attempts_if_needed(user_id: str, user: Dict[str, Any], now: int) -> Dict[str, Any]:
    last_reset = int((user or {}).get("secret_realm_last_reset", 0) or 0)
    if last_reset >= midnight_timestamp():
        return user
    execute(
        "UPDATE users SET secret_realm_attempts = 0, secret_realm_last_reset = ? WHERE user_id = ?",
        (int(now), user_id),
    )
    return get_user_by_id(user_id) or user


def _battle_post_status(user: Dict[str, Any], player_hp: int) -> Dict[str, Any]:
    return {
        "hp": max(0, int(player_hp or 0)),
        "max_hp": int(user.get("max_hp", 100) or 100),
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


def _active_skills_for_user(user_id: str) -> List[Dict[str, Any]]:
    learned = get_user_skills(user_id)
    active_skills: List[Dict[str, Any]] = []
    for row in learned:
        if not row.get("equipped"):
            continue
        skill = get_skill(row.get("skill_id"))
        if skill:
            skill = scale_skill_effect(skill, int(row.get("skill_level", 1) or 1))
        if skill and skill.get("type") == "active":
            active_skills.append(skill)
    return active_skills[:2]


def _hunt_session_payload(session: Dict[str, Any], *, message: Optional[str] = None, resumed: bool = False) -> Dict[str, Any]:
    player = session.get("player") or {}
    enemy = session.get("enemy") or {}
    monster = session.get("monster") or {}
    max_mp = int(player.get("max_mp", 0) or 0)
    active_skills = session.get("active_skills") or []
    return {
        "success": True,
        "message": message or ("已恢复进行中的狩猎战斗" if resumed else "战斗开始"),
        "resumed": bool(resumed),
        "session_id": session.get("id"),
        "round": int(session.get("round", 0) or 0),
        "monster": monster,
        "player": {
            "hp": int(player.get("hp", 0) or 0),
            "max_hp": int(player.get("max_hp", 0) or 0),
            "mp": int(player.get("mp", 0) or 0),
            "max_mp": int(player.get("max_mp", 0) or 0),
            "attack": int(player.get("attack", 0) or 0),
            "defense": int(player.get("defense", 0) or 0),
            "name": player.get("name"),
        },
        "enemy": {
            "hp": int(enemy.get("hp", 0) or 0),
            "max_hp": int(enemy.get("max_hp", 0) or 0),
            "attack": int(enemy.get("attack", 0) or 0),
            "defense": int(enemy.get("defense", 0) or 0),
            "name": enemy.get("name"),
            "elite_affixes": list(enemy.get("elite_affix_names") or session.get("elite_affix_names") or []),
        },
        "active_skills": [_skill_summary(skill, max_mp=max_mp) for skill in active_skills],
        "element_relation": session.get("element_relation"),
        "combat_modifiers": session.get("combat_modifiers"),
    }


def _skill_summary(skill: Dict[str, Any], *, max_mp: Optional[int] = None) -> Dict[str, Any]:
    effect = skill.get("effect", {}) or {}
    if max_mp is None:
        mp_cost = int(skill.get("mp_cost", 0) or 0)
        mp_cost_text = format_skill_mp_cost(skill)
    else:
        mp_cost = compute_skill_mp_cost(skill, int(max_mp or 0))
        mp_cost_text = format_skill_mp_cost(skill, max_mp=int(max_mp or 0))
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "desc": skill.get("desc", "主动技能"),
        "damage_pct": int(round(float(effect.get("attack_multiplier", 1.0) or 1.0) * 100)),
        "mp_cost": int(mp_cost),
        "mp_cost_text": mp_cost_text,
    }


def _apply_element_relation(
    user_data: Dict[str, Any],
    monster: Dict[str, Any],
    player: Dict[str, Any],
    enemy: Dict[str, Any],
) -> Optional[str]:
    ue = user_data.get("element")
    me = monster.get("element")
    if not ue or not me:
        return None
    rel = get_element_relationship(ue, me)
    if rel == "restrained":
        player["damage_mul"] = float(player.get("damage_mul", 1.0) or 1.0) * 0.75
        enemy["damage_mul"] = float(enemy.get("damage_mul", 1.0) or 1.0) * 1.25
        return "被克(-25%)/敌+25%"
    if rel == "mutual":
        player["damage_mul"] = float(player.get("damage_mul", 1.0) or 1.0) * 1.25
        enemy["damage_mul"] = float(enemy.get("damage_mul", 1.0) or 1.0) * 0.75
        return "相生(+25%)/敌-25%"
    return "无"


def _apply_skill_utilities(player: Dict[str, Any], skill: Dict[str, Any], logs: List[str]) -> None:
    effect = skill.get("effect", {}) or {}
    max_hp = int(player.get("max_hp", player.get("hp", 0)) or 0)
    max_mp = int(player.get("max_mp", player.get("mp", 0)) or 0)
    if effect.get("self_shield_pct"):
        shield = int(max_hp * float(effect.get("self_shield_pct")))
        if shield > 0:
            player["_shield"] = int(player.get("_shield", 0) or 0) + shield
            logs.append(f"🔰 护盾 +{shield}")
    if effect.get("restore_hp_pct"):
        heal = int(max_hp * float(effect.get("restore_hp_pct")))
        if heal > 0:
            player["hp"] = min(max_hp, int(player.get("hp", 0) or 0) + heal)
            logs.append(f"✨ 回复生命 {heal}")
    if effect.get("restore_mp_pct"):
        mp_gain = int(max_mp * float(effect.get("restore_mp_pct")))
        if mp_gain > 0:
            player["mp"] = min(max_mp, int(player.get("mp", 0) or 0) + mp_gain)
            logs.append(f"💠 回复灵力 {mp_gain}")
    if effect.get("convert_hp_to_mp_pct"):
        hp_cost = int(max_hp * float(effect.get("convert_hp_to_mp_pct")))
        mp_gain = int(max_mp * float(effect.get("convert_hp_to_mp_pct")))
        if hp_cost > 0:
            player["hp"] = max(1, int(player.get("hp", 0) or 0) - hp_cost)
            player["mp"] = min(max_mp, int(player.get("mp", 0) or 0) + mp_gain)
            logs.append(f"💠 转换灵力 {mp_gain}（消耗生命 {hp_cost}）")
    if effect.get("self_damage_pct"):
        recoil = int(max_hp * float(effect.get("self_damage_pct")))
        if recoil > 0:
            player["hp"] = max(1, int(player.get("hp", 0) or 0) - recoil)
            logs.append(f"⚠️ 反噬伤害 {recoil}")


def _calc_strike_legacy(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    *,
    skill: Optional[Dict[str, Any]] = None,
    variance_roll: Optional[float] = None,
    crit_roll: Optional[float] = None,
) -> Tuple[int, List[str], int, float]:
    logs: List[str] = []
    attack = int(attacker.get("attack", 10) or 10)
    defense = int(defender.get("defense", 0) or 0)
    effect = (skill.get("effect", {}) or {}) if skill else {}
    ignore_def_pct = float(effect.get("ignore_def_pct", 0.0) or 0.0)
    if ignore_def_pct > 0:
        defense = int(defense * max(0.0, 1.0 - min(0.95, ignore_def_pct)))
    effective_def = defense * 0.8
    base_damage = max(1, int(attack * attack / max(1, attack + effective_def)))
    variance = random.uniform(0.85, 1.15) if variance_roll is None else float(variance_roll)
    damage = int(base_damage * variance)
    damage = int(damage * float(attacker.get("damage_mul", 1.0) or 1.0))

    counter_bonus = float(attacker.pop("_counter_bonus", 0.0) or 0.0)
    if counter_bonus > 0:
        damage = int(damage * (1.0 + counter_bonus))
        logs.append("🔁 反击强化")

    if skill:
        multiplier = float(effect.get("attack_multiplier", 1.0) or 1.0)
        multiplier *= 1.0 + float(attacker.get("skill_damage", 0.0) or 0.0)
        damage = int(damage * multiplier)
        logs.append(f"✨ {attacker.get('name', '修士')}施展【{skill.get('name', '技能')}】")

    crit_rate = float(attacker.get("crit_rate", 0.05) or 0.05) + float(effect.get("crit_rate_bonus", 0.0) or 0.0)
    crit = False
    roll = random.random() if crit_roll is None else float(crit_roll)
    if roll < crit_rate:
        crit_dmg = max(1.1, float(attacker.get("crit_dmg", 1.5) or 1.5))
        damage = int(damage * crit_dmg)
        logs.append("💥 暴击！")
        crit = True

    damage = int(damage * float(defender.get("damage_taken_mul", 1.0) or 1.0))
    damage = max(1, damage)
    lifesteal = float(attacker.get("lifesteal", 0.0) or 0.0) + float(effect.get("lifesteal_bonus", 0.0) or 0.0)
    self_taken_mul = float(effect.get("damage_taken_mul", 1.0) or 1.0)
    ls_heal = int(damage * lifesteal) if lifesteal > 0 else 0
    heal = ls_heal
    if ls_heal > 0:
        logs.append(f"🩸 吸血回复 {ls_heal}")
    crit_heal_pct = float(attacker.get("crit_heal_pct", 0.0) or 0.0)
    if crit and crit_heal_pct > 0:
        extra_heal = int(damage * crit_heal_pct)
        if extra_heal > 0:
            heal += extra_heal
            logs.append(f"✨ 暴击回血 {extra_heal}")
    return damage, logs, heal, self_taken_mul


def _calc_strike(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    *,
    skill: Optional[Dict[str, Any]] = None,
    variance_roll: Optional[float] = None,
    crit_roll: Optional[float] = None,
) -> Tuple[int, List[str], int, float]:
    logs: List[str] = []
    effect = (skill.get("effect", {}) or {}) if skill else {}
    damage, _ = calc_base_damage(
        attack=int(attacker.get("attack", 10) or 10),
        defense=int(defender.get("defense", 0) or 0),
        ignore_def_pct=float(effect.get("ignore_def_pct", 0.0) or 0.0),
        variance_roll=variance_roll,
    )
    damage = int(damage * float(attacker.get("damage_mul", 1.0) or 1.0))
    damage = apply_counter_bonus(attacker, damage, logs)

    if skill:
        multiplier = float(effect.get("attack_multiplier", 1.0) or 1.0)
        multiplier *= 1.0 + float(attacker.get("skill_damage", 0.0) or 0.0)
        damage = int(damage * multiplier)
        logs.append(f"✨ {attacker.get('name', '修士')}施展【{skill.get('name', '技能')}】")

    damage, crit, _ = apply_critical(
        attacker,
        damage,
        extra_crit_rate=float(effect.get("crit_rate_bonus", 0.0) or 0.0),
        crit_roll=crit_roll,
        logs=logs,
    )
    damage = int(damage * float(defender.get("damage_taken_mul", 1.0) or 1.0))
    damage = max(1, damage)
    lifesteal = float(attacker.get("lifesteal", 0.0) or 0.0) + float(effect.get("lifesteal_bonus", 0.0) or 0.0)
    self_taken_mul = float(effect.get("damage_taken_mul", 1.0) or 1.0)
    ls_heal = int(damage * lifesteal) if lifesteal > 0 else 0
    heal = ls_heal
    if ls_heal > 0:
        logs.append(f"🩸 吸血回复 {ls_heal}")
    crit_heal_pct = float(attacker.get("crit_heal_pct", 0.0) or 0.0)
    if crit and crit_heal_pct > 0:
        extra_heal = int(damage * crit_heal_pct)
        if extra_heal > 0:
            heal += extra_heal
            logs.append(f"✨ 暴击回血 {extra_heal}")
    return damage, logs, heal, self_taken_mul


def _apply_defensive_affixes(target: Dict[str, Any], damage: int, round_num: int, logs: List[str]) -> int:
    return apply_defensive_kernel(target, damage, round_num, logs)


def _apply_poison_thorns(attacker: Dict[str, Any], defender: Dict[str, Any], damage: int, logs: List[str]) -> None:
    apply_poison_thorns_kernel(attacker, defender, damage, logs)


def _maybe_apply_enrage(target: Dict[str, Any], logs: List[str]) -> None:
    maybe_apply_enrage_kernel(target, logs)


def _kernel_shadow_enabled() -> bool:
    return bool(config.get_nested("battle", "kernel_shadow", "enabled", default=False))


def _kernel_shadow_threshold_pct() -> float:
    try:
        return max(0.0, float(config.get_nested("battle", "kernel_shadow", "max_damage_diff_pct", default=0.15) or 0.15))
    except (TypeError, ValueError):
        return 0.15


def _audit_kernel_shadow(
    session: Dict[str, Any],
    *,
    phase: str,
    damage_new: int,
    damage_old: int,
    heal_new: int,
    heal_old: int,
) -> None:
    if not _kernel_shadow_enabled():
        return
    dmg_base = max(1, abs(int(damage_old or 0)))
    heal_base = max(1, abs(int(heal_old or 0)))
    dmg_diff_pct = abs(int(damage_new or 0) - int(damage_old or 0)) / dmg_base
    heal_diff_pct = abs(int(heal_new or 0) - int(heal_old or 0)) / heal_base
    threshold = _kernel_shadow_threshold_pct()
    if dmg_diff_pct <= threshold and heal_diff_pct <= threshold:
        return
    alerts = session.setdefault("kernel_shadow_alerts", [])
    alerts.append(
        {
            "phase": phase,
            "round": int(session.get("round", 0) or 0),
            "damage_new": int(damage_new or 0),
            "damage_legacy": int(damage_old or 0),
            "heal_new": int(heal_new or 0),
            "heal_legacy": int(heal_old or 0),
            "damage_diff_pct": round(dmg_diff_pct, 4),
            "heal_diff_pct": round(heal_diff_pct, 4),
        }
    )
    logger.warning(
        "kernel_shadow_diff session=%s phase=%s round=%s damage_new=%s damage_legacy=%s heal_new=%s heal_legacy=%s",
        session.get("id"),
        phase,
        int(session.get("round", 0) or 0),
        int(damage_new or 0),
        int(damage_old or 0),
        int(heal_new or 0),
        int(heal_old or 0),
    )


def _run_round(session: Dict[str, Any], action: str, skill_id: Optional[str] = None) -> Dict[str, Any]:
    player = session["player"]
    enemy = session["enemy"]
    session["last_active_at"] = int(time.time())
    selected_skill = None
    selected_skill_mp_cost = 0
    invalid_skill_requested = False
    if action == "skill" and skill_id:
        candidate = next((s for s in session.get("active_skills", []) if s.get("id") == skill_id), None)
        if candidate:
            max_mp_for_cost = int(player.get("max_mp", player.get("mp", 0)) or 0)
            mp_cost = compute_skill_mp_cost(candidate, max_mp_for_cost)
            if int(player.get("mp", 0) or 0) < mp_cost:
                return {
                    "finished": False,
                    "victory": False,
                    "invalid_action": True,
                    "round": int(session.get("round", 0) or 0),
                    "round_log": [f"💙 灵力不足，{candidate.get('name', '技能')}需要 {mp_cost} MP，改用普通攻击或先恢复。"],
                    "player_hp": player["hp"],
                    "enemy_hp": enemy["hp"],
                }
            player["mp"] = max(0, int(player.get("mp", 0) or 0) - mp_cost)
            selected_skill = candidate
            selected_skill_mp_cost = mp_cost
        else:
            invalid_skill_requested = True

    session["round"] = int(session.get("round", 0) or 0) + 1
    round_logs = [f"第 {session['round']} 回合"]
    if invalid_skill_requested:
        round_logs.append("⚠️ 技能无效，已降级为普通攻击。")
    if session["round"] == 1:
        elite_names = (enemy.get("elite_affix_names") or session.get("elite_affix_names")) if enemy else None
        if elite_names:
            round_logs.append(f"👹 精英词缀: {'、'.join(elite_names)}")
    if selected_skill and selected_skill_mp_cost > 0:
        round_logs.append(f"💙 消耗 {selected_skill_mp_cost} MP")
    if selected_skill:
        _apply_skill_utilities(player, selected_skill, round_logs)

    player_variance_roll = random.uniform(0.85, 1.15)
    player_crit_roll = random.random()
    shadow_player = copy.deepcopy(player)
    shadow_enemy = copy.deepcopy(enemy)
    damage, logs, heal, self_taken_mul = _calc_strike(
        player,
        enemy,
        skill=selected_skill,
        variance_roll=player_variance_roll,
        crit_roll=player_crit_roll,
    )
    legacy_damage, _, legacy_heal, _ = _calc_strike_legacy(
        shadow_player,
        shadow_enemy,
        skill=selected_skill,
        variance_roll=player_variance_roll,
        crit_roll=player_crit_roll,
    )
    _audit_kernel_shadow(
        session,
        phase="player_attack",
        damage_new=damage,
        damage_old=legacy_damage,
        heal_new=heal,
        heal_old=legacy_heal,
    )
    round_logs.extend(logs)
    damage = _apply_defensive_affixes(enemy, damage, int(session.get("round", 1) or 1), round_logs)
    enemy["hp"] = max(0, int(enemy.get("hp", 0) or 0) - damage)
    _apply_poison_thorns(player, enemy, damage, round_logs)
    if damage > 0 and float(enemy.get("counter_bonus_pct", 0.0) or 0.0) > 0 and enemy.get("hp", 0) > 0:
        enemy["_counter_bonus"] = max(float(enemy.get("_counter_bonus", 0.0) or 0.0), float(enemy.get("counter_bonus_pct", 0.0) or 0.0))
    _maybe_apply_enrage(enemy, round_logs)
    session["player_damage_dealt"] = int(session.get("player_damage_dealt", 0) or 0) + damage
    round_logs.append(f"🗡️ {player.get('name')}造成 {damage} 伤害，{enemy.get('name')}剩余 {enemy['hp']}/{enemy.get('max_hp', enemy['hp'])} HP")
    if heal > 0:
        player["hp"] = min(int(player.get("max_hp", player["hp"]) or player["hp"]), int(player.get("hp", 0) or 0) + heal)
    if selected_skill:
        session["skill_uses"] = int(session.get("skill_uses", 0) or 0) + 1
        try:
            gain_skill_mastery(session.get("user_id"), selected_skill.get("id"), 1)
        except Exception:
            pass
    if enemy["hp"] <= 0:
        combat_modifiers = session.setdefault("combat_modifiers", {})
        kernel_shadow_meta = combat_modifiers.setdefault("kernel_shadow", {"enabled": _kernel_shadow_enabled(), "alerts": 0})
        kernel_shadow_meta["alerts"] = len(session.get("kernel_shadow_alerts", []))
        session.setdefault("history", []).extend(round_logs)
        return {
            "finished": True,
            "victory": True,
            "round": session["round"],
            "round_log": round_logs,
            "player_hp": player["hp"],
            "enemy_hp": enemy["hp"],
            "skill_used": selected_skill.get("id") if selected_skill else None,
        }
    if player["hp"] <= 0:
        combat_modifiers = session.setdefault("combat_modifiers", {})
        kernel_shadow_meta = combat_modifiers.setdefault("kernel_shadow", {"enabled": _kernel_shadow_enabled(), "alerts": 0})
        kernel_shadow_meta["alerts"] = len(session.get("kernel_shadow_alerts", []))
        session.setdefault("history", []).extend(round_logs)
        return {
            "finished": True,
            "victory": False,
            "round": session["round"],
            "round_log": round_logs,
            "player_hp": player["hp"],
            "enemy_hp": enemy["hp"],
            "skill_used": selected_skill.get("id") if selected_skill else None,
        }

    enemy_variance_roll = random.uniform(0.85, 1.15)
    enemy_crit_roll = random.random()
    shadow_enemy_atk = copy.deepcopy(enemy)
    shadow_player_def = copy.deepcopy(player)
    enemy_damage, enemy_logs, _, _ = _calc_strike(
        enemy,
        player,
        skill=None,
        variance_roll=enemy_variance_roll,
        crit_roll=enemy_crit_roll,
    )
    legacy_enemy_damage, _, _, _ = _calc_strike_legacy(
        shadow_enemy_atk,
        shadow_player_def,
        skill=None,
        variance_roll=enemy_variance_roll,
        crit_roll=enemy_crit_roll,
    )
    _audit_kernel_shadow(
        session,
        phase="enemy_attack",
        damage_new=enemy_damage,
        damage_old=legacy_enemy_damage,
        heal_new=0,
        heal_old=0,
    )
    enemy_damage = int(enemy_damage * self_taken_mul)
    round_logs.extend(enemy_logs)
    enemy_damage = _apply_defensive_affixes(player, enemy_damage, int(session.get("round", 1) or 1), round_logs)
    player["hp"] = max(0, int(player.get("hp", 0) or 0) - enemy_damage)
    _apply_poison_thorns(enemy, player, enemy_damage, round_logs)
    if enemy_damage > 0 and float(player.get("counter_bonus_pct", 0.0) or 0.0) > 0 and player.get("hp", 0) > 0:
        player["_counter_bonus"] = max(float(player.get("_counter_bonus", 0.0) or 0.0), float(player.get("counter_bonus_pct", 0.0) or 0.0))
    _maybe_apply_enrage(player, round_logs)
    session["enemy_damage_dealt"] = int(session.get("enemy_damage_dealt", 0) or 0) + enemy_damage
    round_logs.append(f"👹 {enemy.get('name')}反击造成 {enemy_damage} 伤害，{player.get('name')}剩余 {player['hp']}/{player.get('max_hp', player['hp'])} HP")
    combat_modifiers = session.setdefault("combat_modifiers", {})
    kernel_shadow_meta = combat_modifiers.setdefault("kernel_shadow", {"enabled": _kernel_shadow_enabled(), "alerts": 0})
    kernel_shadow_meta["alerts"] = len(session.get("kernel_shadow_alerts", []))
    session.setdefault("history", []).extend(round_logs)
    return {
        "finished": player["hp"] <= 0 or enemy["hp"] <= 0,
        "victory": player["hp"] > 0 if enemy["hp"] > 0 else True,
        "round": session["round"],
        "round_log": round_logs,
        "player_hp": player["hp"],
        "enemy_hp": enemy["hp"],
        "skill_used": selected_skill.get("id") if selected_skill else None,
    }


def _user_current_hp_for_battle(user: Dict[str, Any], buffed_max_hp: int) -> int:
    current_hp = int(user.get("hp", buffed_max_hp) or buffed_max_hp)
    base_max_hp = int(user.get("max_hp", buffed_max_hp) or buffed_max_hp)
    ratio = current_hp / max(1, base_max_hp)
    return max(1, min(buffed_max_hp, int(round(buffed_max_hp * ratio))))


def _deduct_item(cur, user_id: str, item_id: str, quantity: int, item_type: str) -> bool:
    remaining = int(quantity or 0)
    rows = cur.execute(
        "SELECT id, quantity FROM items WHERE user_id = ? AND item_id = ? AND item_type = ? ORDER BY id ASC",
        (user_id, item_id, item_type),
    ).fetchall()
    for row in rows:
        if remaining <= 0:
            break
        have = int(row["quantity"] or 0)
        if have <= 0:
            continue
        if have <= remaining:
            cur.execute(
                "DELETE FROM items WHERE id = ? AND user_id = ? AND item_id = ? AND item_type = ? AND quantity = ?",
                (row["id"], user_id, item_id, item_type, have),
            )
            if int(cur.rowcount or 0) == 0:
                return False
            remaining -= have
        else:
            cur.execute(
                "UPDATE items SET quantity = quantity - ? WHERE id = ? AND user_id = ? AND item_id = ? AND item_type = ? AND quantity >= ?",
                (remaining, row["id"], user_id, item_id, item_type, remaining),
            )
            if int(cur.rowcount or 0) == 0:
                return False
            remaining = 0
    return remaining <= 0


def _secret_event_choices(event_type: str) -> List[Dict[str, Any]]:
    if event_type == "trap":
        return [
            {"id": "avoid", "label": "使用回血丹规避", "note": "消耗1个回血丹，规避惩罚"},
            {"id": "fight", "label": "强闯守卫", "note": "转战斗保底，胜利可免惩罚"},
            {"id": "endure", "label": "硬吃惩罚", "note": "直接承受陷阱损伤"},
        ]
    if event_type == "treasure_event":
        return [
            {"id": "loot_safe", "label": "稳妥搜刮", "note": "直接获得稳定收获"},
            {"id": "loot_fight", "label": "挑战守宝怪", "note": "转战斗，胜利提升掉落"},
            {"id": "leave", "label": "谨慎撤离", "note": "放弃探索，保守离开"},
        ]
    return []


def _build_secret_battle_session(
    *,
    user_id: str,
    user: Dict[str, Any],
    realm: Dict[str, Any],
    path: str,
    encounter: Dict[str, Any],
    now: int,
    session_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], int]:
    monster = get_monster_by_id(encounter.get("monster_id"))
    if not monster:
        return None, {"success": False, "message": "秘境遭遇失效，请重新进入"}, 404
    monster = scale_secret_realm_monster(monster, encounter)
    buffed_user = apply_sect_stat_buffs(user)
    active_skills = _active_skills_for_user(user_id)
    player = create_combatant_from_user(buffed_user, get_user_skills(user_id), selected_active_skill_id=None)
    player["hp"] = _user_current_hp_for_battle(user, int(player.get("max_hp", user.get("max_hp", 100)) or 100))
    enemy = create_combatant_from_monster(monster)
    elite_keys, elite_names = roll_elite_affixes(user_rank=int(user.get("rank", 1) or 1), source_kind="secret")
    apply_elite_affixes(enemy, elite_keys)
    element_relation = _apply_element_relation(user, monster, player, enemy)
    combat_modifiers = {
        "element_relation": element_relation,
        "elite_affixes": elite_names,
        "source_kind": "secret",
        "path": path,
        "danger_scale": float(encounter.get("danger_scale", 1.0) or 1.0),
        "kernel_shadow": {"enabled": _kernel_shadow_enabled(), "alerts": 0},
    }
    sid = session_id or _allocate_session_id()
    session = {
        "id": sid,
        "kind": "secret",
        "user_id": user_id,
        "realm": realm,
        "path": path,
        "encounter": encounter,
        "monster": monster,
        "user_snapshot": user,
        "player": player,
        "enemy": enemy,
        "elite_affix_names": elite_names,
        "active_skills": active_skills,
        "round": 0,
        "history": [],
        "starting_player_hp": int(player["hp"]),
        "player_damage_dealt": 0,
        "enemy_damage_dealt": 0,
        "skill_uses": 0,
        "element_relation": element_relation,
        "combat_modifiers": combat_modifiers,
        "started_at": now,
        "last_active_at": now,
    }
    response = {
        "success": True,
        "needs_battle": True,
        "session_id": sid,
        "realm": realm,
        "path": path,
        "encounter": encounter,
        "monster": monster,
        "player": {"hp": player["hp"], "max_hp": player["max_hp"], "mp": player["mp"], "max_mp": player["max_mp"], "attack": player["attack"], "defense": player["defense"], "name": player["name"]},
        "enemy": {"hp": enemy["hp"], "max_hp": enemy["max_hp"], "attack": enemy["attack"], "defense": enemy["defense"], "name": enemy["name"], "elite_affixes": elite_names},
        "active_skills": [_skill_summary(skill, max_mp=int(player.get("max_mp", 0) or 0)) for skill in active_skills],
        "element_relation": element_relation,
        "combat_modifiers": combat_modifiers,
    }
    return session, response, 200


def _battle_failure_reasons(session: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    user = session.get("user_snapshot") or {}
    player = session.get("player") or {}
    enemy = session.get("enemy") or {}
    original_hp = int(session.get("starting_player_hp", player.get("max_hp", 1)) or 1)
    max_hp = int(player.get("max_hp", 1) or 1)
    enemy_max_hp = int(enemy.get("max_hp", enemy.get("hp", 1)) or 1)
    rounds = int(session.get("round", 0) or 0)
    player_damage = int(session.get("player_damage_dealt", 0) or 0)
    enemy_damage = int(session.get("enemy_damage_dealt", 0) or 0)
    skill_uses = int(session.get("skill_uses", 0) or 0)

    if original_hp <= max(1, int(max_hp * 0.55)):
        reasons.append("开战时血量偏低，没能扛住后续回合。")
    relation_text = str(session.get("element_relation") or "")
    if relation_text.startswith("被克"):
        reasons.append("你被怪物五行克制，输出被明显压低。")
    if enemy_damage >= max_hp or int(enemy.get("attack", 0) or 0) >= int(player.get("defense", 0) or 0) * 2:
        reasons.append("敌方伤害过高，你的防御和血量不够稳。")
    if rounds >= 3 and player_damage < max(1, int(enemy_max_hp * 0.65)):
        reasons.append("输出不足，没能在前几回合压住敌人血线。")
    if session.get("kind") == "secret":
        encounter = session.get("encounter") or {}
        if encounter.get("type") in {"elite", "guardian"}:
            reasons.append("这次遇到的是高压遭遇，怪物强度高于普通路线。")
    if skill_uses == 0 and session.get("active_skills"):
        reasons.append("本场没有用主动技能，爆发和功能性没有打出来。")
    if not reasons:
        reasons.append("这场对拼里，对方总伤害略高，你需要再补强面板或换稳一点的路线。")
    return reasons[:3]


def start_hunt_session(user_id: str, monster_id: str, *, now: Optional[int] = None) -> Tuple[Dict[str, Any], int]:
    _cleanup_expired_sessions(now)
    refresh_user_vitals(user_id)
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "message": "玩家不存在"}, 404
    if user.get("state"):
        return {"success": False, "message": "请先结束修炼"}, 400
    now = int(time.time()) if now is None else int(now)
    refresh_user_stamina(user_id, now=now)
    user = get_user_by_id(user_id) or user
    existing = _find_active_session_for_user(user_id)
    if existing:
        if existing.get("kind") == "hunt":
            return _hunt_session_payload(existing, resumed=True), 200
        return {"success": False, "message": "已有进行中的战斗", "session_id": existing.get("id"), "kind": existing.get("kind")}, 409
    last_hunt = int(user.get("last_hunt_time", 0) or 0)
    remaining = config.hunt_cooldown - (now - last_hunt)
    if remaining > 0:
        return {"success": False, "message": f"狩猎冷却中，请等待 {remaining} 秒", "cooldown_remaining": remaining}, 400
    monster = get_monster_by_id(monster_id)
    if not monster:
        return {"success": False, "message": "怪物不存在"}, 404
    user_rank = int(user.get("rank", 1) or 1)
    if int(monster.get("min_rank", 1) or 1) > user_rank:
        return {"success": False, "message": f"需要达到 {monster['min_rank']} 级才能挑战此怪物"}, 400

    hcfg = config.get_nested("balance", "hunt", default={}) or {}
    day = local_day_key(now)
    daily_limit = int(hcfg.get("daily_limit", 50))
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
                (day, day, now, user_id, now, config.hunt_cooldown, day, daily_limit),
            )
            if cur.rowcount == 0:
                raise ValueError("HUNT_BLOCKED")
    except ValueError as exc:
        reason = str(exc)
        latest = get_user_by_id(user_id) or user
        if reason == "INSUFFICIENT_STAMINA":
            return {"success": False, "message": "精力不足，狩猎需要 1 点精力", "stamina": format_stamina_value((latest or {}).get("stamina", 0))}, 400
        last_hunt = int(latest.get("last_hunt_time", 0) or 0)
        remaining = config.hunt_cooldown - (now - last_hunt)
        if remaining > 0:
            return {"success": False, "message": f"狩猎冷却中，请等待 {remaining} 秒", "cooldown_remaining": remaining}, 400
        if int(latest.get("hunts_today_reset", 0) or 0) == day and int(latest.get("hunts_today", 0) or 0) >= daily_limit:
            return {"success": False, "message": f"今日狩猎次数已达上限 ({daily_limit}次)"}, 400
        return {"success": False, "message": "操作过快，请重试"}, 409

    user = get_user_by_id(user_id) or user
    reset_day = int(user.get("hunts_today_reset", 0) or 0)
    hunts_today = int(user.get("hunts_today", 0) or 0)

    buffed_user = apply_sect_stat_buffs(user)
    active_skills = _active_skills_for_user(user_id)
    player = create_combatant_from_user(buffed_user, get_user_skills(user_id), selected_active_skill_id=None)
    player["hp"] = _user_current_hp_for_battle(user, int(player.get("max_hp", user.get("max_hp", 100)) or 100))
    player["max_hp"] = int(player.get("max_hp", user.get("max_hp", 100)) or 100)
    enemy = create_combatant_from_monster(monster)
    elite_keys, elite_names = roll_elite_affixes(user_rank=int(user.get("rank", 1) or 1), source_kind="hunt")
    apply_elite_affixes(enemy, elite_keys)
    element_relation = _apply_element_relation(user, monster, player, enemy)
    combat_modifiers = {
        "element_relation": element_relation,
        "elite_affixes": elite_names,
        "source_kind": "hunt",
        "kernel_shadow": {"enabled": _kernel_shadow_enabled(), "alerts": 0},
    }

    try:
        session_id = _allocate_session_id()
    except RuntimeError:
        logger.error("failed_to_allocate_hunt_session_id user_id=%s", user_id)
        return {"success": False, "message": "战斗会话创建失败，请稍后重试"}, 500
    session_obj = {
        "id": session_id,
        "kind": "hunt",
        "user_id": user_id,
        "monster": monster,
        "user_snapshot": user,
        "player": player,
        "enemy": enemy,
        "elite_affix_names": elite_names,
        "active_skills": active_skills,
        "round": 0,
        "history": [],
        "starting_player_hp": int(player["hp"]),
        "player_damage_dealt": 0,
        "enemy_damage_dealt": 0,
        "skill_uses": 0,
        "element_relation": element_relation,
        "combat_modifiers": combat_modifiers,
        "hunt_day": reset_day,
        "hunt_index": hunts_today,
        "started_at": now,
        "last_active_at": now,
    }
    _session_put(session_obj)
    _persist_session(session_obj)
    return _hunt_session_payload(session_obj), 200


def _finalize_hunt(session: Dict[str, Any], victory: bool) -> Dict[str, Any]:
    user_id = session["user_id"]
    monster = session["monster"]
    user = session["user_snapshot"]
    player = session["player"]
    rewards = {"exp": 0, "copper": 0, "gold": 0}
    drops: List[Dict[str, Any]] = []
    drop_pity_meta: Optional[Dict[str, Any]] = None
    if victory:
        try:
            ensure_monster(user_id, monster["id"])
        except Exception as exc:
            logger.warning(
                "ensure_monster_failed user_id=%s monster_id=%s error=%s",
                user_id,
                monster.get("id"),
                type(exc).__name__,
            )
        hcfg = config.get_nested("balance", "hunt", default={}) or {}
        rank = int(user.get("rank", 1) or 1)
        base_rw = hunt_rewards(rank, hcfg, monster=monster)
        mult = fatigue_multiplier(int(session.get("hunt_index", 1) or 1), hcfg)
        exp_mult = exp_fatigue_multiplier(int(session.get("hunt_index", 1) or 1), hcfg)
        rewards["exp"] = int(base_rw["exp"] * exp_mult)
        rewards["copper"] = int(round(base_rw["copper"] * mult))
        reward_mult = 1.0 + float((get_user_sect_buffs(user_id) or {}).get("battle_reward_pct", 0.0)) / 100.0
        rewards["exp"] = int(round(rewards["exp"] * reward_mult))
        rewards["copper"] = int(round(rewards["copper"] * reward_mult))
        if random.random() < 0.01 and int(user.get("rank", 1) or 1) >= 12:
            rewards["gold"] = random.randint(1, 2)
        drops = calculate_drop_rewards(monster["id"], user.get("rank", 1), include_targeted=False)
        targeted_drop, drop_pity_meta = roll_targeted_drop_with_pity(
            user_id=user_id,
            source_kind="monster",
            source_id=monster["id"],
            user_rank=int(user.get("rank", 1) or 1),
            boosted=False,
        )
        if targeted_drop:
            drops.append(targeted_drop)
        with db_transaction() as cur:
            for drop in drops:
                cur.execute(
                    """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                       quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                       first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, drop.get("item_id"), drop.get("item_name"), drop.get("item_type"), drop.get("quality", "common"),
                     drop.get("quantity", 1), drop.get("level", 1), drop.get("attack_bonus", 0), drop.get("defense_bonus", 0),
                     drop.get("hp_bonus", 0), drop.get("mp_bonus", 0),
                     drop.get("first_round_reduction_pct", 0), drop.get("crit_heal_pct", 0),
                     drop.get("element_damage_pct", 0), drop.get("low_hp_shield_pct", 0)),
                )
            cur.execute(
                """UPDATE users SET exp = exp + ?, copper = copper + ?, gold = gold + ?, hp = ?, dy_times = dy_times + 1
                   WHERE user_id = ?""",
                (rewards["exp"], rewards["copper"], rewards["gold"], max(1, int(player["hp"])), user_id),
            )
            cur.execute("UPDATE users SET mp = ?, vitals_updated_at = ? WHERE user_id = ?", (max(0, int(player.get("mp", 0) or 0)), int(time.time()), user_id))
            cur.execute(
                """INSERT INTO battle_logs (user_id, monster_id, victory, rounds, exp_gained, copper_gained, gold_gained, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, monster["id"], 1, int(session.get("round", 0) or 0), rewards["exp"], rewards["copper"], rewards["gold"], int(time.time())),
            )
        for drop in drops:
            ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
        increment_quest(user_id, "daily_hunt")
        increment_sect_quest_progress(user_id, "hunt", 1)
        try:
            increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "hunt", 1)
        except Exception:
            pass
    else:
        now_ts = int(time.time())
        weak_seconds = _hunt_defeat_weak_seconds()
        weak_until = max(int(user.get("weak_until", 0) or 0), now_ts + weak_seconds) if weak_seconds > 0 else int(user.get("weak_until", 0) or 0)
        execute(
            "UPDATE users SET hp = 1, mp = ?, weak_until = ?, vitals_updated_at = ? WHERE user_id = ?",
            (max(0, int(player.get("mp", 0) or 0)), weak_until, now_ts, user_id),
        )

    updated = get_user_by_id(user_id) or user
    event_points: List[Dict[str, Any]] = []
    if victory:
        try:
            from core.services.events_service import grant_event_points_for_action
            event_points = grant_event_points_for_action(user_id, "hunt_victory", meta={"monster_id": monster.get("id")})
        except Exception:
            event_points = []
    weak_seconds_payload = 0
    if not victory:
        weak_seconds_payload = max(0, int(_hunt_defeat_weak_seconds() or 0))
        weak_text = f"，进入虚弱状态 {_format_weak_duration(weak_seconds_payload)}" if weak_seconds_payload > 0 else ""
        message = f"被【{monster['name']}】击败了...{weak_text}"
    else:
        message = f"成功击败【{monster['name']}】！"

    payload = {
        "success": True,
        "victory": victory,
        "monster": monster,
        "message": message,
        "failure_reasons": [] if victory else _battle_failure_reasons(session),
        "rewards": rewards,
        "drops": drops,
        "rounds": int(session.get("round", 0) or 0),
        "battle_log": session.get("history", []),
        "post_status": _battle_post_status(updated, 1 if not victory else max(1, int(player["hp"]))),
        "element_relation": session.get("element_relation"),
        "combat_modifiers": {
            **(session.get("combat_modifiers") or {}),
            "drop_pity": drop_pity_meta,
        },
        "weak_seconds": weak_seconds_payload,
        "weak_until": int(updated.get("weak_until", 0) or 0),
    }
    if event_points:
        payload["event_points"] = event_points
    return payload


def action_hunt_session(
    user_id: str,
    session_id: str,
    *,
    action: str,
    skill_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    _cleanup_expired_sessions()
    dedup_action = "hunt_turn_action"
    dedup_request_id = str(request_id or "").strip()
    if dedup_request_id:
        dedup_status, cached = reserve_request(
            dedup_request_id,
            user_id=user_id,
            action=dedup_action,
            stale_after_seconds=20,
        )
        if dedup_status == "cached":
            if cached is not None:
                return cached, 200
            return {"success": False, "message": "请求结果不可用，请重试"}, 409
        if dedup_status == "in_progress":
            return {"success": False, "message": "请求处理中，请勿重复点击"}, 409

    def _dedup_return(resp: Dict[str, Any], http_status: int) -> Tuple[Dict[str, Any], int]:
        if dedup_request_id:
            save_response(dedup_request_id, user_id, dedup_action, resp)
        return resp, http_status

    lock = _get_session_lock(session_id)
    if not lock.acquire(blocking=False):
        return _dedup_return({"success": False, "message": "上一回合仍在处理，请稍后重试"}, 409)
    try:
        session = _session_get(session_id)
        if not session:
            session = _load_session(session_id)
            if session:
                _session_put(session)
        if not session or session.get("kind") != "hunt" or session.get("user_id") != user_id:
            return _dedup_return({"success": False, "message": "战斗已失效，请重新开始狩猎"}, 404)
        round_result = _run_round(session, action, skill_id)
        if round_result["finished"]:
            try:
                resp = _finalize_hunt(session, bool(round_result["victory"]))
            except Exception as exc:
                logger.exception("hunt_finalize_failed user_id=%s session_id=%s", user_id, session_id)
                _session_pop(session_id)
                _delete_session(session_id)
                return _dedup_return(
                    {
                        "success": False,
                        "message": "战斗结算异常，本场会话已关闭，请重新开始狩猎",
                        "code": "HUNT_FINALIZE_ERROR",
                        "detail": type(exc).__name__,
                    },
                    500,
                )
            resp["round_log"] = round_result["round_log"]
            resp["session_id"] = session_id
            _session_pop(session_id)
            _delete_session(session_id)
            return _dedup_return(resp, 200)
        _persist_session(session)
        return _dedup_return({
            "success": True,
            "finished": False,
            "session_id": session_id,
            "round": round_result["round"],
            "round_log": round_result["round_log"],
            "player": {"hp": session["player"]["hp"], "max_hp": session["player"]["max_hp"], "mp": session["player"]["mp"], "max_mp": session["player"]["max_mp"], "attack": session["player"]["attack"], "defense": session["player"]["defense"], "name": session["player"]["name"]},
            "enemy": {"hp": session["enemy"]["hp"], "max_hp": session["enemy"]["max_hp"], "attack": session["enemy"]["attack"], "defense": session["enemy"]["defense"], "name": session["enemy"]["name"]},
            "active_skills": [
                _skill_summary(skill, max_mp=int(session["player"].get("max_mp", 0) or 0))
                for skill in session.get("active_skills", [])
            ],
            "element_relation": session.get("element_relation"),
            "combat_modifiers": session.get("combat_modifiers"),
        }, 200)
    finally:
        lock.release()


def start_secret_realm_session(
    user_id: str,
    realm_id: str,
    path: str,
    *,
    secret_cooldown_seconds: int,
    now: Optional[int] = None,
    interactive: bool = False,
) -> Tuple[Dict[str, Any], int]:
    _cleanup_expired_sessions(now)
    refresh_user_vitals(user_id)
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "message": "玩家不存在"}, 404
    now = int(time.time()) if now is None else int(now)
    refresh_user_stamina(user_id, now=now)
    user = get_user_by_id(user_id) or user
    existing = _find_active_session_for_user(user_id)
    if existing:
        return {"success": False, "message": "已有进行中的战斗", "session_id": existing.get("id"), "kind": existing.get("kind")}, 409
    user = _reset_secret_realm_attempts_if_needed(user_id, user, now)
    last_secret = int(user.get("last_secret_time", 0) or 0)
    remaining = secret_cooldown_seconds - (now - last_secret)
    if remaining > 0:
        return {"success": False, "message": f"秘境冷却中，请等待 {remaining} 秒", "cooldown_remaining": remaining}, 400
    ok, msg = can_explore_secret_realm(user, realm_id)
    if not ok:
        return {"success": False, "message": msg}, 400
    realm = get_secret_realm_by_id(realm_id)
    if not realm:
        return {"success": False, "message": "秘境不存在"}, 404
    path = (path or "normal").strip()
    if path not in ("normal", "safe", "risky", "loot"):
        path = "normal"
    encounter = roll_secret_realm_encounter(realm, path=path)
    if encounter.get("monster_id"):
        monster_check = get_monster_by_id(encounter["monster_id"])
        if not monster_check:
            return {"success": False, "message": "秘境遭遇失效，请重新进入"}, 404
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
            return {"success": False, "message": "精力不足，探索秘境需要 1 点精力", "stamina": format_stamina_value((latest or {}).get("stamina", 0))}, 400
        last_secret = int(latest.get("last_secret_time", 0) or 0)
        remaining = secret_cooldown_seconds - (now - last_secret)
        if remaining > 0:
            return {"success": False, "message": f"秘境冷却中，请等待 {remaining} 秒", "cooldown_remaining": remaining}, 400
        if get_secret_realm_attempts_left(latest) <= 0:
            return {"success": False, "message": "今日秘境次数已用尽"}, 400
        return {"success": False, "message": "操作过快，请重试"}, 409
    user = get_user_by_id(user_id) or user
    if not encounter.get("monster_id"):
        if interactive and encounter.get("type") in ("trap", "treasure_event"):
            try:
                session_id = _allocate_session_id()
            except RuntimeError:
                logger.error("failed_to_allocate_secret_event_session_id user_id=%s", user_id)
                return {"success": False, "message": "秘境会话创建失败，请稍后重试"}, 500
            combat_modifiers = {
                "element_relation": None,
                "elite_affixes": [],
                "source_kind": "secret",
                "path": path,
                "danger_scale": float(encounter.get("danger_scale", 1.0) or 1.0),
            }
            session_obj = {
                "id": session_id,
                "kind": "secret_event",
                "user_id": user_id,
                "realm": realm,
                "path": path,
                "encounter": encounter,
                "user_snapshot": user,
                "combat_modifiers": combat_modifiers,
                "started_at": now,
                "last_active_at": now,
            }
            _session_put(session_obj)
            _persist_session(session_obj)
            return {
                "success": True,
                "needs_choice": True,
                "session_id": session_id,
                "realm": realm,
                "path": path,
                "encounter": encounter,
                "choices": _secret_event_choices(encounter.get("type", "")),
                "combat_modifiers": combat_modifiers,
            }, 200
        trap_damage = 0
        if encounter.get("type") == "trap":
            max_hp = int(user.get("max_hp", 100) or 100)
            trap_damage = max(3, int(round(max_hp * 0.08)))
            raw_hp = user.get("hp")
            current_hp = max_hp if raw_hp is None else int(raw_hp)
            execute("UPDATE users SET hp = ? WHERE user_id = ?", (max(1, current_hp - trap_damage), user_id))
            user = get_user_by_id(user_id) or user
        return _finalize_secret_no_battle(user_id, user, realm, path, now, encounter=encounter, trap_damage=trap_damage), 200

    session, response, status = _build_secret_battle_session(
        user_id=user_id,
        user=user,
        realm=realm,
        path=path,
        encounter=encounter,
        now=now,
    )
    if not session:
        return response, status
    _session_put(session)
    _persist_session(session)
    return response, status


def _generate_secret_drops(user_id: str, reward_item_ids: List[str], user_rank: int) -> List[Dict[str, Any]]:
    drops: List[Dict[str, Any]] = []
    for reward_item_id in reward_item_ids or []:
        base_item = get_item_by_id(reward_item_id)
        if not base_item:
            continue
        item_type = getattr(base_item.get("type"), "value", base_item.get("type"))
        if item_type == "material":
            drop = generate_material(reward_item_id, 1)
        elif item_type == "pill":
            drop = generate_pill(reward_item_id, 1)
        else:
            drop = generate_equipment(base_item, Quality.COMMON, max(1, int(user_rank or 1) // 3))
        if drop:
            drops.append(drop)
    return drops


def _apply_realm_drop_buff(user: Dict[str, Any], mods: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    now = int(time.time())
    until = int((user or {}).get("realm_drop_boost_until", 0) or 0)
    if until <= now:
        return mods, False
    cfg = config.get_nested("balance", "pill_buffs", "realm_drop", default={}) or {}
    drop_mul = float(cfg.get("drop_mul", 1.35))
    merged = dict(mods or {})
    merged["drop_mul"] = float(merged.get("drop_mul", 1.0) or 1.0) * drop_mul
    return merged, True


def _finalize_secret_no_battle(
    user_id: str,
    user: Dict[str, Any],
    realm: Dict[str, Any],
    path: str,
    now: int,
    *,
    encounter: Optional[Dict[str, Any]] = None,
    trap_damage: int = 0,
) -> Dict[str, Any]:
    mods = apply_secret_realm_modifiers(path)
    mods, _ = _apply_realm_drop_buff(user, mods)
    encounter = encounter or {"type": "none", "label": "空境"}
    reward = roll_secret_realm_rewards(
        realm,
        victory=True,
        user_rank=int(user.get("rank", 1) or 1),
        path=path,
        encounter_type=encounter.get("type", "none"),
        **mods,
    )
    reward_mult = 1.0 + float((get_user_sect_buffs(user_id) or {}).get("battle_reward_pct", 0.0)) / 100.0
    reward["exp"] = int(round(float(reward.get("exp", 0) or 0) * reward_mult))
    reward["copper"] = int(round(float(reward.get("copper", 0) or 0) * reward_mult))
    drops = _generate_secret_drops(user_id, reward.get("drop_item_ids", []) or [], int(user.get("rank", 1) or 1))
    gold_reward = int(reward.get("gold", 0) or 0)
    loot_score = int(reward["exp"] + reward["copper"] + gold_reward * 300 + sum((d.get("quantity", 1) or 1) * 120 for d in drops))
    current_hp = max(1, int(user.get("hp", 1) or 1))
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
            "UPDATE users SET exp = exp + ?, copper = copper + ?, gold = gold + ?, hp = ?, secret_loot_score = secret_loot_score + ?, vitals_updated_at = ? WHERE user_id = ?",
            (reward["exp"], reward["copper"], gold_reward, current_hp, loot_score, int(time.time()), user_id),
        )
    for drop in drops:
        ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
    increment_quest(user_id, "daily_secret_realm")
    increment_sect_quest_progress(user_id, "secret_realm", 1)
    try:
        increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "secret", 1)
    except Exception:
        pass
    updated = get_user_by_id(user_id) or user
    combat_modifiers = {
        "element_relation": None,
        "elite_affixes": [],
        "source_kind": "secret",
        "path": path,
        "danger_scale": float(encounter.get("danger_scale", 1.0) or 1.0),
    }
    payload = {
        "success": True,
        "needs_battle": False,
        "realm": realm,
        "path": path,
        "encounter": encounter.get("label"),
        "encounter_type": encounter.get("type"),
        "victory": True,
        "battle_log": [],
        "event": reward["event"],
        "trap_damage": trap_damage,
        "rewards": {"exp": reward["exp"], "copper": reward["copper"], "gold": reward.get("gold", 0), "drops": drops},
        "attempts_left": get_secret_realm_attempts_left(updated),
        "post_status": _battle_post_status(updated, int(updated.get("hp", 1) or 1)),
        "combat_modifiers": combat_modifiers,
    }
    try:
        from core.services.events_service import grant_event_points_for_action
        grants = grant_event_points_for_action(
            user_id,
            "secret_victory",
            meta={"realm_id": realm.get("id"), "path": path, "encounter_type": encounter.get("type")},
        )
        if grants:
            payload["event_points"] = grants
    except Exception:
        pass
    return payload


def _resolve_secret_event_choice(session: Dict[str, Any], choice: Optional[str]) -> Tuple[Dict[str, Any], int]:
    user_id = session.get("user_id")
    if not user_id:
        return {"success": False, "message": "会话已失效"}, 404
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "message": "玩家不存在"}, 404
    realm = session.get("realm") or {}
    path = session.get("path", "normal")
    encounter = session.get("encounter") or {}
    event_type = encounter.get("type")
    choice = (choice or "").strip().lower()
    now = int(time.time())

    if event_type == "trap":
        if choice == "avoid":
            used = False
            with db_transaction() as cur:
                used = _deduct_item(cur, user_id, "hp_pill", 1, "pill")
            if used:
                encounter = dict(encounter)
                encounter["type"] = "safe_event"
                encounter.setdefault("label", "稳妥规避")
                return _finalize_secret_no_battle(user_id, user, realm, path, now, encounter=encounter, trap_damage=0), 200
            choice = "endure"
        if choice == "fight":
            guardian_id = (realm.get("monster_pool") or [None])[-1]
            encounter = dict(encounter)
            encounter.update({"type": "guardian", "label": "守宝怪", "monster_id": guardian_id, "danger_scale": 1.10})
            session_obj, response, status = _build_secret_battle_session(
                user_id=user_id,
                user=user,
                realm=realm,
                path=path,
                encounter=encounter,
                now=now,
                session_id=session.get("id"),
            )
            if not session_obj:
                return response, status
            _session_put(session_obj)
            _persist_session(session_obj)
            return response, status
        # endure (default)
        max_hp = int(user.get("max_hp", 100) or 100)
        trap_damage = max(3, int(round(max_hp * 0.08)))
        raw_hp = user.get("hp")
        current_hp = max_hp if raw_hp is None else int(raw_hp)
        execute("UPDATE users SET hp = ? WHERE user_id = ?", (max(1, current_hp - trap_damage), user_id))
        user = get_user_by_id(user_id) or user
        return _finalize_secret_no_battle(user_id, user, realm, path, now, encounter=encounter, trap_damage=trap_damage), 200

    if event_type == "treasure_event":
        if choice == "loot_fight":
            guardian_id = (realm.get("monster_pool") or [None])[-1]
            encounter = dict(encounter)
            encounter.update({"type": "guardian", "label": "守宝怪", "monster_id": guardian_id, "danger_scale": 1.08})
            session_obj, response, status = _build_secret_battle_session(
                user_id=user_id,
                user=user,
                realm=realm,
                path=path,
                encounter=encounter,
                now=now,
                session_id=session.get("id"),
            )
            if not session_obj:
                return response, status
            _session_put(session_obj)
            _persist_session(session_obj)
            return response, status
        if choice == "leave":
            encounter = dict(encounter)
            encounter["type"] = "safe_event"
            encounter.setdefault("label", "谨慎撤离")
            return _finalize_secret_no_battle(user_id, user, realm, path, now, encounter=encounter, trap_damage=0), 200
        # loot_safe (default)
        return _finalize_secret_no_battle(user_id, user, realm, path, now, encounter=encounter, trap_damage=0), 200

    return {"success": False, "message": "事件无法处理"}, 400


def _finalize_secret(session: Dict[str, Any], victory: bool) -> Dict[str, Any]:
    user_id = session["user_id"]
    user = session["user_snapshot"]
    realm = session["realm"]
    path = session["path"]
    player = session["player"]
    mods = apply_secret_realm_modifiers(path)
    mods, _ = _apply_realm_drop_buff(user, mods)
    encounter = session.get("encounter") or {}
    reward = roll_secret_realm_rewards(
        realm,
        victory=victory,
        user_rank=int(user.get("rank", 1) or 1),
        path=path,
        encounter_type=encounter.get("type", "monster"),
        **mods,
    )
    reward_mult = 1.0 + float((get_user_sect_buffs(user_id) or {}).get("battle_reward_pct", 0.0)) / 100.0
    reward["exp"] = int(round(float(reward.get("exp", 0) or 0) * reward_mult))
    reward["copper"] = int(round(float(reward.get("copper", 0) or 0) * reward_mult))
    total_gold = int(round(float(reward.get("gold", 0) or 0) * reward_mult))
    drops = _generate_secret_drops(user_id, reward.get("drop_item_ids", []) or [], int(user.get("rank", 1) or 1))
    drop_pity_meta = None
    if victory and path == "risky":
        targeted_drop, drop_pity_meta = roll_targeted_drop_with_pity(
            user_id=user_id,
            source_kind="realm",
            source_id=realm["id"],
            user_rank=int(user.get("rank", 1) or 1),
            boosted=True,
        )
        if targeted_drop:
            drops.append(targeted_drop)
    loot_score = int(reward["exp"] + reward["copper"] + total_gold * 300 + sum((d.get("quantity", 1) or 1) * 120 for d in drops))
    final_hp = max(1, int(player["hp"])) if victory else 1
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
            (reward["exp"], reward["copper"], total_gold, final_hp, max(0, int(player.get("mp", 0) or 0)), loot_score, int(time.time()), user_id),
        )
    for drop in drops:
        ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
    increment_quest(user_id, "daily_secret_realm")
    increment_sect_quest_progress(user_id, "secret_realm", 1)
    try:
        if victory:
            increment_realm_trial(user_id, int(user.get("rank", 1) or 1), "secret", 1)
    except Exception:
        pass
    updated = get_user_by_id(user_id) or user
    payload = {
        "success": True,
        "realm": realm,
        "path": path,
        "encounter": encounter.get("label") or session["monster"]["name"],
        "encounter_type": encounter.get("type", "monster"),
        "victory": victory,
        "battle_message": f"击败了{session['monster']['name']}" if victory else f"败给了{session['monster']['name']}",
        "failure_reasons": [] if victory else _battle_failure_reasons(session),
        "battle_log": session.get("history", []),
        "event": reward["event"],
        "rewards": {"exp": reward["exp"], "copper": reward["copper"], "gold": total_gold, "drops": drops},
        "attempts_left": get_secret_realm_attempts_left(updated),
        "post_status": _battle_post_status(updated, final_hp),
        "combat_modifiers": {
            **(session.get("combat_modifiers") or {}),
            "drop_pity": drop_pity_meta,
        },
    }
    if victory:
        try:
            from core.services.events_service import grant_event_points_for_action
            grants = grant_event_points_for_action(
                user_id,
                "secret_victory",
                meta={"realm_id": realm.get("id"), "path": path},
            )
            if grants:
                payload["event_points"] = grants
        except Exception:
            pass
    return payload


def action_secret_session(
    user_id: str,
    session_id: str,
    *,
    action: str,
    skill_id: Optional[str] = None,
    choice: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    _cleanup_expired_sessions()
    dedup_action = "secret_turn_action"
    dedup_request_id = str(request_id or "").strip()
    if dedup_request_id:
        dedup_status, cached = reserve_request(
            dedup_request_id,
            user_id=user_id,
            action=dedup_action,
            stale_after_seconds=20,
        )
        if dedup_status == "cached":
            if cached is not None:
                return cached, 200
            return {"success": False, "message": "请求结果不可用，请重试"}, 409
        if dedup_status == "in_progress":
            return {"success": False, "message": "请求处理中，请勿重复点击"}, 409

    def _dedup_return(resp: Dict[str, Any], http_status: int) -> Tuple[Dict[str, Any], int]:
        if dedup_request_id:
            save_response(dedup_request_id, user_id, dedup_action, resp)
        return resp, http_status

    lock = _get_session_lock(session_id)
    if not lock.acquire(blocking=False):
        return _dedup_return({"success": False, "message": "上一回合仍在处理，请稍后重试"}, 409)
    try:
        session = _session_get(session_id)
        if not session:
            session = _load_session(session_id)
            if session:
                _session_put(session)
        if not session or session.get("user_id") != user_id:
            return _dedup_return({"success": False, "message": "战斗已失效，请重新进入秘境"}, 404)
        if session.get("kind") == "secret_event":
            if action not in ("choice", "event"):
                return _dedup_return({"success": False, "message": "需要先选择事件处理方式"}, 400)
            resp, status = _resolve_secret_event_choice(session, choice)
            if resp.get("needs_battle"):
                return _dedup_return(resp, status)
            _session_pop(session_id)
            _delete_session(session_id)
            return _dedup_return(resp, status)
        if session.get("kind") != "secret":
            return _dedup_return({"success": False, "message": "战斗已失效，请重新进入秘境"}, 404)
        round_result = _run_round(session, action, skill_id)
        if round_result["finished"]:
            resp = _finalize_secret(session, bool(round_result["victory"]))
            resp["round_log"] = round_result["round_log"]
            resp["session_id"] = session_id
            _session_pop(session_id)
            _delete_session(session_id)
            return _dedup_return(resp, 200)
        _persist_session(session)
        return _dedup_return({
            "success": True,
            "finished": False,
            "session_id": session_id,
            "round": round_result["round"],
            "round_log": round_result["round_log"],
            "realm": session["realm"],
            "path": session["path"],
            "encounter": session.get("encounter"),
            "monster": session["monster"],
            "player": {"hp": session["player"]["hp"], "max_hp": session["player"]["max_hp"], "mp": session["player"]["mp"], "max_mp": session["player"]["max_mp"], "attack": session["player"]["attack"], "defense": session["player"]["defense"], "name": session["player"]["name"]},
            "enemy": {"hp": session["enemy"]["hp"], "max_hp": session["enemy"]["max_hp"], "attack": session["enemy"]["attack"], "defense": session["enemy"]["defense"], "name": session["enemy"]["name"]},
            "active_skills": [
                _skill_summary(skill, max_mp=int(session["player"].get("max_mp", 0) or 0))
                for skill in session.get("active_skills", [])
            ],
            "element_relation": session.get("element_relation"),
            "combat_modifiers": session.get("combat_modifiers"),
        }, 200)
    finally:
        lock.release()
