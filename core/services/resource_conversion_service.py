"""Resource conversion service layer."""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from core.database.connection import (
    fetch_one,
    get_user_by_id,
    db_transaction,
    refresh_user_stamina,
    spend_user_stamina_tx,
)
from core.utils.number import format_stamina_value
from core.database.migrations import save_response, reserve_request
from core.game.items import get_item_by_id, generate_material
from core.game.resource_conversion import get_resource_conversion_config, resolve_target_config
from core.services.metrics_service import log_event, log_economy_ledger


def _sum_material(user_id: str, item_id: str) -> int:
    row = fetch_one(
        "SELECT SUM(quantity) AS qty FROM items WHERE user_id = ? AND item_id = ? AND item_type = 'material'",
        (user_id, item_id),
    )
    return int(row.get("qty", 0) or 0) if row else 0


def _deduct_material(cur, user_id: str, item_id: str, quantity: int) -> None:
    rows = cur.execute(
        "SELECT id, quantity FROM items WHERE user_id = ? AND item_id = ? AND item_type = 'material' ORDER BY id ASC",
        (user_id, item_id),
    ).fetchall()
    remaining = int(quantity)
    for row in rows:
        if remaining <= 0:
            break
        have = int(row["quantity"] or 0)
        if have <= 0:
            continue
        if have <= remaining:
            cur.execute(
                "DELETE FROM items WHERE id = ? AND user_id = ? AND item_id = ? AND item_type = 'material' AND quantity = ?",
                (row["id"], user_id, item_id, have),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("INSUFFICIENT_MATERIAL")
            remaining -= have
        else:
            cur.execute(
                "UPDATE items SET quantity = quantity - ? WHERE id = ? AND user_id = ? AND item_id = ? AND item_type = 'material' AND quantity >= ?",
                (remaining, row["id"], user_id, item_id, remaining),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("INSUFFICIENT_MATERIAL")
            remaining = 0
    if remaining > 0:
        raise ValueError("INSUFFICIENT_MATERIAL")


def list_conversion_options(user_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404

    rank = int(user.get("rank", 1) or 1)
    try:
        cfg = get_resource_conversion_config()
    except ValueError as exc:
        return {"success": False, "code": "CONFIG", "message": f"转化配置错误：{exc}"}, 500
    routes = cfg["routes"]
    targets = []
    configured_target_count = 0
    next_unlock_rank: int | None = None
    for row in cfg["targets"]:
        item_id = row.get("item_id")
        item_def = get_item_by_id(item_id)
        if not item_def:
            continue
        configured_target_count += 1
        min_rank = int(row.get("min_rank", 1) or 1)
        if rank < min_rank:
            if next_unlock_rank is None or min_rank < next_unlock_rank:
                next_unlock_rank = min_rank
            continue
        targets.append({
            "item_id": item_id,
            "name": item_def.get("name", item_id),
            "focus": item_def.get("focus"),
            "usage": item_def.get("usage"),
            "stage_hint": item_def.get("stage_hint"),
            "min_rank": min_rank,
            "base_copper": int(row.get("base_copper", item_def.get("price", 100))),
            "focused_catalyst": cfg["focused_catalyst"].get(item_id),
        })

    return {
        "success": True,
        "rank": rank,
        "routes": routes,
        "targets": targets,
        "configured_target_count": configured_target_count,
        "next_unlock_rank": next_unlock_rank,
        "max_batch": cfg["max_batch"],
        "focused_catalyst_per_batch": cfg["focused_catalyst_per_batch"],
        "stamina_batch_size": int(cfg.get("stamina_batch_size", 5)),
    }, 200


def convert_resources(
    *,
    user_id: str,
    target_item_id: str,
    quantity: int,
    route: str,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="resource_convert")
        if status == "cached" and cached:
            return cached, 200
        if status == "in_progress":
            return {
                "success": False,
                "code": "REQUEST_IN_PROGRESS",
                "message": "请求处理中，请稍后重试",
            }, 409

    def _dedup_return(resp: Dict[str, Any], http_status: int) -> Tuple[Dict[str, Any], int]:
        if request_id:
            save_response(request_id, user_id, "resource_convert", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)

    try:
        cfg = get_resource_conversion_config()
    except ValueError as exc:
        return _dedup_return({"success": False, "code": "CONFIG", "message": f"转化配置错误：{exc}"}, 500)
    route = (route or "steady").strip().lower()
    if route not in cfg["routes"]:
        return _dedup_return({"success": False, "code": "INVALID", "message": "无效的转化路线"}, 400)

    target_cfg = resolve_target_config(cfg["targets"], target_item_id)
    if not target_cfg:
        return _dedup_return({"success": False, "code": "INVALID", "message": "目标资源不可转化"}, 400)

    item_def = get_item_by_id(target_item_id)
    if not item_def:
        return _dedup_return({"success": False, "code": "INVALID", "message": "目标资源不存在"}, 400)
    if getattr(item_def.get("type"), "value", item_def.get("type")) != "material":
        return _dedup_return({"success": False, "code": "INVALID", "message": "只能转化为材料"}, 400)

    rank = int(user.get("rank", 1) or 1)
    min_rank = int(target_cfg.get("min_rank", 1) or 1)
    if rank < min_rank:
        from core.game.realms import format_realm_display
        return _dedup_return({"success": False, "code": "FORBIDDEN", "message": f"境界不足，需要{format_realm_display(min_rank)}"}, 400)

    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return _dedup_return({"success": False, "code": "INVALID", "message": "转化数量必须为正整数"}, 400)
    if qty <= 0:
        return _dedup_return({"success": False, "code": "INVALID", "message": "转化数量必须大于0"}, 400)
    max_batch = int(cfg.get("max_batch", 20))
    if qty > max_batch:
        return _dedup_return(
            {"success": False, "code": "INVALID", "message": f"单次最多只能转化 {max_batch} 个"},
            400,
        )
    stamina_batch_size = int(cfg.get("stamina_batch_size", 5))
    stamina_cost = max(1, int(math.ceil(qty / max(1, stamina_batch_size))))

    base_copper = int(target_cfg.get("base_copper", item_def.get("price", 100)))
    route_cfg = cfg["routes"][route]
    cost_copper = int(math.ceil(base_copper * qty * float(route_cfg.get("cost_mult", 1.0))))

    if int(user.get("copper", 0) or 0) < cost_copper:
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {cost_copper} 下品灵石"}, 400)

    catalyst_item_id = None
    catalyst_need = 0
    if route_cfg.get("requires_catalyst"):
        catalyst_item_id = cfg["focused_catalyst"].get(target_item_id)
        if not catalyst_item_id:
            return _dedup_return({"success": False, "code": "INVALID", "message": "该资源不支持专精转化"}, 400)
        catalyst_need = int(cfg.get("focused_catalyst_per_batch", 1)) * qty
        have = _sum_material(user_id, catalyst_item_id)
        if have < catalyst_need:
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_MATERIAL",
                "message": f"专精材料不足，需要 {catalyst_need} 个 {catalyst_item_id}",
                "material": {"item_id": catalyst_item_id, "need": catalyst_need, "have": have},
            }, 400)
    success_rate = float(route_cfg.get("success_rate", 1.0) or 1.0)
    success = random.random() <= success_rate
    output_mult = float(route_cfg.get("output_mult", 1.0) or 1.0)
    fail_mult = float(route_cfg.get("fail_output_mult", 1.0) or 1.0)
    if success:
        output_qty = int(math.ceil(qty * output_mult))
    else:
        output_qty = int(math.floor(qty * fail_mult))
        if qty <= 3:
            output_qty = max(1, output_qty)
    output_qty = max(0, output_qty)

    now = int(time.time())
    stamina_user = refresh_user_stamina(user_id, now=now)
    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, stamina_cost, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            cur.execute(
                "UPDATE users SET copper = copper - ? WHERE user_id = ? AND copper >= ?",
                (cost_copper, user_id, cost_copper),
            )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")
            if catalyst_item_id and catalyst_need > 0:
                _deduct_material(cur, user_id, catalyst_item_id, catalyst_need)
            if output_qty > 0:
                product = generate_material(target_item_id, output_qty)
                cur.execute(
                    """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                       quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                       first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        product.get("item_id"),
                        product.get("item_name"),
                        product.get("item_type"),
                        product.get("quality", "common"),
                        product.get("quantity", 1),
                        product.get("level", 1),
                        product.get("attack_bonus", 0),
                        product.get("defense_bonus", 0),
                        product.get("hp_bonus", 0),
                        product.get("mp_bonus", 0),
                        product.get("first_round_reduction_pct", 0),
                        product.get("crit_heal_pct", 0),
                        product.get("element_damage_pct", 0),
                        product.get("low_hp_shield_pct", 0),
                    ),
                )
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            current = get_user_by_id(user_id) or stamina_user or user
            log_event(
                "resource_convert",
                user_id=user_id,
                success=False,
                request_id=request_id,
                rank=rank,
                reason="INSUFFICIENT_STAMINA",
                meta={"target_item_id": target_item_id, "quantity": qty, "route": route},
            )
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": f"精力不足，资源转化需要 {stamina_cost} 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": stamina_cost,
            }, 400)
        if reason == "INSUFFICIENT_MATERIAL":
            have = _sum_material(user_id, catalyst_item_id) if catalyst_item_id else 0
            log_event(
                "resource_convert",
                user_id=user_id,
                success=False,
                request_id=request_id,
                rank=rank,
                reason="INSUFFICIENT_MATERIAL",
                meta={"target_item_id": target_item_id, "quantity": qty, "route": route},
            )
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_MATERIAL",
                "message": f"专精材料不足，需要 {catalyst_need} 个 {catalyst_item_id}",
                "material": {"item_id": catalyst_item_id, "need": catalyst_need, "have": have},
            }, 400)
        log_event(
            "resource_convert",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="INSUFFICIENT",
            meta={"target_item_id": target_item_id, "quantity": qty, "route": route},
        )
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {cost_copper} 下品灵石"}, 400)

    if output_qty > 0:
        try:
            from core.services.codex_service import ensure_item
            ensure_item(user_id, target_item_id, output_qty)
        except Exception:
            pass

    route_name = route_cfg.get("name", route)
    message = f"{route_name}完成"
    if route == "risky":
        message = f"{route_name}成功，收益放大" if success else f"{route_name}失利，收益受损"
    elif route == "focused":
        message = f"{route_name}完成，路线效率提升"
    resp = {
        "success": True,
        "convert_success": success,
        "route": route,
        "route_name": route_name,
        "message": message,
        "target_item_id": target_item_id,
        "target_name": item_def.get("name", target_item_id),
        "quantity": qty,
        "cost_copper": cost_copper,
        "output_quantity": output_qty,
        "catalyst": {"item_id": catalyst_item_id, "used": catalyst_need} if catalyst_item_id else None,
        "timestamp": now,
        "stamina_cost": stamina_cost,
    }
    log_event(
        "resource_convert",
        user_id=user_id,
        success=success,
        request_id=request_id,
        rank=rank,
        meta={"target_item_id": target_item_id, "quantity": qty, "route": route, "success": success},
    )
    log_economy_ledger(
        user_id=user_id,
        module="resource_convert",
        action="resource_convert",
        delta_copper=-cost_copper,
        delta_stamina=-stamina_cost,
        currency="copper",
        item_id=target_item_id,
        qty=output_qty,
        success=success,
        request_id=request_id,
        rank=rank,
        meta={"route": route, "success": success, "catalyst_item_id": catalyst_item_id, "catalyst_used": catalyst_need},
    )
    return _dedup_return(resp, 200)
