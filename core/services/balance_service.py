"""Balance helpers for rewards and sinks (REVIEW_balance).

Implements:
- Hunt reward formula by user rank (exp + copper)
- Hunt fatigue / diminishing returns (copper only)

Config keys (config.json):
balance:
  hunt:
    base_exp: 20
    growth: 1.25
    copper_ratio: 0.6
    copper_rand: 0.2
    fatigue_free: 10
    fatigue_step: 0.10
    fatigue_min_mult: 0.30
"""

from __future__ import annotations

import math
import random
from typing import Dict, Any, Optional


def _piecewise_growth_for_level(level: int, growth: float, rank_curve: Any) -> float:
    if not isinstance(rank_curve, list) or not rank_curve:
        return float(growth)
    for seg in rank_curve:
        if not isinstance(seg, dict):
            continue
        try:
            max_rank = int(seg.get("max_rank", 0) or 0)
            seg_growth = float(seg.get("growth", growth) or growth)
        except (TypeError, ValueError):
            continue
        if max_rank > 0 and level <= max_rank:
            return seg_growth
    try:
        tail = rank_curve[-1]
        if isinstance(tail, dict):
            return float(tail.get("growth", growth) or growth)
    except (TypeError, ValueError):
        pass
    return float(growth)


def hunt_base_exp(rank: int, base: float = 20.0, growth: float = 1.25, rank_curve: Any = None) -> int:
    rank = max(1, int(rank or 1))
    if isinstance(rank_curve, list) and rank_curve:
        exp = float(base)
        for level in range(2, rank + 1):
            exp *= _piecewise_growth_for_level(level, growth, rank_curve)
    else:
        exp = float(base) * (float(growth) ** (rank - 1))
    return max(1, int(round(exp)))


def hunt_rewards(rank: int, cfg: Dict[str, Any], monster: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    base = float(cfg.get("base_exp", 20))
    growth = float(cfg.get("growth", 1.25))
    rank_curve = cfg.get("rank_growth_segments")
    copper_ratio = float(cfg.get("copper_ratio", 0.6))
    copper_rand = float(cfg.get("copper_rand", 0.2))

    rank_exp = hunt_base_exp(rank, base=base, growth=growth, rank_curve=rank_curve)
    # Keep rank progression as baseline, but allow stronger monsters to yield
    # higher rewards than weaker ones when monster exp is configured.
    exp = int(rank_exp)
    if isinstance(monster, dict):
        try:
            monster_exp = int(monster.get("exp_reward", 0) or 0)
        except (TypeError, ValueError):
            monster_exp = 0
        if monster_exp > 0:
            exp = max(exp, monster_exp)

    copper_mean = exp * copper_ratio
    # ± rand
    low = int(round(copper_mean * (1 - copper_rand)))
    high = int(round(copper_mean * (1 + copper_rand)))
    low = max(1, low)
    high = max(low, high)
    copper = random.randint(low, high)
    # Unified currency hardening: spirit stone acquisition is intentionally tighter.
    gain_mult = float(cfg.get("currency_gain_mult", 0.25) or 0.25)
    gain_mult = max(0.05, min(1.0, gain_mult))
    copper = max(1, int(round(copper * gain_mult)))
    return {"exp": exp, "copper": copper}


def fatigue_multiplier(hunt_count_today: int, cfg: Dict[str, Any]) -> float:
    free = int(cfg.get("fatigue_free", 10))
    step = float(cfg.get("fatigue_step", 0.10))
    min_mult = float(cfg.get("fatigue_min_mult", 0.30))

    n = int(hunt_count_today or 0)
    if n <= free:
        return 1.0
    extra = n - free
    mult = 1.0 - step * extra
    if mult < min_mult:
        mult = min_mult
    return mult


def exp_fatigue_multiplier(hunt_count_today: int, cfg: Dict[str, Any]) -> float:
    """经验疲劳 - 比下品灵石疲劳更宽松但仍有上限"""
    free = int(cfg.get("exp_fatigue_start", 20))
    step = float(cfg.get("exp_fatigue_step", 0.05))
    min_mult = float(cfg.get("exp_fatigue_min_mult", 0.40))

    n = int(hunt_count_today or 0)
    if n <= free:
        return 1.0
    extra = n - free
    mult = 1.0 - step * extra
    if mult < min_mult:
        mult = min_mult
    return mult
