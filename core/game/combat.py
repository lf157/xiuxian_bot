"""
战斗系统 - Combat System
"""

from typing import Dict, Any, Optional, List, Tuple
import logging
import random
import time
from .realms import calculate_user_stats, ELEMENT_BONUSES
from .elements import get_element_relationship
from .skills import compute_skill_mp_cost, get_skill, scale_skill_effect
from .combat_kernel import (
    LOW_HP_SHIELD_THRESHOLD,
    apply_counter_bonus,
    apply_critical,
    apply_defensive_affixes,
    apply_poison_thorns,
    calc_base_damage,
    maybe_apply_enrage,
)
from core.database.connection import get_item_by_db_id
from core.game.elite_affixes import roll_elite_affixes, apply_elite_affixes

logger = logging.getLogger("core.combat")
AFFIX_CAPS = {
    "first_round_reduction_pct": 0.6,
    "crit_heal_pct": 0.7,
    "element_damage_pct": 0.6,
    "low_hp_shield_pct": 0.7,
}


MONSTERS = [
    {"id": "wild_boar", "name": "野猪", "element": "土", "hp": 50, "attack": 8, "defense": 3, "exp_reward": 20, "copper_reward": (5, 15), "min_rank": 1},
    {"id": "wolf", "name": "灰狼", "element": "木", "hp": 80, "attack": 12, "defense": 5, "exp_reward": 35, "copper_reward": (10, 25), "min_rank": 1},
    {"id": "giant_snake", "name": "巨蛇", "element": "水", "hp": 120, "attack": 18, "defense": 8, "exp_reward": 60, "copper_reward": (20, 40), "min_rank": 2},
    {"id": "spirit_rat", "name": "灵鼠", "element": "金", "hp": 60, "attack": 15, "defense": 2, "exp_reward": 50, "copper_reward": (15, 30), "min_rank": 2},
    {"id": "stone_golem", "name": "石魔", "element": "土", "hp": 300, "attack": 30, "defense": 25, "exp_reward": 150, "copper_reward": (50, 100), "min_rank": 5},
    {"id": "fire_sprite", "name": "火精", "element": "火", "hp": 200, "attack": 45, "defense": 10, "exp_reward": 200, "copper_reward": (80, 150), "min_rank": 6},
    {"id": "ice_demon", "name": "冰妖", "element": "水", "hp": 250, "attack": 40, "defense": 15, "exp_reward": 180, "copper_reward": (70, 130), "min_rank": 6},
    {"id": "thunder_beast", "name": "雷兽", "element": "金", "hp": 350, "attack": 50, "defense": 20, "exp_reward": 250, "copper_reward": (100, 200), "min_rank": 8},
    {"id": "blood_dragon", "name": "血龙", "element": "火", "hp": 800, "attack": 80, "defense": 40, "exp_reward": 500, "copper_reward": (200, 400), "min_rank": 10},
    {"id": "phantom_spirit", "name": "幽魂", "element": "水", "hp": 500, "attack": 100, "defense": 20, "exp_reward": 600, "copper_reward": (300, 500), "min_rank": 12},
    {"id": "golden_lion", "name": "金毛狮王", "element": "金", "hp": 1200, "attack": 120, "defense": 60, "exp_reward": 800, "copper_reward": (400, 700), "min_rank": 14},
    {"id": "ancient_demon", "name": "上古魔尊", "element": "土", "hp": 5000, "attack": 300, "defense": 150, "exp_reward": 3000, "copper_reward": (1000, 2000), "min_rank": 18},
    {"id": "heavenly_dragon", "name": "天龙", "element": "木", "hp": 10000, "attack": 500, "defense": 250, "exp_reward": 8000, "copper_reward": (3000, 5000), "min_rank": 22},
    # === 新增高等级怪物 (Rank 24-32) ===
    {"id": "void_phantom", "name": "虚空幻灵", "element": "水", "hp": 18000, "attack": 850, "defense": 400, "exp_reward": 15000, "copper_reward": (5000, 8000), "min_rank": 24},
    {"id": "infernal_lord", "name": "炼狱魔君", "element": "火", "hp": 25000, "attack": 1200, "defense": 550, "exp_reward": 25000, "copper_reward": (8000, 12000), "min_rank": 26},
    {"id": "celestial_tiger", "name": "天玄白虎", "element": "金", "hp": 35000, "attack": 1600, "defense": 750, "exp_reward": 40000, "copper_reward": (12000, 18000), "min_rank": 27},
    {"id": "primordial_tortoise", "name": "太古玄龟", "element": "土", "hp": 60000, "attack": 1200, "defense": 1200, "exp_reward": 50000, "copper_reward": (15000, 22000), "min_rank": 28},
    {"id": "divine_phoenix", "name": "九天凤凰", "element": "火", "hp": 45000, "attack": 2200, "defense": 900, "exp_reward": 65000, "copper_reward": (20000, 30000), "min_rank": 29},
    {"id": "chaos_dragon", "name": "混沌神龙", "element": "木", "hp": 80000, "attack": 3000, "defense": 1400, "exp_reward": 100000, "copper_reward": (30000, 50000), "min_rank": 30},
    {"id": "immortal_sovereign", "name": "不朽帝君", "element": "金", "hp": 120000, "attack": 5000, "defense": 2200, "exp_reward": 180000, "copper_reward": (50000, 80000), "min_rank": 31},
    {"id": "heavenly_dao_beast", "name": "天道圣兽", "element": "土", "hp": 200000, "attack": 8000, "defense": 3500, "exp_reward": 300000, "copper_reward": (80000, 120000), "min_rank": 32},
]


class Combat:
    def __init__(self, attacker: Dict[str, Any], defender: Dict[str, Any]):
        self.attacker = attacker
        self.defender = defender
        self.log: List[str] = []
        self.active_skill = attacker.get("active_skill")
        self.last_skill_round = 0
        self.skill_uses = 0

    def _restore_temp_mods(self, combatant: Dict[str, Any]) -> None:
        temp_mods = combatant.pop("_temp_skill_mods", None)
        if not temp_mods:
            return
        for key, (existed, value) in temp_mods.items():
            if existed:
                combatant[key] = value
            else:
                combatant.pop(key, None)

    def _maybe_apply_enrage(self, combatant: Dict[str, Any]) -> None:
        maybe_apply_enrage(combatant, self.log)

    def calculate_damage(self, attacker: Dict, defender: Dict, *, round_num: int) -> int:
        skill_multiplier = 1.0
        if attacker is self.attacker and self.active_skill and self.last_skill_round != round_num:
            eff = self.active_skill.get("effect", {}) or {}
            max_mp_for_cost = int(attacker.get("max_mp", attacker.get("mp", 0)) or 0)
            mp_cost = compute_skill_mp_cost(self.active_skill, max_mp_for_cost)
            if int(attacker.get("mp", 0) or 0) < mp_cost:
                self.log.append("💙 灵力不足，主动技能未能施展。")
            else:
                if mp_cost > 0:
                    attacker["mp"] = max(0, int(attacker.get("mp", 0) or 0) - mp_cost)
                    self.log.append(f"💙 消耗 {mp_cost} MP")
                # temporary per-skill effects for this strike/round
                temp_mods = attacker.setdefault("_temp_skill_mods", {})

                def _store(key: str) -> None:
                    if key not in temp_mods:
                        temp_mods[key] = (key in attacker, attacker.get(key))

                if eff.get("ignore_def_pct"):
                    _store("ignore_def_pct")
                    attacker["ignore_def_pct"] = float(eff.get("ignore_def_pct"))
                if eff.get("lifesteal_bonus"):
                    _store("lifesteal")
                    attacker["lifesteal"] = float(attacker.get("lifesteal", 0.0) or 0.0) + float(eff.get("lifesteal_bonus"))
                if eff.get("crit_rate_bonus"):
                    _store("crit_rate")
                    attacker["crit_rate"] = float(attacker.get("crit_rate", 0.05) or 0.05) + float(eff.get("crit_rate_bonus"))
                if eff.get("damage_taken_mul"):
                    _store("damage_taken_mul")
                    attacker["damage_taken_mul"] = float(attacker.get("damage_taken_mul", 1.0) or 1.0) * float(eff.get("damage_taken_mul"))
                multiplier = float(eff.get("attack_multiplier", 1.0) or 1.0)
                sd = float(attacker.get("skill_damage", 0.0) or 0.0)
                skill_multiplier = multiplier * (1.0 + max(0.0, sd))
                if skill_multiplier > 1.0:
                    self.log.append(f"✨ 施展技能【{self.active_skill['name']}】！")
                max_hp = int(attacker.get("max_hp", attacker.get("hp", 0)) or 0)
                max_mp = int(attacker.get("max_mp", attacker.get("mp", 0)) or 0)
                if eff.get("self_shield_pct"):
                    shield = int(max_hp * float(eff.get("self_shield_pct")))
                    if shield > 0:
                        attacker["_shield"] = int(attacker.get("_shield", 0) or 0) + shield
                        self.log.append(f"🔰 护盾 +{shield}")
                if eff.get("restore_hp_pct"):
                    heal = int(max_hp * float(eff.get("restore_hp_pct")))
                    if heal > 0:
                        attacker["hp"] = min(max_hp, int(attacker.get("hp", 0) or 0) + heal)
                        self.log.append(f"✨ 回复生命 {heal}")
                if eff.get("restore_mp_pct"):
                    mp_gain = int(max_mp * float(eff.get("restore_mp_pct")))
                    if mp_gain > 0:
                        attacker["mp"] = min(max_mp, int(attacker.get("mp", 0) or 0) + mp_gain)
                        self.log.append(f"💠 回复灵力 {mp_gain}")
                if eff.get("convert_hp_to_mp_pct"):
                    hp_cost = int(max_hp * float(eff.get("convert_hp_to_mp_pct")))
                    mp_gain = int(max_mp * float(eff.get("convert_hp_to_mp_pct")))
                    if hp_cost > 0:
                        attacker["hp"] = max(1, int(attacker.get("hp", 0) or 0) - hp_cost)
                        attacker["mp"] = min(max_mp, int(attacker.get("mp", 0) or 0) + mp_gain)
                        self.log.append(f"💠 转换灵力 {mp_gain}（消耗生命 {hp_cost}）")
                if eff.get("self_damage_pct"):
                    recoil = int(max_hp * float(eff.get("self_damage_pct")))
                    if recoil > 0:
                        attacker["hp"] = max(1, int(attacker.get("hp", 0) or 0) - recoil)
                        self.log.append(f"⚠️ 反噬伤害 {recoil}")
                self.skill_uses += 1
                self.last_skill_round = round_num

        ignore_def = float(attacker.get("ignore_def_pct", 0.0) or 0.0)
        damage, _ = calc_base_damage(
            attack=int(attacker.get("attack", 10) or 10),
            defense=int(defender.get("defense", 0) or 0),
            ignore_def_pct=ignore_def,
        )
        damage = int(damage * float(attacker.get("damage_mul", 1.0) or 1.0))
        damage = apply_counter_bonus(attacker, damage, self.log)
        if skill_multiplier != 1.0:
            damage = int(damage * skill_multiplier)

        damage, crit, _ = apply_critical(attacker, damage, logs=self.log)
        attacker["_last_hit_crit"] = bool(crit)

        # clear one-strike modifiers
        if "ignore_def_pct" in attacker:
            attacker.pop("ignore_def_pct", None)
        return max(1, damage)

    def fight(self, max_rounds: int = 50) -> Dict[str, Any]:
        round_num = 0
        while round_num < max_rounds:
            round_num += 1
            damage = self.calculate_damage(self.attacker, self.defender, round_num=round_num)
            # defender mitigation
            dtm = float(self.defender.get("damage_taken_mul", 1.0) or 1.0)
            if dtm != 1.0:
                damage = int(damage * dtm)
            damage = apply_defensive_affixes(self.defender, damage, round_num, self.log)
            self.defender["hp"] -= damage
            # lifesteal
            ls = float(self.attacker.get("lifesteal", 0.0) or 0.0)
            heal = 0
            ls_heal = 0
            if ls > 0 and damage > 0:
                ls_heal = int(damage * ls)
                heal += ls_heal
            if self.attacker.get("_last_hit_crit") and float(self.attacker.get("crit_heal_pct", 0.0) or 0.0) > 0:
                extra_heal = int(damage * float(self.attacker.get("crit_heal_pct", 0.0) or 0.0))
                if extra_heal > 0:
                    heal += extra_heal
                    self.log.append(f"✨ 暴击回血 {extra_heal}")
            if heal > 0:
                self.attacker["hp"] = min(self.attacker.get("max_hp", self.attacker["hp"]), self.attacker["hp"] + heal)
                if ls_heal > 0:
                    self.log.append(f"🩸 吸血回复 {ls_heal}")
            apply_poison_thorns(self.attacker, self.defender, damage, self.log)
            if damage > 0 and float(self.defender.get("counter_bonus_pct", 0.0) or 0.0) > 0 and self.defender.get("hp", 0) > 0:
                self.defender["_counter_bonus"] = max(float(self.defender.get("_counter_bonus", 0.0) or 0.0), float(self.defender.get("counter_bonus_pct", 0.0) or 0.0))
            self._maybe_apply_enrage(self.defender)
            if self.defender["hp"] <= 0:
                self._restore_temp_mods(self.attacker)
                return {
                    "winner": "attacker",
                    "rounds": round_num,
                    "log": self.log,
                    "remaining_hp": max(0, self.attacker["hp"]),
                    "attacker_remaining_hp": max(0, self.attacker["hp"]),
                    "attacker_remaining_mp": max(0, int(self.attacker.get("mp", 0) or 0)),
                    "defender_remaining_hp": max(0, self.defender["hp"]),
                    "skill_uses": self.skill_uses,
                    "active_skill_id": self.active_skill.get("id") if self.active_skill else None,
                }
            damage = self.calculate_damage(self.defender, self.attacker, round_num=round_num)
            dtm2 = float(self.attacker.get("damage_taken_mul", 1.0) or 1.0)
            if dtm2 != 1.0:
                damage = int(damage * dtm2)
            damage = apply_defensive_affixes(self.attacker, damage, round_num, self.log)
            self.attacker["hp"] -= damage
            apply_poison_thorns(self.defender, self.attacker, damage, self.log)
            if damage > 0 and float(self.attacker.get("counter_bonus_pct", 0.0) or 0.0) > 0 and self.attacker.get("hp", 0) > 0:
                self.attacker["_counter_bonus"] = max(float(self.attacker.get("_counter_bonus", 0.0) or 0.0), float(self.attacker.get("counter_bonus_pct", 0.0) or 0.0))
            self._maybe_apply_enrage(self.attacker)
            if self.attacker["hp"] <= 0:
                self._restore_temp_mods(self.attacker)
                return {
                    "winner": "defender",
                    "rounds": round_num,
                    "log": self.log,
                    "remaining_hp": max(0, self.defender["hp"]),
                    "attacker_remaining_hp": max(0, self.attacker["hp"]),
                    "attacker_remaining_mp": max(0, int(self.attacker.get("mp", 0) or 0)),
                    "defender_remaining_hp": max(0, self.defender["hp"]),
                    "skill_uses": self.skill_uses,
                    "active_skill_id": self.active_skill.get("id") if self.active_skill else None,
                }
            self._restore_temp_mods(self.attacker)
        self._restore_temp_mods(self.attacker)
        return {
            "winner": "draw",
            "rounds": max_rounds,
            "log": self.log,
            "attacker_remaining_hp": max(0, self.attacker["hp"]),
            "attacker_remaining_mp": max(0, int(self.attacker.get("mp", 0) or 0)),
            "defender_remaining_hp": max(0, self.defender["hp"]),
            "skill_uses": self.skill_uses,
            "active_skill_id": self.active_skill.get("id") if self.active_skill else None,
        }

    def _apply_first_round_reduction(self, target: Dict[str, Any], damage: int, round_num: int) -> int:
        pct = float(target.get("first_round_reduction_pct", 0.0) or 0.0)
        if round_num == 1 and pct > 0 and damage > 0:
            reduced = int(damage * pct)
            if reduced > 0:
                self.log.append(f"🛡️ 首回合减伤 -{reduced}")
                damage = max(0, damage - reduced)
        return damage

    def _apply_low_hp_shield(self, target: Dict[str, Any], damage: int) -> int:
        pct = float(target.get("low_hp_shield_pct", 0.0) or 0.0)
        if pct <= 0 or damage <= 0:
            pct = 0.0
        max_hp = int(target.get("max_hp", 0) or 0)
        if max_hp <= 0:
            return damage
        fh_pct = float(target.get("first_hit_shield_pct", 0.0) or 0.0)
        if fh_pct > 0 and not target.get("_first_hit_shield_used"):
            shield = int(max_hp * fh_pct)
            if shield > 0:
                target["_first_hit_shield_used"] = True
                target["_shield"] = int(target.get("_shield", 0) or 0) + shield
                self.log.append(f"🛡️ 首击护盾 +{shield}")
        threshold = float(target.get("low_hp_shield_threshold", LOW_HP_SHIELD_THRESHOLD) or LOW_HP_SHIELD_THRESHOLD)
        current_hp = int(target.get("hp", 0) or 0)
        if not target.get("_low_hp_shield_used") and (current_hp <= int(max_hp * threshold) or current_hp - damage <= int(max_hp * threshold)):
            shield = int(max_hp * pct)
            if shield > 0:
                target["_low_hp_shield_used"] = True
                target["_shield"] = int(target.get("_shield", 0) or 0) + shield
                self.log.append(f"🔰 残血护盾 +{shield}")
        shield_val = int(target.get("_shield", 0) or 0)
        if shield_val > 0 and damage > 0:
            absorbed = min(shield_val, damage)
            if absorbed > 0:
                target["_shield"] = shield_val - absorbed
                damage -= absorbed
                self.log.append(f"🛡️ 护盾吸收 {absorbed}")
        return damage


def get_available_monsters(user_rank: int) -> List[Dict[str, Any]]:
    unlocked = [m for m in MONSTERS if m["min_rank"] <= user_rank]
    if len(unlocked) <= 4:
        return unlocked
    # Keep only the latest unlocked monsters to avoid flooding hunt panels.
    return unlocked[-4:]


def get_monster_by_id(monster_id: str) -> Optional[Dict[str, Any]]:
    for m in MONSTERS:
        if m["id"] == monster_id:
            return m.copy()
    return None


def create_combatant_from_user(
    user_data: Dict[str, Any],
    learned_skills: Optional[List[Dict[str, Any]]] = None,
    *,
    selected_active_skill_id: Optional[str] = None,
) -> Dict[str, Any]:
    precomputed_stats = bool(user_data.get("_combat_stats_precomputed", False))
    stats = calculate_user_stats(user_data)
    attack = int((user_data.get("attack") if precomputed_stats else stats["attack"]) or stats["attack"])
    defense = int((user_data.get("defense") if precomputed_stats else stats["defense"]) or stats["defense"])
    max_hp = int((user_data.get("max_hp") if precomputed_stats else stats["max_hp"]) or stats["max_hp"])
    max_mp = int((user_data.get("max_mp") if precomputed_stats else stats["max_mp"]) or stats["max_mp"])
    crit_rate = user_data.get("crit_rate", 0.05)
    active_skill = None
    best_active_skill = None

    learned_skills = learned_skills or []
    crit_rate_bonus = 0.0
    crit_dmg_bonus = 0.0
    damage_mul = 1.0
    damage_taken_mul = 1.0
    lifesteal = 0.0
    skill_damage_bonus = 0.0
    mp_bonus_pct = 0.0
    affix_totals = {
        "first_round_reduction_pct": 0.0,
        "crit_heal_pct": 0.0,
        "element_damage_pct": 0.0,
        "low_hp_shield_pct": 0.0,
    }
    for slot in ("equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2"):
        db_id = user_data.get(slot)
        if not db_id:
            continue
        item = get_item_by_db_id(db_id)
        if not item:
            continue
        for key in affix_totals:
            affix_totals[key] += float(item.get(key, 0.0) or 0.0)
    for key, cap in AFFIX_CAPS.items():
        if affix_totals.get(key, 0.0) > cap:
            affix_totals[key] = cap
    for row in learned_skills:
        skill = get_skill(row.get("skill_id"))
        if not skill:
            continue
        level = int(row.get("skill_level", 1) or 1)
        skill = scale_skill_effect(skill, level)
        effect = skill.get("effect", {})
        if effect.get("defense_bonus_pct"):
            defense = int(defense * (1 + effect["defense_bonus_pct"]))
        if effect.get("hp_bonus_pct"):
            max_hp = int(max_hp * (1 + effect["hp_bonus_pct"]))
        if effect.get("mp_bonus_pct"):
            mp_bonus_pct += float(effect.get("mp_bonus_pct"))
        if row.get("equipped") and skill.get("type") == "active":
            if selected_active_skill_id and skill.get("id") == selected_active_skill_id:
                active_skill = skill
            multiplier = float((effect or {}).get("attack_multiplier", 1.0) or 1.0)
            if best_active_skill is None or multiplier > float((best_active_skill.get("effect", {}) or {}).get("attack_multiplier", 1.0) or 1.0):
                best_active_skill = skill

        if effect.get("crit_rate_bonus"):
            crit_rate_bonus += float(effect.get("crit_rate_bonus"))
        if effect.get("crit_dmg_bonus"):
            crit_dmg_bonus += float(effect.get("crit_dmg_bonus"))
        if effect.get("damage_mul"):
            damage_mul *= float(effect.get("damage_mul"))
        if effect.get("damage_taken_mul"):
            damage_taken_mul *= float(effect.get("damage_taken_mul"))
        if effect.get("lifesteal"):
            lifesteal += float(effect.get("lifesteal"))
        if effect.get("skill_damage"):
            skill_damage_bonus += float(effect.get("skill_damage"))

    now = int(time.time())
    if not precomputed_stats:
        atk_until = int(user_data.get("attack_buff_until", 0) or 0)
        atk_val = int(user_data.get("attack_buff_value", 0) or 0)
        if atk_val > 0 and atk_until > now:
            attack += atk_val
        def_until = int(user_data.get("defense_buff_until", 0) or 0)
        def_val = int(user_data.get("defense_buff_value", 0) or 0)
        if def_val > 0 and def_until > now:
            defense += def_val

    weak_until = int(user_data.get("weak_until", 0) or 0)
    if weak_until > now and not precomputed_stats:
        weak_mult = 0.7
        max_hp = max(1, int(round(max_hp * weak_mult)))
        max_mp = max(1, int(round(max_mp * weak_mult)))
        attack = max(1, int(round(attack * weak_mult)))
        defense = max(0, int(round(defense * weak_mult)))
        crit_rate = max(0.0, float(crit_rate) * weak_mult)

    if active_skill is None:
        active_skill = best_active_skill
    if mp_bonus_pct:
        max_mp = int(max_mp * (1 + mp_bonus_pct))
    current_mp = min(max_mp, int(user_data.get("mp", max_mp) or max_mp))

    element = user_data.get("element")
    if element and affix_totals.get("element_damage_pct", 0.0) > 0:
        damage_mul *= 1.0 + float(affix_totals.get("element_damage_pct", 0.0))

    combatant = {
        "name": user_data.get("in_game_username", "未知"),
        "hp": max_hp,
        "max_hp": max_hp,
        "mp": current_mp,
        "max_mp": max_mp,
        "attack": attack,
        "defense": defense,
        "crit_rate": crit_rate + crit_rate_bonus,
        "crit_dmg": 1.5 + crit_dmg_bonus,
        "damage_mul": damage_mul,
        "damage_taken_mul": damage_taken_mul,
        "lifesteal": lifesteal,
        "skill_damage": skill_damage_bonus,
        "active_skill": active_skill,
        "first_round_reduction_pct": affix_totals.get("first_round_reduction_pct", 0.0),
        "crit_heal_pct": affix_totals.get("crit_heal_pct", 0.0),
        "element_damage_pct": affix_totals.get("element_damage_pct", 0.0),
        "low_hp_shield_pct": affix_totals.get("low_hp_shield_pct", 0.0),
        "low_hp_shield_threshold": LOW_HP_SHIELD_THRESHOLD,
    }

    if element and element in ELEMENT_BONUSES:
        bonus = ELEMENT_BONUSES[element]
        combatant["crit_rate"] = max(combatant.get("crit_rate", 0.05), bonus.get("crit_rate", 0.05))
        combatant["element"] = element

    return combatant


def create_combatant_from_monster(monster: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": monster["name"],
        "hp": monster["hp"],
        "max_hp": monster["hp"],
        "attack": monster["attack"],
        "defense": monster["defense"],
        "monster_id": monster["id"],
        "crit_rate": 0.03,
    }


def hunt_monster(
    user_data: Dict[str, Any],
    monster_id: str,
    learned_skills: Optional[List[Dict[str, Any]]] = None,
    *,
    use_active: bool = True,
    active_skill_id: Optional[str] = None,
    source_kind: str = "hunt",
    ignore_min_rank: bool = False,
    monster_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base_monster = get_monster_by_id(monster_id)
    if not base_monster:
        return {"success": False, "message": "未知的怪物"}
    user_rank = user_data.get("rank", 1)
    if not ignore_min_rank and base_monster["min_rank"] > user_rank:
        return {"success": False, "message": f"需要达到 {base_monster['min_rank']} 级才能挑战此怪物"}

    user_combatant = create_combatant_from_user(
        user_data,
        learned_skills,
        selected_active_skill_id=active_skill_id,
    )
    try:
        base_max_hp = int(user_data.get("max_hp", user_combatant.get("max_hp", 1)) or user_combatant.get("max_hp", 1))
        current_hp = int(user_data.get("hp", base_max_hp) or base_max_hp)
        ratio = float(current_hp) / max(1, base_max_hp)
        scaled_hp = int(round(int(user_combatant.get("max_hp", base_max_hp) or base_max_hp) * ratio))
        user_combatant["hp"] = max(1, min(int(user_combatant.get("max_hp", base_max_hp) or base_max_hp), scaled_hp))
    except Exception:
        pass
    if not use_active:
        user_combatant["active_skill"] = None
    combat_monster = dict(monster_override or base_monster)
    combat_monster.setdefault("id", base_monster.get("id"))
    combat_monster.setdefault("name", base_monster.get("name"))
    combat_monster.setdefault("element", base_monster.get("element"))
    monster_combatant = create_combatant_from_monster(combat_monster)
    elite_keys, elite_names = roll_elite_affixes(user_rank=int(user_rank or 1), source_kind=source_kind)
    apply_elite_affixes(monster_combatant, elite_keys)

    # Five-elements interaction (克制/相生) affects both sides.
    try:
        ue = user_data.get("element")
        me = combat_monster.get("element")
        if ue and me:
            rel = get_element_relationship(ue, me)
            if rel == "restrained":
                user_combatant["damage_mul"] = float(user_combatant.get("damage_mul", 1.0) or 1.0) * 0.75   # 被克 -25%
                monster_combatant["damage_mul"] = float(monster_combatant.get("damage_mul", 1.0) or 1.0) * 1.25  # 敌方 +25%
            elif rel == "mutual":
                user_combatant["damage_mul"] = float(user_combatant.get("damage_mul", 1.0) or 1.0) * 1.25   # 相生 +25%
                monster_combatant["damage_mul"] = float(monster_combatant.get("damage_mul", 1.0) or 1.0) * 0.75  # 敌方 -25%
    except Exception as exc:
        logger.warning(
            "element_relation_apply_failed user_id=%s monster_id=%s error=%s",
            user_data.get("user_id"),
            monster_id,
            type(exc).__name__,
        )
    combat = Combat(user_combatant, monster_combatant)
    result = combat.fight()
    if elite_names:
        result["log"] = [f"👹 精英词缀: {'、'.join(elite_names)}"] + (result.get("log") or [])
    rewards = {"exp": 0, "copper": 0, "gold": 0}

    if result["winner"] == "attacker":
        rewards["exp"] = base_monster["exp_reward"]
        rewards["copper"] = random.randint(*base_monster["copper_reward"])
        if random.random() < 0.01:
            # gold drop only after mid game
            if int(user_data.get("rank", 1) or 1) >= 12:
                rewards["gold"] = random.randint(1, 2)
        return {
            "success": True,
            "victory": True,
            "message": f"成功击败【{combat_monster['name']}】！",
            "monster": combat_monster,
            "elite_affixes": elite_names,
            "rewards": rewards,
            "rounds": result["rounds"],
            "remaining_hp": result.get("remaining_hp", 0),
            "attacker_remaining_hp": result.get("attacker_remaining_hp", 0),
            "attacker_remaining_mp": result.get("attacker_remaining_mp", 0),
            "defender_remaining_hp": result.get("defender_remaining_hp", 0),
            "log": result["log"],
        }

    return {
        "success": True,
        "victory": False,
        "message": f"被【{combat_monster['name']}】击败了...",
        "monster": combat_monster,
        "elite_affixes": elite_names,
        "rewards": rewards,
        "rounds": result["rounds"],
        "attacker_remaining_hp": result.get("attacker_remaining_hp", 0),
        "attacker_remaining_mp": result.get("attacker_remaining_mp", 0),
        "defender_remaining_hp": result.get("defender_remaining_hp", 0),
        "log": result["log"],
    }


def pvp_battle(
    challenger: Dict[str, Any],
    defender: Dict[str, Any],
    challenger_skills: Optional[List[Dict[str, Any]]] = None,
    defender_skills: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    challenger_combatant = create_combatant_from_user(challenger, challenger_skills)
    defender_combatant = create_combatant_from_user(defender, defender_skills)
    combat = Combat(challenger_combatant, defender_combatant)
    result = combat.fight()
    if result["winner"] == "draw":
        return {
            "success": True,
            "winner_id": None,
            "loser_id": None,
            "draw": True,
            "rounds": result["rounds"],
            "log": result["log"],
            "message": "双方势均力敌，战斗平局！",
        }
    winner_data = challenger if result["winner"] == "attacker" else defender
    loser_data = defender if result["winner"] == "attacker" else challenger
    return {
        "success": True,
        "winner_id": winner_data.get("user_id"),
        "loser_id": loser_data.get("user_id"),
        "draw": False,
        "rounds": result["rounds"],
        "log": result["log"],
        "message": f"【{winner_data.get('in_game_username')}】击败了【{loser_data.get('in_game_username')}】！",
    }


def format_monster_list(user_rank: int) -> str:
    monsters = get_available_monsters(user_rank)
    lines = ["👹 可挑战的怪物\n"]
    for m in monsters:
        difficulty = "简单" if m["min_rank"] <= user_rank - 2 else "普通" if m["min_rank"] <= user_rank else "困难"
        lines.append(f"▸ {m['name']} (HP:{m['hp']} ATK:{m['attack']})")
        lines.append(f"  奖励: {m['exp_reward']}修为 {m['copper_reward'][0]}-{m['copper_reward'][1]}下品灵石 [{difficulty}]")
    return "\n".join(lines)
