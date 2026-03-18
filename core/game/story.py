"""Mainline story chapter definitions."""

from __future__ import annotations

from typing import Any, Dict, List


MAINLINE_CHAPTERS: List[Dict[str, Any]] = [
    {
        "order": 1,
        "id": "prologue_three_realms",
        "title": "序章：三界归源",
        "summary": "恒道、逆道、衍道并行，凡尘只是漫长修途的起点。",
        "requirements": {},
        "rewards": {"copper": 80, "exp": 50},
    },
    {
        "order": 2,
        "id": "chapter_1_mortal_spark",
        "title": "第一章：凡尘初醒",
        "summary": "你在第一次签到后，感知到体内微弱灵机。",
        "requirements": {"signin_count": 1},
        "rewards": {"copper": 120, "exp": 80},
    },
    {
        "order": 3,
        "id": "chapter_2_qi_breath",
        "title": "第二章：引气入体",
        "summary": "第一次完整修炼，让你真正触摸到气海脉动。",
        "requirements": {"cultivate_count": 1},
        "rewards": {"copper": 180, "exp": 140},
    },
    {
        "order": 4,
        "id": "chapter_3_hunt_trial",
        "title": "第三章：试锋山野",
        "summary": "连续胜过数次狩猎后，你开始理解生死之距。",
        "requirements": {"hunt_victory_count": 3},
        "rewards": {"copper": 280, "exp": 220, "gold": 1},
    },
    {
        "order": 5,
        "id": "chapter_4_secret_trace",
        "title": "第四章：秘境回响",
        "summary": "秘境残痕指向更古老的修行传承。",
        "requirements": {"secret_realm_count": 1},
        "rewards": {"copper": 360, "exp": 280, "gold": 1},
    },
    {
        "order": 6,
        "id": "chapter_5_breakthrough",
        "title": "第五章：破境问心",
        "summary": "第一次成功破境后，道心与命数同时改写。",
        "requirements": {"breakthrough_success_count": 1},
        "rewards": {"copper": 560, "exp": 420, "gold": 2},
    },
]


def list_chapters() -> List[Dict[str, Any]]:
    return [dict(ch) for ch in MAINLINE_CHAPTERS]


def get_chapter_by_id(chapter_id: str) -> Dict[str, Any] | None:
    for chapter in MAINLINE_CHAPTERS:
        if chapter["id"] == chapter_id:
            return dict(chapter)
    return None
