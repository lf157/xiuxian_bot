"""
物品和装备系统 - Items and Equipment System
"""

import re
import random
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum

from core.utils.timeutil import local_day_key
from core.config import config


class ItemType(Enum):
    """物品类型"""
    WEAPON = "weapon"        # 武器
    ARMOR = "armor"          # 护甲
    ACCESSORY = "accessory"  # 饰品
    PILL = "pill"           # 丹药
    MATERIAL = "material"    # 材料
    SKILL_BOOK = "skill_book"  # 技能书


class Quality(Enum):
    """品质等级"""
    COMMON = "common"      # 凡品（白）
    SPIRIT = "spirit"      # 灵品（绿）
    IMMORTAL = "immortal"  # 仙品（蓝）
    DIVINE = "divine"      # 神品（紫）
    HOLY = "holy"          # 圣品（金）


QUALITY_MULTIPLIERS = {
    Quality.COMMON: 1.0,
    Quality.SPIRIT: 1.2,
    Quality.IMMORTAL: 1.5,
    Quality.DIVINE: 2.0,
    Quality.HOLY: 3.0,
}

QUALITY_NAMES = {
    Quality.COMMON: "凡品",
    Quality.SPIRIT: "灵品",
    Quality.IMMORTAL: "仙品",
    Quality.DIVINE: "神品",
    Quality.HOLY: "圣品",
}

QUALITY_EMOJI = {
    Quality.COMMON: "⚪",
    Quality.SPIRIT: "🟢",
    Quality.IMMORTAL: "🔵",
    Quality.DIVINE: "🟣",
    Quality.HOLY: "🟡",
}

# ==================== 装备机制词条 ====================

AFFIX_DEFS = [
    {
        "key": "first_round_reduction_pct",
        "name": "首回合减伤",
        "min": 0.08,
        "max": 0.15,
        "cap": 0.45,
    },
    {
        "key": "crit_heal_pct",
        "name": "暴击回血",
        "min": 0.12,
        "max": 0.24,
        "cap": 0.60,
    },
    {
        "key": "element_damage_pct",
        "name": "元素增伤",
        "min": 0.06,
        "max": 0.16,
        "cap": 0.50,
    },
    {
        "key": "low_hp_shield_pct",
        "name": "残血护盾",
        "min": 0.12,
        "max": 0.28,
        "cap": 0.60,
    },
]

QUALITY_AFFIX_SCALE = {
    Quality.COMMON: 1.0,
    Quality.SPIRIT: 1.15,
    Quality.IMMORTAL: 1.3,
    Quality.DIVINE: 1.5,
    Quality.HOLY: 1.8,
}


_LEADING_EMOJI_RE = re.compile(
    r"^([\U0001f300-\U0001faff\u2600-\u27bf\ufe0f\u200d]+)",
)


def _quality_item_name(quality: Quality, base_name: str) -> str:
    """生成带品质前缀的物品名，将 emoji 提到最前面。

    例: Quality.COMMON + "🗡️仙剑" → "🗡️凡品仙剑"
    """
    m = _LEADING_EMOJI_RE.match(base_name)
    if m:
        emoji = m.group(1)
        text = base_name[m.end():]
        return f"{emoji}{QUALITY_NAMES[quality]}{text}"
    return f"{QUALITY_NAMES[quality]}{base_name}"


def _affix_count_for_quality(quality: Quality) -> int:
    if quality == Quality.COMMON:
        return 1 if random.random() < 0.25 else 0
    if quality == Quality.SPIRIT:
        return 1
    if quality == Quality.IMMORTAL:
        return 2
    if quality == Quality.DIVINE:
        return 3
    return 4


def roll_equipment_affixes(quality: Quality) -> Dict[str, float]:
    count = _affix_count_for_quality(quality)
    if count <= 0:
        return {}
    pool = AFFIX_DEFS[:]
    random.shuffle(pool)
    scale = QUALITY_AFFIX_SCALE.get(quality, 1.0)
    picked = {}
    for affix in pool[:count]:
        raw = random.uniform(affix["min"], affix["max"]) * scale
        value = min(float(affix.get("cap", 1.0)), raw)
        picked[affix["key"]] = round(value, 4)
    return picked


# ==================== 装备定义 ====================

WEAPONS = [
    {"id": "wooden_sword", "name": "🗡️木剑", "type": ItemType.WEAPON, "base_attack": 5, "min_rank": 1},
    {"id": "iron_sword", "name": "🗡️铁剑", "type": ItemType.WEAPON, "base_attack": 15, "min_rank": 2},
    {"id": "steel_sword", "name": "🗡️钢剑", "type": ItemType.WEAPON, "base_attack": 30, "min_rank": 4},
    {"id": "spirit_sword", "name": "🗡️灵剑", "type": ItemType.WEAPON, "base_attack": 60, "min_rank": 6},
    {"id": "immortal_sword", "name": "🗡️仙剑", "type": ItemType.WEAPON, "base_attack": 120, "min_rank": 10},
    {"id": "divine_sword", "name": "🗡️神剑", "type": ItemType.WEAPON, "base_attack": 250, "min_rank": 15},
    {"id": "heavenly_sword", "name": "🗡️天剑", "type": ItemType.WEAPON, "base_attack": 500, "min_rank": 20},

    {"id": "wooden_staff", "name": "🪄木杖", "type": ItemType.WEAPON, "base_attack": 3, "base_mp": 20, "min_rank": 1},
    {"id": "spirit_staff", "name": "🪄灵杖", "type": ItemType.WEAPON, "base_attack": 10, "base_mp": 50, "min_rank": 5},
    {"id": "immortal_staff", "name": "🪄仙杖", "type": ItemType.WEAPON, "base_attack": 30, "base_mp": 120, "min_rank": 10},
]

ARMORS = [
    {"id": "cloth_armor", "name": "🧥布衣", "type": ItemType.ARMOR, "base_defense": 3, "base_hp": 20, "min_rank": 1},
    {"id": "leather_armor", "name": "🧥皮甲", "type": ItemType.ARMOR, "base_defense": 8, "base_hp": 50, "min_rank": 2},
    {"id": "iron_armor", "name": "🛡️铁甲", "type": ItemType.ARMOR, "base_defense": 20, "base_hp": 100, "min_rank": 5},
    {"id": "spirit_armor", "name": "🛡️灵甲", "type": ItemType.ARMOR, "base_defense": 50, "base_hp": 250, "min_rank": 8},
    {"id": "immortal_armor", "name": "🛡️仙甲", "type": ItemType.ARMOR, "base_defense": 100, "base_hp": 500, "min_rank": 12},
    {"id": "divine_armor", "name": "🛡️神甲", "type": ItemType.ARMOR, "base_defense": 200, "base_hp": 1000, "min_rank": 18},
]

ACCESSORIES = [
    {"id": "wooden_ring", "name": "💍木戒", "type": ItemType.ACCESSORY, "base_attack": 2, "base_defense": 2, "min_rank": 1},
    {"id": "jade_pendant", "name": "📿玉佩", "type": ItemType.ACCESSORY, "base_hp": 30, "base_mp": 30, "min_rank": 3},
    {"id": "spirit_necklace", "name": "📿灵珠", "type": ItemType.ACCESSORY, "base_attack": 10, "base_hp": 50, "min_rank": 6},
    {"id": "spirit_ring", "name": "💍灵戒", "type": ItemType.ACCESSORY, "base_attack": 12, "base_defense": 12, "min_rank": 6},
    {"id": "immortal_ring", "name": "💍仙戒", "type": ItemType.ACCESSORY, "base_attack": 25, "base_defense": 25, "min_rank": 10},
    {"id": "divine_amulet", "name": "🔮神符", "type": ItemType.ACCESSORY, "base_hp": 200, "base_mp": 200, "base_attack": 30, "min_rank": 15},
]


# ==================== 丹药定义 ====================

PILLS = [
    # 修为丹
    {"id": "small_exp_pill", "name": "💊小修为丹", "type": ItemType.PILL, "effect": "exp", "value": 50, "price": 100},
    {"id": "medium_exp_pill", "name": "💊中修为丹", "type": ItemType.PILL, "effect": "exp", "value": 200, "price": 350},
    {"id": "large_exp_pill", "name": "💊大修为丹", "type": ItemType.PILL, "effect": "exp", "value": 500, "price": 800},
    {"id": "super_exp_pill", "name": "💊超级修为丹", "type": ItemType.PILL, "effect": "exp", "value": 2000, "price": 2500},

    # 突破丹
    {"id": "breakthrough_pill", "name": "💊突破丹", "type": ItemType.PILL, "effect": "breakthrough", "value": 10, "duration": 3600, "price": 500},
    {"id": "advanced_breakthrough_pill", "name": "💊高级突破丹", "type": ItemType.PILL, "effect": "breakthrough", "value": 20, "duration": 3600, "price": 1500},
    {"id": "super_breakthrough_pill", "name": "💊超级突破丹", "type": ItemType.PILL, "effect": "breakthrough", "value": 50, "duration": 3600, "price": 100},
    {"id": "spirit_array_low", "name": "🔯下品聚灵阵", "type": ItemType.PILL, "effect": "spirit_array", "value": 8, "value_pct": 0.20, "duration": 3600, "price": 650},
    {"id": "spirit_array_mid", "name": "🔯中品聚灵阵", "type": ItemType.PILL, "effect": "spirit_array", "value": 15, "value_pct": 0.35, "duration": 7200, "price": 1800},
    {"id": "spirit_array_high", "name": "🔯上品聚灵阵", "type": ItemType.PILL, "effect": "spirit_array", "value": 25, "value_pct": 0.50, "duration": 10800, "price": 3600},

    # 恢复丹
    {"id": "hp_pill", "name": "❤️回血丹", "type": ItemType.PILL, "effect": "hp", "value_pct": 0.30, "price": 50},
    {"id": "mp_pill", "name": "💙回蓝丹", "type": ItemType.PILL, "effect": "mp", "value_pct": 0.30, "price": 50},
    {"id": "full_restore_pill", "name": "💊大还丹", "type": ItemType.PILL, "effect": "full_restore", "value": 0, "price": 300},

    # 增益丹
    {"id": "attack_buff_pill", "name": "💊大力丹", "type": ItemType.PILL, "effect": "attack_buff", "value": 20, "duration": 3600, "price": 200},
    {"id": "defense_buff_pill", "name": "💊铁甲丹", "type": ItemType.PILL, "effect": "defense_buff", "value": 20, "duration": 3600, "price": 200},
    {"id": "cultivation_buff_pill", "name": "💊悟道丹", "type": ItemType.PILL, "effect": "cultivation_buff", "value": 50, "duration": 7200, "price": 500},
    {"id": "cultivation_sprint_pill", "name": "💊修炼冲刺丹", "type": ItemType.PILL, "effect": "cultivation_sprint", "value": 35, "duration": 7200, "price": 600},
    {"id": "realm_drop_pill", "name": "💊秘境掉落丹", "type": ItemType.PILL, "effect": "realm_drop_boost", "value": 35, "duration": 3600, "price": 650},
    {"id": "breakthrough_guard_pill", "name": "💊突破保护丹", "type": ItemType.PILL, "effect": "breakthrough_protect", "value": 50, "duration": 3600, "price": 800},
]


# ==================== 材料定义 ====================

MATERIALS = [
    {
        "id": "iron_ore",
        "name": "⛏️铁矿石",
        "type": ItemType.MATERIAL,
        "price": 10,
        "drop_rate": 0.3,
        "focus": "强化流",
        "usage": "装备强化与前期锻造",
        "stage_hint": "练气-元婴",
    },
    {
        "id": "spirit_stone",
        "name": "🪨聚元石",
        "type": ItemType.MATERIAL,
        "price": 50,
        "drop_rate": 0.15,
        "focus": "推进流",
        "usage": "突破准备与关键资源补位",
        "stage_hint": "筑基-元婴",
    },
    {
        "id": "immortal_stone",
        "name": "💎仙石",
        "type": ItemType.MATERIAL,
        "price": 200,
        "drop_rate": 0.05,
        "focus": "高阶推进流",
        "usage": "化神后高阶秘境与稀有成长消耗",
        "stage_hint": "化神以上",
    },
    {
        "id": "herb",
        "name": "🌿灵草",
        "type": ItemType.MATERIAL,
        "price": 20,
        "drop_rate": 0.25,
        "focus": "炼丹流",
        "usage": "低阶炼丹与日常丹药补给",
        "stage_hint": "练气-筑基",
    },
    {
        "id": "beast_hide",
        "name": "🐾兽皮",
        "type": ItemType.MATERIAL,
        "price": 15,
        "drop_rate": 0.22,
        "focus": "悬赏流",
        "usage": "常见悬赏道具，也可作为基础锻造辅料",
        "stage_hint": "炼气-筑基",
    },
    {
        "id": "spirit_herb",
        "name": "🌸仙草",
        "type": ItemType.MATERIAL,
        "price": 100,
        "drop_rate": 0.08,
        "focus": "高阶炼丹流",
        "usage": "中后期炼丹与稀有丹方",
        "stage_hint": "金丹以上",
    },
    {
        "id": "dragon_scale",
        "name": "🐲龙鳞",
        "type": ItemType.MATERIAL,
        "price": 500,
        "drop_rate": 0.02,
        "focus": "高阶合成流",
        "usage": "高阶合成、终局装备与稀有目标追逐",
        "stage_hint": "化神以上",
    },
    {
        "id": "phoenix_feather",
        "name": "🪶凤羽",
        "type": ItemType.MATERIAL,
        "price": 500,
        "drop_rate": 0.02,
        "focus": "高阶合成流",
        "usage": "高阶合成、终局装备与稀有目标追逐",
        "stage_hint": "化神以上",
    },
    {
        "id": "demon_core",
        "name": "👹妖丹",
        "type": ItemType.MATERIAL,
        "price": 300,
        "drop_rate": 0.03,
        "focus": "突破流",
        "usage": "突破准备、冲关强化与高风险成长",
        "stage_hint": "金丹以上",
    },
    {
        "id": "recipe_fragment",
        "name": "📜丹方残页",
        "type": ItemType.MATERIAL,
        "price": 180,
        "drop_rate": 0.04,
        "focus": "稀有配方流",
        "usage": "用于稀有炼丹配方与寻宝路线奖励",
        "stage_hint": "金丹以上",
    },
]

# ==================== 技能书定义 ====================

SKILL_BOOKS = [
    {
        "id": "skill_book_basic",
        "name": "📕基础技能书",
        "type": ItemType.SKILL_BOOK,
        "price": 120,
        "focus": "技能成长",
        "usage": "用于提升技能熟练度",
        "stage_hint": "筑基以上",
    },
    {
        "id": "skill_book_advanced",
        "name": "📗高阶技能书",
        "type": ItemType.SKILL_BOOK,
        "price": 360,
        "focus": "技能成长",
        "usage": "用于提升技能熟练度",
        "stage_hint": "金丹以上",
    },
]


# ==================== 掉落系统 ====================

DROP_TABLES = {
    # 怪物ID -> 可能掉落的物品
    "wild_boar": [
        {"item_id": "iron_ore", "rate": 0.28, "quantity": (1, 2)},
        {"item_id": "herb", "rate": 0.20, "quantity": (1, 2)},
        {"item_id": "beast_hide", "rate": 0.26, "quantity": (1, 3)},
    ],
    "wolf": [
        {"item_id": "iron_ore", "rate": 0.30, "quantity": (1, 3)},
        {"item_id": "herb", "rate": 0.22, "quantity": (1, 2)},
        {"item_id": "beast_hide", "rate": 0.34, "quantity": (1, 4)},
        {"item_id": "wooden_sword", "rate": 0.05, "quality": Quality.COMMON},
    ],
    "giant_snake": [
        {"item_id": "iron_ore", "rate": 0.22, "quantity": (1, 3)},
        {"item_id": "herb", "rate": 0.30, "quantity": (2, 4)},
        {"item_id": "iron_sword", "rate": 0.08, "quality": Quality.COMMON},
    ],
    "stone_golem": [
        {"item_id": "iron_ore", "rate": 0.45, "quantity": (3, 6)},
        {"item_id": "spirit_stone", "rate": 0.12, "quantity": (1, 2)},
        {"item_id": "steel_sword", "rate": 0.1, "quality": Quality.COMMON},
        {"item_id": "iron_armor", "rate": 0.08, "quality": Quality.COMMON},
    ],
    "fire_sprite": [
        {"item_id": "spirit_stone", "rate": 0.24, "quantity": (1, 3)},
        {"item_id": "spirit_herb", "rate": 0.12, "quantity": (1, 2)},
        {"item_id": "spirit_sword", "rate": 0.1, "quality": Quality.COMMON},
        {"item_id": "spirit_sword", "rate": 0.03, "quality": Quality.SPIRIT},
    ],
    "blood_dragon": [
        {"item_id": "demon_core", "rate": 0.22, "quantity": (1, 2)},
        {"item_id": "spirit_herb", "rate": 0.20, "quantity": (1, 3)},
        {"item_id": "immortal_stone", "rate": 0.10, "quantity": (1, 2)},
        {"item_id": "immortal_sword", "rate": 0.1, "quality": Quality.COMMON},
        {"item_id": "immortal_sword", "rate": 0.05, "quality": Quality.SPIRIT},
        {"item_id": "immortal_sword", "rate": 0.02, "quality": Quality.IMMORTAL},
    ],
    "ancient_demon": [
        {"item_id": "demon_core", "rate": 0.28, "quantity": (1, 3)},
        {"item_id": "immortal_stone", "rate": 0.20, "quantity": (2, 4)},
        {"item_id": "spirit_herb", "rate": 0.18, "quantity": (2, 3)},
        {"item_id": "divine_sword", "rate": 0.1, "quality": Quality.COMMON},
        {"item_id": "divine_sword", "rate": 0.05, "quality": Quality.SPIRIT},
        {"item_id": "divine_armor", "rate": 0.08, "quality": Quality.COMMON},
    ],
    "heavenly_dragon": [
        {"item_id": "dragon_scale", "rate": 0.32, "quantity": (2, 4)},
        {"item_id": "phoenix_feather", "rate": 0.18, "quantity": (1, 2)},
        {"item_id": "immortal_stone", "rate": 0.20, "quantity": (2, 3)},
        {"item_id": "heavenly_sword", "rate": 0.1, "quality": Quality.SPIRIT},
        {"item_id": "heavenly_sword", "rate": 0.05, "quality": Quality.IMMORTAL},
        {"item_id": "heavenly_sword", "rate": 0.02, "quality": Quality.DIVINE},
    ],
    # === 新增高等级怪物掉落 (Rank 24-32) ===
    "void_phantom": [
        {"item_id": "immortal_stone", "rate": 0.30, "quantity": (3, 6)},
        {"item_id": "spirit_herb", "rate": 0.18, "quantity": (2, 4)},
        {"item_id": "demon_core", "rate": 0.20, "quantity": (1, 3)},
        {"item_id": "dragon_scale", "rate": 0.12, "quantity": (1, 2)},
        {"item_id": "heavenly_sword", "rate": 0.06, "quality": Quality.SPIRIT},
        {"item_id": "divine_armor", "rate": 0.05, "quality": Quality.SPIRIT},
    ],
    "infernal_lord": [
        {"item_id": "immortal_stone", "rate": 0.28, "quantity": (4, 8)},
        {"item_id": "phoenix_feather", "rate": 0.22, "quantity": (2, 3)},
        {"item_id": "demon_core", "rate": 0.22, "quantity": (2, 4)},
        {"item_id": "dragon_scale", "rate": 0.12, "quantity": (1, 2)},
        {"item_id": "heavenly_sword", "rate": 0.08, "quality": Quality.IMMORTAL},
        {"item_id": "divine_armor", "rate": 0.06, "quality": Quality.IMMORTAL},
    ],
    "celestial_tiger": [
        {"item_id": "dragon_scale", "rate": 0.34, "quantity": (3, 5)},
        {"item_id": "phoenix_feather", "rate": 0.22, "quantity": (2, 4)},
        {"item_id": "heavenly_sword", "rate": 0.10, "quality": Quality.IMMORTAL},
        {"item_id": "heavenly_sword", "rate": 0.04, "quality": Quality.DIVINE},
        {"item_id": "divine_armor", "rate": 0.08, "quality": Quality.IMMORTAL},
    ],
    "primordial_tortoise": [
        {"item_id": "dragon_scale", "rate": 0.35, "quantity": (4, 7)},
        {"item_id": "demon_core", "rate": 0.25, "quantity": (3, 5)},
        {"item_id": "divine_armor", "rate": 0.10, "quality": Quality.IMMORTAL},
        {"item_id": "divine_armor", "rate": 0.04, "quality": Quality.DIVINE},
        {"item_id": "divine_amulet", "rate": 0.05, "quality": Quality.IMMORTAL},
    ],
    "divine_phoenix": [
        {"item_id": "phoenix_feather", "rate": 0.40, "quantity": (4, 8)},
        {"item_id": "dragon_scale", "rate": 0.30, "quantity": (3, 6)},
        {"item_id": "heavenly_sword", "rate": 0.08, "quality": Quality.DIVINE},
        {"item_id": "divine_armor", "rate": 0.06, "quality": Quality.DIVINE},
        {"item_id": "divine_amulet", "rate": 0.06, "quality": Quality.DIVINE},
    ],
    "chaos_dragon": [
        {"item_id": "dragon_scale", "rate": 0.45, "quantity": (5, 10)},
        {"item_id": "phoenix_feather", "rate": 0.35, "quantity": (3, 7)},
        {"item_id": "heavenly_sword", "rate": 0.10, "quality": Quality.DIVINE},
        {"item_id": "heavenly_sword", "rate": 0.03, "quality": Quality.HOLY},
        {"item_id": "divine_armor", "rate": 0.08, "quality": Quality.DIVINE},
    ],
    "immortal_sovereign": [
        {"item_id": "dragon_scale", "rate": 0.50, "quantity": (6, 12)},
        {"item_id": "phoenix_feather", "rate": 0.40, "quantity": (5, 8)},
        {"item_id": "heavenly_sword", "rate": 0.05, "quality": Quality.HOLY},
        {"item_id": "divine_armor", "rate": 0.10, "quality": Quality.DIVINE},
        {"item_id": "divine_armor", "rate": 0.03, "quality": Quality.HOLY},
    ],
    "heavenly_dao_beast": [
        {"item_id": "dragon_scale", "rate": 0.55, "quantity": (8, 15)},
        {"item_id": "phoenix_feather", "rate": 0.45, "quantity": (6, 10)},
        {"item_id": "demon_core", "rate": 0.40, "quantity": (5, 8)},
        {"item_id": "heavenly_sword", "rate": 0.08, "quality": Quality.HOLY},
        {"item_id": "divine_armor", "rate": 0.06, "quality": Quality.HOLY},
        {"item_id": "divine_amulet", "rate": 0.05, "quality": Quality.HOLY},
    ],
}


def get_item_by_id(item_id: str) -> Optional[Dict[str, Any]]:
    """根据ID获取物品定义"""
    all_items = WEAPONS + ARMORS + ACCESSORIES + PILLS + MATERIALS + SKILL_BOOKS
    for item in all_items:
        if item["id"] == item_id:
            return item.copy()
    return None


def generate_equipment(base_item: Dict, quality: Quality, level: int = 1) -> Dict[str, Any]:
    """生成装备实例"""
    multiplier = QUALITY_MULTIPLIERS[quality]
    
    equipment = {
        "item_id": base_item["id"],
        "item_name": _quality_item_name(quality, base_item["name"]),
        "item_type": base_item["type"].value,
        "quality": quality.value,
        "level": level,
        "quantity": 1,
        "first_round_reduction_pct": 0.0,
        "crit_heal_pct": 0.0,
        "element_damage_pct": 0.0,
        "low_hp_shield_pct": 0.0,
    }
    
    # 计算属性
    if "base_attack" in base_item:
        equipment["attack_bonus"] = int(base_item["base_attack"] * multiplier * (1 + level * 0.1))
    if "base_defense" in base_item:
        equipment["defense_bonus"] = int(base_item["base_defense"] * multiplier * (1 + level * 0.1))
    if "base_hp" in base_item:
        equipment["hp_bonus"] = int(base_item["base_hp"] * multiplier * (1 + level * 0.1))
    if "base_mp" in base_item:
        equipment["mp_bonus"] = int(base_item["base_mp"] * multiplier * (1 + level * 0.1))

    affixes = roll_equipment_affixes(quality)
    if affixes:
        for key, val in affixes.items():
            equipment[key] = val

    return equipment


def generate_material(item_id: str, quantity: int = 1) -> Dict[str, Any]:
    """生成材料实例"""
    base_item = get_item_by_id(item_id)
    if not base_item:
        return None
    
    return {
        "item_id": item_id,
        "item_name": base_item["name"],
        "item_type": ItemType.MATERIAL.value,
        "quality": Quality.COMMON.value,
        "quantity": quantity,
    }


def generate_pill(item_id: str, quantity: int = 1) -> Dict[str, Any]:
    """生成丹药实例"""
    base_item = get_item_by_id(item_id)
    if not base_item:
        return None
    
    return {
        "item_id": item_id,
        "item_name": base_item["name"],
        "item_type": ItemType.PILL.value,
        "effect": base_item.get("effect"),
        "value": base_item.get("value"),
        "duration": base_item.get("duration"),
        "quality": Quality.COMMON.value,
        "quantity": quantity,
    }


def generate_skill_book(item_id: str, quantity: int = 1) -> Dict[str, Any]:
    """生成技能书实例"""
    base_item = get_item_by_id(item_id)
    if not base_item:
        return None
    return {
        "item_id": item_id,
        "item_name": base_item["name"],
        "item_type": ItemType.SKILL_BOOK.value,
        "quality": Quality.COMMON.value,
        "quantity": quantity,
    }


def calculate_drop_rewards(monster_id: str, user_rank: int, *, include_targeted: bool = True) -> List[Dict[str, Any]]:
    """计算怪物掉落"""
    drops = []
    drop_table = DROP_TABLES.get(monster_id, [])
    
    for drop in drop_table:
        if random.random() < drop["rate"]:
            item_id = drop["item_id"]
            
            if "quantity" in drop:
                # 材料类
                min_q, max_q = drop["quantity"]
                quantity = random.randint(min_q, max_q)
                item = generate_material(item_id, quantity)
            else:
                # 装备类
                quality = drop.get("quality", Quality.COMMON)
                base_item = get_item_by_id(item_id)
                if base_item:
                    item = generate_equipment(base_item, quality, max(1, user_rank - base_item.get("min_rank", 1)))
                else:
                    continue
            
            if item:
                drops.append(item)

    if include_targeted:
        target_drop = roll_targeted_equipment_drop(monster_id, source_kind="monster", user_rank=user_rank)
        if target_drop:
            drops.append(target_drop)
    
    return drops


def calculate_equipment_score(equipment: Dict) -> int:
    """计算装备战力分数"""
    score = 0
    score += equipment.get("attack_bonus", 0) * 2
    score += equipment.get("defense_bonus", 0) * 2
    score += equipment.get("hp_bonus", 0) // 10
    score += equipment.get("mp_bonus", 0) // 10
    return score


def format_item_info(item: Dict) -> str:
    """格式化物品信息"""
    quality = Quality(item.get("quality", "common"))
    emoji = QUALITY_EMOJI[quality]
    name = item.get("item_name", item.get("name", "未知物品"))
    
    lines = [f"{emoji} *{name}*"]
    
    if item.get("attack_bonus"):
        lines.append(f"  ⚔️ 攻击 +{item['attack_bonus']}")
    if item.get("defense_bonus"):
        lines.append(f"  🛡️ 防御 +{item['defense_bonus']}")
    if item.get("hp_bonus"):
        lines.append(f"  ❤️ HP +{item['hp_bonus']}")
    if item.get("mp_bonus"):
        lines.append(f"  💙 MP +{item['mp_bonus']}")
    affix_lines = []
    fr = float(item.get("first_round_reduction_pct", 0) or 0)
    if fr > 0:
        affix_lines.append(f"首回合减伤 {int(fr * 100)}%")
    ch = float(item.get("crit_heal_pct", 0) or 0)
    if ch > 0:
        affix_lines.append(f"暴击回血 {int(ch * 100)}%")
    ed = float(item.get("element_damage_pct", 0) or 0)
    if ed > 0:
        affix_lines.append(f"元素增伤 {int(ed * 100)}%")
    lh = float(item.get("low_hp_shield_pct", 0) or 0)
    if lh > 0:
        affix_lines.append(f"残血护盾 {int(lh * 100)}%")
    if affix_lines:
        lines.append("  ✨ 词条: " + " / ".join(affix_lines))
    if item.get("quantity", 1) > 1:
        lines.append(f"  📦 数量: {item['quantity']}")
    
    return "\n".join(lines)


# ==================== 商店系统 ====================

TARGETED_MONSTER_DROPS = {
    "wolf": ["wooden_ring"],
    "giant_snake": ["jade_pendant"],
    "stone_golem": ["iron_armor", "steel_sword"],
    "fire_sprite": ["spirit_staff", "spirit_sword"],
    "blood_dragon": ["immortal_sword", "immortal_ring"],
    "ancient_demon": ["divine_sword", "divine_armor"],
    "heavenly_dragon": ["heavenly_sword", "divine_amulet"],
}

TARGETED_REALM_DROPS = {
    "mist_forest": ["wooden_sword", "wooden_ring"],
    "black_rock_cave": ["iron_sword", "iron_armor"],
    "burning_ruins": ["spirit_sword", "spirit_armor"],
    "sky_fall_palace": ["immortal_sword", "immortal_armor", "immortal_ring"],
    "void_abyss": ["divine_sword", "divine_armor", "divine_amulet"],
    "celestial_battlefield": ["heavenly_sword", "divine_armor", "divine_amulet"],
}


def roll_targeted_equipment_drop(
    source_id: str,
    *,
    source_kind: str,
    user_rank: int,
    boosted: bool = False,
    pity_state: Optional[Dict[str, Any]] = None,
    return_meta: bool = False,
) -> Any:
    pool = TARGETED_MONSTER_DROPS.get(source_id, []) if source_kind == "monster" else TARGETED_REALM_DROPS.get(source_id, [])
    if not pool:
        return (None, {"base_rate": 0.0, "effective_rate": 0.0, "streak": 0, "extra_rate": 0.0, "pity_triggered": False}) if return_meta else None
    base_rate = 0.10 if source_kind == "monster" else 0.18
    if boosted:
        base_rate += 0.10
    state = pity_state or {}
    streak = max(0, int(state.get("streak", 0) or 0))
    step = max(0.0, float(state.get("step", 0.015) or 0.015))
    cap = max(0.0, float(state.get("cap", 0.30) or 0.30))
    extra_rate = min(cap, streak * step)
    effective_rate = min(0.95, base_rate + extra_rate)
    roll = random.random()
    if roll >= effective_rate:
        meta = {
            "base_rate": round(base_rate, 4),
            "effective_rate": round(effective_rate, 4),
            "streak": streak,
            "extra_rate": round(extra_rate, 4),
            "pity_triggered": False,
        }
        return (None, meta) if return_meta else None
    item_id = random.choice(pool)
    base_item = get_item_by_id(item_id)
    if not base_item:
        meta = {
            "base_rate": round(base_rate, 4),
            "effective_rate": round(effective_rate, 4),
            "streak": streak,
            "extra_rate": round(extra_rate, 4),
            "pity_triggered": False,
        }
        return (None, meta) if return_meta else None
    def _quality_weight_map(rank: int) -> Dict[Quality, float]:
        if rank < 12:
            return {Quality.COMMON: 0.72, Quality.SPIRIT: 0.28}
        if rank < 20:
            return {Quality.COMMON: 0.55, Quality.SPIRIT: 0.30, Quality.IMMORTAL: 0.15}
        if rank < 28:
            return {Quality.COMMON: 0.40, Quality.SPIRIT: 0.30, Quality.IMMORTAL: 0.20, Quality.DIVINE: 0.10}
        return {Quality.COMMON: 0.25, Quality.SPIRIT: 0.30, Quality.IMMORTAL: 0.25, Quality.DIVINE: 0.20}

    def _weighted_quality(rank: int) -> Quality:
        weights = _quality_weight_map(rank)
        total = sum(weights.values())
        roll = random.random() * total
        acc = 0.0
        for q, w in weights.items():
            acc += w
            if roll <= acc:
                return q
        return Quality.COMMON

    quality = _weighted_quality(int(user_rank or 1))
    drop = generate_equipment(base_item, quality, max(1, int(user_rank or 1) - base_item.get("min_rank", 1)))
    if not return_meta:
        return drop
    meta = {
        "base_rate": round(base_rate, 4),
        "effective_rate": round(effective_rate, 4),
        "streak": streak,
        "extra_rate": round(extra_rate, 4),
        "pity_triggered": streak > 0 and roll >= base_rate,
    }
    return drop, meta

SHOP_ITEMS = {
    "copper": [
        # 丹药
        {"item_id": "small_exp_pill", "name": "💊小修为丹", "price": 100, "stock": -1, "category": "pill"},
        {"item_id": "medium_exp_pill", "name": "💊中修为丹", "price": 350, "stock": -1, "category": "pill"},
        {"item_id": "hp_pill", "name": "❤️回血丹", "price": 50, "stock": -1, "category": "pill"},
        {"item_id": "mp_pill", "name": "💙回蓝丹", "price": 50, "stock": -1, "category": "pill"},
        {"item_id": "breakthrough_pill", "name": "💊突破丹", "price": 500, "stock": -1, "category": "pill"},
        # 法阵（低级在铜币）
        {"item_id": "spirit_array_low", "name": "🔯下品聚灵阵", "price": 650, "stock": -1, "category": "array"},
        # 材料
        {"item_id": "iron_ore", "name": "⛏️铁矿石", "price": 10, "stock": -1, "category": "material"},
        {"item_id": "herb", "name": "🌿灵草", "price": 20, "stock": -1, "category": "material"},
        {"item_id": "spirit_stone", "name": "🪨聚元石", "price": 50, "stock": -1, "category": "material"},
    ],
    "gold": [
        # 丹药
        {"item_id": "large_exp_pill", "name": "💊大修为丹", "price": 10, "stock": -1, "category": "pill"},
        {"item_id": "super_exp_pill", "name": "💊超级修为丹", "price": 25, "stock": -1, "category": "pill"},
        {"item_id": "advanced_breakthrough_pill", "name": "💊高级突破丹", "price": 15, "stock": -1, "category": "pill"},
        {"item_id": "cultivation_buff_pill", "name": "💊悟道丹", "price": 5, "stock": -1, "category": "pill"},
        {"item_id": "cultivation_sprint_pill", "name": "💊修炼冲刺丹", "price": 8, "stock": -1, "category": "pill"},
        {"item_id": "realm_drop_pill", "name": "💊秘境掉落丹", "price": 8, "stock": -1, "category": "pill"},
        {"item_id": "breakthrough_guard_pill", "name": "💊突破保护丹", "price": 12, "stock": -1, "category": "pill"},
        # 法阵（中高级在金币）
        {"item_id": "spirit_array_mid", "name": "🔯中品聚灵阵", "price": 12, "stock": -1, "category": "array"},
        {"item_id": "spirit_array_high", "name": "🔯上品聚灵阵", "price": 24, "stock": -1, "category": "array"},
    ],
    "spirit_high": [
        {"item_id": "super_breakthrough_pill", "name": "💊超级突破丹", "price": 100, "stock": 10, "category": "pill"},
    ]
}

SHOP_ROTATIONS = {
    "copper": {
        "daily_specials": [
            # hp_pill / mp_pill / small_exp_pill / spirit_array_low / iron_ore / herb 均已在铜币常驻，此处为限时折扣版
            {"item_id": "hp_pill", "price": 35, "stock": 3, "tag": "限时折扣", "category": "pill", "limit": 3, "limit_period": "day"},
            {"item_id": "mp_pill", "price": 35, "stock": 3, "tag": "限时折扣", "category": "pill", "limit": 3, "limit_period": "day"},
            {"item_id": "small_exp_pill", "price": 80, "stock": 2, "tag": "限时折扣", "category": "pill", "limit": 2, "limit_period": "day"},
            {"item_id": "spirit_array_low", "price": 520, "stock": 2, "tag": "限时折扣", "category": "array", "limit": 2, "limit_period": "day"},
            {"item_id": "iron_ore", "price": 8, "stock": 10, "tag": "限时折扣", "category": "material", "limit": 10, "limit_period": "day"},
            {"item_id": "herb", "price": 15, "stock": 10, "tag": "限时折扣", "category": "material", "limit": 10, "limit_period": "day"},
        ],
        "weekly_rare": [
            # spirit_stone (聚元石) 已在铜币常驻，此处为限时折扣版
            {"item_id": "spirit_stone", "price": 35, "stock": 12, "tag": "限时折扣", "category": "material", "limit": 12, "limit_period": "week"},
            {"item_id": "spirit_herb", "price": 70, "stock": 8, "tag": "周稀有材料", "category": "material", "limit": 8, "limit_period": "week"},
            {"item_id": "demon_core", "price": 220, "stock": 3, "tag": "周稀有材料", "category": "material", "limit": 3, "limit_period": "week"},
            {"item_id": "skill_book_basic", "price": 90, "stock": 4, "tag": "技能成长", "category": "book", "limit": 4, "limit_period": "week"},
        ],
    },
    "gold": {
        "daily_specials": [
            # advanced_breakthrough_pill / cultivation_buff_pill 已在金币常驻，此处为限时折扣版
            {"item_id": "advanced_breakthrough_pill", "price": 10, "stock": 10, "tag": "限时折扣", "category": "pill", "limit": 10, "limit_period": "day"},
            {"item_id": "cultivation_buff_pill", "price": 4, "stock": 2, "tag": "限时折扣", "category": "pill", "limit": 2, "limit_period": "day"},
        ],
        "weekly_rare": [
            {"item_id": "demon_core", "price": 8, "stock": 2, "tag": "周稀有材料", "category": "material", "limit": 2, "limit_period": "week"},
            {"item_id": "recipe_fragment", "price": 6, "stock": 3, "tag": "周稀有材料", "category": "material", "limit": 3, "limit_period": "week"},
            # spirit_array_mid / spirit_array_high 已在金币常驻，此处为限时折扣版
            {"item_id": "spirit_array_mid", "price": 10, "stock": 4, "tag": "限时折扣", "category": "array", "limit": 4, "limit_period": "week"},
            {"item_id": "spirit_array_high", "price": 20, "stock": 2, "tag": "限时折扣", "category": "array", "limit": 2, "limit_period": "week"},
            {"item_id": "phoenix_feather", "price": 15, "stock": 1, "tag": "限量突破资源", "category": "material", "limit": 1, "limit_period": "week", "min_rank": 20},
            # cultivation_sprint_pill / realm_drop_pill / breakthrough_guard_pill 已在金币常驻，此处为限时折扣版
            {"item_id": "cultivation_sprint_pill", "price": 6, "stock": 2, "tag": "限时折扣", "category": "pill", "limit": 2, "limit_period": "week"},
            {"item_id": "realm_drop_pill", "price": 6, "stock": 2, "tag": "限时折扣", "category": "pill", "limit": 2, "limit_period": "week"},
            {"item_id": "breakthrough_guard_pill", "price": 9, "stock": 1, "tag": "限时折扣", "category": "pill", "limit": 1, "limit_period": "week"},
            {"item_id": "skill_book_advanced", "price": 6, "stock": 2, "tag": "技能成长", "category": "book", "limit": 2, "limit_period": "week", "min_rank": 12},
        ],
    },
    "spirit_high": {
        "daily_specials": [
            {"item_id": "super_breakthrough_pill", "price": 100, "stock": 10, "tag": "限时折扣", "category": "pill", "limit": 10, "limit_period": "day"},
        ],
        "weekly_rare": [],
    },
}

SHOP_CURRENCY_ROLES = {
    "copper": "下品灵石用于日常消耗，覆盖修炼补给、基础材料与低风险准备。",
    "gold": "中品灵石用于阶段推进与稀缺机会，优先用于突破、爆发成长和关键窗口资源。",
    "spirit_high": "上品灵石用于高阶破境与关键冲关资源，建议在圆满关口集中投入。",
}

PROGRESSION_STAGE_THEMES = [
    {
        "min_rank": 1,
        "max_rank": 9,
        "label": "练气-筑基",
        "theme": "基础资源与低级炼丹",
        "focus": "优先囤下品灵石、灵草、铁矿石，建立日常修炼与补给循环。",
    },
    {
        "min_rank": 10,
        "max_rank": 19,
        "label": "金丹-元婴",
        "theme": "突破准备与装备强化",
        "focus": "开始围绕妖丹、聚元石和强化材料做冲关准备。",
    },
    {
        "min_rank": 20,
        "max_rank": 999,
        "label": "化神以上",
        "theme": "高阶秘境、中品灵石资源、稀有材料追逐",
        "focus": "核心目标转为高阶秘境掉落、中品灵石机会和龙鳞凤羽等稀有材料。",
    },
]


def _rotation_index(period: str) -> int:
    day_key = local_day_key()
    if period == "week":
        return day_key // 7
    return day_key


def _pick_rotating_offers(currency: str, bucket: str) -> List[Dict[str, Any]]:
    offers = (SHOP_ROTATIONS.get(currency, {}) or {}).get(bucket, []) or []
    if not offers:
        return []
    idx = _rotation_index("week" if bucket == "weekly_rare" else "day")
    if len(offers) <= 2:
        return [offers[idx % len(offers)].copy()]
    start = idx % len(offers)
    count = 2 if bucket == "daily_specials" else 1
    result = []
    for offset in range(count):
        result.append(offers[(start + offset) % len(offers)].copy())
    return result


def _shop_copper_extra_fee(total_price: int) -> int:
    sink_cfg = config.get_nested("balance", "shop_sink", default={}) or {}
    pct = float(sink_cfg.get("copper_to_vendors_pct", 0.15))
    return max(0, int(round(int(total_price) * pct)))


def calculate_shop_price(price: int, currency: str, quantity: int = 1) -> Dict[str, int]:
    base_total = int(price) * max(1, int(quantity or 1))
    if currency == "copper":
        extra_fee = _shop_copper_extra_fee(base_total)
    else:
        extra_fee = 0
    return {
        "base_total": base_total,
        "extra_fee": extra_fee,
        "actual_total": base_total + extra_fee,
    }


def _enrich_shop_price(item: Dict[str, Any]) -> Dict[str, Any]:
    price = int(item.get("price", 0) or 0)
    currency = item.get("currency", "copper")
    pricing = calculate_shop_price(price, currency, 1)
    item["base_price"] = pricing["base_total"]
    item["extra_fee"] = pricing["extra_fee"]
    item["actual_price"] = pricing["actual_total"]
    return item


def list_all_shop_offers(currency: str = "copper") -> List[Dict[str, Any]]:
    items = [item.copy() for item in SHOP_ITEMS.get(currency, [])]
    items.extend(_pick_rotating_offers(currency, "daily_specials"))
    items.extend(_pick_rotating_offers(currency, "weekly_rare"))
    dedup: dict[str, Dict[str, Any]] = {}
    for item in items:
        key = item["item_id"]
        if key in dedup:
            # 优先保留价格更低的版本（限时折扣）
            if int(item.get("price", 0)) < int(dedup[key].get("price", 0)):
                dedup[key] = item
        else:
            dedup[key] = item
    return list(dedup.values())


def get_shop_offer(item_id: str, currency: Optional[str] = None) -> Optional[Dict[str, Any]]:
    currencies = [currency] if currency else list(SHOP_ITEMS.keys())
    for cur in currencies:
        for item in list_all_shop_offers(cur):
            if item["item_id"] == item_id:
                enriched = item.copy()
                enriched["currency"] = cur
                return _enrich_shop_price(enriched)
    return None


def get_shop_items(currency: str = "copper") -> List[Dict]:
    """获取商店物品列表"""
    # item_type -> category 的映射，用于没有显式 category 的旧数据兜底
    _type_to_category = {
        ItemType.PILL: "pill",
        ItemType.MATERIAL: "material",
        ItemType.SKILL_BOOK: "book",
    }
    result = []
    role = SHOP_CURRENCY_ROLES.get(currency, "")
    for item in list_all_shop_offers(currency):
        enriched = item.copy()
        item_def = get_item_by_id(item["item_id"]) or {}
        if not enriched.get("name"):
            enriched["name"] = item_def.get("name") or str(item.get("item_id", "未知物品"))
        enriched["currency"] = currency
        enriched["currency_role"] = role
        if item_def.get("focus"):
            enriched["focus"] = item_def["focus"]
        if item_def.get("usage"):
            enriched["usage"] = item_def["usage"]
        if item_def.get("stage_hint"):
            enriched["stage_hint"] = item_def["stage_hint"]
        # 确保 category 字段存在
        if not enriched.get("category"):
            item_type = item_def.get("type")
            if item_type and item_type in _type_to_category:
                enriched["category"] = _type_to_category[item_type]
            elif item_def.get("effect") == "spirit_array":
                enriched["category"] = "array"
            else:
                enriched["category"] = "pill"
        enriched.setdefault("tag", "常驻货架")
        result.append(_enrich_shop_price(enriched))
    return result


def get_currency_role(currency: str) -> str:
    """获取货币定位文案"""
    return SHOP_CURRENCY_ROLES.get(currency, "")


def get_progression_stage_theme(rank: int) -> Dict[str, Any]:
    """根据境界等级返回当前阶段的玩法主题。"""
    current_rank = int(rank or 1)
    for stage in PROGRESSION_STAGE_THEMES:
        if stage["min_rank"] <= current_rank <= stage["max_rank"]:
            return stage.copy()
    return PROGRESSION_STAGE_THEMES[-1].copy()


def can_buy_item(
    item_id: str,
    user_copper: int,
    user_gold: int,
    *,
    user_rank: int = 1,
    preferred_currency: Optional[str] = None,
    quantity: int = 1,
) -> Tuple[bool, str, str]:
    """检查是否可以购买（带金铜分离策略）。"""
    user_rank = int(user_rank or 1)
    if preferred_currency:
        preferred_currency = str(preferred_currency).strip().lower()
        if preferred_currency not in SHOP_ITEMS:
            return False, preferred_currency, "货币类型无效"
    offer = get_shop_offer(item_id, preferred_currency)
    if offer:
        currency = offer["currency"]
        price = int(offer["price"])
        min_rank = int(offer.get("min_rank", 1) or 1)
        if user_rank < min_rank:
            from core.game.realms import format_realm_display
            return False, currency, f"境界不足，需达到{format_realm_display(min_rank)}才可购买"
        if currency == "gold":
            try:
                import json, os
                cfg = json.load(open(os.path.join(os.path.dirname(__file__), '..', '..', 'config.json'), 'r', encoding='utf-8'))
                gold_min_rank = int(((cfg.get('balance', {}) or {}).get('gold_policy', {}) or {}).get('min_rank_for_gold_shop', 8))
            except Exception:
                gold_min_rank = 8
            if user_rank < gold_min_rank:
                from core.game.realms import format_realm_display
                return False, "gold", f"境界不足，需达到{format_realm_display(gold_min_rank)}才可使用中品灵石商店"
            total_price = int(price) * max(1, int(quantity or 1))
            if user_gold >= total_price:
                return True, "gold", f"需要 {total_price} 中品灵石"
            return False, "gold", f"中品灵石不足，需要 {total_price}"
        pricing = calculate_shop_price(price, "copper", int(quantity or 1))
        actual_total = int(pricing.get("actual_total", price))
        if user_copper >= actual_total:
            return True, "copper", f"需要 {actual_total} 下品灵石"
        return False, "copper", f"下品灵石不足，需要 {actual_total}"
    return False, "", "物品不存在"
