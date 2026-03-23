"""HTTP client helpers for aiogram adapter."""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any
from urllib.parse import urlparse

import aiohttp

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from adapters.actor_paths import compiled_actor_path_patterns
from core.config import config


SERVER_URL = str(getattr(config, "core_server_url", "") or f"http://127.0.0.1:{int(config.core_server_port)}").rstrip("/")
INTERNAL_API_TOKEN = str(config.internal_api_token or "").strip()
_ACTOR_PATTERNS = compiled_actor_path_patterns()
_HTTP_SESSION: aiohttp.ClientSession | None = None


def new_request_id() -> str:
    return uuid.uuid4().hex


def _extract_actor_user_id(
    url: str,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str | None:
    payload = payload or {}
    params = params or {}
    if payload.get("user_id"):
        return str(payload.get("user_id"))
    if params.get("user_id"):
        return str(params.get("user_id"))
    path = urlparse(str(url or "")).path
    for pattern in _ACTOR_PATTERNS:
        matched = pattern.match(path)
        if matched:
            return str(matched.group(1))
    return None


async def _get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        _HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _HTTP_SESSION


async def close_http_session() -> None:
    global _HTTP_SESSION
    if _HTTP_SESSION is not None and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
    _HTTP_SESSION = None


async def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    actor_uid: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    url = path if path.startswith("http://") or path.startswith("https://") else f"{SERVER_URL}{path}"
    headers: dict[str, str] = {}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN
    explicit_actor_uid = str(actor_uid or "").strip()
    inferred_actor_uid = _extract_actor_user_id(url, payload=payload, params=params)
    final_actor_uid = explicit_actor_uid or inferred_actor_uid
    if final_actor_uid:
        headers["X-Actor-User-Id"] = final_actor_uid
    rid = str(request_id or "").strip()
    if rid:
        headers["X-Request-Id"] = rid

    session = await _get_http_session()
    async with session.request(method, url, json=payload, params=params, headers=headers) as response:
        try:
            data = await response.json(content_type=None)
            if isinstance(data, dict):
                return data
            return {"success": False, "message": "Invalid response format"}
        except Exception:
            raw = await response.text()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            return {
                "success": False,
                "code": "NON_JSON_RESPONSE",
                "message": "Core returned non-json response",
                "status_code": int(getattr(response, "status", 0) or 0),
                "raw_text": raw[:500],
            }


async def api_get(
    path: str,
    params: dict[str, Any] | None = None,
    actor_uid: str | None = None,
) -> dict[str, Any]:
    return await request_json("GET", path, params=params, actor_uid=actor_uid)


async def api_post(
    path: str,
    payload: dict[str, Any],
    actor_uid: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    rid = request_id or new_request_id()
    return await request_json("POST", path, payload=payload, actor_uid=actor_uid, request_id=rid)


async def resolve_uid(tg_user_id: int) -> str | None:
    data = await api_get("/api/user/lookup", params={"platform": "telegram", "platform_id": str(tg_user_id)})
    if data.get("success"):
        return str(data.get("user_id"))
    return None
