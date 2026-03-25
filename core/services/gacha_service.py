"""Gacha service layer."""

from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import config
from core.database.connection import (
    fetch_one,
    fetch_all,
    db_transaction,
    get_user_by_id,
    get_db,
    refresh_user_stamina,
    spend_user_stamina_tx,
)
from core.database.migrations import reserve_request, save_response
from core.utils.number import format_stamina_value
from core.game.items import get_item_by_id, generate_pill, generate_material, generate_equipment, generate_skill_book, Quality
from core.services.metrics_service import log_event, log_economy_ledger
from core.utils.timeutil import midnight_timestamp


def _gacha_cfg_int(key: str, default: int) -> int:
    try:
        return int(config.get_nested("gacha", key, default=default))
    except (TypeError, ValueError):
        return int(default)


GACHA_FREE_DAILY_LIMIT = max(0, _gacha_cfg_int("free_daily_limit", 3))
GACHA_PAID_DAILY_LIMIT = max(1, _gacha_cfg_int("paid_daily_limit", 15))
GACHA_FIVE_PULL_COUNT = max(2, _gacha_cfg_int("five_pull_count", 5))
GACHA_FIVE_PULL_PRICE_GOLD = max(1, _gacha_cfg_int("five_pull_price_gold", 4))
GACHA_FIVE_PULL_STAMINA = max(1, _gacha_cfg_int("five_pull_stamina", 4))
GACHA_SINGLE_PULL_STAMINA = max(0, _gacha_cfg_int("single_pull_stamina", 1))
GACHA_FIVE_PULL_PRICE_MULT_NON_GOLD = max(1, _gacha_cfg_int("five_pull_price_mult_non_gold", 4))
_GACHA_TABLES_READY = False
_GACHA_TABLES_LOCK = threading.Lock()


def _ensure_gacha_tables() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gacha_pity (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            banner_id INTEGER NOT NULL,
            pity_count INTEGER DEFAULT 0,
            sr_pity_count INTEGER DEFAULT 0,
            total_pulls INTEGER DEFAULT 0,
            UNIQUE(user_id, banner_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gacha_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            banner_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()


def _prepare_gacha_tables_once() -> None:
    global _GACHA_TABLES_READY
    if _GACHA_TABLES_READY:
        return
    with _GACHA_TABLES_LOCK:
        if _GACHA_TABLES_READY:
            return
        _ensure_gacha_tables()
        _GACHA_TABLES_READY = True


def _gacha_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "gacha.json")


def _load_gacha() -> Dict[str, Any]:
    path = _gacha_path()
    if not os.path.exists(path):
        return {"banners": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {"banners": []}


def list_banners() -> List[Dict[str, Any]]:
    data = _load_gacha()
    now = int(time.time())
    banners = []
    for b in data.get("banners", []):
        start_ts = int(b.get("start_ts", 0) or 0)
        end_ts = int(b.get("end_ts", 0) or 0)
        if start_ts and now < start_ts:
            continue
        if end_ts and now > end_ts:
            continue
        banners.append(b)
    return banners


def _get_banner(banner_id: int) -> Optional[Dict[str, Any]]:
    for b in _load_gacha().get("banners", []):
        if int(b.get("banner_id", 0) or 0) == int(banner_id):
            return b
    return None


def _is_banner_active(banner: Dict[str, Any]) -> bool:
    now = int(time.time())
    start_ts = int(banner.get("start_ts", 0) or 0)
    end_ts = int(banner.get("end_ts", 0) or 0)
    if start_ts and now < start_ts:
        return False
    if end_ts and now > end_ts:
        return False
    return True


def _choose_by_weight(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(int(i.get("weight", 1) or 1) for i in items)
    roll = random.uniform(0, total)
    upto = 0
    for it in items:
        upto += int(it.get("weight", 1) or 1)
        if roll <= upto:
            return it
    return items[-1]


def _roll_rarity(pools: List[Dict[str, Any]]) -> str:
    roll = random.random()
    acc = 0.0
    for p in pools:
        acc += float(p.get("rate", 0.0) or 0.0)
        if roll <= acc:
            return p.get("rarity")
    return pools[-1].get("rarity")


def _normalize_pools(pools: List[Dict[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    total = 0.0
    for p in pools:
        rate = float(p.get("rate", 0.0) or 0.0)
        if rate < 0:
            return None, False
        total += rate
    if total <= 0:
        return None, False
    if abs(total - 1.0) <= 0.001:
        return pools, False
    normalized = []
    for p in pools:
        row = dict(p)
        row["rate"] = float(p.get("rate", 0.0) or 0.0) / total
        normalized.append(row)
    return normalized, True


def _equipment_level_for_rank(rank: int, base_item: Dict[str, Any]) -> int:
    base_rank = int(base_item.get("min_rank", 1) or 1)
    delta = max(0, int(rank or 1) - base_rank)
    return max(1, min(10, 1 + delta // 3))


def _duplicate_compensation(rarity: str) -> Optional[Dict[str, Any]]:
    rarity = (rarity or "R").upper()
    if rarity == "SSR":
        return {"item_id": "immortal_stone", "quantity": 1}
    if rarity == "SR":
        return {"item_id": "spirit_herb", "quantity": 1}
    return {"item_id": "spirit_stone", "quantity": 1}


def _ensure_item(cur, user_id: str, item_id: str, rarity: str, *, user_rank: int) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], bool]:
    base = get_item_by_id(item_id)
    if not base:
        return {}, None, False
    item_type = getattr(base.get("type"), "value", base.get("type"))
    if item_type == "pill":
        generated = generate_pill(item_id, 1)
    elif item_type == "material":
        generated = generate_material(item_id, 1)
    elif item_type == "skill_book":
        generated = generate_skill_book(item_id, 1)
    else:
        rarity = (rarity or "R").upper()
        if rarity == "SSR":
            quality = Quality.DIVINE
        elif rarity == "SR":
            quality = Quality.IMMORTAL
        else:
            quality = Quality.SPIRIT
        level = _equipment_level_for_rank(user_rank, base)
        generated = generate_equipment(base, quality, level)
    if not generated:
        return {}, None, False
    if item_type in ("weapon", "armor", "accessory"):
        cur.execute(
            """SELECT MAX(level) AS max_level FROM items
               WHERE user_id = %s AND item_id = %s AND item_type IN ('weapon', 'armor', 'accessory')
                 AND quality = %s""",
            (user_id, generated.get("item_id"), generated.get("quality", "common")),
        )
        dup_row = cur.fetchone()
        max_level = int(dup_row["max_level"] or 0) if dup_row else 0
        if max_level >= int(generated.get("level", 1) or 1):
            compensation = _duplicate_compensation(rarity)
            comp_item = None
            if compensation:
                comp_item = generate_material(compensation["item_id"], int(compensation.get("quantity", 1) or 1))
                if comp_item:
                    cur.execute(
                        """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                           quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                           first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            user_id,
                            comp_item.get("item_id"),
                            comp_item.get("item_name"),
                            comp_item.get("item_type"),
                            comp_item.get("quality", "common"),
                            comp_item.get("quantity", 1),
                            comp_item.get("level", 1),
                            comp_item.get("attack_bonus", 0),
                            comp_item.get("defense_bonus", 0),
                            comp_item.get("hp_bonus", 0),
                            comp_item.get("mp_bonus", 0),
                            comp_item.get("first_round_reduction_pct", 0),
                            comp_item.get("crit_heal_pct", 0),
                            comp_item.get("element_damage_pct", 0),
                            comp_item.get("low_hp_shield_pct", 0),
                        ),
                    )
            return generated, comp_item, True
    cur.execute(
        """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
           quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
           first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            user_id,
            generated.get("item_id"),
            generated.get("item_name"),
            generated.get("item_type"),
            generated.get("quality", "common"),
            1,
            generated.get("level", 1),
            generated.get("attack_bonus", 0),
            generated.get("defense_bonus", 0),
            generated.get("hp_bonus", 0),
            generated.get("mp_bonus", 0),
            generated.get("first_round_reduction_pct", 0),
            generated.get("crit_heal_pct", 0),
            generated.get("element_damage_pct", 0),
            generated.get("low_hp_shield_pct", 0),
        ),
    )
    return generated, None, False


def get_pity(user_id: str, banner_id: int) -> Dict[str, Any]:
    _prepare_gacha_tables_once()
    row = fetch_one(
        "SELECT pity_count, sr_pity_count, total_pulls FROM gacha_pity WHERE user_id = %s AND banner_id = %s",
        (user_id, banner_id),
    )
    return row or {"pity_count": 0, "sr_pity_count": 0, "total_pulls": 0}


def get_gacha_status(user_id: str) -> Dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        return {
            "free_remaining": GACHA_FREE_DAILY_LIMIT,
            "paid_remaining": GACHA_PAID_DAILY_LIMIT,
            "free_limit": GACHA_FREE_DAILY_LIMIT,
            "paid_limit": GACHA_PAID_DAILY_LIMIT,
            "five_pull_count": GACHA_FIVE_PULL_COUNT,
            "five_pull_price_gold": GACHA_FIVE_PULL_PRICE_GOLD,
            "five_pull_stamina": GACHA_FIVE_PULL_STAMINA,
            "single_pull_stamina": GACHA_SINGLE_PULL_STAMINA,
            "five_pull_price_mult_non_gold": GACHA_FIVE_PULL_PRICE_MULT_NON_GOLD,
        }
    user = _reset_daily_gacha_if_needed(user)
    free_today = int(user.get("gacha_free_today", 0) or 0)
    paid_today = int(user.get("gacha_paid_today", 0) or 0)
    return {
        "free_remaining": max(0, GACHA_FREE_DAILY_LIMIT - free_today),
        "paid_remaining": max(0, GACHA_PAID_DAILY_LIMIT - paid_today),
        "free_used": free_today,
        "paid_used": paid_today,
        "free_limit": GACHA_FREE_DAILY_LIMIT,
        "paid_limit": GACHA_PAID_DAILY_LIMIT,
        "five_pull_count": GACHA_FIVE_PULL_COUNT,
        "five_pull_price_gold": GACHA_FIVE_PULL_PRICE_GOLD,
        "five_pull_stamina": GACHA_FIVE_PULL_STAMINA,
        "single_pull_stamina": GACHA_SINGLE_PULL_STAMINA,
        "five_pull_price_mult_non_gold": GACHA_FIVE_PULL_PRICE_MULT_NON_GOLD,
    }


def _reset_daily_gacha_if_needed(user: Dict[str, Any]) -> Dict[str, Any]:
    today = midnight_timestamp()
    last_reset = int(user.get("gacha_daily_reset", 0) or 0)
    if last_reset >= today:
        return user
    with db_transaction() as cur:
        cur.execute(
            "UPDATE users SET gacha_free_today = 0, gacha_paid_today = 0, gacha_daily_reset = %s WHERE user_id = %s",
            (today, user["user_id"]),
        )
    fresh = get_user_by_id(user["user_id"])
    return fresh or user


def pull_gacha(
    user_id: str,
    banner_id: int,
    count: int = 1,
    *,
    force_paid: bool = False,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    _prepare_gacha_tables_once()
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="gacha_pull")
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
            save_response(request_id, user_id, "gacha_pull", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        log_event("gacha_pull", user_id=user_id, success=False, request_id=request_id, reason="USER_NOT_FOUND", meta={"banner_id": banner_id})
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "玩家不存在"}, 404)
    user = _reset_daily_gacha_if_needed(user)
    banner = _get_banner(banner_id)
    requested_count = int(count or 1)
    rank = int(user.get("rank", 1) or 1)
    base_meta = {"banner_id": banner_id, "requested_count": requested_count}

    def _log_failure(reason: str, meta: Optional[Dict[str, Any]] = None) -> None:
        payload = dict(base_meta)
        if meta:
            payload.update(meta)
        log_event(
            "gacha_pull",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=rank,
            reason=reason,
            meta=payload,
        )

    if not banner:
        _log_failure("INVALID_BANNER")
        return _dedup_return({"success": False, "code": "INVALID", "message": "卡池不存在"}, 404)
    if not _is_banner_active(banner):
        _log_failure("EXPIRED")
        return _dedup_return({"success": False, "code": "EXPIRED", "message": "卡池未开启或已结束"}, 400)

    count = GACHA_FIVE_PULL_COUNT if requested_count >= GACHA_FIVE_PULL_COUNT else 1
    currency = banner.get("currency", "gold")
    free_today = int(user.get("gacha_free_today", 0) or 0)
    paid_today = int(user.get("gacha_paid_today", 0) or 0)
    requested_free_pull = count == 1 and (not force_paid)
    if requested_free_pull and free_today >= GACHA_FREE_DAILY_LIMIT:
        _log_failure("FREE_LIMIT", meta={"free_remaining": 0})
        return _dedup_return({
            "success": False,
            "code": "FREE_LIMIT",
            "message": "今日免费抽奖次数已用尽，请使用付费单抽或连抽",
            "free_remaining": 0,
            "paid_remaining": max(0, GACHA_PAID_DAILY_LIMIT - paid_today),
        }, 400)
    is_free_pull = requested_free_pull and free_today < GACHA_FREE_DAILY_LIMIT
    price_single = int(banner.get("price_single", 1) or 1)
    if count == GACHA_FIVE_PULL_COUNT:
        price = (
            GACHA_FIVE_PULL_PRICE_GOLD
            if currency == "gold"
            else max(1, price_single * GACHA_FIVE_PULL_PRICE_MULT_NON_GOLD)
        )
        stamina_cost = GACHA_FIVE_PULL_STAMINA
    elif is_free_pull:
        price = 0
        stamina_cost = 0
    else:
        price = price_single
        stamina_cost = GACHA_SINGLE_PULL_STAMINA

    paid_increment = 0 if is_free_pull else count
    if paid_today + paid_increment > GACHA_PAID_DAILY_LIMIT:
        remaining = max(0, GACHA_PAID_DAILY_LIMIT - paid_today)
        _log_failure("DAILY_LIMIT", meta={"paid_remaining": remaining})
        return _dedup_return({
            "success": False,
            "code": "DAILY_LIMIT",
            "message": f"今日付费抽奖次数已达上限，剩余可付费抽奖 {remaining} 次",
            "free_remaining": max(0, GACHA_FREE_DAILY_LIMIT - free_today),
            "paid_remaining": remaining,
        }, 400)

    if price > 0:
        if currency == "gold":
            if int(user.get("gold", 0) or 0) < price:
                _log_failure("INSUFFICIENT_GOLD", meta={"currency": currency, "price": price})
                return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": "中品灵石不足"}, 400)
        else:
            if int(user.get("copper", 0) or 0) < price:
                _log_failure("INSUFFICIENT_COPPER", meta={"currency": currency, "price": price})
                return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": "下品灵石不足"}, 400)

    pools = banner.get("pools", [])
    if not pools or any(not p.get("items") for p in pools):
        _log_failure("INVALID_CONFIG", meta={"currency": currency})
        return _dedup_return({"success": False, "code": "INVALID", "message": "卡池配置无效"}, 400)
    invalid_items: set[str] = set()
    checked_items: set[str] = set()
    for pool in pools:
        for entry in pool.get("items", []) or []:
            item_id = str(entry.get("item_id", "") or "").strip()
            if not item_id:
                invalid_items.add(item_id or "未知物品")
                continue
            if item_id in checked_items:
                continue
            checked_items.add(item_id)
            if not get_item_by_id(item_id):
                invalid_items.add(item_id)
    if invalid_items:
        invalid_list = "、".join(sorted(invalid_items))
        _log_failure("INVALID_ITEM", meta={"invalid_items": invalid_list})
        return _dedup_return({"success": False, "code": "INVALID", "message": f"卡池配置包含无效物品：{invalid_list}"}, 400)
    normalized_pools, normalized_rates = _normalize_pools(pools)
    if normalized_pools is None:
        _log_failure("INVALID_RATE", meta={"currency": currency})
        return _dedup_return({"success": False, "code": "INVALID", "message": "卡池概率配置无效"}, 400)
    pools = normalized_pools
    result_items: List[Dict[str, Any]] = []
    now = int(time.time())
    stamina_user = None
    if stamina_cost > 0:
        stamina_user = refresh_user_stamina(user_id, now=now)

    pity_count = 0
    sr_pity = 0
    total_pulls = 0
    try:
        with db_transaction() as cur:
            cur.execute(
                """INSERT INTO gacha_pity (user_id, banner_id, pity_count, sr_pity_count, total_pulls)
                   VALUES (%s, %s, 0, 0, 0)
                   ON CONFLICT (user_id, banner_id) DO NOTHING""",
                (user_id, banner_id),
            )
            cur.execute(
                "SELECT pity_count, sr_pity_count, total_pulls FROM gacha_pity WHERE user_id = %s AND banner_id = %s FOR UPDATE",
                (user_id, banner_id),
            )
            pity_row = cur.fetchone()
            pity_count = int((pity_row or {}).get("pity_count", 0) or 0)
            sr_pity = int((pity_row or {}).get("sr_pity_count", 0) or 0)
            total_pulls = int((pity_row or {}).get("total_pulls", 0) or 0)

            if stamina_cost > 0:
                if not spend_user_stamina_tx(cur, user_id, stamina_cost, now=now):
                    raise ValueError("INSUFFICIENT_STAMINA")
            if price > 0 and currency == "gold":
                cur.execute(
                    "UPDATE users SET gold = gold - %s WHERE user_id = %s AND gold >= %s",
                    (price, user_id, price),
                )
                if cur.rowcount == 0:
                    raise ValueError("INSUFFICIENT_GOLD")
            elif price > 0:
                cur.execute(
                    "UPDATE users SET copper = copper - %s WHERE user_id = %s AND copper >= %s",
                    (price, user_id, price),
                )
                if cur.rowcount == 0:
                    raise ValueError("INSUFFICIENT_COPPER")

            if is_free_pull:
                cur.execute(
                    "UPDATE users SET gacha_free_today = gacha_free_today + 1 WHERE user_id = %s AND gacha_free_today < %s",
                    (user_id, GACHA_FREE_DAILY_LIMIT),
                )
                if cur.rowcount == 0:
                    raise ValueError("FREE_LIMIT")
            else:
                cur.execute(
                    """UPDATE users
                       SET gacha_paid_today = gacha_paid_today + %s
                       WHERE user_id = %s AND gacha_paid_today + %s <= %s""",
                    (paid_increment, user_id, paid_increment, GACHA_PAID_DAILY_LIMIT),
                )
                if cur.rowcount == 0:
                    raise ValueError("PAID_LIMIT")

            for _ in range(count):
                pity_count += 1
                sr_pity += 1
                total_pulls += 1

                rarity = _roll_rarity(pools)
                if pity_count >= int(banner.get("guaranteed_ssr_every", 90)):
                    rarity = "SSR"
                elif sr_pity >= int(banner.get("guaranteed_sr_every", 10)) and rarity == "R":
                    rarity = "SR"

                pool = next((p for p in pools if p.get("rarity") == rarity), pools[-1])
                choice = _choose_by_weight(pool.get("items", []))
                item_id = choice.get("item_id")

                generated, compensation, is_duplicate = _ensure_item(cur, user_id, item_id, rarity, user_rank=rank)
                if not generated:
                    raise ValueError("INVALID_ITEM")
                entry = {"item_id": item_id, "item_name": generated.get("item_name"), "rarity": rarity}
                if is_duplicate:
                    entry["duplicate"] = True
                    if compensation:
                        entry["compensation"] = {
                            "item_id": compensation.get("item_id"),
                            "item_name": compensation.get("item_name"),
                            "quantity": compensation.get("quantity", 1),
                        }
                result_items.append(entry)

                cur.execute(
                    "INSERT INTO gacha_logs (user_id, banner_id, item_id, rarity, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, banner_id, item_id, rarity, now),
                )

                if rarity == "SSR":
                    pity_count = 0
                    sr_pity = 0
                elif rarity == "SR":
                    sr_pity = 0

            cur.execute(
                """UPDATE gacha_pity
                   SET pity_count = %s, sr_pity_count = %s, total_pulls = %s
                   WHERE user_id = %s AND banner_id = %s""",
                (pity_count, sr_pity, total_pulls, user_id, banner_id),
            )
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            current = get_user_by_id(user_id) or stamina_user or user
            _log_failure("INSUFFICIENT_STAMINA", meta={"stamina_cost": stamina_cost})
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": f"精力不足，抽奖需要 {stamina_cost} 点精力",
                "stamina": format_stamina_value((current or {}).get("stamina", 0)),
                "stamina_cost": stamina_cost,
            }, 400)
        if reason == "FREE_LIMIT":
            latest_user = get_user_by_id(user_id) or user
            free_remaining = max(0, GACHA_FREE_DAILY_LIMIT - int((latest_user or {}).get("gacha_free_today", 0) or 0))
            paid_remaining = max(0, GACHA_PAID_DAILY_LIMIT - int((latest_user or {}).get("gacha_paid_today", 0) or 0))
            _log_failure("FREE_LIMIT", meta={"free_remaining": free_remaining, "paid_remaining": paid_remaining})
            return _dedup_return({
                "success": False,
                "code": "FREE_LIMIT",
                "message": "今日免费抽奖次数已用尽，请使用付费单抽或连抽",
                "free_remaining": free_remaining,
                "paid_remaining": paid_remaining,
            }, 400)
        if reason == "INVALID_ITEM":
            _log_failure("INVALID_ITEM")
            return _dedup_return({"success": False, "code": "INVALID", "message": "卡池配置无效，包含不存在的物品"}, 400)
        if reason in {"INSUFFICIENT_GOLD", "INSUFFICIENT_COPPER"}:
            _log_failure(reason, meta={"currency": currency, "price": price})
            return _dedup_return({
                "success": False,
                "code": "INSUFFICIENT",
                "message": "中品灵石不足" if reason == "INSUFFICIENT_GOLD" else "下品灵石不足",
            }, 400)
        if reason in {"PAID_LIMIT", "DAILY_LIMIT"}:
            latest = get_user_by_id(user_id) or {}
            remaining = max(0, GACHA_PAID_DAILY_LIMIT - int(latest.get("gacha_paid_today", 0) or 0))
            free_remaining = max(0, GACHA_FREE_DAILY_LIMIT - int(latest.get("gacha_free_today", 0) or 0))
            _log_failure("DAILY_LIMIT", meta={"paid_remaining": remaining})
            return _dedup_return({
                "success": False,
                "code": "DAILY_LIMIT",
                "message": f"今日付费抽奖次数已达上限，剩余可付费抽奖 {remaining} 次",
                "free_remaining": free_remaining,
                "paid_remaining": remaining,
            }, 400)
        _log_failure("UNKNOWN", meta={"reason": reason})
        return _dedup_return({"success": False, "code": "INVALID", "message": "抽卡请求无效"}, 400)
    except Exception as exc:
        _log_failure("DB_ERROR", meta={"error_type": type(exc).__name__})
        return _dedup_return({"success": False, "code": "CONFLICT", "message": "抽卡请求冲突，请稍后重试"}, 409)

    rarity_counts: Dict[str, int] = {}
    for item in result_items:
        rarity = str(item.get("rarity") or "R")
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1

    pull_mode = "free" if is_free_pull else ("five" if count == GACHA_FIVE_PULL_COUNT else "paid")
    log_event(
        "gacha_pull",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=rank,
        meta={
            "banner_id": banner_id,
            "count": count,
            "requested_count": requested_count,
            "pull_mode": pull_mode,
            "currency": currency,
            "price": price,
            "stamina_cost": stamina_cost,
            "rarity_counts": rarity_counts,
            "rate_normalized": bool(normalized_rates),
        },
    )
    log_economy_ledger(
        user_id=user_id,
        module="gacha",
        action="gacha_pull",
        delta_copper=-price if currency != "gold" else 0,
        delta_gold=-price if currency == "gold" else 0,
        delta_stamina=-stamina_cost,
        currency=currency,
        shown_price=price if price > 0 else None,
        actual_price=price if price > 0 else None,
        success=True,
        request_id=request_id,
        rank=rank,
        meta={
            "banner_id": banner_id,
            "count": count,
            "pull_mode": pull_mode,
            "rarity_counts": rarity_counts,
            "free_pull": bool(is_free_pull),
            "rate_normalized": bool(normalized_rates),
        },
    )

    return _dedup_return({
        "success": True,
        "results": result_items,
        "cost": {"currency": currency, "amount": price},
        "pull_mode": pull_mode,
        "stamina_cost": stamina_cost,
        "free_remaining": max(0, GACHA_FREE_DAILY_LIMIT - (free_today + (1 if is_free_pull else 0))),
        "paid_remaining": max(0, GACHA_PAID_DAILY_LIMIT - (paid_today + paid_increment)),
        "pity": {"pity_count": pity_count, "sr_pity_count": sr_pity, "total_pulls": total_pulls},
    }, 200)
