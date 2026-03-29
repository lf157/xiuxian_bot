"""Forge sink service.

Consumes copper + materials to roll a reward item.
Used as long-term sink to combat inflation.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.database.connection import (
    fetch_one,
    fetch_all,
    db_transaction,
    get_db,
    get_item_by_db_id,
    get_user_by_id,
    refresh_user_stamina,
    spend_user_stamina_tx,
)
from core.database.migrations import reserve_request, save_response
from core.game.items import get_item_by_id, generate_material, generate_pill, generate_equipment, Quality
from core.services.metrics_service import log_event, log_economy_ledger
from core.utils.number import format_stamina_value

logger = logging.getLogger("core.forge")
_FORGE_SCHEMA_READY = False
_FORGE_SCHEMA_LOCK = threading.Lock()

_QUALITY_ORDER = {
    "common": 0,
    "spirit": 1,
    "immortal": 2,
    "divine": 3,
    "holy": 4,
}


def _ensure_forge_schema() -> None:
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS codex_items (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            item_id TEXT,
            first_seen_at INTEGER,
            last_seen_at INTEGER,
            total_obtained INTEGER DEFAULT 0,
            UNIQUE(user_id, item_id)
        )
        """
    )

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = 'public'")
    user_columns = {row[0] for row in cur.fetchall()}
    for col_name, sql in [
        ("stamina", "ALTER TABLE users ADD COLUMN stamina INTEGER DEFAULT 24"),
        ("stamina_updated_at", "ALTER TABLE users ADD COLUMN stamina_updated_at INTEGER DEFAULT 0"),
    ]:
        if col_name not in user_columns:
            cur.execute(sql)

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'items' AND table_schema = 'public'")
    item_columns = {row[0] for row in cur.fetchall()}
    for col_name, sql in [
        ("mp_bonus", "ALTER TABLE items ADD COLUMN mp_bonus INTEGER DEFAULT 0"),
        ("first_round_reduction_pct", "ALTER TABLE items ADD COLUMN first_round_reduction_pct REAL DEFAULT 0"),
        ("crit_heal_pct", "ALTER TABLE items ADD COLUMN crit_heal_pct REAL DEFAULT 0"),
        ("element_damage_pct", "ALTER TABLE items ADD COLUMN element_damage_pct REAL DEFAULT 0"),
        ("low_hp_shield_pct", "ALTER TABLE items ADD COLUMN low_hp_shield_pct REAL DEFAULT 0"),
    ]:
        if col_name not in item_columns:
            cur.execute(sql)

    conn.commit()


def _ensure_forge_schema_once() -> None:
    global _FORGE_SCHEMA_READY
    if _FORGE_SCHEMA_READY:
        return
    with _FORGE_SCHEMA_LOCK:
        if _FORGE_SCHEMA_READY:
            return
        _ensure_forge_schema()
        _FORGE_SCHEMA_READY = True


def _item_display_name(item_id: str) -> str:
    raw = str(item_id or "").strip()
    aliases = {
        "ironore": "iron_ore",
        "iron-ore": "iron_ore",
        "iron ore": "iron_ore",
    }
    normalized = aliases.get(raw.lower(), raw)
    base = get_item_by_id(normalized) or {}
    name = str(base.get("name") or "").strip()
    if name:
        return name
    if raw.lower() in aliases:
        return "铁矿石"
    return normalized


def _normalize_mode(mode: str) -> str:
    value = str(mode or "normal").strip().lower()
    return value if value in {"normal", "high"} else "normal"


def _boosted_pool(pool: List[Dict[str, Any]], *, high_cfg: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    if mode != "high":
        return [dict(p) for p in pool]
    rare_bonus = max(1.0, float(high_cfg.get("rare_weight_bonus", 1.8) or 1.8))
    focus_ids = {str(x) for x in (high_cfg.get("focus_item_ids", []) or []) if str(x)}
    result: List[Dict[str, Any]] = []
    for p in pool:
        row = dict(p)
        weight = int(row.get("weight", 1) or 1)
        item_def = get_item_by_id(str(row.get("item_id", "") or ""))
        item_type = getattr((item_def or {}).get("type"), "value", (item_def or {}).get("type"))
        boosted = False
        if item_type in ("weapon", "armor", "accessory", "material"):
            weight = int(max(1, round(weight * rare_bonus)))
            boosted = True
        if str(row.get("item_id")) in focus_ids:
            weight = int(max(1, round(weight * rare_bonus)))
            boosted = True
        row["weight"] = max(1, weight)
        row["_high_weight_boosted"] = boosted
        result.append(row)
    return result


def _ensure_min_quality(quality: Quality, min_quality: str) -> Quality:
    target = str(min_quality or "common").strip().lower()
    current_idx = _QUALITY_ORDER.get(quality.value, 0)
    min_idx = _QUALITY_ORDER.get(target, 0)
    final_idx = max(current_idx, min_idx)
    for q in (Quality.COMMON, Quality.SPIRIT, Quality.IMMORTAL, Quality.DIVINE, Quality.HOLY):
        if _QUALITY_ORDER.get(q.value, 0) == final_idx:
            return q
    return quality


def _pick(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(int(p.get("weight", 1)) for p in pool)
    r = random.randint(1, max(1, total))
    acc = 0
    for p in pool:
        acc += int(p.get("weight", 1))
        if r <= acc:
            return p
    return pool[-1]


def _insert_item_row(cur, user_id: str, item: Dict[str, Any]) -> None:
    cur.execute(
        """INSERT INTO items (user_id, item_id, item_name, item_type, quality, quantity, level,
           attack_bonus, defense_bonus, hp_bonus, mp_bonus,
           first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            item.get("item_id"),
            item.get("item_name"),
            item.get("item_type"),
            item.get("quality", "common"),
            item.get("quantity", 1),
            item.get("level", 1),
            item.get("attack_bonus", 0),
            item.get("defense_bonus", 0),
            item.get("hp_bonus", 0),
            item.get("mp_bonus", 0),
            item.get("first_round_reduction_pct", 0),
            item.get("crit_heal_pct", 0),
            item.get("element_damage_pct", 0),
            item.get("low_hp_shield_pct", 0),
        ),
    )


def _sum_material(user_id: str, item_id: str) -> int:
    row = fetch_one(
        "SELECT SUM(quantity) AS qty FROM items WHERE user_id = ? AND item_id = ? AND item_type = 'material'",
        (user_id, item_id),
    )
    return int(row.get("qty", 0) or 0) if row else 0


def _deduct_material(cur, user_id: str, item_id: str, quantity: int) -> None:
    cur.execute(
        "SELECT id, quantity FROM items WHERE user_id = ? AND item_id = ? AND item_type = 'material' ORDER BY id ASC",
        (user_id, item_id),
    )
    rows = cur.fetchall()
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


def forge(
    *,
    user_id: str,
    cfg: Dict[str, Any],
    mode: str = "normal",
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    _ensure_forge_schema_once()
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="forge")
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
            save_response(request_id, user_id, "forge", resp)
        return resp, http_status

    if not cfg.get("enabled", True):
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="DISABLED")
        return _dedup_return({"success": False, "code": "DISABLED", "message": "锻造功能未开启"}, 400)

    mode_key = _normalize_mode(mode)
    high_cfg = cfg.get("high_invest", {}) or {}
    if mode_key == "high" and not bool(high_cfg.get("enabled", True)):
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="HIGH_MODE_DISABLED")
        return _dedup_return({"success": False, "code": "DISABLED", "message": "高投入锻造未开启"}, 400)

    base_cost = int(cfg.get("base_cost_copper", 500))
    mat_id = str(cfg.get("material_item_id", "iron_ore"))
    mat_name = _item_display_name(mat_id)
    mat_need = int(cfg.get("material_need", 8))
    if mode_key == "high":
        base_cost = max(1, int(round(base_cost * float(high_cfg.get("cost_mult", 2.5) or 2.5))))
        mat_need = max(1, int(round(mat_need * float(high_cfg.get("material_mult", 2.0) or 2.0))))
    pool = _boosted_pool(cfg.get("reward_pool") or [], high_cfg=high_cfg, mode=mode_key)
    if not pool:
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="CONFIG")
        return _dedup_return({"success": False, "code": "CONFIG", "message": "锻造奖励池未配置"}, 500)
    for entry in pool:
        entry_item_id = str(entry.get("item_id", "") or "").strip()
        if not entry_item_id or not get_item_by_id(entry_item_id):
            invalid = entry_item_id or "EMPTY_ITEM_ID"
            log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="CONFIG", meta={"invalid_item_id": invalid})
            return _dedup_return({"success": False, "code": "CONFIG", "message": f"锻造奖励池配置错误：{invalid}"}, 500)

    user = fetch_one("SELECT user_id, copper, rank FROM users WHERE user_id = ?", (user_id,))
    if not user:
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="USER_NOT_FOUND")
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)

    if int(user.get("copper", 0) or 0) < base_cost:
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {base_cost}"}, 400)

    have = _sum_material(user_id, mat_id)
    if have < mat_need:
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_MATERIAL", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({
            "success": False,
            "code": "INSUFFICIENT_MATERIAL",
            "message": f"材料不足，需要 {mat_need} 个 {mat_name}",
            "material": {"item_id": mat_id, "item_name": mat_name, "need": mat_need, "have": have},
        }, 400)

    picked = _pick(pool)
    item_id = picked.get("item_id")
    qty = int(picked.get("qty", 1))
    stamina_cost = int(high_cfg.get("stamina_cost", 2) or 2) if mode_key == "high" else 1

    base_item = get_item_by_id(item_id)
    if not base_item:
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="CONFIG", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "CONFIG", "message": f"锻造奖励池配置错误：{item_id}"}, 500)
    drop = None
    it = base_item.get("type")
    it_val = getattr(it, "value", it)
    if it_val == "material":
        if mode_key == "high":
            qty = max(1, int(round(qty * float(high_cfg.get("reward_qty_mult", 1.7) or 1.7))))
        drop = generate_material(item_id, qty)
    elif it_val == "pill":
        if mode_key == "high":
            qty = max(1, int(round(qty * float(high_cfg.get("reward_qty_mult", 1.7) or 1.7))))
        drop = generate_pill(item_id, qty)
    else:
        quality = Quality.COMMON
        if mode_key == "high":
            quality = _ensure_min_quality(quality, str(high_cfg.get("guaranteed_min_quality", "spirit")))
        drop = generate_equipment(base_item, quality, 1)
        drop["quantity"] = qty

    now = int(time.time())
    stamina_user = refresh_user_stamina(user_id, now=now)

    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, stamina_cost, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            cur.execute(
                "UPDATE users SET copper = copper - ? WHERE user_id = ? AND copper >= ?",
                (base_cost, user_id, base_cost),
            )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")

            _deduct_material(cur, user_id, mat_id, mat_need)

            if drop:
                _insert_item_row(cur, user_id, drop)
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            current = get_user_by_id(user_id) or stamina_user or user
            log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_STAMINA", rank=int(user.get("rank", 1) or 1))
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": f"精力不足，锻造需要 {stamina_cost} 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": stamina_cost,
            }, 400)
        if reason == "INSUFFICIENT_MATERIAL":
            current_have = _sum_material(user_id, mat_id)
            log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_MATERIAL", rank=int(user.get("rank", 1) or 1))
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_MATERIAL",
                "message": f"材料不足，需要 {mat_need} 个 {mat_name}",
                "material": {"item_id": mat_id, "item_name": mat_name, "need": mat_need, "have": current_have},
            }, 400)
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {base_cost}"}, 400)
    except Exception as exc:
        logger.exception(
            "forge unexpected error user_id=%s mode=%s request_id=%s",
            user_id,
            mode_key,
            request_id,
            exc_info=exc,
        )
        log_event("forge", user_id=user_id, success=False, request_id=request_id, reason="SERVER_ERROR", rank=int(user.get("rank", 1) or 1))
        return _dedup_return(
            {
                "success": False,
                "code": "FORGE_SERVER_ERROR",
                "message": "锻造服务异常，请稍后重试",
            },
            500,
        )

    if drop:
        try:
            from core.services.codex_service import ensure_item
            ensure_item(user_id, drop.get("item_id"), drop.get("quantity", 1))
        except Exception as exc:
            logger.warning(
                "ensure_item_failed user_id=%s item_id=%s error=%s",
                user_id,
                drop.get("item_id") if drop else None,
                type(exc).__name__,
            )

    log_event(
        "forge",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"item_id": drop.get("item_id") if drop else None, "quantity": drop.get("quantity", 1) if drop else 0},
    )
    log_economy_ledger(
        user_id=user_id,
        module="forge",
        action="forge" if mode_key == "normal" else "forge_high",
        delta_copper=-base_cost,
        delta_stamina=-stamina_cost,
        item_id=drop.get("item_id") if drop else None,
        qty=drop.get("quantity", 1) if drop else 0,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"material_item_id": mat_id, "material_used": mat_need, "mode": mode_key},
    )
    return _dedup_return({
        "success": True,
        "message": "锻造成功！" if mode_key == "normal" else "高投入锻造成功！",
        "mode": mode_key,
        "cost": {"copper": base_cost, "material": {"item_id": mat_id, "item_name": mat_name, "used": mat_need}},
        "stamina_cost": stamina_cost,
        "reward": drop,
    }, 200)


def forge_catalog(user_id: str) -> List[Dict[str, Any]]:
    _ensure_forge_schema_once()
    try:
        rows = fetch_all(
            "SELECT item_id, total_obtained FROM codex_items WHERE user_id = ? ORDER BY total_obtained DESC, last_seen_at DESC",
            (user_id,),
        )
    except Exception as exc:
        logger.exception("forge_catalog unexpected error user_id=%s", user_id, exc_info=exc)
        return []
    result = []
    for row in rows:
        item = get_item_by_id(row["item_id"])
        if not item:
            continue
        item_type = getattr(item.get("type"), "value", item.get("type"))
        if item_type not in ("weapon", "armor", "accessory"):
            continue
        result.append(
            {
                "item_id": row["item_id"],
                "name": item["name"],
                "min_rank": item.get("min_rank", 1),
                "obtained": int(row.get("total_obtained", 0) or 0),
            }
        )
    return result


def forge_targeted(
    *,
    user_id: str,
    item_id: str,
    cfg: Dict[str, Any],
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    _ensure_forge_schema_once()
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="forge_targeted")
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
            save_response(request_id, user_id, "forge_targeted", resp)
        return resp, http_status

    if not cfg.get("enabled", True):
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="DISABLED")
        return _dedup_return({"success": False, "code": "DISABLED", "message": "锻造功能未开启"}, 400)

    user = get_user_by_id(user_id)
    if not user:
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="USER_NOT_FOUND")
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)
    base_item = get_item_by_id(item_id)
    if not base_item:
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="NOT_FOUND")
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "目标装备不存在"}, 404)
    item_type = getattr(base_item.get("type"), "value", base_item.get("type"))
    if item_type not in ("weapon", "armor", "accessory"):
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INVALID")
        return _dedup_return({"success": False, "code": "INVALID", "message": "只能定向锻造装备"}, 400)
    if int(user.get("rank", 1) or 1) < int(base_item.get("min_rank", 1) or 1):
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="FORBIDDEN", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "FORBIDDEN", "message": "境界不足，无法定向该装备"}, 400)
    codex_row = fetch_one(
        "SELECT total_obtained FROM codex_items WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    )
    if not codex_row:
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="LOCKED", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "LOCKED", "message": "尚未收录该装备，无法定向锻造"}, 400)

    base_cost = int(cfg.get("base_cost_copper", 500))
    mat_need = int(cfg.get("material_need", 8))
    target_cost = base_cost * 2
    target_mat_need = mat_need * 2
    mat_id = str(cfg.get("material_item_id", "iron_ore"))
    mat_name = _item_display_name(mat_id)
    have = _sum_material(user_id, mat_id)
    if int(user.get("copper", 0) or 0) < target_cost:
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {target_cost}"}, 400)
    if have < target_mat_need:
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_MATERIAL", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "INSUFFICIENT_MATERIAL", "message": f"材料不足，需要 {target_mat_need} 个 {mat_name}"}, 400)

    reward = generate_equipment(base_item, Quality.SPIRIT if int(user.get("rank", 1) or 1) >= 10 else Quality.COMMON, 1)
    now = int(time.time())
    stamina_user = refresh_user_stamina(user_id, now=now)
    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, 1, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            cur.execute(
                "UPDATE users SET copper = copper - ? WHERE user_id = ? AND copper >= ?",
                (target_cost, user_id, target_cost),
            )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")
            _deduct_material(cur, user_id, mat_id, target_mat_need)
            _insert_item_row(cur, user_id, reward)
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            current = get_user_by_id(user_id) or stamina_user or user
            log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_STAMINA", rank=int(user.get("rank", 1) or 1))
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": "精力不足，定向锻造需要 1 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": 1,
            }, 400)
        if reason == "INSUFFICIENT_MATERIAL":
            log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT_MATERIAL", rank=int(user.get("rank", 1) or 1))
            return _dedup_return({"success": False, "code": "INSUFFICIENT_MATERIAL", "message": f"材料不足，需要 {target_mat_need} 个 {mat_name}"}, 400)
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="INSUFFICIENT", rank=int(user.get("rank", 1) or 1))
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": f"下品灵石不足，需要 {target_cost}"}, 400)
    except Exception as exc:
        logger.exception(
            "forge_targeted unexpected error user_id=%s item_id=%s request_id=%s",
            user_id,
            item_id,
            request_id,
            exc_info=exc,
        )
        log_event("forge_targeted", user_id=user_id, success=False, request_id=request_id, reason="SERVER_ERROR", rank=int(user.get("rank", 1) or 1))
        return _dedup_return(
            {
                "success": False,
                "code": "FORGE_TARGETED_SERVER_ERROR",
                "message": "定向锻造服务异常，请稍后重试",
            },
            500,
        )
    try:
        from core.services.codex_service import ensure_item
        ensure_item(user_id, reward["item_id"], 1)
    except Exception as exc:
        logger.warning(
            "ensure_item_failed user_id=%s item_id=%s error=%s",
            user_id,
            reward.get("item_id"),
            type(exc).__name__,
        )
    log_event(
        "forge_targeted",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"item_id": reward.get("item_id")},
    )
    log_economy_ledger(
        user_id=user_id,
        module="forge",
        action="forge_targeted",
        delta_copper=-target_cost,
        delta_stamina=-1,
        item_id=reward.get("item_id"),
        qty=1,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"material_item_id": mat_id, "material_used": target_mat_need},
    )
    return _dedup_return({
        "success": True,
        "message": f"定向锻造成功：{reward['item_name']}",
        "reward": reward,
        "cost": {"copper": target_cost, "material": {"item_id": mat_id, "item_name": mat_name, "used": target_mat_need}},
    }, 200)


def decompose_item(*, user_id: str, item_db_id: int) -> Tuple[Dict[str, Any], int]:
    _ensure_forge_schema_once()
    item = get_item_by_db_id(item_db_id)
    if not item or str(item.get("user_id")) != str(user_id):
        log_event("decompose", user_id=user_id, success=False, reason="NOT_FOUND")
        return {"success": False, "code": "NOT_FOUND", "message": "装备不存在"}, 404
    if item.get("item_type") not in ("weapon", "armor", "accessory"):
        log_event("decompose", user_id=user_id, success=False, reason="INVALID")
        return {"success": False, "code": "INVALID", "message": "只能分解装备"}, 400
    user = get_user_by_id(user_id)
    if not user:
        log_event("decompose", user_id=user_id, success=False, reason="USER_NOT_FOUND")
        return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404
    for slot in ("equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2"):
        if str(user.get(slot)) == str(item_db_id):
            log_event("decompose", user_id=user_id, success=False, reason="EQUIPPED", rank=int(user.get("rank", 1) or 1))
            return {"success": False, "code": "EQUIPPED", "message": "请先卸下装备再分解"}, 400

    base = get_item_by_id(item.get("item_id")) or {}
    min_rank = int(base.get("min_rank", 1) or 1)
    if min_rank >= 12:
        mat_id = "immortal_stone"
    elif min_rank >= 6:
        mat_id = "spirit_stone"
    else:
        mat_id = "iron_ore"
    mat_qty = max(1, min_rank // 3 + 1)
    copper = max(40, min_rank * 25)
    salvage_mult = 1.0
    if min_rank >= 28:
        salvage_mult = 0.55
    elif min_rank >= 20:
        salvage_mult = 0.65
    elif min_rank >= 12:
        salvage_mult = 0.75
    quality = str(item.get("quality", "common"))
    quality_mult = {
        "common": 1.0,
        "spirit": 0.9,
        "immortal": 0.85,
        "divine": 0.8,
        "holy": 0.75,
    }.get(quality, 1.0)
    mat_qty = max(1, int(round(mat_qty * salvage_mult * quality_mult)))
    copper = max(20, int(round(copper * salvage_mult * quality_mult)))
    reward_mat = generate_material(mat_id, mat_qty)
    try:
        with db_transaction() as cur:
            cur.execute("DELETE FROM items WHERE id = ? AND user_id = ?", (item_db_id, user_id))
            if cur.rowcount == 0:
                raise ValueError("NOT_FOUND")
            cur.execute("UPDATE users SET copper = copper + ? WHERE user_id = ?", (copper, user_id))
            _insert_item_row(cur, user_id, reward_mat)
    except ValueError:
        log_event("decompose", user_id=user_id, success=False, reason="NOT_FOUND", rank=int(user.get("rank", 1) or 1))
        return {"success": False, "code": "NOT_FOUND", "message": "装备不存在"}, 404
    except Exception as exc:
        logger.exception("decompose unexpected error user_id=%s item_db_id=%s", user_id, item_db_id, exc_info=exc)
        log_event("decompose", user_id=user_id, success=False, reason="SERVER_ERROR", rank=int(user.get("rank", 1) or 1))
        return {"success": False, "code": "DECOMPOSE_SERVER_ERROR", "message": "分解服务异常，请稍后重试"}, 500
    try:
        from core.services.codex_service import ensure_item
        ensure_item(user_id, reward_mat.get("item_id"), reward_mat.get("quantity", 1))
    except Exception as exc:
        logger.warning(
            "ensure_item_failed user_id=%s item_id=%s error=%s",
            user_id,
            reward_mat.get("item_id") if reward_mat else None,
            type(exc).__name__,
        )
    log_event(
        "decompose",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"item_id": item.get("item_id"), "reward_mat": mat_id, "reward_qty": mat_qty},
    )
    log_economy_ledger(
        user_id=user_id,
        module="decompose",
        action="decompose",
        delta_copper=copper,
        item_id=mat_id,
        qty=mat_qty,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"item_id": item.get("item_id")},
    )
    return {"success": True, "message": f"分解成功，获得 {reward_mat['item_name']} x{mat_qty} 与 {copper} 下品灵石", "rewards": {"copper": copper, "items": [{"item_id": mat_id, "quantity": mat_qty}]}}, 200
