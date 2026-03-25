"""Smoke test for social chat stamina rules (auto creates temp DB)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path


def _assert_close(label: str, actual: float, expected: float, tol: float = 1e-6) -> None:
    if abs(float(actual) - float(expected)) > tol:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _expect(
    result: tuple[dict, int],
    label: str,
    *,
    expected_status: int,
    expected_code: str | None = None,
) -> dict:
    payload, status = result
    if status != expected_status:
        raise AssertionError(f"{label}: expected status={expected_status}, got {status}, payload={payload}")
    if expected_code is not None and payload.get("code") != expected_code:
        raise AssertionError(f"{label}: expected code={expected_code}, got {payload}")
    return payload


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from core.database.connection import connect_db, create_tables, execute, fetch_one, close_db
    from core.services.social_service import (
        CHAT_REQUEST_TTL_SECONDS,
        accept_chat_request,
        reject_chat_request,
        request_chat,
    )
    from core.utils.timeutil import midnight_timestamp

    connect_db()
    create_tables()
    now = int(time.time())

    def create_user(user_id: str, name: str, telegram_id: str, stamina: float = 5.0) -> None:
        execute(
            """
            INSERT INTO users (
                user_id, in_game_username, telegram_id,
                stamina, stamina_updated_at,
                chat_energy_today, chat_energy_reset,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, telegram_id, float(stamina), now, 0.0, now, now),
        )

    def get_user(uid: str):
        return fetch_one("SELECT * FROM users WHERE user_id = %s", (uid,))

    try:
        # Create users:
        # u1/u2/u3: normal participants
        # u4: request-count-limit check
        # u5: pending/concurrent request check
        # u_off: offline target (no telegram_id)
        create_user("u1", "A", "tg_a")
        create_user("u2", "B", "tg_b")
        create_user("u3", "C", "tg_c")
        create_user("u4", "D", "tg_d")
        create_user("u5", "E", "tg_e")
        create_user("u_off", "OFF", "")

        # Validation matrix for request + accept + reject.
        _expect(request_chat(user_id="u1"), "request missing params", expected_status=400, expected_code="MISSING_PARAMS")
        _expect(request_chat(user_id="u1", target_user_id="u1"), "request invalid target", expected_status=400, expected_code="INVALID_TARGET")
        _expect(request_chat(user_id="u1", target_name="不存在用户"), "request target not found", expected_status=404, expected_code="TARGET_NOT_FOUND")
        _expect(request_chat(user_id="ghost", target_user_id="u2"), "request user not found", expected_status=404, expected_code="USER_NOT_FOUND")
        _expect(request_chat(user_id="u1", target_user_id="u_off"), "request target offline", expected_status=400, expected_code="TARGET_OFFLINE")
        _expect(accept_chat_request(user_id="u2", request_id=999999), "accept missing request", expected_status=404, expected_code="NOT_FOUND")
        _expect(reject_chat_request(user_id="u2", request_id=999998), "reject missing request", expected_status=404, expected_code="NOT_FOUND")

        # target_name positive path.
        req = _expect(request_chat(user_id="u1", target_name="B"), "request by name", expected_status=200)
        _expect(reject_chat_request(user_id="u2", request_id=int(req["request_id"])), "reject by name cleanup", expected_status=200)

        # accept FORBIDDEN + reject FORBIDDEN.
        req = _expect(request_chat(user_id="u1", target_user_id="u2"), "request for accept forbidden", expected_status=200)
        req_id = int(req["request_id"])
        _expect(accept_chat_request(user_id="u3", request_id=req_id), "accept forbidden", expected_status=403, expected_code="FORBIDDEN")
        _expect(reject_chat_request(user_id="u2", request_id=req_id), "accept forbidden cleanup", expected_status=200)

        req = _expect(request_chat(user_id="u1", target_user_id="u2"), "request for reject forbidden", expected_status=200)
        req_id = int(req["request_id"])
        _expect(reject_chat_request(user_id="u3", request_id=req_id), "reject forbidden", expected_status=403, expected_code="FORBIDDEN")
        _expect(reject_chat_request(user_id="u2", request_id=req_id), "reject forbidden cleanup", expected_status=200)

        # PENDING branch.
        req = _expect(request_chat(user_id="u5", target_user_id="u2"), "request pending seed", expected_status=200)
        pending_req_id = int(req["request_id"])
        _expect(request_chat(user_id="u5", target_user_id="u2"), "request pending duplicate", expected_status=400, expected_code="PENDING")
        _expect(reject_chat_request(user_id="u2", request_id=pending_req_id), "pending cleanup", expected_status=200)

        # Concurrent duplicate request: one should succeed, one should hit PENDING.
        barrier = threading.Barrier(3, timeout=5)
        req_results: list[tuple[dict, int]] = []
        req_errors: list[Exception] = []

        def _request_worker() -> None:
            try:
                barrier.wait()
                req_results.append(request_chat(user_id="u5", target_user_id="u2"))
            except Exception as exc:
                req_errors.append(exc)

        t1 = threading.Thread(target=_request_worker, daemon=True)
        t2 = threading.Thread(target=_request_worker, daemon=True)
        t1.start()
        t2.start()
        barrier.wait()
        t1.join(timeout=5)
        t2.join(timeout=5)
        if t1.is_alive() or t2.is_alive():
            raise AssertionError("concurrent request threads did not finish")
        if req_errors:
            raise AssertionError(f"concurrent request errors: {req_errors}")
        req_statuses = sorted([status for _, status in req_results])
        if req_statuses != [200, 400]:
            raise AssertionError(f"concurrent request expected [200, 400], got {req_statuses} results={req_results}")
        req_400 = [payload for payload, status in req_results if status == 400]
        if not req_400 or req_400[0].get("code") != "PENDING":
            raise AssertionError(f"concurrent request expected PENDING payload, got {req_results}")
        req_success = [payload for payload, status in req_results if status == 200]
        if req_success and req_success[0].get("request_id"):
            _expect(
                reject_chat_request(user_id="u2", request_id=int(req_success[0]["request_id"])),
                "concurrent request cleanup",
                expected_status=200,
            )

        # EXPIRED branch.
        req = _expect(request_chat(user_id="u1", target_user_id="u2"), "request for expired", expected_status=200)
        expired_req_id = int(req["request_id"])
        execute(
            "UPDATE social_chat_requests SET created_at = %s WHERE id = %s",
            (int(time.time()) - CHAT_REQUEST_TTL_SECONDS - 1, expired_req_id),
        )
        _expect(accept_chat_request(user_id="u2", request_id=expired_req_id), "accept expired", expected_status=400, expected_code="EXPIRED")
        expired_row = fetch_one("SELECT status FROM social_chat_requests WHERE id = %s", (expired_req_id,))
        if (expired_row or {}).get("status") != "expired":
            raise AssertionError(f"expired request status mismatch: {expired_row}")

        # accept USER_NOT_FOUND branch via manual row.
        execute(
            "INSERT INTO social_chat_requests (from_user_id, to_user_id, status, created_at) VALUES (%s, %s, 'pending', %s)",
            ("ghost_from", "u2", int(time.time())),
        )
        ghost_req = fetch_one("SELECT id FROM social_chat_requests WHERE from_user_id = %s ORDER BY id DESC LIMIT 1", ("ghost_from",))
        if not ghost_req:
            raise AssertionError("failed to seed ghost request")
        _expect(
            accept_chat_request(user_id="u2", request_id=int(ghost_req["id"])),
            "accept user not found",
            expected_status=404,
            expected_code="USER_NOT_FOUND",
        )

        # A initiates 10 chats to B, both should gain +1 stamina each time.
        for _ in range(10):
            req = _expect(request_chat(user_id="u1", target_user_id="u2"), "request main loop", expected_status=200)
            _expect(accept_chat_request(user_id="u2", request_id=int(req["request_id"])), "accept main loop", expected_status=200)

        a = get_user("u1")
        b = get_user("u2")
        _assert_close("A chat_energy_today after 10", a.get("chat_energy_today", 0), 10.0)
        _assert_close("B chat_energy_today after 10", b.get("chat_energy_today", 0), 10.0)
        _assert_close("A stamina after 10", a.get("stamina", 0), 15.0)
        _assert_close("B stamina after 10", b.get("stamina", 0), 15.0)

        # A should be blocked from initiating now.
        _expect(request_chat(user_id="u1", target_user_id="u2"), "request chat limit", expected_status=400, expected_code="CHAT_LIMIT")

        # C initiates to A; A accepts and should gain 0.0 (hard cap), C gains 1.0.
        req = _expect(request_chat(user_id="u3", target_user_id="u1"), "request C->A", expected_status=200)
        accepted = _expect(
            accept_chat_request(user_id="u1", request_id=int(req["request_id"])),
            "accept C->A",
            expected_status=200,
        )
        _assert_close("A 11th stamina gain", accepted.get("to_stamina_gain", 0), 0.0)
        _assert_close("C 1st stamina gain", accepted.get("from_stamina_gain", 0), 1.0)

        a = get_user("u1")
        c = get_user("u3")
        _assert_close("A chat_energy_today after 11", a.get("chat_energy_today", 0), 10.0)
        _assert_close("C chat_energy_today after 1", c.get("chat_energy_today", 0), 1.0)
        _assert_close("A stamina after 11", a.get("stamina", 0), 15.0)
        _assert_close("C stamina after 1", c.get("stamina", 0), 6.0)

        # Daily reset branch.
        execute(
            "UPDATE users SET chat_energy_today = %s, chat_energy_reset = %s, stamina = %s, stamina_updated_at = %s WHERE user_id = %s",
            (10.0, midnight_timestamp() - 86400, 5.0, now, "u1"),
        )
        req = _expect(request_chat(user_id="u1", target_user_id="u2"), "request after reset", expected_status=200)
        _expect(accept_chat_request(user_id="u2", request_id=int(req["request_id"])), "accept after reset", expected_status=200)
        a = get_user("u1")
        if float(a.get("chat_energy_today", 0) or 0) > 2.0:
            raise AssertionError(f"daily reset not applied, chat_energy_today too high: {a.get('chat_energy_today')}")

        # Reject flow + second reject should become INVALID.
        req = _expect(request_chat(user_id="u3", target_user_id="u2"), "request for reject", expected_status=200)
        reject_id = int(req["request_id"])
        _expect(reject_chat_request(user_id="u2", request_id=reject_id), "reject success", expected_status=200)
        _expect(reject_chat_request(user_id="u2", request_id=reject_id), "reject second invalid", expected_status=400, expected_code="INVALID")

        # Serial second accept should be INVALID.
        req = _expect(request_chat(user_id="u3", target_user_id="u2"), "request for serial accept", expected_status=200)
        serial_req_id = int(req["request_id"])
        _expect(accept_chat_request(user_id="u2", request_id=serial_req_id), "serial accept first", expected_status=200)
        _expect(
            accept_chat_request(user_id="u2", request_id=serial_req_id),
            "serial accept second invalid",
            expected_status=400,
            expected_code="INVALID",
        )

        # Concurrency: accept same request in two threads, expect one success and one INVALID.
        req = _expect(request_chat(user_id="u3", target_user_id="u1"), "request for concurrent accept", expected_status=200)
        concurrent_req_id = int(req["request_id"])
        barrier = threading.Barrier(3, timeout=5)
        results: list[tuple[int, dict]] = []
        errors: list[Exception] = []

        def _accept_worker() -> None:
            try:
                barrier.wait()
                payload, status = accept_chat_request(user_id="u1", request_id=concurrent_req_id)
                results.append((status, payload))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_accept_worker, daemon=True)
        t2 = threading.Thread(target=_accept_worker, daemon=True)
        t1.start()
        t2.start()
        barrier.wait()
        t1.join(timeout=5)
        t2.join(timeout=5)
        if t1.is_alive() or t2.is_alive():
            raise AssertionError("concurrent accept threads did not finish")
        if errors:
            raise AssertionError(f"concurrent accept errors: {errors}")
        statuses = sorted([status for status, _ in results])
        if statuses != [200, 400]:
            raise AssertionError(f"concurrent accept expected [200, 400], got {statuses}, results={results}")
        invalid_payloads = [payload for status, payload in results if status == 400]
        if not invalid_payloads or invalid_payloads[0].get("code") != "INVALID":
            raise AssertionError(f"concurrent accept invalid payload mismatch: {results}")

        # Concurrency: accept vs reject same request, only one should succeed.
        req = _expect(request_chat(user_id="u3", target_user_id="u2"), "request for accept-vs-reject", expected_status=200)
        ar_req_id = int(req["request_id"])
        barrier = threading.Barrier(3, timeout=5)
        ar_results: list[tuple[str, int, dict]] = []
        ar_errors: list[Exception] = []

        def _accept_vs_reject_accept() -> None:
            try:
                barrier.wait()
                payload, status = accept_chat_request(user_id="u2", request_id=ar_req_id)
                ar_results.append(("accept", status, payload))
            except Exception as exc:
                ar_errors.append(exc)

        def _accept_vs_reject_reject() -> None:
            try:
                barrier.wait()
                payload, status = reject_chat_request(user_id="u2", request_id=ar_req_id)
                ar_results.append(("reject", status, payload))
            except Exception as exc:
                ar_errors.append(exc)

        t1 = threading.Thread(target=_accept_vs_reject_accept, daemon=True)
        t2 = threading.Thread(target=_accept_vs_reject_reject, daemon=True)
        t1.start()
        t2.start()
        barrier.wait()
        t1.join(timeout=5)
        t2.join(timeout=5)
        if t1.is_alive() or t2.is_alive():
            raise AssertionError("accept-vs-reject threads did not finish")
        if ar_errors:
            raise AssertionError(f"accept-vs-reject errors: {ar_errors}")
        ar_statuses = sorted([status for _, status, _ in ar_results])
        if ar_statuses != [200, 400]:
            raise AssertionError(f"accept-vs-reject expected [200, 400], got {ar_statuses}, results={ar_results}")
        invalid_ar = [payload for _, status, payload in ar_results if status == 400]
        if not invalid_ar or invalid_ar[0].get("code") != "INVALID":
            raise AssertionError(f"accept-vs-reject invalid payload mismatch: {ar_results}")

        # CHAT_REQUEST_LIMIT branch: fake 20 requests today for u4, then request should be blocked.
        day_start = midnight_timestamp()
        execute("UPDATE users SET chat_energy_today = 0, chat_energy_reset = %s WHERE user_id = %s", (int(time.time()), "u4"))
        for i in range(20):
            execute(
                "INSERT INTO social_chat_requests (from_user_id, to_user_id, status, created_at, responded_at) VALUES (%s, %s, %s, %s, %s)",
                ("u4", "u2", "rejected", day_start + i + 1, day_start + i + 1),
            )
        _expect(
            request_chat(user_id="u4", target_user_id="u2"),
            "request daily request-count limit",
            expected_status=400,
            expected_code="CHAT_REQUEST_LIMIT",
        )

        print(
            "OK: social chat smoke validated "
            "(validation/forbidden/pending/expired/reset/limit/concurrency)."
        )
    finally:
        close_db()


if __name__ == "__main__":
    main()
