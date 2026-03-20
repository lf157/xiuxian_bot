"""Achievement definitions."""

from __future__ import annotations

from typing import Dict, Any, List, Optional


ACHIEVEMENTS = [
    {"id": "hunt_10", "name": "初出茅庐", "desc": "累计狩猎 10 次", "type": "hunt_count", "goal": 10,
     "rewards": {"copper": 200, "exp": 100}},
    {"id": "hunt_100", "name": "猎人", "desc": "累计狩猎 100 次", "type": "hunt_count", "goal": 100,
     "rewards": {"copper": 500, "exp": 300}},
    {"id": "hunt_300", "name": "百战不殆", "desc": "累计狩猎 300 次", "type": "hunt_count", "goal": 300,
     "rewards": {"copper": 900, "exp": 600}},
    {"id": "rank_7", "name": "筑基修士", "desc": "突破至筑基期（中期）", "type": "rank_reach", "goal": 7,
     "rewards": {"copper": 300, "exp": 200}},
    {"id": "rank_14", "name": "元婴修士", "desc": "突破至元婴期（初期）", "type": "rank_reach", "goal": 14,
     "rewards": {"copper": 800, "exp": 500, "gold": 1}},
    {"id": "rank_20", "name": "化神修士", "desc": "突破至化神期（后期）", "type": "rank_reach", "goal": 20,
     "rewards": {"copper": 1200, "exp": 900, "gold": 1}},
    {"id": "rank_26", "name": "合体修士", "desc": "突破至合体期（初期）", "type": "rank_reach", "goal": 26,
     "rewards": {"copper": 1800, "exp": 1300, "gold": 2}},
    {"id": "skills_5", "name": "初学者", "desc": "学习 5 个技能", "type": "skill_count", "goal": 5,
     "rewards": {"copper": 400, "exp": 250}},
    {"id": "skill_level_3", "name": "熟练掌握", "desc": "任意技能提升到 3 级", "type": "skill_level", "goal": 3,
     "rewards": {"copper": 600, "exp": 400}},
    {"id": "skill_level_5", "name": "炉火纯青", "desc": "任意技能提升到 5 级", "type": "skill_level", "goal": 5,
     "rewards": {"copper": 900, "exp": 700, "gold": 1}},
    {"id": "pvp_10", "name": "初战", "desc": "PVP 胜利 10 次", "type": "pvp_wins", "goal": 10,
     "rewards": {"copper": 600, "exp": 400}},
    {"id": "breakthrough_5", "name": "破境之路", "desc": "突破成功 5 次", "type": "breakthrough_success", "goal": 5,
     "rewards": {"copper": 700, "exp": 500}},
    {"id": "breakthrough_15", "name": "破境大师", "desc": "突破成功 15 次", "type": "breakthrough_success", "goal": 15,
     "rewards": {"copper": 1200, "exp": 900, "gold": 1}},
    {"id": "quests_20", "name": "勤劳", "desc": "完成 20 次每日任务", "type": "quest_complete", "goal": 20,
     "rewards": {"copper": 500, "exp": 300}},
]


def list_achievements() -> List[Dict[str, Any]]:
    return [a.copy() for a in ACHIEVEMENTS]


def get_achievement(ach_id: str) -> Optional[Dict[str, Any]]:
    for a in ACHIEVEMENTS:
        if a["id"] == ach_id:
            return a.copy()
    return None
