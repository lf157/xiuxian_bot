"""商店 / 物品使用路由。"""

import math
from flask import Blueprint, request, jsonify

from core.routes._helpers import error, success, log_action, parse_json_payload, resolve_actor_user_id
from core.game.items import get_shop_items, get_item_by_id
from core.services.settlement_extra import settle_shop_buy, settle_use_item, get_shop_remaining_limit
from core.database.connection import get_user_by_id
from core.services.quests_service import increment_quest

shop_bp = Blueprint("shop", __name__)


def _parse_page_params():
    """解析分页参数。返回 (page, page_size) 或 (None, None) 表示不分页。"""
    raw_page = request.args.get("page")
    raw_size = request.args.get("page_size")
    if raw_page is None and raw_size is None:
        return None, None
    try:
        page = max(1, int(raw_page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, int(raw_size or 10))
    except (TypeError, ValueError):
        page_size = 10
    return page, page_size


def _paginate(items: list, page, page_size):
    """对列表做内存分页，返回 (paged_items, pagination_meta)。"""
    if page is None:
        return items, {}
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


@shop_bp.route("/api/shop", methods=["GET"])
def shop_list():
    """获取商店物品"""
    currency = request.args.get("currency", "copper")
    user_id = request.args.get("user_id")
    if user_id:
        _, auth_error = resolve_actor_user_id({"user_id": user_id})
        if auth_error:
            return auth_error
    items = get_shop_items(currency)
    if user_id and get_user_by_id(user_id):
        increment_quest(user_id, "daily_shop")
        enriched = []
        for item in items:
            row = item.copy()
            item_id = str(row.get("item_id", "") or "")
            if not row.get("name"):
                base = get_item_by_id(item_id) or {}
                row["name"] = str(base.get("name") or item_id or "未知物品")
            row["remaining_limit"] = get_shop_remaining_limit(user_id, item["item_id"], item)
            enriched.append(row)
        items = enriched
    else:
        hydrated = []
        for item in items:
            row = item.copy()
            item_id = str(row.get("item_id", "") or "")
            if not row.get("name"):
                base = get_item_by_id(item_id) or {}
                row["name"] = str(base.get("name") or item_id or "未知物品")
            hydrated.append(row)
        items = hydrated

    page, page_size = _parse_page_params()
    items, pagination = _paginate(items, page, page_size)
    return success(items=items, currency=currency, **pagination)


@shop_bp.route("/api/shop/buy", methods=["POST"])
def shop_buy():
    """购买物品"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_id = data.get("item_id")
    quantity = data.get("quantity", 1)
    currency = data.get("currency")
    request_id = data.get("request_id")
    log_action("shop_buy", user_id=user_id, request_id=request_id, item_id=item_id, quantity=quantity)

    if not item_id:
        return error("MISSING_PARAMS", "Missing parameters", 400)

    try:
        quantity = int(quantity or 1)
    except (TypeError, ValueError):
        return error("INVALID", "Invalid quantity", 400)

    if quantity <= 0:
        return error("INVALID", "Quantity must be greater than 0", 400)

    if currency is not None:
        currency = str(currency).strip().lower()
        if currency not in ("copper", "gold", "spirit_high"):
            return error("INVALID", "Invalid currency", 400)

    resp, http_status = settle_shop_buy(
        user_id=user_id,
        item_id=item_id,
        quantity=quantity,
        currency=currency,
        request_id=request_id,
    )
    return jsonify(resp), http_status


@shop_bp.route("/api/item/use", methods=["POST"])
def use_item():
    """使用物品"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_id = data.get("item_id")
    log_action("use_item", user_id=user_id, item_id=item_id)

    if not item_id:
        return error("MISSING_PARAMS", "Missing parameters", 400)

    resp, http_status = settle_use_item(user_id=user_id, item_id=item_id)
    return jsonify(resp), http_status


@shop_bp.route("/api/admin/daily-restock", methods=["POST"])
def admin_daily_restock():
    """每日限量道具补货（内部管理接口）。

    重置每日限购计数，并返回补货结果与公告文案。
    此接口由定时任务或外部 cron 调用。
    """
    from core.services.daily_restock_service import daily_restock, get_daily_restock_announcement

    log_action("admin_daily_restock")

    try:
        result = daily_restock()
        announcement = get_daily_restock_announcement()
    except Exception as exc:
        return error("RESTOCK_FAILED", f"补货执行失败: {exc}", 500)

    return success(
        deleted_records=result["deleted_records"],
        restocked_items=result["restocked_items"],
        today_key=result["today_key"],
        announcement=announcement,
    )
