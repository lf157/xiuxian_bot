"""
排行榜与战力计算
"""

from typing import Dict, Any

from core.game.items import get_progression_stage_theme


def calculate_power(user: Dict[str, Any]) -> int:
    rank = user.get("rank", 1)
    exp = user.get("exp", 0)
    atk = user.get("attack", 10)
    defense = user.get("defense", 5)
    hp = user.get("max_hp", 100)
    mp = user.get("max_mp", 50)
    power = (
        rank * 120
        + atk * 3
        + defense * 3
        + hp // 8
        + mp // 10
        + exp // 40
    )
    return int(power)


def leaderboard_entry(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": user.get("user_id"),
        "name": user.get("in_game_username", "未知修士"),
        "rank": user.get("rank", 1),
        "exp": user.get("exp", 0),
        "power": calculate_power(user),
        "dy_times": user.get("dy_times", 0),
        # Backward-compatible alias: historical "exp_growth" now explicitly means total exp.
        "exp_growth": user.get("exp", 0),
        "realm_loot": user.get("secret_loot_score", 0),
        "alchemy_output": user.get("alchemy_output_score", 0),
        "attack": user.get("attack", 0),
        "defense": user.get("defense", 0),
        "affix_score": user.get("affix_score", 0),
        "growth_7d": user.get("growth_7d", 0),
        "realm_name": user.get("realm_name"),
        "current_map": user.get("current_map"),
        "current_map_name": user.get("current_map_name"),
        "sect_name": user.get("sect_name"),
    }


def get_stage_goal(rank: int) -> Dict[str, Any]:
    """阶段目标与推荐榜单。"""
    stage = get_progression_stage_theme(rank)
    current_rank = int(rank or 1)
    if current_rank <= 9:
        mode = "exp"
        goal = "修为总量榜"
    elif current_rank <= 19:
        mode = "realm_loot"
        goal = "秘境收获榜"
    else:
        mode = "alchemy_output"
        goal = "炼丹产出榜"
    return {
        "label": stage.get("label"),
        "theme": stage.get("theme"),
        "focus": stage.get("focus"),
        "min_rank": stage.get("min_rank"),
        "max_rank": stage.get("max_rank"),
        "recommended_mode": mode,
        "goal_label": goal,
    }
