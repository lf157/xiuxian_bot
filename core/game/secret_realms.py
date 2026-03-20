"""
秘境系统 - Mystic Realm / Dungeon System
"""

from typing import Dict, Any, List, Optional
import random
from core.config import config

SECRET_REALMS = [
    {
        "id": "mist_forest",
        "name": "迷雾森林",
        "min_rank": 1,
        "cost_stamina": 1,
        "rewards": {"exp": (40, 90), "copper": (20, 60), "drops": ["herb", "iron_ore"]},
        "monster_pool": ["giant_snake", "spirit_rat", "wolf"],
        "flavor": "林中灵气飘散，偶有低阶妖兽潜伏。",
    },
    {
        "id": "black_rock_cave",
        "name": "黑岩洞窟",
        "min_rank": 5,
        "cost_stamina": 1,
        "rewards": {"exp": (120, 220), "copper": (80, 160), "drops": ["spirit_stone", "iron_ore", "herb"]},
        "monster_pool": ["stone_golem", "fire_sprite", "ice_demon"],
        "flavor": "洞窟深处矿脉密布，也暗藏凶险。",
    },
    {
        "id": "burning_ruins",
        "name": "焚炎遗迹",
        "min_rank": 8,
        "cost_stamina": 1,
        "rewards": {"exp": (260, 420), "copper": (150, 320), "drops": ["spirit_stone", "spirit_herb", "demon_core"]},
        "monster_pool": ["thunder_beast", "blood_dragon", "fire_sprite"],
        "flavor": "残破古迹仍有火灵游荡，机缘与危险并存。",
    },
    {
        "id": "sky_fall_palace",
        "name": "坠天古殿",
        "min_rank": 12,
        "cost_stamina": 1,
        "rewards": {"exp": (500, 900), "copper": (300, 650), "drops": ["immortal_stone", "demon_core", "dragon_scale"]},
        "monster_pool": ["phantom_spirit", "golden_lion", "blood_dragon"],
        "flavor": "古殿残阵未灭，深处常有重宝现世。",
    },
    # === 新增高等级秘境 ===
    {
        "id": "void_abyss",
        "name": "虚空深渊",
        "min_rank": 22,
        "cost_stamina": 1,
        "rewards": {"exp": (1200, 2500), "copper": (800, 1800), "drops": ["dragon_scale", "phoenix_feather", "demon_core"]},
        "monster_pool": ["heavenly_dragon", "void_phantom", "infernal_lord"],
        "flavor": "深渊之下，虚空裂缝不断涌出异界妖兽。",
    },
    {
        "id": "celestial_battlefield",
        "name": "仙界战场",
        "min_rank": 28,
        "cost_stamina": 1,
        "rewards": {"exp": (5000, 12000), "copper": (3000, 8000), "drops": ["dragon_scale", "phoenix_feather", "immortal_stone"]},
        "monster_pool": ["celestial_tiger", "divine_phoenix", "chaos_dragon"],
        "flavor": "上古仙人大战遗留的战场，处处暗藏杀机与机缘。",
    },
]

def _secret_realm_cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("secret_realm", key, default=default))
    except (TypeError, ValueError):
        return int(default)


DAILY_SECRET_REALM_LIMIT = max(1, _secret_realm_cfg_int("daily_limit", 3))

SECRET_REALM_PATHS = {
    "safe": {
        "name": "稳妥探索",
        "summary": "低强度怪更多，安全事件更多，适合稳拿基础收获。",
        "mods": {"exp_mul": 0.8, "copper_mul": 0.85, "drop_mul": 0.75},
    },
    "risky": {
        "name": "冒险探索",
        "summary": "精英怪概率更高，打赢后更容易摸到稀有材料和高阶掉落。",
        "mods": {"exp_mul": 1.25, "copper_mul": 1.05, "drop_mul": 1.45},
    },
    "loot": {
        "name": "寻宝路线",
        "summary": "战斗更少，但可能踩中陷阱或撞上守宝怪，偏素材与配方残页。",
        "mods": {"exp_mul": 0.75, "copper_mul": 0.9, "drop_mul": 1.6},
    },
    "normal": {
        "name": "普通探索",
        "summary": "平衡收益。",
        "mods": {"exp_mul": 1.0, "copper_mul": 1.0, "drop_mul": 1.0},
    },
}

SECRET_REALM_BRANCH_GRAPH = {
    "normal": ["safe", "normal", "risky", "loot"],
    "safe": ["safe", "normal", "loot"],
    "risky": ["risky", "normal", "loot"],
    "loot": ["loot", "normal", "safe"],
}


def _next_branch_path(current_path: str, encounter_type: str) -> str:
    options = SECRET_REALM_BRANCH_GRAPH.get(current_path, SECRET_REALM_BRANCH_GRAPH["normal"])
    if encounter_type in {"elite", "guardian"}:
        weighted = [p for p in options if p in {"risky", "normal"}] or options
        return random.choice(weighted)
    if encounter_type in {"trap", "treasure_event"}:
        weighted = [p for p in options if p in {"loot", "safe", "normal"}] or options
        return random.choice(weighted)
    if encounter_type in {"safe_event"}:
        weighted = [p for p in options if p in {"safe", "normal"}] or options
        return random.choice(weighted)
    return random.choice(options)


def build_secret_realm_node_chain(realm: Dict[str, Any], path: str = "normal", steps: int = 3) -> List[Dict[str, Any]]:
    total_steps = max(1, min(5, int(steps or 1)))
    current_path = path if path in SECRET_REALM_PATHS else "normal"
    nodes: List[Dict[str, Any]] = []
    for idx in range(total_steps):
        encounter = roll_secret_realm_encounter(realm, path=current_path)
        node = dict(encounter)
        node["node_index"] = idx + 1
        node["node_path"] = current_path
        nodes.append(node)
        current_path = _next_branch_path(current_path, node.get("type", "monster"))
    return nodes


def get_secret_realm_by_id(realm_id: str) -> Optional[Dict[str, Any]]:
    for realm in SECRET_REALMS:
        if realm["id"] == realm_id:
            return realm.copy()
    return None


def get_available_secret_realms(rank: int) -> List[Dict[str, Any]]:
    return [r.copy() for r in SECRET_REALMS if rank >= r["min_rank"]]


def get_secret_realm_attempts_left(user: Dict[str, Any]) -> int:
    used = int(user.get("secret_realm_attempts", 0) or 0)
    return max(0, DAILY_SECRET_REALM_LIMIT - used)


def can_explore_secret_realm(user: Dict[str, Any], realm_id: str) -> (bool, str):
    realm = get_secret_realm_by_id(realm_id)
    if not realm:
        return False, "秘境不存在"
    if user.get("rank", 1) < realm["min_rank"]:
        return False, f"需要达到境界 Lv.{realm['min_rank']} 才能进入"
    if get_secret_realm_attempts_left(user) <= 0:
        return False, "今日秘境次数已用尽"
    if user.get("state"):
        return False, "请先结束修炼"
    return True, ""


def roll_secret_realm_encounter(realm: Dict[str, Any], path: str = "normal") -> Dict[str, Any]:
    pool = list(realm.get("monster_pool") or [])
    if not pool:
        return {
            "type": "none",
            "label": "空境",
            "monster_id": None,
            "danger_scale": 1.0,
            "event_text": "此地灵机平和，你一路未遇强敌。",
        }

    low_monster = pool[0]
    mid_monster = pool[min(1, len(pool) - 1)]
    elite_monster = pool[-1]

    roll = random.random()
    if path == "safe":
        if roll < 0.35:
            return {
                "type": "safe_event",
                "label": "安稳机缘",
                "monster_id": None,
                "danger_scale": 1.0,
                "event_text": "你避开了乱流与凶物，顺手采走了一批稳妥资源。",
            }
        if roll < 0.90:
            return {
                "type": "monster",
                "label": "低强度妖兽",
                "monster_id": low_monster,
                "danger_scale": 0.88,
            }
        return {
            "type": "monster",
            "label": "寻常拦路妖兽",
            "monster_id": mid_monster,
            "danger_scale": 0.96,
        }

    if path == "risky":
        if roll < 0.65:
            return {
                "type": "elite",
                "label": "精英妖兽",
                "monster_id": elite_monster,
                "danger_scale": 1.18,
            }
        if roll < 0.90:
            return {
                "type": "elite",
                "label": "强横守关妖兽",
                "monster_id": mid_monster,
                "danger_scale": 1.08,
            }
        return {
            "type": "monster",
            "label": "凶悍妖兽",
            "monster_id": low_monster,
            "danger_scale": 1.0,
        }

    if path == "loot":
        if roll < 0.45:
            return {
                "type": "treasure_event",
                "label": "寻得藏宝点",
                "monster_id": None,
                "danger_scale": 1.0,
                "event_text": "你循着残图找到一处藏宝点，省下了一场硬仗。",
            }
        if roll < 0.70:
            return {
                "type": "trap",
                "label": "机关陷阱",
                "monster_id": None,
                "danger_scale": 1.0,
                "event_text": "你误触机关，虽未陷入苦战，却为避险耗去不少心神。",
            }
        return {
            "type": "guardian",
            "label": "守宝怪",
            "monster_id": elite_monster,
            "danger_scale": 1.10,
        }

    return {
        "type": "monster",
        "label": "普通遭遇",
        "monster_id": random.choice(pool),
        "danger_scale": 1.0,
    }


def scale_secret_realm_monster(monster: Dict[str, Any], encounter: Dict[str, Any]) -> Dict[str, Any]:
    scaled = dict(monster or {})
    scale = float(encounter.get("danger_scale", 1.0) or 1.0)
    scaled["hp"] = max(1, int(round(int(monster.get("hp", 1) or 1) * scale)))
    scaled["max_hp"] = scaled["hp"]
    scaled["attack"] = max(1, int(round(int(monster.get("attack", 1) or 1) * (0.8 + scale * 0.2))))
    scaled["defense"] = max(0, int(round(int(monster.get("defense", 0) or 0) * (0.85 + scale * 0.15))))
    return scaled


def roll_secret_realm_rewards(
    realm: Dict[str, Any],
    victory: bool = True,
    *,
    user_rank: int = 1,
    exp_mul: float = 1.0,
    copper_mul: float = 1.0,
    drop_mul: float = 1.0,
    path: str = "normal",
    encounter_type: str = "monster",
) -> Dict[str, Any]:
    """Reward model anchored to hunt_exp(rank).

    REVIEW_balance target:
      - victory: ~ hunt_exp(rank) * 3 ~ * 6 (varies by realm tier)
      - failure: 20-30% baseline; here use 25%
    """
    from core.services.balance_service import hunt_base_exp

    rank = max(1, int(user_rank or 1))
    hunt_cfg = config.get_nested("balance", "hunt", default={}) or {}
    base_hunt = hunt_base_exp(
        rank,
        base=float(hunt_cfg.get("base_exp", 20.0) or 20.0),
        growth=float(hunt_cfg.get("growth", 1.25) or 1.25),
        rank_curve=hunt_cfg.get("rank_growth_segments"),
    )

    # tier based on realm min_rank
    tier = 1
    if realm.get("min_rank", 1) >= 28:
        tier = 6
    elif realm.get("min_rank", 1) >= 22:
        tier = 5
    elif realm.get("min_rank", 1) >= 12:
        tier = 4
    elif realm.get("min_rank", 1) >= 8:
        tier = 3
    elif realm.get("min_rank", 1) >= 5:
        tier = 2

    # tier multipliers range (configurable)
    default_ranges = {
        1: (3.0, 4.5),
        2: (3.5, 5.0),
        3: (4.0, 5.5),
        4: (4.5, 6.0),
        5: (5.0, 7.0),
        6: (6.0, 8.0),
    }
    curve_cfg = config.get_nested("balance", "secret_realm_curve", default={}) or {}
    raw_ranges = curve_cfg.get("tier_ranges", {}) or {}
    ranges: Dict[int, tuple[float, float]] = {}
    for k, v in raw_ranges.items():
        try:
            ti = int(k)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                lo_v = float(v[0])
                hi_v = float(v[1])
                if lo_v > 0 and hi_v >= lo_v:
                    ranges[ti] = (lo_v, hi_v)
        except (TypeError, ValueError):
            continue
    for ti, val in default_ranges.items():
        ranges.setdefault(ti, val)
    lo, hi = ranges.get(tier, default_ranges[1])
    failure_mult = max(0.0, float(curve_cfg.get("failure_mult", 0.25) or 0.25))

    if victory:
        mult = random.uniform(lo, hi)
    else:
        mult = random.uniform(lo, hi) * failure_mult

    exp = int(base_hunt * mult * exp_mul)
    copper = int((exp * 0.6) * copper_mul)

    if encounter_type == "safe_event":
        exp = int(exp * 0.85)
        copper = int(copper * 0.95)
    elif encounter_type == "elite":
        exp = int(exp * 1.18)
        copper = int(copper * 1.05)
        drop_mul *= 1.2
    elif encounter_type == "guardian":
        exp = int(exp * 1.08)
        copper = int(copper * 0.9)
        drop_mul *= 1.35
    elif encounter_type == "trap":
        exp = int(exp * 0.45)
        copper = int(copper * 0.55)
        drop_mul *= 0.6
    elif encounter_type == "treasure_event":
        exp = int(exp * 0.65)
        copper = int(copper * 0.8)
        drop_mul *= 1.45

    found_items: list[str] = []
    drops = (realm.get("rewards", {}) or {}).get("drops") or []
    drop_chance = 0.55 * drop_mul
    if victory and drops and random.random() < min(0.97, drop_chance):
        if path == "safe":
            found_items.append(drops[0])
        elif path == "risky":
            found_items.append(drops[-1])
        elif path == "loot":
            found_items.append(random.choice(drops[: max(1, len(drops) - 1)]))
            if random.random() < 0.45:
                found_items.append("recipe_fragment")
        else:
            found_items.append(random.choice(drops))
    if victory and path == "safe" and drops:
        found_items.append(drops[0])
    if victory and path == "risky" and len(drops) > 1 and random.random() < 0.35:
        found_items.append(drops[-1])
    if victory and path == "loot" and encounter_type in {"guardian", "treasure_event"} and random.random() < 0.7:
        found_items.append("recipe_fragment")

    event_roll = random.random()
    if encounter_type == "safe_event":
        event = "你沿着稳妥路线避开了凶险地带，带回了一批安稳收获。"
    elif encounter_type == "elite":
        event = "你闯入深处，撞见了精英妖兽，胜者方能带走更珍贵的战利品。"
    elif encounter_type == "guardian":
        event = "你逼近藏宝点时惊动守宝怪，险中求宝，材料与残页更容易到手。"
    elif encounter_type == "trap":
        event = "你踩中了秘境机关，虽未陷入鏖战，但被迫耗去一部分收获来脱身。"
    elif encounter_type == "treasure_event":
        event = "你顺着残缺线索找到藏宝角落，虽无大战，却摸到了更偏素材的收获。"
    elif victory and event_roll < 0.12:
        exp = int(exp * 1.5)
        event = "你在秘境中顿悟，额外获得了大量修为。"
    elif victory and event_roll < 0.22:
        copper = int(copper * 1.5)
        event = "你发现了一处藏宝角落，收获了更多下品灵石。"
    elif event_roll > 0.94:
        exp = int(exp * 0.7)
        copper = int(copper * 0.7)
        event = "你遭遇乱流，收获打了些折扣。"
    else:
        event = "你一路谨慎探索，稳稳带回了收获。" if victory else "你在混战中受创，只勉强带回少量战利品。"

    if not victory:
        found_items = []

    # 高等级秘境中品灵石掉落
    gold = 0
    realm_id = realm.get("id", "")
    if victory:
        if realm_id == "celestial_battlefield" and random.random() < 0.15:
            gold = random.randint(2, 3)
        elif realm_id == "void_abyss" and random.random() < 0.10:
            gold = random.randint(1, 2)

    # Hard cap: prevent single-run reward runaway.
    # Ceiling = hunt_base_exp(rank) * 15 for exp, copper proportional.
    # This bounds the worst-case elite+顿悟 combo to ~15x base instead of unbounded.
    from core.services.balance_service import hunt_base_exp as _hbe
    _cap_base = _hbe(
        rank,
        base=float(hunt_cfg.get("base_exp", 20.0) or 20.0),
        growth=float(hunt_cfg.get("growth", 1.25) or 1.25),
        rank_curve=hunt_cfg.get("rank_growth_segments"),
    )
    exp_cap_mult = max(1.0, float(curve_cfg.get("exp_cap_mult", 15.0) or 15.0))
    copper_cap_ratio = max(0.1, float(curve_cfg.get("copper_cap_ratio", 0.8) or 0.8))
    EXP_CAP = int(_cap_base * exp_cap_mult)
    COPPER_CAP = int(EXP_CAP * copper_cap_ratio)
    exp = min(exp, EXP_CAP)
    copper = min(copper, COPPER_CAP)

    return {"exp": max(0, exp), "copper": max(0, copper), "gold": gold, "drop_item_ids": found_items, "event": event}


def roll_secret_realm_monster(realm: Dict[str, Any]) -> Optional[str]:
    pool = realm.get("monster_pool") or []
    if not pool:
        return None
    return random.choice(pool)


def pick_secret_realm_monster(realm: Dict[str, Any], path: str = "normal") -> Optional[str]:
    return roll_secret_realm_encounter(realm, path=path).get("monster_id")


def apply_secret_realm_modifiers(path: str) -> Dict[str, float]:
    """Return reward modifiers for a path."""
    return (SECRET_REALM_PATHS.get(path) or SECRET_REALM_PATHS["normal"]).get("mods", {}).copy()
