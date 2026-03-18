"""Social features: chat/dao discussion rewards."""

from __future__ import annotations

import psycopg2.errors
import time
from typing import Any, Dict, Optional, Tuple

from core.config import config
from core.database.connection import (
    DEFAULT_STAMINA_MAX,
    db_transaction,
    execute,
    fetch_one,
    get_user_by_id,
    get_user_by_username,
    refresh_user_stamina,
    update_user,
)
from core.services.metrics_service import log_event
from core.utils.timeutil import midnight_timestamp


CHAT_DAILY_LIMIT = 10.0
CHAT_REQUEST_DAILY_LIMIT = 20
CHAT_REQUEST_TTL_SECONDS = 6 * 3600


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _refresh_chat_energy(user_id: str, user: Dict[str, Any], now: int) -> Dict[str, Any]:
    last_reset = int(user.get("chat_energy_reset", 0) or 0)
    if last_reset < midnight_timestamp():
        update_user(user_id, {"chat_energy_today": 0, "chat_energy_reset": now})
        return get_user_by_id(user_id) or user
    return user


def _chat_gain_for(user: Dict[str, Any], *, initiated_today: int = 0) -> float:
    current = _coerce_float(user.get("chat_energy_today", 0), 0.0)
    if current < CHAT_DAILY_LIMIT:
        return 1.0
    return 0.0


def _expire_chat_requests(now: int) -> None:
    execute(
        """UPDATE social_chat_requests
           SET status = 'expired', responded_at = %s
           WHERE status = 'pending' AND created_at <= %s""",
        (now, now - CHAT_REQUEST_TTL_SECONDS),
    )


def _daily_chat_requests(user_id: str, day_start: int) -> int:
    row = fetch_one(
        "SELECT COUNT(1) AS c FROM social_chat_requests WHERE from_user_id = %s AND created_at >= %s",
        (user_id, day_start),
    )
    return int(row.get("c", 0) or 0) if row else 0


def request_chat(
    *,
    user_id: str,
    target_name: Optional[str] = None,
    target_user_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}, 404
    now = int(time.time())
    user = _refresh_chat_energy(user_id, user, now)
    if _coerce_float(user.get("chat_energy_today", 0), 0.0) >= CHAT_DAILY_LIMIT:
        return {
            "success": False,
            "code": "CHAT_LIMIT",
            "message": "今日论道收益已达上限，无法主动发起",
        }, 400
    _expire_chat_requests(now)
    if _daily_chat_requests(user_id, midnight_timestamp()) >= CHAT_REQUEST_DAILY_LIMIT:
        return {
            "success": False,
            "code": "CHAT_REQUEST_LIMIT",
            "message": "今日论道请求次数已达上限",
        }, 400
    if target_user_id:
        target = get_user_by_id(str(target_user_id))
    else:
        target_name = (target_name or "").strip()
        if not target_name:
            return {"success": False, "code": "MISSING_PARAMS", "message": "缺少玩家名"}, 400
        target = get_user_by_username(target_name)
    if not target:
        return {"success": False, "code": "TARGET_NOT_FOUND", "message": "未找到该玩家"}, 404
    if str(target.get("user_id")) == str(user_id):
        return {"success": False, "code": "INVALID_TARGET", "message": "不能向自己发起论道"}, 400
    target_tid = (target.get("telegram_id") or "").strip()
    if not target_tid:
        return {"success": False, "code": "TARGET_OFFLINE", "message": "对方尚未激活机器人"}, 400

    try:
        with db_transaction() as cur:
            cur.execute(
                """
                INSERT INTO social_chat_requests (from_user_id, to_user_id, status, created_at)
                VALUES (%s, %s, 'pending', %s)
                RETURNING id
                """,
                (user_id, target["user_id"], now),
            )
            request_id = cur.fetchone()["id"]
    except psycopg2.errors.UniqueViolation:
        return {"success": False, "code": "PENDING", "message": "已有待处理的论道请求"}, 400

    log_event(
        "social_chat_request",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"target_user_id": target.get("user_id")},
    )
    return {
        "success": True,
        "request_id": int(request_id),
        "from_user_id": user_id,
        "from_username": user.get("in_game_username"),
        "target_user_id": target.get("user_id"),
        "target_username": target.get("in_game_username"),
        "target_telegram_id": target_tid,
    }, 200


def accept_chat_request(*, user_id: str, request_id: int) -> Tuple[Dict[str, Any], int]:
    req = fetch_one("SELECT * FROM social_chat_requests WHERE id = %s", (request_id,))
    if not req:
        return {"success": False, "code": "NOT_FOUND", "message": "请求不存在"}, 404
    if str(req.get("to_user_id")) != str(user_id):
        return {"success": False, "code": "FORBIDDEN", "message": "无权处理该请求"}, 403
    if req.get("status") != "pending":
        return {"success": False, "code": "INVALID", "message": "请求已处理"}, 400
    now = int(time.time())
    if int(req.get("created_at", 0) or 0) <= now - CHAT_REQUEST_TTL_SECONDS:
        execute(
            "UPDATE social_chat_requests SET status = 'expired', responded_at = %s WHERE id = %s AND status = 'pending'",
            (now, request_id),
        )
        return {"success": False, "code": "EXPIRED", "message": "论道请求已过期"}, 400

    from_id = str(req.get("from_user_id"))
    to_id = str(req.get("to_user_id"))
    from_user = get_user_by_id(from_id)
    to_user = get_user_by_id(to_id)
    if not from_user or not to_user:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404

    from_user = _refresh_chat_energy(from_id, from_user, now)
    to_user = _refresh_chat_energy(to_id, to_user, now)

    day_start = midnight_timestamp()
    from_initiated = _daily_chat_requests(from_id, day_start)
    to_initiated = _daily_chat_requests(to_id, day_start)
    from_gain = _chat_gain_for(from_user, initiated_today=from_initiated)
    to_gain = _chat_gain_for(to_user, initiated_today=to_initiated)

    exp_gain = int(config.get_nested("balance", "social_chat_exp", default=10) or 10)
    from_exp_gain = exp_gain if from_gain > 0 else 0
    to_exp_gain = exp_gain if to_gain > 0 else 0

    refreshed_from = refresh_user_stamina(from_id, now=now) or from_user
    refreshed_to = refresh_user_stamina(to_id, now=now) or to_user
    before_from_stamina = _coerce_float(refreshed_from.get("stamina", DEFAULT_STAMINA_MAX), float(DEFAULT_STAMINA_MAX))
    before_to_stamina = _coerce_float(refreshed_to.get("stamina", DEFAULT_STAMINA_MAX), float(DEFAULT_STAMINA_MAX))

    with db_transaction() as cur:
        cur.execute(
            """
            UPDATE social_chat_requests
            SET status = 'accepted', responded_at = %s
            WHERE id = %s AND status = 'pending'
            """,
            (now, request_id),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "INVALID", "message": "请求已处理"}, 400
        cur.execute(
            """
            UPDATE users
            SET stamina = LEAST(%s, stamina + %s),
                stamina_updated_at = CASE WHEN stamina + %s >= %s THEN %s ELSE stamina_updated_at END,
                exp = exp + %s,
                chat_energy_today = (CASE WHEN chat_energy_reset < %s THEN 0 ELSE chat_energy_today END) + %s,
                chat_energy_reset = CASE WHEN chat_energy_reset < %s THEN %s ELSE chat_energy_reset END
            WHERE user_id = %s
            """,
            (
                float(DEFAULT_STAMINA_MAX),
                float(from_gain),
                float(from_gain),
                float(DEFAULT_STAMINA_MAX),
                now,
                from_exp_gain,
                day_start,
                float(from_gain),
                day_start,
                now,
                from_id,
            ),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404
        cur.execute(
            """
            UPDATE users
            SET stamina = LEAST(%s, stamina + %s),
                stamina_updated_at = CASE WHEN stamina + %s >= %s THEN %s ELSE stamina_updated_at END,
                exp = exp + %s,
                chat_energy_today = (CASE WHEN chat_energy_reset < %s THEN 0 ELSE chat_energy_today END) + %s,
                chat_energy_reset = CASE WHEN chat_energy_reset < %s THEN %s ELSE chat_energy_reset END
            WHERE user_id = %s
            """,
            (
                float(DEFAULT_STAMINA_MAX),
                float(to_gain),
                float(to_gain),
                float(DEFAULT_STAMINA_MAX),
                now,
                to_exp_gain,
                day_start,
                float(to_gain),
                day_start,
                now,
                to_id,
            ),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "玩家不存在"}, 404

    updated_from = get_user_by_id(from_id) or refreshed_from
    updated_to = get_user_by_id(to_id) or refreshed_to
    from_stamina_applied = max(0.0, _coerce_float(updated_from.get("stamina", before_from_stamina), before_from_stamina) - before_from_stamina)
    to_stamina_applied = max(0.0, _coerce_float(updated_to.get("stamina", before_to_stamina), before_to_stamina) - before_to_stamina)
    from_energy = _coerce_float(updated_from.get("chat_energy_today", 0), 0.0)
    to_energy = _coerce_float(updated_to.get("chat_energy_today", 0), 0.0)

    log_event(
        "social_chat_accept",
        user_id=to_id,
        success=True,
        rank=int(to_user.get("rank", 1) or 1),
        meta={"from_user_id": from_id, "from_gain": from_gain, "to_gain": to_gain},
    )
    return {
        "success": True,
        "request_id": int(request_id),
        "from_user_id": from_id,
        "from_username": updated_from.get("in_game_username"),
        "from_telegram_id": (updated_from.get("telegram_id") or "").strip(),
        "to_user_id": to_id,
        "to_username": updated_to.get("in_game_username"),
        "to_telegram_id": (updated_to.get("telegram_id") or "").strip(),
        "from_stamina_gain": round(from_stamina_applied, 2),
        "to_stamina_gain": round(to_stamina_applied, 2),
        "exp_gain": exp_gain,
        "from_exp_gain": from_exp_gain,
        "to_exp_gain": to_exp_gain,
        "from_chat_capped": from_gain <= 0,
        "to_chat_capped": to_gain <= 0,
        "from_chat_energy": round(from_energy, 2),
        "to_chat_energy": round(to_energy, 2),
    }, 200


def reject_chat_request(*, user_id: str, request_id: int) -> Tuple[Dict[str, Any], int]:
    req = fetch_one("SELECT * FROM social_chat_requests WHERE id = %s", (request_id,))
    if not req:
        return {"success": False, "code": "NOT_FOUND", "message": "请求不存在"}, 404
    if str(req.get("to_user_id")) != str(user_id):
        return {"success": False, "code": "FORBIDDEN", "message": "无权处理该请求"}, 403
    if req.get("status") != "pending":
        return {"success": False, "code": "INVALID", "message": "请求已处理"}, 400
    now = int(time.time())
    from_id = str(req.get("from_user_id"))
    to_id = str(req.get("to_user_id"))
    from_user = get_user_by_id(from_id)
    to_user = get_user_by_id(to_id)
    with db_transaction() as cur:
        cur.execute(
            "UPDATE social_chat_requests SET status = 'rejected', responded_at = %s WHERE id = %s AND status = 'pending'",
            (now, request_id),
        )
        if cur.rowcount == 0:
            return {"success": False, "code": "INVALID", "message": "请求已处理"}, 400
    return {
        "success": True,
        "request_id": int(request_id),
        "from_user_id": from_id,
        "from_username": (from_user or {}).get("in_game_username"),
        "from_telegram_id": ((from_user or {}).get("telegram_id") or "").strip(),
        "to_user_id": to_id,
        "to_username": (to_user or {}).get("in_game_username"),
    }, 200
