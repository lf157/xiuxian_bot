"""Unified currency metadata and conversion helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.config import config
from core.game.realms import REALMS


def _economy_cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("economy", key, default=default))
    except (TypeError, ValueError):
        return int(default)


EXCHANGE_RATE = max(1, _economy_cfg_int("exchange_rate", 1000))


def _stage_min_rank(stage: str, default_rank: int = 1) -> int:
    ranks = [int(r.get("id", 0) or 0) for r in REALMS if str(r.get("stage")) == stage]
    ranks = [r for r in ranks if r > 0]
    return min(ranks) if ranks else int(default_rank)


DACHENG_MIN_RANK = _stage_min_rank("dacheng", 30)
ZHENXIAN_MIN_RANK = _stage_min_rank("zhenxian", 32)


CURRENCY_DEFINITIONS: list[Dict[str, Any]] = [
    {
        "id": "spirit_low",
        "label": "下品灵石",
        "group": "spirit",
        "tier": 1,
        "db_field": "copper",
        "hold_min_rank": 1,
        "gain_min_rank": 1,
    },
    {
        "id": "spirit_mid",
        "label": "中品灵石",
        "group": "spirit",
        "tier": 2,
        "db_field": "gold",
        "hold_min_rank": 1,
        "gain_min_rank": 1,
    },
    {
        "id": "spirit_high",
        "label": "上品灵石",
        "group": "spirit",
        "tier": 3,
        "db_field": "spirit_high",
        "hold_min_rank": 1,
        "gain_min_rank": 1,
    },
    {
        "id": "spirit_exquisite",
        "label": "精品灵石",
        "group": "spirit",
        "tier": 4,
        "db_field": "spirit_exquisite",
        "hold_min_rank": 1,
        "gain_min_rank": 1,
    },
    {
        "id": "spirit_supreme",
        "label": "极品灵石",
        "group": "spirit",
        "tier": 5,
        "db_field": "spirit_supreme",
        "hold_min_rank": 1,
        "gain_min_rank": 1,
    },
    {
        "id": "immortal_flawed",
        "label": "瑕疵仙石",
        "group": "immortal",
        "tier": 1,
        "db_field": "immortal_flawed",
        "hold_min_rank": DACHENG_MIN_RANK,
        "gain_min_rank": ZHENXIAN_MIN_RANK,
    },
    {
        "id": "immortal_low",
        "label": "下品仙石",
        "group": "immortal",
        "tier": 2,
        "db_field": "immortal_low",
        "hold_min_rank": DACHENG_MIN_RANK,
        "gain_min_rank": ZHENXIAN_MIN_RANK,
    },
    {
        "id": "immortal_mid",
        "label": "中品仙石",
        "group": "immortal",
        "tier": 3,
        "db_field": "immortal_mid",
        "hold_min_rank": DACHENG_MIN_RANK,
        "gain_min_rank": ZHENXIAN_MIN_RANK,
    },
    {
        "id": "immortal_high",
        "label": "上品仙石",
        "group": "immortal",
        "tier": 4,
        "db_field": "immortal_high",
        "hold_min_rank": DACHENG_MIN_RANK,
        "gain_min_rank": ZHENXIAN_MIN_RANK,
    },
    {
        "id": "immortal_supreme",
        "label": "极品仙石",
        "group": "immortal",
        "tier": 5,
        "db_field": "immortal_supreme",
        "hold_min_rank": DACHENG_MIN_RANK,
        "gain_min_rank": ZHENXIAN_MIN_RANK,
    },
]

_BY_ID = {row["id"]: row for row in CURRENCY_DEFINITIONS}
_GROUP_TIER_TO_ID = {(row["group"], int(row["tier"])): row["id"] for row in CURRENCY_DEFINITIONS}

_ALIASES = {
    # legacy
    "copper": "spirit_low",
    "gold": "spirit_mid",
    # ids
    "spirit_low": "spirit_low",
    "spirit_mid": "spirit_mid",
    "spirit_high": "spirit_high",
    "spirit_exquisite": "spirit_exquisite",
    "spirit_supreme": "spirit_supreme",
    "immortal_flawed": "immortal_flawed",
    "immortal_low": "immortal_low",
    "immortal_mid": "immortal_mid",
    "immortal_high": "immortal_high",
    "immortal_supreme": "immortal_supreme",
    # chinese
    "下品灵石": "spirit_low",
    "中品灵石": "spirit_mid",
    "上品灵石": "spirit_high",
    "精品灵石": "spirit_exquisite",
    "极品灵石": "spirit_supreme",
    "瑕疵仙石": "immortal_flawed",
    "下品仙石": "immortal_low",
    "中品仙石": "immortal_mid",
    "上品仙石": "immortal_high",
    "极品仙石": "immortal_supreme",
}


def normalize_currency_id(currency: Optional[str]) -> Optional[str]:
    if currency is None:
        return None
    token = str(currency).strip()
    if not token:
        return None
    lowered = token.lower()
    return _ALIASES.get(lowered) or _ALIASES.get(token)


def get_currency_definition(currency: Optional[str]) -> Optional[Dict[str, Any]]:
    cid = normalize_currency_id(currency)
    if not cid:
        return None
    row = _BY_ID.get(cid)
    return dict(row) if row else None


def currency_label(currency: Optional[str]) -> str:
    row = get_currency_definition(currency)
    if not row:
        return str(currency or "未知货币")
    return str(row.get("label") or row.get("id"))


def to_db_field(currency: Optional[str]) -> Optional[str]:
    row = get_currency_definition(currency)
    if not row:
        return None
    return str(row.get("db_field") or "")


def can_hold_currency(rank: int, currency: Optional[str]) -> bool:
    row = get_currency_definition(currency)
    if not row:
        return False
    return int(rank or 0) >= int(row.get("hold_min_rank", 1) or 1)


def can_gain_currency(rank: int, currency: Optional[str]) -> bool:
    row = get_currency_definition(currency)
    if not row:
        return False
    return int(rank or 0) >= int(row.get("gain_min_rank", 1) or 1)


def next_tier_currency(currency: Optional[str]) -> Optional[str]:
    row = get_currency_definition(currency)
    if not row:
        return None
    return _GROUP_TIER_TO_ID.get((str(row["group"]), int(row["tier"]) + 1))


def prev_tier_currency(currency: Optional[str]) -> Optional[str]:
    row = get_currency_definition(currency)
    if not row:
        return None
    return _GROUP_TIER_TO_ID.get((str(row["group"]), int(row["tier"]) - 1))


def is_adjacent_exchange(from_currency: Optional[str], to_currency: Optional[str]) -> bool:
    src = get_currency_definition(from_currency)
    dst = get_currency_definition(to_currency)
    if not src or not dst:
        return False
    if str(src["group"]) != str(dst["group"]):
        return False
    return abs(int(src["tier"]) - int(dst["tier"])) == 1


def calc_exchange_amounts(from_currency: Optional[str], to_currency: Optional[str], input_amount: int) -> Tuple[int, int]:
    """Return (spent_from, gained_to). input_amount is in from-currency units."""
    src = get_currency_definition(from_currency)
    dst = get_currency_definition(to_currency)
    if not src or not dst:
        return 0, 0
    amount = max(0, int(input_amount or 0))
    if amount <= 0:
        return 0, 0
    src_tier = int(src["tier"])
    dst_tier = int(dst["tier"])
    if dst_tier == src_tier + 1:
        gained = amount // EXCHANGE_RATE
        spent = gained * EXCHANGE_RATE
        return spent, gained
    if dst_tier == src_tier - 1:
        spent = amount
        gained = amount * EXCHANGE_RATE
        return spent, gained
    return 0, 0


def wallet_from_user(user: Dict[str, Any]) -> Dict[str, int]:
    wallet: Dict[str, int] = {}
    for row in CURRENCY_DEFINITIONS:
        cid = str(row["id"])
        field = str(row["db_field"])
        wallet[cid] = int((user or {}).get(field, 0) or 0)
    return wallet
