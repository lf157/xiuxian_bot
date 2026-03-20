"""
技能系统
"""

from typing import Dict, Any, List, Optional

from core.config import config

SKILLS = [
    {
        "id": "qixue_slash",
        "name": "气血斩",
        "type": "active",
        "category": "attack",
        "unlock_rank": 3,
        "cost_gold": 0,
        "cost_copper": 300,
        "mp_cost": 8,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.35},
        "desc": "下次攻击造成 135% 伤害（灵力消耗随境界提升）"
    },
    {
        "id": "stone_skin",
        "name": "磐石护体",
        "type": "passive",
        "category": "defense",
        "unlock_rank": 4,
        "cost_gold": 0,
        "cost_copper": 400,
        "effect": {"defense_bonus_pct": 0.18},
        "desc": "防御提高 18%"
    },
    {
        "id": "focus_breath",
        "name": "凝神吐纳",
        "type": "passive",
        "category": "cultivation",
        "unlock_rank": 5,
        "cost_gold": 1,
        "cost_copper": 500,
        "effect": {"cultivation_bonus_pct": 0.20},
        "desc": "修炼收益提高 20%"
    },
    {
        "id": "flame_burst",
        "name": "炎爆术",
        "type": "active",
        "category": "attack",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 14,
        "mp_cost_tier": "burst",
        "effect": {"attack_multiplier": 1.6},
        "desc": "下次攻击造成 160% 伤害（灵力消耗随境界提升）"
    },
    {
        "id": "life_spring",
        "name": "回春诀",
        "type": "passive",
        "category": "sustain",
        "unlock_rank": 9,
        "cost_gold": 2,
        "cost_copper": 1500,
        "effect": {"hp_bonus_pct": 0.15},
        "desc": "最大生命提高 15%"
    },
    {
        "id": "spirit_shield",
        "name": "灵盾诀",
        "type": "active",
        "category": "defense",
        "unlock_rank": 7,
        "cost_gold": 1,
        "cost_copper": 900,
        "mp_cost": 12,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.15, "self_shield_pct": 0.22},
        "desc": "造成 115% 伤害并生成护盾（最大生命22%，灵力消耗随境界提升）"
    },
    {
        "id": "vital_bloom",
        "name": "回元术",
        "type": "active",
        "category": "sustain",
        "unlock_rank": 10,
        "cost_gold": 2,
        "cost_copper": 1400,
        "mp_cost": 10,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.0, "restore_hp_pct": 0.18, "restore_mp_pct": 0.05},
        "desc": "攻击后回复生命18%与灵力5%（灵力消耗随境界提升）"
    },
    {
        "id": "mana_convert",
        "name": "灵气转换",
        "type": "active",
        "category": "resource",
        "unlock_rank": 11,
        "cost_gold": 2,
        "cost_copper": 1500,
        "mp_cost": 8,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.2, "convert_hp_to_mp_pct": 0.06},
        "desc": "造成 120% 伤害，将生命6%转化为灵力（灵力消耗随境界提升）"
    },
    {
        "id": "blood_fury",
        "name": "燃魂诀",
        "type": "active",
        "category": "risk",
        "unlock_rank": 12,
        "cost_gold": 3,
        "cost_copper": 1800,
        "mp_cost": 16,
        "mp_cost_tier": "ultimate",
        "effect": {"attack_multiplier": 1.9, "self_damage_pct": 0.10},
        "desc": "造成 190% 伤害，但自身承受10%生命反噬（灵力消耗随境界提升）"
    },

    # --- Five-elements specializations (流派) ---
    {
        "id": "metal_edge",
        "name": "金·锋刃",
        "type": "passive",
        "category": "element",
        "element": "金",
        "unlock_rank": 6,
        "cost_gold": 1,
        "cost_copper": 800,
        "effect": {"crit_rate_bonus": 0.06, "crit_dmg_bonus": 0.10},
        "desc": "暴击率+6%，暴击伤害+10%"
    },
    {
        "id": "wood_vigor",
        "name": "木·生机",
        "type": "passive",
        "category": "element",
        "element": "木",
        "unlock_rank": 6,
        "cost_gold": 1,
        "cost_copper": 800,
        "effect": {"hp_bonus_pct": 0.08, "lifesteal": 0.03},
        "desc": "最大生命+8%，吸血+3%"
    },
    {
        "id": "water_flow",
        "name": "水·回气",
        "type": "passive",
        "category": "element",
        "element": "水",
        "unlock_rank": 6,
        "cost_gold": 1,
        "cost_copper": 800,
        "effect": {"mp_bonus_pct": 0.10, "skill_damage": 0.06},
        "desc": "最大法力+10%，技能伤害+6%"
    },
    {
        "id": "fire_fury",
        "name": "火·狂炎",
        "type": "passive",
        "category": "element",
        "element": "火",
        "unlock_rank": 6,
        "cost_gold": 1,
        "cost_copper": 800,
        "effect": {"damage_mul": 1.08, "crit_rate_bonus": 0.03},
        "desc": "最终伤害+8%，暴击率+3%"
    },
    {
        "id": "earth_guard",
        "name": "土·厚甲",
        "type": "passive",
        "category": "element",
        "element": "土",
        "unlock_rank": 6,
        "cost_gold": 1,
        "cost_copper": 800,
        "effect": {"damage_taken_mul": 0.92, "defense_bonus_pct": 0.08},
        "desc": "受到伤害-8%，防御+8%"
    },

    # --- Five-elements active signature skills ---
    {
        "id": "metal_pierce",
        "name": "金·破甲刺",
        "type": "active",
        "category": "element_active",
        "element": "金",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 14,
        "mp_cost_tier": "burst",
        "effect": {"attack_multiplier": 1.55, "ignore_def_pct": 0.30},
        "desc": "下次攻击造成 155% 伤害，并无视目标30%防御（灵力消耗随境界提升）"
    },
    {
        "id": "wood_bloom",
        "name": "木·绽生",
        "type": "active",
        "category": "element_active",
        "element": "木",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 12,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.35, "lifesteal_bonus": 0.10},
        "desc": "下次攻击造成 135% 伤害，并额外吸血+10%（本次攻击，灵力消耗随境界提升）"
    },
    {
        "id": "water_wave",
        "name": "水·潮汐击",
        "type": "active",
        "category": "element_active",
        "element": "水",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 12,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.45, "crit_rate_bonus": 0.10},
        "desc": "下次攻击造成 145% 伤害，并提升本次暴击率+10%（灵力消耗随境界提升）"
    },
    {
        "id": "fire_blast",
        "name": "火·爆炎",
        "type": "active",
        "category": "element_active",
        "element": "火",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 16,
        "mp_cost_tier": "burst",
        "effect": {"attack_multiplier": 1.70},
        "desc": "下次攻击造成 170% 伤害（灵力消耗随境界提升）"
    },
    {
        "id": "earth_quake",
        "name": "土·震山",
        "type": "active",
        "category": "element_active",
        "element": "土",
        "unlock_rank": 8,
        "cost_gold": 2,
        "cost_copper": 1200,
        "mp_cost": 13,
        "mp_cost_tier": "basic",
        "effect": {"attack_multiplier": 1.40, "damage_taken_mul": 0.85},
        "desc": "下次攻击造成 140% 伤害，并在本回合获得15%减伤（灵力消耗随境界提升）"
    },
]

SKILL_MAX_LEVEL = 5
SKILL_LEVEL_STEP = 0.05

_MP_COST_RATIO_DEFAULTS = {
    "basic": 0.06,
    "burst": 0.08,
    "ultimate": 0.10,
}


def _skill_cfg_float(*path: str, default: float) -> float:
    try:
        return float(config.get_nested(*path, default=default))
    except (TypeError, ValueError):
        return float(default)


def _skill_cfg_str(*path: str, default: str) -> str:
    value = config.get_nested(*path, default=default)
    return str(value if value is not None else default).strip().lower()


def get_skill_mp_cost_ratio(skill: Dict[str, Any]) -> float:
    explicit_ratio = skill.get("mp_cost_ratio")
    if explicit_ratio is not None:
        try:
            explicit = float(explicit_ratio)
            if explicit > 0:
                return explicit
        except (TypeError, ValueError):
            pass
    tier = str(skill.get("mp_cost_tier") or "basic").strip().lower()
    if tier not in _MP_COST_RATIO_DEFAULTS:
        tier = "basic"
    default_ratio = _MP_COST_RATIO_DEFAULTS[tier]
    configured = _skill_cfg_float("battle", "mp", "cost_ratio_by_tier", tier, default=default_ratio)
    return max(0.0, float(configured))


def compute_skill_mp_cost(skill: Dict[str, Any], max_mp: int) -> int:
    base_cost = max(0, int(skill.get("mp_cost", 0) or 0))
    mp_pool = max(0, int(max_mp or 0))
    ratio = get_skill_mp_cost_ratio(skill)
    scaled_cost = int(round(mp_pool * ratio))
    mode = _skill_cfg_str("battle", "mp", "skill_cost_mode", default="max")
    if mode == "base":
        return max(0, base_cost)
    if mode == "scaled":
        return max(0, scaled_cost)
    # default: max(base, scaled)
    return max(base_cost, scaled_cost)


def format_skill_mp_cost(skill: Dict[str, Any], *, max_mp: Optional[int] = None) -> str:
    base_cost = max(0, int(skill.get("mp_cost", 0) or 0))
    ratio_pct = max(0.0, get_skill_mp_cost_ratio(skill) * 100.0)
    if max_mp is not None:
        actual = compute_skill_mp_cost(skill, int(max_mp or 0))
        return f"{actual}蓝（基础{base_cost}，按最大MP {ratio_pct:.0f}%）"
    return f"基础{base_cost}蓝（按最大MP {ratio_pct:.0f}% 动态提高）"


def scale_skill_effect(skill: Dict[str, Any], level: int) -> Dict[str, Any]:
    lvl = max(1, min(SKILL_MAX_LEVEL, int(level or 1)))
    if lvl == 1:
        return skill.copy()
    scaled = skill.copy()
    effect = dict(skill.get("effect", {}) or {})
    scale = 1.0 + SKILL_LEVEL_STEP * (lvl - 1)
    for key, val in list(effect.items()):
        if not isinstance(val, (int, float)):
            continue
        if key in ("attack_multiplier", "damage_mul", "damage_taken_mul"):
            effect[key] = 1.0 + (float(val) - 1.0) * scale
        else:
            effect[key] = float(val) * scale
    scaled["effect"] = effect
    scaled["skill_level"] = lvl
    return scaled


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    for skill in SKILLS:
        if skill["id"] == skill_id:
            return skill.copy()
    return None


def get_unlockable_skills(rank: int, *, user_element: Optional[str] = None) -> List[Dict[str, Any]]:
    unlocked = [s.copy() for s in SKILLS if rank >= s["unlock_rank"]]
    normalized_element = str(user_element or "").strip()
    if not normalized_element:
        # Accounts without selected element can only learn non-elemental skills.
        return [s for s in unlocked if not s.get("element")]
    return [s for s in unlocked if not s.get("element") or str(s.get("element")) == normalized_element]


def calc_skill_bonus(user_skills: List[Dict[str, Any]]) -> Dict[str, float]:
    bonus = {
        "attack_multiplier": 1.0,
        "defense_bonus_pct": 0.0,
        "cultivation_bonus_pct": 0.0,
        "hp_bonus_pct": 0.0,
        "mp_bonus_pct": 0.0,
    }
    for skill in user_skills:
        effect = skill.get("effect", {})
        if "attack_multiplier" in effect:
            bonus["attack_multiplier"] = max(bonus["attack_multiplier"], effect["attack_multiplier"])
        bonus["defense_bonus_pct"] += effect.get("defense_bonus_pct", 0.0)
        bonus["cultivation_bonus_pct"] += effect.get("cultivation_bonus_pct", 0.0)
        bonus["hp_bonus_pct"] += effect.get("hp_bonus_pct", 0.0)
        bonus["mp_bonus_pct"] += effect.get("mp_bonus_pct", 0.0)
    return bonus
