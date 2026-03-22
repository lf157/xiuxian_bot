"""每日限量道具补货服务。

重置 shop_purchase_limits 表中每日限购计数，并生成补货公告。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.database.connection import db_transaction, fetch_all
from core.utils.timeutil import local_day_key
from core.game.items import list_all_shop_offers, get_item_by_id, SHOP_ROTATIONS

logger = logging.getLogger("core.services.daily_restock")


def _current_day_period_key() -> str:
    """返回当前日 period_key，例如 'day:19876'。"""
    return f"day:{local_day_key()}"


def daily_restock() -> Dict[str, Any]:
    """重置每日限购计数并返回补货信息。

    做法：删除所有 period_key 以 'day:' 开头、且不是今天的记录。
    这样玩家今天的限购额度就被清空（因为旧 key 被删，而新 key 还没有记录）。

    Returns:
        dict 包含 restocked_items 列表和统计信息。
    """
    today_key = _current_day_period_key()
    deleted_count = 0

    with db_transaction() as cur:
        # 删除非今天的每日限购记录（即旧天的记录）
        cur.execute(
            "DELETE FROM shop_purchase_limits WHERE period_key LIKE 'day:%%' AND period_key != %s",
            (today_key,),
        )
        deleted_count = cur.rowcount or 0

    # 收集今日补货的限量商品列表
    restocked_items = _collect_daily_limited_items()

    logger.info("daily_restock completed: deleted %d old records, %d items restocked",
                deleted_count, len(restocked_items))

    return {
        "deleted_records": deleted_count,
        "restocked_items": restocked_items,
        "today_key": today_key,
    }


def _collect_daily_limited_items() -> List[Dict[str, Any]]:
    """收集所有每日限购商品的信息。"""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for currency in ("copper", "gold", "spirit_high"):
        rotation = SHOP_ROTATIONS.get(currency, {})
        daily_specials = rotation.get("daily_specials", [])
        for offer in daily_specials:
            item_id = offer.get("item_id", "")
            if not item_id or item_id in seen:
                continue
            if offer.get("limit_period") != "day":
                continue
            seen.add(item_id)

            item_def = get_item_by_id(item_id) or {}
            name = str(offer.get("name") or item_def.get("name") or item_id)
            limit = int(offer.get("limit", 0) or 0)
            price = int(offer.get("price", 0) or 0)

            items.append({
                "item_id": item_id,
                "name": name,
                "limit": limit,
                "price": price,
                "currency": currency,
                "tag": offer.get("tag", ""),
            })

    return items


def get_daily_restock_announcement() -> str:
    """生成每日补货公告文案。

    Returns:
        格式化的公告文本。
    """
    items = _collect_daily_limited_items()
    if not items:
        return "今日商店暂无限量补货商品。"

    currency_labels = {
        "copper": "铜币",
        "gold": "金币",
        "spirit_high": "上品灵石",
    }

    lines: List[str] = []
    lines.append("--- 每日商店补货公告 ---")
    lines.append("")
    lines.append("以下限量商品已补货，先到先得：")
    lines.append("")

    # 按货币分组
    by_currency: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        cur = item["currency"]
        by_currency.setdefault(cur, []).append(item)

    for currency, group in by_currency.items():
        label = currency_labels.get(currency, currency)
        lines.append(f"[{label}商店]")
        for item in group:
            tag_str = f"({item['tag']}) " if item.get("tag") else ""
            lines.append(
                f"  {tag_str}{item['name']} x{item['limit']}  - {item['price']}{label}/个"
            )
        lines.append("")

    lines.append("祝各位道友修行顺利！")
    return "\n".join(lines)
