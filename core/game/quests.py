"""
每日任务系统 - Daily Quest System
"""

from __future__ import annotations

from typing import Dict, Any, List

# Quest definitions: each quest has a fixed id, name, description, goal, and reward.
DAILY_QUESTS = [
    {
        "id": "daily_signin",
        "name": "日常签到",
        "desc": "完成每日签到",
        "goal": 1,
        "rewards": {"copper": 80, "exp": 30},
    },
    {
        "id": "daily_hunt",
        "name": "降妖除魔",
        "desc": "成功狩猎怪物 3 次",
        "goal": 3,
        "rewards": {"copper": 150, "exp": 80},
    },
    {
        "id": "daily_cultivate",
        "name": "勤修苦练",
        "desc": "完成一次修炼（开始并结束）",
        "goal": 1,
        "rewards": {"copper": 70, "exp": 60},
    },
    {
        "id": "daily_secret_realm",
        "name": "秘境探险",
        "desc": "探索秘境 1 次",
        "goal": 1,
        "rewards": {"copper": 90, "exp": 80, "gold": 1},
    },
    {
        "id": "daily_shop",
        "name": "逛逛商店",
        "desc": "进入商店 1 次",
        "goal": 1,
        "rewards": {"copper": 40, "exp": 20},
    },
]


def get_quest_def(quest_id: str) -> Dict[str, Any] | None:
    for q in DAILY_QUESTS:
        if q["id"] == quest_id:
            return q.copy()
    return None


def get_all_quest_defs() -> List[Dict[str, Any]]:
    return [q.copy() for q in DAILY_QUESTS]


def format_quest_status(quest_rows: List[Dict], quest_defs: List[Dict]) -> str:
    """Format quest list for display."""
    def_map = {q["id"]: q for q in quest_defs}
    lines = []
    all_done = True
    for row in quest_rows:
        qdef = def_map.get(row["quest_id"])
        if not qdef:
            continue
        progress = row.get("progress", 0)
        goal = row.get("goal", 1)
        claimed = row.get("claimed", 0)
        if claimed:
            status = "✅"
        elif progress >= goal:
            status = "🎁"  # ready to claim
            all_done = False
        else:
            status = f"({progress}/{goal})"
            all_done = False
        reward_parts = []
        for k, v in qdef["rewards"].items():
            if k == "copper":
                reward_parts.append(f"{v}下品灵石")
            elif k == "exp":
                reward_parts.append(f"{v}修为")
            elif k == "gold":
                reward_parts.append(f"{v}中品灵石")
        lines.append(f"{status} {qdef['name']} - {qdef['desc']}  [{' '.join(reward_parts)}]")

    if all_done and quest_rows and all(r.get("claimed") for r in quest_rows):
        lines.append("\n🎉 今日任务全部完成！")
    return "\n".join(lines)
