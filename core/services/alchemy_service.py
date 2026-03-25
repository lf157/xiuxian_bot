"""Alchemy service layer."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

from core.database.connection import (
    fetch_one,
    db_transaction,
    get_db,
    get_user_by_id,
    refresh_user_stamina,
    spend_user_stamina_tx,
)
from core.database.migrations import save_response, reserve_request
from core.game.alchemy import get_recipe, list_recipes, get_featured_recipe_ids, ALCHEMY_CATEGORY_LABELS
from core.game.items import generate_pill, get_item_by_id
from core.services.metrics_service import log_event, log_economy_ledger
from core.utils.number import format_stamina_value
from core.config import config

logger = logging.getLogger("AlchemyService")
_ALCHEMY_SCHEMA_READY = False
_ALCHEMY_SCHEMA_LOCK = threading.Lock()


def _ensure_alchemy_schema() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alchemy_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            recipe_id TEXT,
            success INTEGER,
            created_at INTEGER,
            result_item_id TEXT,
            quantity INTEGER DEFAULT 1
        )
        """
    )

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = 'public'")
    user_columns = {row[0] for row in cur.fetchall()}
    if "alchemy_output_score" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN alchemy_output_score INTEGER DEFAULT 0")

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


def _ensure_alchemy_schema_once() -> None:
    global _ALCHEMY_SCHEMA_READY
    if _ALCHEMY_SCHEMA_READY:
        return
    with _ALCHEMY_SCHEMA_LOCK:
        if _ALCHEMY_SCHEMA_READY:
            return
        _ensure_alchemy_schema()
        _ALCHEMY_SCHEMA_READY = True


def _alchemy_mastery_profile(user: Dict[str, Any], recipe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    score = max(0, int((user or {}).get("alchemy_output_score", 0) or 0))
    cfg = config.get_nested("balance", "alchemy_growth", default={}) or {}
    raw_thresholds = cfg.get("level_thresholds", [0, 800, 2500, 6000, 12000, 22000]) or [0]
    thresholds = sorted({max(0, int(x or 0)) for x in raw_thresholds})
    if not thresholds:
        thresholds = [0]
    level = 1
    for idx, threshold in enumerate(thresholds, start=1):
        if score >= threshold:
            level = idx
    next_threshold = next((t for t in thresholds if t > score), None)
    per_level = max(0.0, float(cfg.get("success_bonus_per_level", 0.015) or 0.015))
    max_success_bonus = max(0.0, float(cfg.get("max_success_bonus", 0.12) or 0.12))
    success_bonus = min(max_success_bonus, (level - 1) * per_level)
    if recipe and str(recipe.get("category") or "") == "rare":
        success_bonus += max(0.0, float(cfg.get("rare_recipe_bonus", 0.02) or 0.02))
    success_bonus = min(max_success_bonus, success_bonus)
    score_per_level = max(0.0, float(cfg.get("output_score_bonus_per_level", 0.06) or 0.06))
    output_score_multiplier = 1.0 + (level - 1) * score_per_level
    return {
        "score": score,
        "level": int(level),
        "next_level_score": next_threshold,
        "success_bonus": round(success_bonus, 4),
        "output_score_multiplier": round(output_score_multiplier, 4),
    }


def get_recipes_for_user(user_id: Optional[str] = None) -> Dict[str, Any]:
    _ensure_alchemy_schema_once()
    if not user_id:
        return {"success": True, "recipes": list_recipes(1), "featured_recipe_ids": get_featured_recipe_ids(1), "category_labels": ALCHEMY_CATEGORY_LABELS}
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}
    rank = int(user.get("rank", 1) or 1)
    mastery = _alchemy_mastery_profile(user, None)
    recipes = []
    for recipe in list_recipes(rank):
        profile = _alchemy_mastery_profile(user, recipe)
        effective_rate = min(0.98, max(0.05, float(recipe.get("success_rate", 1.0) or 1.0) + float(profile.get("success_bonus", 0.0) or 0.0)))
        recipe = dict(recipe)
        recipe["effective_success_rate"] = round(effective_rate, 4)
        recipes.append(recipe)
    return {
        "success": True,
        "recipes": recipes,
        "rank": rank,
        "alchemy_mastery": mastery,
        "featured_recipe_ids": get_featured_recipe_ids(rank),
        "category_labels": ALCHEMY_CATEGORY_LABELS,
    }


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


def brew_pill(user_id: str, recipe_id: str, request_id: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    _ensure_alchemy_schema_once()
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="alchemy_brew")
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
            save_response(request_id, user_id, "alchemy_brew", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)

    recipe = get_recipe(recipe_id)
    if not recipe:
        return _dedup_return({"success": False, "code": "INVALID", "message": "配方不存在"}, 404)

    rank = int(user.get("rank", 1) or 1)
    min_rank = int(recipe.get("min_rank", 1) or 1)
    if rank < min_rank:
        log_event(
            "alchemy_brew",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="FORBIDDEN",
            meta={"recipe_id": recipe_id},
        )
        from core.game.realms import format_realm_display
        return _dedup_return({"success": False, "code": "FORBIDDEN", "message": f"境界不足，需要{format_realm_display(min_rank)}"}, 400)

    cost = int(recipe.get("copper_cost", 0) or 0)
    if user.get("copper", 0) < cost:
        log_event(
            "alchemy_brew",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="INSUFFICIENT",
            meta={"recipe_id": recipe_id},
        )
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": "下品灵石不足"}, 400)

    missing = []
    for mat in recipe.get("materials", []):
        have = _sum_material(user_id, mat["item_id"])
        if have < int(mat.get("quantity", 0) or 0):
            missing.append({"item_id": mat["item_id"], "need": int(mat["quantity"]), "have": have})
    if missing:
        log_event(
            "alchemy_brew",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="MISSING_MATERIAL",
            meta={"recipe_id": recipe_id},
        )
        return _dedup_return({"success": False, "code": "MISSING_MATERIAL", "message": "材料不足", "missing": missing}, 400)
    mastery = _alchemy_mastery_profile(user, recipe)
    base_success_rate = float(recipe.get("success_rate", 1.0) or 1.0)
    effective_success_rate = min(0.98, max(0.05, base_success_rate + float(mastery.get("success_bonus", 0.0) or 0.0)))
    success = random.random() < effective_success_rate
    now = int(time.time())
    stamina_user = refresh_user_stamina(user_id, now=now)
    product = None
    output_score = 0

    try:
        with db_transaction() as cur:
            if not spend_user_stamina_tx(cur, user_id, 1, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")
            # consume materials
            for mat in recipe.get("materials", []):
                _deduct_material(cur, user_id, mat["item_id"], int(mat.get("quantity", 0) or 0))

            # consume copper
            cur.execute(
                "UPDATE users SET copper = copper - ? WHERE user_id = ? AND copper >= ?",
                (cost, user_id, cost),
            )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")

            if success:
                product = generate_pill(recipe["product_item_id"], int(recipe.get("product_qty", 1) or 1))
                product_def = get_item_by_id(recipe["product_item_id"]) or {}
                base_value = int(product_def.get("price", 100) or 100)
                category = recipe.get("category")
                score_scale = 1.0
                if category == "route":
                    score_scale = 1.1
                elif category == "short_term":
                    score_scale = 1.2
                elif category == "rare":
                    score_scale = 1.6
                output_score = int(
                    base_value
                    * int(recipe.get("product_qty", 1) or 1)
                    * score_scale
                    * float(mastery.get("output_score_multiplier", 1.0) or 1.0)
                )
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
                cur.execute(
                    "UPDATE users SET alchemy_output_score = alchemy_output_score + ? WHERE user_id = ?",
                    (output_score, user_id),
                )

            # log
            cur.execute(
                "INSERT INTO alchemy_logs (user_id, recipe_id, success, created_at, result_item_id, quantity) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, recipe_id, 1 if success else 0, now, recipe.get("product_item_id"), int(recipe.get("product_qty", 1) or 1)),
            )
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            current = get_user_by_id(user_id) or stamina_user or user
            log_event(
                "alchemy_brew",
                user_id=user_id,
                success=False,
                request_id=request_id,
                rank=rank,
                reason="INSUFFICIENT_STAMINA",
                meta={"recipe_id": recipe_id},
            )
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": "精力不足，炼丹需要 1 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": 1,
            }, 400)
        if reason == "INSUFFICIENT_MATERIAL":
            missing = []
            for mat in recipe.get("materials", []):
                have = _sum_material(user_id, mat["item_id"])
                need = int(mat.get("quantity", 0) or 0)
                if have < need:
                    missing.append({"item_id": mat["item_id"], "need": need, "have": have})
            log_event(
                "alchemy_brew",
                user_id=user_id,
                success=False,
                request_id=request_id,
                rank=rank,
                reason="MISSING_MATERIAL",
                meta={"recipe_id": recipe_id},
            )
            return _dedup_return({"success": False, "code": "MISSING_MATERIAL", "message": "材料不足", "missing": missing}, 400)
        log_event(
            "alchemy_brew",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="INSUFFICIENT",
            meta={"recipe_id": recipe_id},
        )
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": "下品灵石不足"}, 400)
    except Exception as exc:
        logger.exception(
            "brew_pill unexpected error user_id=%s recipe_id=%s request_id=%s",
            user_id,
            recipe_id,
            request_id,
            exc_info=exc,
        )
        log_event(
            "alchemy_brew",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason="SERVER_ERROR",
            meta={"recipe_id": recipe_id},
        )
        return _dedup_return(
            {
                "success": False,
                "code": "ALCHEMY_SERVER_ERROR",
                "message": "炼丹服务异常，请稍后重试",
            },
            500,
        )

    resp = {
        "success": True,
        "brew_success": success,
        "message": "炼丹成功！" if success else "炼丹失败。",
        "product": product,
        "cost": cost,
        "recipe_id": recipe_id,
        "output_score": output_score,
        "base_success_rate": round(base_success_rate, 4),
        "effective_success_rate": round(effective_success_rate, 4),
        "alchemy_mastery": mastery,
    }
    log_event(
        "alchemy_brew",
        user_id=user_id,
        success=success,
        request_id=request_id,
        rank=rank,
        meta={"recipe_id": recipe_id, "brew_success": success},
    )
    log_economy_ledger(
        user_id=user_id,
        module="alchemy",
        action="alchemy_brew",
        delta_copper=-cost,
        delta_stamina=-1,
        item_id=recipe.get("product_item_id"),
        qty=int(recipe.get("product_qty", 1) or 1) if success else 0,
        success=success,
        request_id=request_id,
        rank=rank,
        meta={"brew_success": success},
    )
    return _dedup_return(resp, 200)
