"""Shared combat math kernel used by auto and turn battles."""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from core.config import config


def _kernel_cfg_float(key: str, default: float) -> float:
    try:
        return float(config.get_nested("battle", "kernel", key, default=default))
    except (TypeError, ValueError):
        return float(default)


LOW_HP_SHIELD_THRESHOLD = _kernel_cfg_float("low_hp_shield_threshold", 0.3)
DAMAGE_VARIANCE_MIN = _kernel_cfg_float("damage_variance_min", 0.85)
DAMAGE_VARIANCE_MAX = _kernel_cfg_float("damage_variance_max", 1.15)
DEF_IGNORE_CAP = _kernel_cfg_float("ignore_def_cap", 0.95)
DEFAULT_CRIT_RATE = _kernel_cfg_float("default_crit_rate", 0.05)
DEFAULT_CRIT_DMG = _kernel_cfg_float("default_crit_dmg", 1.5)


def calc_base_damage(
    *,
    attack: int,
    defense: int,
    ignore_def_pct: float = 0.0,
    variance_roll: Optional[float] = None,
) -> Tuple[int, float]:
    """ATK^2 / (ATK + DEF*0.8), with optional armor-penetration and variance."""
    atk_val = max(1, int(attack or 1))
    def_val = max(0, int(defense or 0))
    ig = float(ignore_def_pct or 0.0)
    if ig > 0:
        def_val = int(def_val * max(0.0, 1.0 - min(max(0.0, DEF_IGNORE_CAP), ig)))
    effective_def = def_val * 0.8
    base = max(1, int(atk_val * atk_val / max(1, atk_val + effective_def)))
    var_min = min(DAMAGE_VARIANCE_MIN, DAMAGE_VARIANCE_MAX)
    var_max = max(DAMAGE_VARIANCE_MIN, DAMAGE_VARIANCE_MAX)
    roll = random.uniform(var_min, var_max) if variance_roll is None else float(variance_roll)
    return max(1, int(base * roll)), roll


def apply_counter_bonus(attacker: Dict[str, Any], damage: int, logs: Optional[List[str]] = None) -> int:
    counter_bonus = float(attacker.pop("_counter_bonus", 0.0) or 0.0)
    if counter_bonus > 0:
        damage = int(damage * (1.0 + counter_bonus))
        if logs is not None:
            logs.append("🔁 反击强化")
    return damage


def apply_critical(
    attacker: Dict[str, Any],
    damage: int,
    *,
    extra_crit_rate: float = 0.0,
    crit_roll: Optional[float] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[int, bool, float]:
    rate = max(0.0, float(attacker.get("crit_rate", DEFAULT_CRIT_RATE) or DEFAULT_CRIT_RATE) + float(extra_crit_rate or 0.0))
    roll = random.random() if crit_roll is None else float(crit_roll)
    crit = roll < rate
    if crit:
        crit_dmg = max(1.1, float(attacker.get("crit_dmg", DEFAULT_CRIT_DMG) or DEFAULT_CRIT_DMG))
        damage = int(damage * crit_dmg)
        if logs is not None:
            logs.append("💥 暴击！")
    return max(1, int(damage)), crit, roll


def apply_defensive_affixes(target: Dict[str, Any], damage: int, round_num: int, logs: Optional[List[str]] = None) -> int:
    if damage <= 0:
        return damage
    fr_pct = float(target.get("first_round_reduction_pct", 0.0) or 0.0)
    if round_num == 1 and fr_pct > 0:
        reduced = int(damage * fr_pct)
        if reduced > 0:
            damage = max(0, damage - reduced)
            if logs is not None:
                logs.append(f"🛡️ 首回合减伤 -{reduced}")

    max_hp = int(target.get("max_hp", 0) or 0)
    if max_hp <= 0:
        return damage
    fh_pct = float(target.get("first_hit_shield_pct", 0.0) or 0.0)
    if fh_pct > 0 and not target.get("_first_hit_shield_used"):
        shield = int(max_hp * fh_pct)
        if shield > 0:
            target["_first_hit_shield_used"] = True
            target["_shield"] = int(target.get("_shield", 0) or 0) + shield
            if logs is not None:
                logs.append(f"🛡️ 首击护盾 +{shield}")
    shield_pct = float(target.get("low_hp_shield_pct", 0.0) or 0.0)
    threshold = float(target.get("low_hp_shield_threshold", LOW_HP_SHIELD_THRESHOLD) or LOW_HP_SHIELD_THRESHOLD)
    current_hp = int(target.get("hp", 0) or 0)
    if not target.get("_low_hp_shield_used") and (current_hp <= int(max_hp * threshold) or current_hp - damage <= int(max_hp * threshold)):
        shield = int(max_hp * shield_pct)
        if shield > 0:
            target["_low_hp_shield_used"] = True
            target["_shield"] = int(target.get("_shield", 0) or 0) + shield
            if logs is not None:
                logs.append(f"🔰 残血护盾 +{shield}")
    shield_val = int(target.get("_shield", 0) or 0)
    if shield_val > 0 and damage > 0:
        absorbed = min(shield_val, damage)
        if absorbed > 0:
            target["_shield"] = shield_val - absorbed
            damage -= absorbed
            if logs is not None:
                logs.append(f"🛡️ 护盾吸收 {absorbed}")
    return damage


def apply_poison_thorns(attacker: Dict[str, Any], defender: Dict[str, Any], damage: int, logs: Optional[List[str]] = None) -> None:
    if damage <= 0:
        return
    poison_pct = float(attacker.get("poison_pct", 0.0) or 0.0)
    if poison_pct > 0:
        poison_dmg = int(damage * poison_pct)
        if poison_dmg > 0:
            defender["hp"] = max(0, int(defender.get("hp", 0) or 0) - poison_dmg)
            if logs is not None:
                logs.append(f"☠️ 剧毒造成 {poison_dmg} 真实伤害")
    thorns_pct = float(defender.get("thorns_pct", 0.0) or 0.0)
    if thorns_pct > 0:
        thorns_dmg = int(damage * thorns_pct)
        if thorns_dmg > 0:
            attacker["hp"] = max(0, int(attacker.get("hp", 0) or 0) - thorns_dmg)
            if logs is not None:
                logs.append(f"🔁 反震造成 {thorns_dmg} 伤害")


def maybe_apply_enrage(target: Dict[str, Any], logs: Optional[List[str]] = None) -> None:
    if target.get("_enraged"):
        return
    threshold = float(target.get("enrage_threshold", 0.0) or 0.0)
    if threshold <= 0:
        return
    max_hp = int(target.get("max_hp", 0) or 0)
    if max_hp <= 0:
        return
    if int(target.get("hp", 0) or 0) <= int(max_hp * threshold):
        mul = float(target.get("enrage_damage_mul", 1.0) or 1.0)
        target["damage_mul"] = float(target.get("damage_mul", 1.0) or 1.0) * mul
        target["_enraged"] = True
        if logs is not None:
            logs.append(f"🔥 {target.get('name', '敌人')}进入狂暴！")
