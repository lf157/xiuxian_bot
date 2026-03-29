"""Global bounty board service."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

from core.database.connection import db_transaction, fetch_all, fetch_one, get_user_by_id, get_db
from core.game.items import get_item_by_id
from core.services.audit_log_service import write_audit_log
from core.services.metrics_service import log_event, log_economy_ledger


BOUNTY_STATUS_OPEN = "open"
BOUNTY_STATUS_CLAIMED = "claimed"
BOUNTY_STATUS_COMPLETED = "completed"
BOUNTY_STATUS_CANCELLED = "cancelled"
_BOUNTY_SCHEMA_READY = False
_BOUNTY_SCHEMA_LOCK = threading.Lock()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _ensure_bounty_schema() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'bounty_orders'"
    )
    cols = {str(row[0]) for row in (cur.fetchall() or [])}
    if "is_escrowed" not in cols:
        cur.execute("ALTER TABLE bounty_orders ADD COLUMN is_escrowed INTEGER DEFAULT 0")
    if "escrow_copper" not in cols:
        cur.execute("ALTER TABLE bounty_orders ADD COLUMN escrow_copper INTEGER DEFAULT 0")
    conn.commit()


def _ensure_bounty_schema_once() -> None:
    global _BOUNTY_SCHEMA_READY
    if _BOUNTY_SCHEMA_READY:
        return
    with _BOUNTY_SCHEMA_LOCK:
        if _BOUNTY_SCHEMA_READY:
            return
        _ensure_bounty_schema()
        _BOUNTY_SCHEMA_READY = True


def _deduct_item_tx(cur: Any, *, user_id: str, item_id: str, quantity: int) -> Dict[str, Any] | None:
    need = max(1, int(quantity or 1))
    cur.execute(
        """
        SELECT id, user_id, item_id, item_name, item_type, quality, quantity, level,
               attack_bonus, defense_bonus, hp_bonus, mp_bonus,
               first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct
        FROM items
        WHERE user_id = %s AND item_id = %s AND item_type = 'material'
        ORDER BY id ASC
        """,
        (user_id, item_id),
    )
    rows = cur.fetchall() or []
    total = sum(_as_int(row.get("quantity", 0), 0) for row in rows)
    if total < need:
        return None
    template = dict(rows[0])
    remain = need
    for row in rows:
        if remain <= 0:
            break
        row_qty = _as_int(row.get("quantity", 0), 0)
        consume = min(remain, row_qty)
        if consume <= 0:
            continue
        if consume == row_qty:
            cur.execute(
                "DELETE FROM items WHERE id = %s AND user_id = %s AND item_id = %s AND item_type = 'material' AND quantity = %s",
                (row["id"], user_id, item_id, row_qty),
            )
            if int(cur.rowcount or 0) == 0:
                return None
        else:
            cur.execute(
                "UPDATE items SET quantity = quantity - %s WHERE id = %s AND user_id = %s AND item_id = %s AND item_type = 'material' AND quantity >= %s",
                (consume, row["id"], user_id, item_id, consume),
            )
            if int(cur.rowcount or 0) == 0:
                return None
        remain -= consume
    return template


def _grant_item_tx(cur: Any, *, user_id: str, template: Dict[str, Any], quantity: int) -> None:
    qty = max(1, int(quantity or 1))
    item_id = str(template.get("item_id") or "")
    item_type = "material"
    quality = "common"
    level = 1
    item_def = get_item_by_id(item_id) or {}
    item_name = str(item_def.get("name") or template.get("item_name") or item_id)
    cur.execute(
        """
        SELECT id FROM items
        WHERE user_id = %s AND item_id = %s AND item_type = 'material'
        ORDER BY id ASC LIMIT 1
        """,
        (user_id, item_id),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE items SET quantity = quantity + %s WHERE id = %s", (qty, row["id"]))
        return
    cur.execute(
        """
        INSERT INTO items (
            user_id, item_id, item_name, item_type, quality, quantity, level,
            attack_bonus, defense_bonus, hp_bonus, mp_bonus,
            first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            item_id,
            item_name,
            item_type,
            quality,
            qty,
            level,
            _as_int(template.get("attack_bonus", 0), 0),
            _as_int(template.get("defense_bonus", 0), 0),
            _as_int(template.get("hp_bonus", 0), 0),
            _as_int(template.get("mp_bonus", 0), 0),
            float(template.get("first_round_reduction_pct", 0) or 0),
            float(template.get("crit_heal_pct", 0) or 0),
            float(template.get("element_damage_pct", 0) or 0),
            float(template.get("low_hp_shield_pct", 0) or 0),
        ),
    )


def publish_bounty(
    *,
    user_id: str,
    wanted_item_id: str,
    wanted_quantity: int,
    reward_spirit_low: int,
    description: str = "",
) -> Tuple[Dict[str, Any], int]:
    _ensure_bounty_schema_once()
    poster = get_user_by_id(user_id)
    if not poster:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404

    item_id = str(wanted_item_id or "").strip()
    if not item_id:
        return {"success": False, "code": "MISSING_PARAMS", "message": "缺少道具ID"}, 400
    item_def = get_item_by_id(item_id)
    if not item_def:
        return {"success": False, "code": "INVALID_ITEM", "message": "道具不存在，无法发布悬赏"}, 400
    item_type = getattr(item_def.get("type"), "value", item_def.get("type"))
    if str(item_type) != "material":
        return {"success": False, "code": "INVALID_ITEM", "message": "悬赏当前仅支持材料类道具"}, 400

    qty = _as_int(wanted_quantity, 0)
    reward = _as_int(reward_spirit_low, 0)
    if qty <= 0:
        return {"success": False, "code": "INVALID_QTY", "message": "悬赏数量必须大于 0"}, 400
    if reward <= 0:
        return {"success": False, "code": "INVALID_REWARD", "message": "悬赏奖励必须大于 0"}, 400

    now = int(time.time())
    desc = str(description or "").strip()
    try:
        with db_transaction() as cur:
            cur.execute(
                "UPDATE users SET copper = copper - %s WHERE user_id = %s AND copper >= %s",
                (reward, user_id, reward),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("INSUFFICIENT")
            cur.execute(
                """
                INSERT INTO bounty_orders (
                    poster_user_id, wanted_item_id, wanted_item_name, wanted_quantity,
                    reward_spirit_low, escrow_copper, is_escrowed, description, status, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                RETURNING id
                """,
                (user_id, item_id, item_def.get("name") or item_id, qty, reward, reward, desc, BOUNTY_STATUS_OPEN, now),
            )
            row = cur.fetchone()
            bounty_id = int(row["id"])
    except ValueError:
        return {"success": False, "code": "INSUFFICIENT", "message": "下品灵石不足，无法发布悬赏"}, 400

    log_event(
        "bounty_publish",
        user_id=user_id,
        success=True,
        rank=int(poster.get("rank", 1) or 1),
        meta={"bounty_id": bounty_id, "item_id": item_id, "qty": qty, "reward_low": reward},
    )
    write_audit_log(
        module="bounty",
        action="publish",
        user_id=user_id,
        success=True,
        detail={"bounty_id": bounty_id, "item_id": item_id, "qty": qty, "reward_spirit_low": reward},
    )
    log_economy_ledger(
        user_id=user_id,
        module="bounty",
        action="bounty_publish",
        delta_copper=-reward,
        success=True,
        rank=int(poster.get("rank", 1) or 1),
        meta={"bounty_id": bounty_id, "item_id": item_id, "qty": qty, "escrow": True},
    )
    return {
        "success": True,
        "bounty_id": bounty_id,
        "message": f"悬赏已发布：需要 {qty} 个 {item_def.get('name', item_id)}，奖励 {reward} 下品灵石",
    }, 200


def list_bounties(*, status: str = BOUNTY_STATUS_OPEN, limit: int = 30) -> Tuple[Dict[str, Any], int]:
    st = str(status or BOUNTY_STATUS_OPEN).strip().lower()
    if st not in {BOUNTY_STATUS_OPEN, BOUNTY_STATUS_CLAIMED, BOUNTY_STATUS_COMPLETED, BOUNTY_STATUS_CANCELLED, "all"}:
        return {"success": False, "code": "INVALID_STATUS", "message": "状态参数无效"}, 400
    lim = max(1, min(100, _as_int(limit, 30)))
    if st == "all":
        rows = fetch_all(
            """
            SELECT b.*, p.in_game_username AS poster_name, c.in_game_username AS claimer_name
            FROM bounty_orders b
            LEFT JOIN users p ON p.user_id = b.poster_user_id
            LEFT JOIN users c ON c.user_id = b.claimer_user_id
            ORDER BY b.created_at DESC
            LIMIT %s
            """,
            (lim,),
        )
    else:
        rows = fetch_all(
            """
            SELECT b.*, p.in_game_username AS poster_name, c.in_game_username AS claimer_name
            FROM bounty_orders b
            LEFT JOIN users p ON p.user_id = b.poster_user_id
            LEFT JOIN users c ON c.user_id = b.claimer_user_id
            WHERE b.status = %s
            ORDER BY b.created_at DESC
            LIMIT %s
            """,
            (st, lim),
        )
    return {"success": True, "bounties": [dict(row) for row in rows]}, 200


def accept_bounty(*, user_id: str, bounty_id: int) -> Tuple[Dict[str, Any], int]:
    claimer = get_user_by_id(user_id)
    if not claimer:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404

    row = fetch_one("SELECT * FROM bounty_orders WHERE id = %s", (int(bounty_id),))
    if not row:
        return {"success": False, "code": "NOT_FOUND", "message": "悬赏不存在"}, 404
    if str(row.get("poster_user_id")) == str(user_id):
        return {"success": False, "code": "INVALID", "message": "不能接受自己发布的悬赏"}, 400
    if str(row.get("status")) != BOUNTY_STATUS_OPEN:
        return {"success": False, "code": "INVALID_STATUS", "message": "悬赏当前不可接取"}, 400

    now = int(time.time())
    with db_transaction() as cur:
        cur.execute(
            """
            UPDATE bounty_orders
            SET status = %s, claimer_user_id = %s, claimed_at = %s
            WHERE id = %s AND status = %s
            """,
            (BOUNTY_STATUS_CLAIMED, user_id, now, int(bounty_id), BOUNTY_STATUS_OPEN),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "INVALID_STATUS", "message": "悬赏已被他人接取"}, 400

    write_audit_log(
        module="bounty",
        action="accept",
        user_id=user_id,
        target_user_id=str(row.get("poster_user_id") or ""),
        success=True,
        detail={"bounty_id": int(bounty_id)},
    )
    return {"success": True, "message": "已接受悬赏", "bounty_id": int(bounty_id)}, 200


def submit_bounty(*, user_id: str, bounty_id: int) -> Tuple[Dict[str, Any], int]:
    _ensure_bounty_schema_once()
    claimer = get_user_by_id(user_id)
    if not claimer:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404
    now = int(time.time())
    poster_id = ""
    wanted_item_id = ""
    wanted_qty = 0
    reward_low = 0
    try:
        with db_transaction() as cur:
            cur.execute("SELECT * FROM bounty_orders WHERE id = %s FOR UPDATE", (int(bounty_id),))
            bounty = cur.fetchone()
            if not bounty:
                raise ValueError("NOT_FOUND")
            bounty = dict(bounty)
            if str(bounty.get("status")) != BOUNTY_STATUS_CLAIMED:
                raise ValueError("INVALID_STATUS")
            if str(bounty.get("claimer_user_id") or "") != str(user_id):
                raise ValueError("FORBIDDEN")

            poster_id = str(bounty.get("poster_user_id") or "")
            if not poster_id:
                raise ValueError("POSTER_NOT_FOUND")
            cur.execute("SELECT user_id FROM users WHERE user_id = %s FOR UPDATE", (poster_id,))
            if not cur.fetchone():
                raise ValueError("POSTER_NOT_FOUND")

            wanted_item_id = str(bounty.get("wanted_item_id") or "")
            wanted_qty = _as_int(bounty.get("wanted_quantity", 0), 0)
            reward_low = _as_int(bounty.get("reward_spirit_low", 0), 0)
            if wanted_qty <= 0 or reward_low <= 0:
                raise ValueError("INVALID")
            item_def = get_item_by_id(wanted_item_id) or {}
            item_type = getattr(item_def.get("type"), "value", item_def.get("type"))
            if str(item_type) != "material":
                raise ValueError("INVALID_ITEM")

            template = _deduct_item_tx(cur, user_id=user_id, item_id=wanted_item_id, quantity=wanted_qty)
            if not template:
                raise ValueError("INSUFFICIENT_ITEM")
            _grant_item_tx(cur, user_id=poster_id, template=template, quantity=wanted_qty)

            is_escrowed = int(bounty.get("is_escrowed", 0) or 0) == 1
            escrow_copper = _as_int(bounty.get("escrow_copper", reward_low), reward_low)
            payout = reward_low if is_escrowed else reward_low
            if is_escrowed and escrow_copper < reward_low:
                payout = max(0, escrow_copper)

            if not is_escrowed:
                cur.execute(
                    "UPDATE users SET copper = copper - %s WHERE user_id = %s AND copper >= %s",
                    (reward_low, poster_id, reward_low),
                )
                if int(cur.rowcount or 0) == 0:
                    raise ValueError("POSTER_FUNDS")

            cur.execute("UPDATE users SET copper = copper + %s WHERE user_id = %s", (payout, user_id))
            if int(cur.rowcount or 0) == 0:
                raise ValueError("USER_NOT_FOUND")

            cur.execute(
                """UPDATE bounty_orders
                   SET status = %s, completed_at = %s, escrow_copper = CASE WHEN is_escrowed = 1 THEN 0 ELSE escrow_copper END
                   WHERE id = %s AND status = %s AND claimer_user_id = %s""",
                (BOUNTY_STATUS_COMPLETED, now, int(bounty_id), BOUNTY_STATUS_CLAIMED, user_id),
            )
            if int(cur.rowcount or 0) == 0:
                raise ValueError("INVALID_STATUS")
            reward_low = payout
    except ValueError as exc:
        reason = str(exc)
        if reason == "NOT_FOUND":
            return {"success": False, "code": "NOT_FOUND", "message": "悬赏不存在"}, 404
        if reason == "INVALID_STATUS":
            return {"success": False, "code": "INVALID_STATUS", "message": "该悬赏未处于进行中"}, 400
        if reason == "FORBIDDEN":
            return {"success": False, "code": "FORBIDDEN", "message": "仅接取者可提交悬赏"}, 403
        if reason == "POSTER_NOT_FOUND":
            return {"success": False, "code": "POSTER_NOT_FOUND", "message": "发布者不存在"}, 404
        if reason == "INSUFFICIENT_ITEM":
            return {"success": False, "code": "INSUFFICIENT_ITEM", "message": "提交失败，你的道具数量不足"}, 400
        if reason == "POSTER_FUNDS":
            return {"success": False, "code": "POSTER_FUNDS", "message": "发布者下品灵石不足，暂无法结算"}, 400
        if reason == "INVALID_ITEM":
            return {"success": False, "code": "INVALID_ITEM", "message": "该悬赏仅支持材料类道具结算"}, 400
        return {"success": False, "code": "INVALID", "message": "悬赏配置异常，无法结算"}, 400

    log_event(
        "bounty_submit",
        user_id=user_id,
        success=True,
        rank=int(claimer.get("rank", 1) or 1),
        meta={"bounty_id": int(bounty_id), "item_id": wanted_item_id, "qty": wanted_qty, "reward_low": reward_low},
    )
    log_economy_ledger(
        user_id=user_id,
        module="bounty",
        action="bounty_submit",
        delta_copper=reward_low,
        success=True,
        rank=int(claimer.get("rank", 1) or 1),
        meta={"bounty_id": int(bounty_id), "poster_user_id": poster_id},
    )
    write_audit_log(
        module="bounty",
        action="submit",
        user_id=user_id,
        target_user_id=poster_id,
        success=True,
        detail={"bounty_id": int(bounty_id), "item_id": wanted_item_id, "qty": wanted_qty, "reward_spirit_low": reward_low},
    )
    return {
        "success": True,
        "message": f"悬赏已完成，获得 {reward_low} 下品灵石",
        "bounty_id": int(bounty_id),
        "reward_spirit_low": reward_low,
    }, 200
