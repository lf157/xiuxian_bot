"""
境界系统 - Cultivation Realm System

扩展字段说明:
  stage       - 大境界标识 (lianqi/zhuji/jiedan/...)
  sub_level   - 细分等级 (1初期/2中期/3后期/4圆满, 0=特殊)
  lifespan    - 寿元描述
  tribulation - 是否触发天劫
  world_tier  - 所属世界层 (1凡界/2灵界/3真界)
  description - 简短境界描述
  cultivate_cd       - 修炼冷却秒数
  base_exp_per_session - 每次修炼基础获取修为
"""

from typing import Dict, Any, Optional, List, Tuple
import random
from core.config import config

# ─── 三道亲和常量 ───────────────────────────────────────────────
DAO_PATHS = {
    "heng": {"name": "恒道", "desc": "水滴石穿、凡人之道", "bonus": "根基扎实，突破稳定"},
    "ni":   {"name": "逆道", "desc": "逆天改命、万法归一", "bonus": "越级战斗能力强"},
    "yan":  {"name": "衍道", "desc": "星辰演化、宇宙创生", "bonus": "后期爆发力最强"},
}

# ─── 心境系统常量 ───────────────────────────────────────────────
# key = (min, max) 心境值区间（左闭右开），value = 对应效果
MENTALITY_EFFECTS = {
    (80, 100): {"break_bonus": 0.20,  "enlighten_bonus": 0.002,  "qi_deviation_rate": 0.001},
    (50, 80):  {"break_bonus": 0.10,  "enlighten_bonus": 0.001,  "qi_deviation_rate": 0.003},
    (30, 50):  {"break_bonus": 0.00,  "enlighten_bonus": 0.0005, "qi_deviation_rate": 0.005},
    (0, 30):   {"break_bonus": -0.10, "enlighten_bonus": 0.0002, "qi_deviation_rate": 0.01},
}

# ─── 境界定义（DEF/ATK 比维持在 50%-65%，防止后期防御倒挂）─────
# 原有字段 id/name/exp_required/hp/mp/attack/defense/break_rate 全部保留，新增字段见模块文档
REALMS = [
    # === 凡人 ===
    {"id": 1, "name": "凡人", "exp_required": 0, "hp": 100, "mp": 50, "attack": 10, "defense": 5, "break_rate": 1.0,
     "stage": "fanren", "sub_level": 0, "lifespan": "60-80岁", "tribulation": False, "world_tier": 1,
     "description": "尚未踏入修行之人", "cultivate_cd": 300, "base_exp_per_session": 10},
    # === 练气期 ===
    {"id": 2, "name": "练气初期", "exp_required": 100, "hp": 150, "mp": 80, "attack": 15, "defense": 8, "break_rate": 0.95,
     "stage": "lianqi", "sub_level": 1, "lifespan": "100-120岁", "tribulation": False, "world_tier": 1,
     "description": "感知灵气，初窥门径", "cultivate_cd": 300, "base_exp_per_session": 15},
    {"id": 3, "name": "练气中期", "exp_required": 300, "hp": 220, "mp": 120, "attack": 22, "defense": 12, "break_rate": 0.90,
     "stage": "lianqi", "sub_level": 2, "lifespan": "100-120岁", "tribulation": False, "world_tier": 1,
     "description": "灵气入体，经脉初通", "cultivate_cd": 300, "base_exp_per_session": 20},
    {"id": 4, "name": "练气后期", "exp_required": 600, "hp": 320, "mp": 180, "attack": 32, "defense": 18, "break_rate": 0.85,
     "stage": "lianqi", "sub_level": 3, "lifespan": "100-120岁", "tribulation": False, "world_tier": 1,
     "description": "灵气充盈，可御小术", "cultivate_cd": 300, "base_exp_per_session": 25},
    {"id": 5, "name": "练气圆满", "exp_required": 1000, "hp": 450, "mp": 260, "attack": 45, "defense": 25, "break_rate": 0.80,
     "stage": "lianqi", "sub_level": 4, "lifespan": "100-120岁", "tribulation": False, "world_tier": 1,
     "description": "练气大圆满，可尝试筑基", "cultivate_cd": 300, "base_exp_per_session": 30},
    # === 筑基期 ===
    {"id": 6, "name": "筑基初期", "exp_required": 2000, "hp": 600, "mp": 350, "attack": 60, "defense": 35, "break_rate": 0.70,
     "stage": "zhuji", "sub_level": 1, "lifespan": "150-200岁", "tribulation": False, "world_tier": 1,
     "description": "道基初成，脱胎换骨", "cultivate_cd": 600, "base_exp_per_session": 50},
    {"id": 7, "name": "筑基中期", "exp_required": 4000, "hp": 800, "mp": 480, "attack": 80, "defense": 46, "break_rate": 0.65,
     "stage": "zhuji", "sub_level": 2, "lifespan": "150-200岁", "tribulation": False, "world_tier": 1,
     "description": "根基稳固，灵力绵长", "cultivate_cd": 600, "base_exp_per_session": 65},
    {"id": 8, "name": "筑基后期", "exp_required": 7000, "hp": 1050, "mp": 640, "attack": 105, "defense": 60, "break_rate": 0.60,
     "stage": "zhuji", "sub_level": 3, "lifespan": "150-200岁", "tribulation": False, "world_tier": 1,
     "description": "灵台明澈，可驾驭法器", "cultivate_cd": 600, "base_exp_per_session": 80},
    {"id": 9, "name": "筑基圆满", "exp_required": 12000, "hp": 1350, "mp": 850, "attack": 135, "defense": 78, "break_rate": 0.55,
     "stage": "zhuji", "sub_level": 4, "lifespan": "150-200岁", "tribulation": False, "world_tier": 1,
     "description": "筑基大圆满，静待结丹", "cultivate_cd": 600, "base_exp_per_session": 100},
    # === 金丹期 ===
    {"id": 10, "name": "金丹初期", "exp_required": 20000, "hp": 1700, "mp": 1100, "attack": 170, "defense": 100, "break_rate": 0.45,
     "stage": "jiedan", "sub_level": 1, "lifespan": "300-500岁", "tribulation": False, "world_tier": 1,
     "description": "金丹初成，寿元大增", "cultivate_cd": 1200, "base_exp_per_session": 150},
    {"id": 11, "name": "金丹中期", "exp_required": 35000, "hp": 2100, "mp": 1400, "attack": 210, "defense": 125, "break_rate": 0.40,
     "stage": "jiedan", "sub_level": 2, "lifespan": "300-500岁", "tribulation": False, "world_tier": 1,
     "description": "丹力浑厚，可御剑飞行", "cultivate_cd": 1200, "base_exp_per_session": 200},
    {"id": 12, "name": "金丹后期", "exp_required": 55000, "hp": 2600, "mp": 1800, "attack": 260, "defense": 155, "break_rate": 0.35,
     "stage": "jiedan", "sub_level": 3, "lifespan": "300-500岁", "tribulation": False, "world_tier": 1,
     "description": "丹力凝实，法力精纯", "cultivate_cd": 1200, "base_exp_per_session": 260},
    {"id": 13, "name": "金丹圆满", "exp_required": 80000, "hp": 3200, "mp": 2300, "attack": 320, "defense": 190, "break_rate": 0.30,
     "stage": "jiedan", "sub_level": 4, "lifespan": "300-500岁", "tribulation": False, "world_tier": 1,
     "description": "金丹大圆满，元婴将成", "cultivate_cd": 1200, "base_exp_per_session": 330},
    # === 元婴期 ===
    {"id": 14, "name": "元婴初期", "exp_required": 120000, "hp": 3900, "mp": 3000, "attack": 400, "defense": 240, "break_rate": 0.25,
     "stage": "yuanying", "sub_level": 1, "lifespan": "800-1200岁", "tribulation": False, "world_tier": 1,
     "description": "元婴出窍，神通初显", "cultivate_cd": 1800, "base_exp_per_session": 450},
    {"id": 15, "name": "元婴中期", "exp_required": 180000, "hp": 4700, "mp": 3900, "attack": 500, "defense": 300, "break_rate": 0.20,
     "stage": "yuanying", "sub_level": 2, "lifespan": "800-1200岁", "tribulation": False, "world_tier": 1,
     "description": "元婴壮大，法力通天", "cultivate_cd": 1800, "base_exp_per_session": 600},
    {"id": 16, "name": "元婴后期", "exp_required": 260000, "hp": 5600, "mp": 5000, "attack": 620, "defense": 370, "break_rate": 0.15,
     "stage": "yuanying", "sub_level": 3, "lifespan": "800-1200岁", "tribulation": False, "world_tier": 1,
     "description": "元婴凝实，举手投足皆有威能", "cultivate_cd": 1800, "base_exp_per_session": 780},
    {"id": 17, "name": "元婴圆满", "exp_required": 380000, "hp": 6600, "mp": 6400, "attack": 760, "defense": 450, "break_rate": 0.10,
     "stage": "yuanying", "sub_level": 4, "lifespan": "800-1200岁", "tribulation": False, "world_tier": 1,
     "description": "元婴大圆满，化神在望", "cultivate_cd": 1800, "base_exp_per_session": 1000},
    # === 化神期 ===
    {"id": 18, "name": "化神初期", "exp_required": 550000, "hp": 7800, "mp": 8100, "attack": 920, "defense": 540, "break_rate": 0.08,
     "stage": "huashen", "sub_level": 1, "lifespan": "2000-3000岁", "tribulation": False, "world_tier": 2,
     "description": "神魂化形，可遨游虚空", "cultivate_cd": 3600, "base_exp_per_session": 1500},
    {"id": 19, "name": "化神中期", "exp_required": 800000, "hp": 9200, "mp": 10200, "attack": 1100, "defense": 650, "break_rate": 0.06,
     "stage": "huashen", "sub_level": 2, "lifespan": "2000-3000岁", "tribulation": False, "world_tier": 2,
     "description": "化神凝练，神通广大", "cultivate_cd": 3600, "base_exp_per_session": 2000},
    {"id": 20, "name": "化神后期", "exp_required": 1200000, "hp": 10800, "mp": 12800, "attack": 1350, "defense": 800, "break_rate": 0.04,
     "stage": "huashen", "sub_level": 3, "lifespan": "2000-3000岁", "tribulation": False, "world_tier": 2,
     "description": "化神大成，移山填海", "cultivate_cd": 3600, "base_exp_per_session": 2800},
    {"id": 21, "name": "化神圆满", "exp_required": 1800000, "hp": 12600, "mp": 16000, "attack": 1650, "defense": 980, "break_rate": 0.02,
     "stage": "huashen", "sub_level": 4, "lifespan": "2000-3000岁", "tribulation": False, "world_tier": 2,
     "description": "化神大圆满，窥探天道", "cultivate_cd": 3600, "base_exp_per_session": 3800},
    # === 炼虚期 ===
    {"id": 22, "name": "炼虚初期", "exp_required": 3000000, "hp": 15000, "mp": 20000, "attack": 2050, "defense": 1200, "break_rate": 0.01,
     "stage": "lianxu", "sub_level": 1, "lifespan": "5000-8000岁", "tribulation": False, "world_tier": 2,
     "description": "炼化虚空，初触大道", "cultivate_cd": 7200, "base_exp_per_session": 5500},
    {"id": 23, "name": "炼虚中期", "exp_required": 5000000, "hp": 18000, "mp": 25000, "attack": 2550, "defense": 1500, "break_rate": 0.008,
     "stage": "lianxu", "sub_level": 2, "lifespan": "5000-8000岁", "tribulation": False, "world_tier": 2,
     "description": "虚空凝炼，法则初悟", "cultivate_cd": 7200, "base_exp_per_session": 7500},
    {"id": 24, "name": "炼虚后期", "exp_required": 8000000, "hp": 22000, "mp": 32000, "attack": 3200, "defense": 1900, "break_rate": 0.005,
     "stage": "lianxu", "sub_level": 3, "lifespan": "5000-8000岁", "tribulation": False, "world_tier": 2,
     "description": "虚空通透，法则在握", "cultivate_cd": 7200, "base_exp_per_session": 10000},
    {"id": 25, "name": "炼虚圆满", "exp_required": 15000000, "hp": 28000, "mp": 42000, "attack": 4000, "defense": 2350, "break_rate": 0.003,
     "stage": "lianxu", "sub_level": 4, "lifespan": "5000-8000岁", "tribulation": False, "world_tier": 2,
     "description": "炼虚大圆满，合体之基已固", "cultivate_cd": 7200, "base_exp_per_session": 14000},
    # === 合体期 ===
    {"id": 26, "name": "合体初期", "exp_required": 30000000, "hp": 36000, "mp": 56000, "attack": 5200, "defense": 3050, "break_rate": 0.002,
     "stage": "heti", "sub_level": 1, "lifespan": "10000-15000岁", "tribulation": False, "world_tier": 2,
     "description": "神魂与肉身合一，超凡入圣", "cultivate_cd": 7200, "base_exp_per_session": 20000},
    {"id": 27, "name": "合体中期", "exp_required": 60000000, "hp": 48000, "mp": 75000, "attack": 6800, "defense": 4000, "break_rate": 0.001,
     "stage": "heti", "sub_level": 2, "lifespan": "10000-15000岁", "tribulation": False, "world_tier": 2,
     "description": "合体稳固，天地法相初现", "cultivate_cd": 7200, "base_exp_per_session": 28000},
    {"id": 28, "name": "合体后期", "exp_required": 120000000, "hp": 65000, "mp": 100000, "attack": 9000, "defense": 5300, "break_rate": 0.0005,
     "stage": "heti", "sub_level": 3, "lifespan": "10000-15000岁", "tribulation": False, "world_tier": 2,
     "description": "合体大成，弹指间天崩地裂", "cultivate_cd": 7200, "base_exp_per_session": 38000},
    {"id": 29, "name": "合体圆满", "exp_required": 250000000, "hp": 90000, "mp": 140000, "attack": 12000, "defense": 7000, "break_rate": 0.0003,
     "stage": "heti", "sub_level": 4, "lifespan": "10000-15000岁", "tribulation": False, "world_tier": 2,
     "description": "合体大圆满，大乘可期", "cultivate_cd": 7200, "base_exp_per_session": 50000},
    # === 大乘期 ===
    {"id": 30, "name": "大乘期", "exp_required": 500000000, "hp": 130000, "mp": 200000, "attack": 16000, "defense": 9500, "break_rate": 0.0001,
     "stage": "dacheng", "sub_level": 0, "lifespan": "20000-50000岁", "tribulation": False, "world_tier": 2,
     "description": "大道将成，半步天仙", "cultivate_cd": 7200, "base_exp_per_session": 75000},
    # === 渡劫期 ===
    {"id": 31, "name": "渡劫期", "exp_required": 1000000000, "hp": 200000, "mp": 300000, "attack": 22000, "defense": 13000, "break_rate": 0.0001,
     "stage": "dujie", "sub_level": 0, "lifespan": "50000岁+", "tribulation": True, "world_tier": 2,
     "description": "天劫降临，渡则飞仙", "cultivate_cd": 7200, "base_exp_per_session": 120000},
    # === 真仙 ===
    {"id": 32, "name": "真仙", "exp_required": None, "hp": 500000, "mp": 500000, "attack": 55000, "defense": 32000, "break_rate": 0,
     "stage": "zhenxian", "sub_level": 0, "lifespan": "不朽", "tribulation": False, "world_tier": 3,
     "description": "超脱轮回，长生不死", "cultivate_cd": 7200, "base_exp_per_session": 200000},
]

# 按 id 建索引，加速查询（避免每次线性扫描）
_REALM_INDEX: Dict[int, Dict[str, Any]] = {r["id"]: r for r in REALMS}

# 五行属性加成（均衡化：水属性增加闪避/防御，火属性移除独享突破加成）
ELEMENT_BONUSES = {
    "金": {
        "hp": -0.05, "mp": -0.05, "attack": 0.15, "defense": 0,
        "crit_rate": 0.05,
        "special": "暴击率+5%"
    },
    "木": {
        "hp": 0.12, "mp": 0.05, "attack": -0.03, "defense": 0,
        "hp_regen": 0.25, "lifesteal_base": 0.02,
        "special": "生命恢复+25%, 基础吸血+2%"
    },
    "水": {
        "hp": 0.05, "mp": 0.10, "attack": 0.00, "defense": 0.05,
        "dodge_rate": 0.08, "skill_damage": 0.06,
        "special": "闪避率+8%, 技能伤害+6%, 防御+5%"
    },
    "火": {
        "hp": -0.10, "mp": 0.05, "attack": 0.20, "defense": 0,
        "skill_damage": 0.10,
        "special": "技能伤害+10%"
    },
    "土": {
        "hp": 0.15, "mp": -0.10, "attack": 0.05, "defense": 0.10,
        "special": "防御+10%"
    },
}


def get_realm_by_id(realm_id: int) -> Optional[Dict[str, Any]]:
    """根据ID获取境界信息"""
    return _REALM_INDEX.get(realm_id)


def get_realm_by_exp(exp: int) -> Dict[str, Any]:
    """根据修为获取当前境界"""
    current_realm = REALMS[0]
    for realm in REALMS:
        if realm["exp_required"] is not None and exp >= realm["exp_required"]:
            current_realm = realm
        else:
            break
    return current_realm


def get_next_realm(realm_id: int) -> Optional[Dict[str, Any]]:
    """获取下一个境界"""
    if realm_id >= len(REALMS):
        return None
    return get_realm_by_id(realm_id + 1)


def can_breakthrough(exp: int, current_realm_id: int) -> bool:
    """检查是否可以突破"""
    next_realm = get_next_realm(current_realm_id)
    if next_realm is None:
        return False
    return exp >= next_realm["exp_required"]


def calculate_breakthrough_cost(realm_id: int) -> int:
    """计算突破消耗的灵石"""
    return realm_id * 100


def attempt_breakthrough(
    user_data: Dict[str, Any],
    use_pill: bool = False,
    extra_bonus: float = 0.0,
    forced_success_rate: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    尝试突破
    返回: (是否成功, 消息)

    金丹期及以上需要特定突破材料（全局限量物品）。
    材料检查由调用方在调用前完成，本函数通过 user_data["has_break_material"] 判断。
    """
    realm_id = user_data.get("rank", 1)
    realm = get_realm_by_id(realm_id)
    next_realm = get_next_realm(realm_id)

    if next_realm is None:
        return False, "你已达到最高境界！"

    # 允许调用方直接传入最终成功率，确保"展示成功率"与"实际判定"完全一致。
    # 此时所有前置校验（修为、材料、灵石等）由调用方负责，此处只做概率判定。
    if forced_success_rate is not None:
        success_rate = max(0.0, min(1.0, float(forced_success_rate)))
        if success_rate >= 1.0:
            return True, f"恭喜！突破成功，进入【{next_realm['name']}】境界！"
        success = random.random() < success_rate
        if success:
            return True, f"恭喜！突破成功，进入【{next_realm['name']}】境界！"
        return False, "突破失败。"

    if user_data["exp"] < next_realm["exp_required"]:
        return False, f"修为不足，需要 {next_realm['exp_required']:,} 点修为才能突破"

    # 金丹期及以上：检查突破材料
    next_stage = next_realm.get("stage", "")
    mat_req = BREAKTHROUGH_MATERIALS.get(next_stage)
    if mat_req:
        if not user_data.get("has_break_material"):
            hint = mat_req["hint_text"]
            return False, (
                f"突破至【{next_realm['name']}】需要特殊材料：{mat_req['item_name']} x{mat_req['count']}\n\n"
                f"💡 线索：{hint}"
            )

    # 计算成功率（一次性汇总后再截断，避免中间截断导致显示与实际不一致）
    success_rate = float(next_realm["break_rate"] or 0.0)
    total_bonus = 0.0

    steady_bonus = float(config.get_nested("balance", "breakthrough", "steady_bonus", default=0.10) or 0.10)
    if use_pill:
        total_bonus += steady_bonus

    if extra_bonus:
        total_bonus += float(extra_bonus)

    # 五行影响
    element = user_data.get("element")
    if element and element in ELEMENT_BONUSES:
        fire_bonus = float(config.get_nested("balance", "breakthrough", "fire_bonus", default=0.03) or 0.03)
        if element == "火":
            total_bonus += fire_bonus

    success_rate = max(0.0, min(1.0, success_rate + total_bonus))

    # 随机判定
    success = random.random() < success_rate

    if success:
        return True, f"恭喜！突破成功，进入【{next_realm['name']}】境界！"
    else:
        return False, "突破失败。"


def calculate_user_stats(user_data: Dict[str, Any]) -> Dict[str, int]:
    """计算用户完整属性（境界 + 五行加成）"""
    realm_id = user_data.get("rank", 1)
    realm = get_realm_by_id(realm_id) or REALMS[0]
    
    base_stats = {
        "max_hp": realm["hp"],
        "max_mp": realm["mp"],
        "attack": realm["attack"],
        "defense": realm["defense"],
    }
    
    # 应用五行加成
    element = user_data.get("element")
    if element and element in ELEMENT_BONUSES:
        bonus = ELEMENT_BONUSES[element]
        base_stats["max_hp"] = int(base_stats["max_hp"] * (1 + bonus.get("hp", 0)))
        base_stats["max_mp"] = int(base_stats["max_mp"] * (1 + bonus.get("mp", 0)))
        base_stats["attack"] = int(base_stats["attack"] * (1 + bonus.get("attack", 0)))
        base_stats["defense"] = int(base_stats["defense"] * (1 + bonus.get("defense", 0)))
    
    return base_stats


def format_realm_progress(user_data: Dict[str, Any]) -> str:
    """格式化境界进度显示"""
    realm_id = user_data.get("rank", 1)
    exp = user_data.get("exp", 0)
    realm = get_realm_by_id(realm_id) or REALMS[0]
    next_realm = get_next_realm(realm_id)
    
    progress = f"【{realm['name']}】"
    
    if next_realm and next_realm["exp_required"]:
        current_exp = exp - (realm["exp_required"] or 0)
        needed_exp = next_realm["exp_required"] - (realm["exp_required"] or 0)
        percent = min(100, int(current_exp / needed_exp * 100)) if needed_exp > 0 else 100
        
        # 进度条
        bar_length = 20
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        progress += f"\n{bar} {percent}%"
        progress += f"\n修为: {exp:,} / {next_realm['exp_required']:,}"
    else:
        progress += f"\n修为: {exp:,} (已满级)"
    
    return progress


def get_all_realms_summary() -> str:
    """获取所有境界概览"""
    lines = ["📖 修炼境界一览\n"]
    for i, realm in enumerate(REALMS[:15]):  # 只显示前15个
        exp_str = f"{realm['exp_required']:,}" if realm['exp_required'] else "∞"
        lines.append(f"{realm['id']:2d}. {realm['name']} - 需 {exp_str} 修为")
    if len(REALMS) > 15:
        lines.append(f"... 共 {len(REALMS)} 个境界")
    return "\n".join(lines)


# ─── 新增查询 / 计算函数 ────────────────────────────────────────

# stage 标识 → 中文名映射
_STAGE_NAMES: Dict[str, str] = {
    "fanren": "凡人", "lianqi": "练气", "zhuji": "筑基",
    "jiedan": "金丹", "yuanying": "元婴", "huashen": "化神",
    "lianxu": "炼虚", "heti": "合体", "dacheng": "大乘",
    "dujie": "渡劫", "zhenxian": "真仙",
}


def get_stage(realm_id: int) -> Optional[str]:
    """返回大境界中文名，如 '练气'、'金丹'。找不到返回 None。"""
    realm = get_realm_by_id(realm_id)
    if realm is None:
        return None
    return _STAGE_NAMES.get(realm["stage"])


# sub_level → 中文阶段名映射
_SUB_LEVEL_NAMES: Dict[int, str] = {
    0: "",       # 凡人/大乘/渡劫/真仙 等无细分
    1: "初期",
    2: "中期",
    3: "后期",
    4: "圆满",
}


def format_realm_display(realm_id: int) -> str:
    """统一境界显示格式。

    示例:
        realm_id=1  -> '凡人'
        realm_id=2  -> '练气期（初期）'
        realm_id=9  -> '筑基期（圆满）'
        realm_id=10 -> '金丹期（初期）'
        realm_id=30 -> '大乘期'
        realm_id=32 -> '真仙'

    所有 UI 层应使用此函数替代 'Lv.X' 格式。
    """
    realm = get_realm_by_id(realm_id)
    if realm is None:
        return f"未知境界"

    stage_name = _STAGE_NAMES.get(realm["stage"], "未知")
    sub = realm.get("sub_level", 0)
    sub_name = _SUB_LEVEL_NAMES.get(sub, "")

    # 凡人/真仙 等无细分的直接返回
    if not sub_name:
        # 大乘期/渡劫期 这种单阶段加"期"
        if stage_name in ("大乘", "渡劫"):
            return f"{stage_name}期"
        return stage_name

    return f"{stage_name}期（{sub_name}）"


def format_realm_rank(realm_id: int) -> str:
    """返回简短的境界标识，用于排行榜等紧凑场景。

    示例: '筑基圆满', '金丹初期', '凡人'
    """
    realm = get_realm_by_id(realm_id)
    if realm is None:
        return "未知"
    return realm.get("name", "未知")


def get_cultivate_cd(realm_id: int) -> int:
    """返回该境界的修炼冷却秒数。找不到时回退到默认 300s。"""
    realm = get_realm_by_id(realm_id)
    if realm is None:
        return 300
    return realm.get("cultivate_cd", 300)


def get_mentality_effect(mentality: float) -> Dict[str, float]:
    """
    根据心境值 (0-100) 返回效果 dict。
    包含 break_bonus / enlighten_bonus / qi_deviation_rate 三个键。
    心境值超出 0-100 会被截断到合法范围。
    """
    mentality = max(0.0, min(100.0, float(mentality)))
    for (low, high), effect in MENTALITY_EFFECTS.items():
        # 区间约定: low <= mentality < high，100 归入最高档
        if low <= mentality < high or (high == 100 and mentality == 100):
            return dict(effect)  # 返回副本，避免外部修改常量
    # 兜底：心境极低
    return {"break_bonus": -0.10, "enlighten_bonus": 0.0002, "qi_deviation_rate": 0.01}


def calc_dao_bonus(dao_heng: float, dao_ni: float, dao_yan: float,
                   realm_id: int) -> float:
    """
    计算三道亲和对突破的加成。

    设计逻辑:
      - 恒道：稳定线性加成，各境界通用
      - 逆道：低境界加成高，高境界递减（体现「逆天改命」早期优势）
      - 衍道：高境界加成陡增（体现「后期爆发」）
    亲和值范围 0-100，返回 float 加成（如 0.05 = +5%）。
    """
    # 归一化到 0-1
    h = max(0.0, min(100.0, float(dao_heng))) / 100.0
    n = max(0.0, min(100.0, float(dao_ni))) / 100.0
    y = max(0.0, min(100.0, float(dao_yan))) / 100.0

    # 用 realm_id 做阶段因子: 1~32 映射到 0~1
    stage_factor = max(0.0, min(1.0, (realm_id - 1) / 31.0))

    # 恒道: 固定 0~5% 线性加成
    bonus_heng = h * 0.05

    # 逆道: 低境界最高 8%，高境界衰减到 2%
    bonus_ni = n * (0.08 - 0.06 * stage_factor)

    # 衍道: 低境界仅 1%，高境界可达 10%
    bonus_yan = y * (0.01 + 0.09 * stage_factor)

    return round(bonus_heng + bonus_ni + bonus_yan, 6)


# ─── 游戏时间系统 ────────────────────────────────────────────
# 现实 1 小时 = 游戏 1 天, 现实 24 小时 = 游戏 1 个月(24天)
# 服务器启动时刻作为 epoch, 对应 "凡界元年正月初一"

import time as _time

# epoch: 2025-01-01 00:00:00 UTC 作为游戏时间起点
_GAME_EPOCH = 1735689600
_REAL_TO_GAME_RATIO = 24  # 1现实小时 = 24游戏小时 = 1游戏日

_WORLD_TIER_NAMES = {1: "凡界", 2: "灵界", 3: "真界", 4: "仙界",
                     5: "神界", 6: "造化", 7: "鸿蒙"}


def get_game_time(world_tier: int = 1) -> Dict[str, Any]:
    """返回当前游戏时间。

    Returns:
        {"year": 3, "month": 7, "day": 15, "season": "夏",
         "time_of_day": "正午", "display": "凡界3年·夏·七月十五·正午"}
    """
    now = _time.time()
    game_seconds = (now - _GAME_EPOCH) * _REAL_TO_GAME_RATIO
    if game_seconds < 0:
        game_seconds = 0

    game_days = int(game_seconds // 86400)
    game_hour = int((game_seconds % 86400) // 3600)

    year = game_days // 360 + 1       # 360天/年
    month = (game_days % 360) // 30 + 1  # 30天/月
    day = (game_days % 30) + 1

    # 季节
    if month in (1, 2, 3):
        season = "春"
    elif month in (4, 5, 6):
        season = "夏"
    elif month in (7, 8, 9):
        season = "秋"
    else:
        season = "冬"

    # 时辰
    if 5 <= game_hour < 8:
        tod = "清晨"
    elif 8 <= game_hour < 12:
        tod = "上午"
    elif 12 <= game_hour < 14:
        tod = "正午"
    elif 14 <= game_hour < 18:
        tod = "午后"
    elif 18 <= game_hour < 20:
        tod = "黄昏"
    elif 20 <= game_hour < 23:
        tod = "夜晚"
    else:
        tod = "深夜"

    tier_name = _WORLD_TIER_NAMES.get(world_tier, "凡界")
    display = f"{tier_name}{year}年·{season}·{_cn_month(month)}月{_cn_day(day)}·{tod}"

    return {
        "year": year, "month": month, "day": day,
        "season": season, "time_of_day": tod,
        "world_tier": world_tier, "display": display,
    }


def _cn_month(m: int) -> str:
    cn = {1:"正",2:"二",3:"三",4:"四",5:"五",6:"六",
          7:"七",8:"八",9:"九",10:"十",11:"冬",12:"腊"}
    return cn.get(m, str(m))


def _cn_day(d: int) -> str:
    if d <= 10:
        cn = {1:"初一",2:"初二",3:"初三",4:"初四",5:"初五",
              6:"初六",7:"初七",8:"初八",9:"初九",10:"初十"}
        return cn.get(d, str(d))
    if d == 20:
        return "二十"
    if d == 30:
        return "三十"
    if d < 20:
        return f"十{_cn_day(d - 10).replace('初','')}"
    if d < 30:
        return f"廿{_cn_day(d - 20).replace('初','')}"
    return str(d)


# ─── 突破材料需求 ────────────────────────────────────────────
# 金丹期及以上需要天才地宝才能突破
# 材料线索从NPC/剧情中获得, 材料本身是全局限量物品

BREAKTHROUGH_MATERIALS: Dict[str, Dict[str, Any]] = {
    # stage -> {item_id, item_name, hint_npc, hint_text}
    "jiedan": {
        "item_id": "break_jiedan_pill",
        "item_name": "凝丹露",
        "count": 1,
        "hint_npc": "陈丹师",
        "hint_text": "金丹突破需要凝丹露，丹鼎阁每年只炼制有限数量。可向陈丹师打听。",
    },
    "yuanying": {
        "item_id": "break_yuanying_core",
        "item_name": "元婴结晶",
        "count": 1,
        "hint_npc": "墨无常",
        "hint_text": "元婴突破需要元婴结晶，传闻星陨秘境深处有产出。",
    },
    "huashen": {
        "item_id": "break_huashen_lotus",
        "item_name": "化神莲台",
        "count": 1,
        "hint_npc": "星璇",
        "hint_text": "化神突破需要化神莲台，需集齐三瓣莲花，仙人遗府中或有线索。",
    },
    "lianxu": {
        "item_id": "break_lianxu_void",
        "item_name": "炼虚虚晶",
        "count": 1,
        "hint_npc": "幽冥鬼帝",
        "hint_text": "炼虚突破需要虚空法则之结晶，灵界深处的时空裂隙中偶有出现。",
    },
    "heti": {
        "item_id": "break_heti_stone",
        "item_name": "合体道石",
        "count": 1,
        "hint_npc": "天道化身",
        "hint_text": "合体突破需要蕴含天地大道之力的道石，传说只有古战场秘境最深处才有。",
    },
}
