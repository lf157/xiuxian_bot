"""
Migrating mechanics, very buggy
"""

from typing import Dict, Any, List, Union

ELEMENTS = ["木", "土", "水", "火", "金"]

# 正确的五行相克关系（A被B克）
# 传统五行: 金克木、木克土、土克水、水克火、火克金
RESTRAINED_ELEMENTS = {
    # "A": "B"     A is restrained by B (A被B克)
    "木": "金",   # 木被金克
    "金": "火",   # 金被火克
    "火": "水",   # 火被水克
    "水": "土",   # 水被土克
    "土": "木",   # 土被木克
}

# 正确的五行相生关系（A生B）
# 传统五行: 金生水、水生木、木生火、火生土、土生金
MUTUAL_ELEMENTS = {
    "金": "水",   # 金生水
    "水": "木",   # 水生木
    "木": "火",   # 木生火
    "火": "土",   # 火生土
    "土": "金",   # 土生金
}

def get_element_relationship(user_element: str, target_element: str) -> str:

    if user_element == target_element:
        return "same"
    elif RESTRAINED_ELEMENTS.get(user_element) == target_element:
        return "restrained"
    elif MUTUAL_ELEMENTS.get(user_element) == target_element:
        return "mutual"
    else:
        return "neutral"

def get_element_multipliers(user_element: str, daily_element: str) -> Dict[str, Union[float, int]]:

    relationship = get_element_relationship(user_element, daily_element)
    
    if relationship == "same":
        return {
            "cul": 1.5,
            "hunt_copper": 2.0,
            "hunt_gold": 2.0,
            "hunt_cultivation": 1.0,
            "asc_fail": 8
        }
    elif relationship == "restrained":
        return {
            "cul": 0.60,
            "hunt_copper": 0.65,
            "hunt_gold": 1.0,
            "hunt_cultivation": 0.60,
            "asc_fail": 18
        }
    elif relationship == "mutual":
        return {
            "cul": 2.5,
            "hunt_copper": 1.8,
            "hunt_gold": 1.5,
            "hunt_cultivation": 2.5,
            "asc_fail": 6
        }
    else:
        return {
            "cul": 1.0,
            "hunt_copper": 1.0,
            "hunt_gold": 1.0,
            "hunt_cultivation": 1.0,
            "asc_fail": 10
        }

def get_element_description(element: str) -> Dict[str, str]:
 
    descriptions = {
        "木": {
            "name_en": "Wood",
            "name_zh": "木",
            "description_en": "Wood element: High HP regen and lifesteal, thrives in prolonged battles.",
            "description_zh": "木灵根：生命恢复+25%，基础吸血+2%。擅长持久战，以生生不息克敌制胜。"
        },
        "土": {
            "name_en": "Earth",
            "name_zh": "土",
            "description_en": "Earth element: Solid defense and high HP, an immovable fortress.",
            "description_zh": "土灵根：生命+15%，防御+10%。厚土护身，稳如磐石，适合以守为攻。"
        },
        "水": {
            "name_en": "Water",
            "name_zh": "水",
            "description_en": "Water element: High dodge rate and skill damage, elusive and deadly.",
            "description_zh": "水灵根：闪避+8%，技能伤害+6%，防御+5%。身法飘逸，以巧破力。"
        },
        "火": {
            "name_en": "Fire",
            "name_zh": "火",
            "description_en": "Fire element: Highest attack and skill damage, a glass cannon.",
            "description_zh": "火灵根：攻击+20%，技能伤害+10%。一力破万法，攻势如烈焰燎原。"
        },
        "金": {
            "name_en": "Metal",
            "name_zh": "金",
            "description_en": "Metal element: High crit rate with balanced offense.",
            "description_zh": "金灵根：攻击+15%，暴击率+5%。锋锐无匹，一击致命。"
        }
    }
    
    return descriptions.get(element, {
        "name_en": "Unknown",
        "name_zh": "未知",
        "description_en": "ERROR",
        "description_zh": "ERROR"
    })
