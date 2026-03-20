"""Breakthrough pity / compensation service.

Mechanics:
- On failure: increase pity counter.
- Each pity point increases next success rate by +2% (cap +20%).
- Hard pity: after N consecutive failures, guaranteed success.
- When breakthrough succeeds: pity resets to 0.

DB: users.breakthrough_pity INTEGER
"""

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List

from core.config import config


DEFAULT_PITY_BONUS_PER_POINT = 0.02
DEFAULT_PITY_BONUS_MAX = 0.20

# 硬保底默认次数表 - 按境界ID区间划分
# 可在 config.json 中通过 balance.breakthrough.hard_pity_thresholds 覆盖
DEFAULT_HARD_PITY_TABLE = {
    (1, 5):   5,     # 练气期: 5次必成
    (6, 9):   8,     # 筑基期: 8次必成
    (10, 13): 12,    # 金丹期: 12次必成
    (14, 17): 18,    # 元婴期: 18次必成
    (18, 21): 25,    # 化神期: 25次必成
    (22, 25): 35,    # 炼虚期: 35次必成
    (26, 29): 50,    # 合体期: 50次必成
    (30, 31): 80,    # 大乘/渡劫: 80次必成
}

DEFAULT_HARD_PITY = 100


def _parse_realm_range(raw_key: Any) -> Optional[Tuple[int, int]]:
    if isinstance(raw_key, str):
        text = raw_key.strip()
        if not text:
            return None
        if "-" in text:
            left, right = text.split("-", 1)
        else:
            left, right = text, text
    elif isinstance(raw_key, (list, tuple)) and len(raw_key) == 2:
        left, right = raw_key[0], raw_key[1]
    else:
        return None

    try:
        low = int(left)
        high = int(right)
    except (TypeError, ValueError):
        return None
    if low > high:
        low, high = high, low
    return low, high


def _to_positive_int(value: Any) -> Optional[int]:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _to_non_negative_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def _load_pity_bonus_per_point() -> float:
    configured = config.get_nested(
        "balance", "breakthrough", "pity_bonus_per_point", default=DEFAULT_PITY_BONUS_PER_POINT
    )
    parsed = _to_non_negative_float(configured)
    return parsed if parsed is not None else DEFAULT_PITY_BONUS_PER_POINT


def _load_pity_bonus_cap() -> float:
    configured = config.get_nested(
        "balance", "breakthrough", "pity_bonus_cap", default=DEFAULT_PITY_BONUS_MAX
    )
    parsed = _to_non_negative_float(configured)
    return parsed if parsed is not None else DEFAULT_PITY_BONUS_MAX


def _load_hard_pity_table() -> List[Tuple[Tuple[int, int], int]]:
    raw = config.get_nested("balance", "breakthrough", "hard_pity_thresholds", default={})
    rows: List[Tuple[Tuple[int, int], int]] = []
    if isinstance(raw, dict):
        for raw_range, raw_threshold in raw.items():
            parsed_range = _parse_realm_range(raw_range)
            parsed_threshold = _to_positive_int(raw_threshold)
            if not parsed_range or parsed_threshold is None:
                continue
            rows.append((parsed_range, parsed_threshold))
    if rows:
        rows.sort(key=lambda item: (item[0][0], item[0][1]))
        return rows
    return list(DEFAULT_HARD_PITY_TABLE.items())


def _load_hard_pity_default() -> int:
    configured = config.get_nested("balance", "breakthrough", "hard_pity_default", default=DEFAULT_HARD_PITY)
    parsed = _to_positive_int(configured)
    return parsed if parsed is not None else DEFAULT_HARD_PITY


def get_hard_pity_threshold(realm_id: int) -> int:
    """获取指定境界的硬保底次数"""
    realm_id = int(realm_id or 1)
    for (low, high), threshold in _load_hard_pity_table():
        if low <= realm_id <= high:
            return threshold
    return _load_hard_pity_default()


def bonus(pity: int) -> float:
    """计算保底加成概率"""
    pity = int(pity or 0)
    if pity <= 0:
        return 0.0
    b = pity * _load_pity_bonus_per_point()
    cap = _load_pity_bonus_cap()
    if b > cap:
        b = cap
    return b


def is_hard_pity(pity: int, realm_id: int) -> bool:
    """判断是否触发硬保底（下一次必定成功）"""
    pity = int(pity or 0)
    threshold = get_hard_pity_threshold(realm_id)
    return pity >= threshold


def apply_on_failure(user: Dict[str, Any]) -> Dict[str, Any]:
    """失败时增加保底计数"""
    pity = int(user.get("breakthrough_pity", 0) or 0)
    pity += 1
    return {"breakthrough_pity": pity}


def apply_on_success(_: Dict[str, Any]) -> Dict[str, Any]:
    """成功时重置保底计数"""
    return {"breakthrough_pity": 0}
