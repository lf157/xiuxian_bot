"""Service helpers for aiogram adapter."""

from adapters.aiogram.services.api_client import (
    api_get,
    api_post,
    close_http_session,
    new_request_id,
    resolve_uid,
)
from adapters.aiogram.services.callback_protocol import (
    ACTION_RE,
    ARG_RE,
    CALLBACK_ACTIONS,
    DOMAIN_RE,
    MAX_CALLBACK_BYTES,
    build_callback,
    is_allowed,
    parse_callback,
)
from adapters.aiogram.services.navigation import handle_expired_callback, reject_non_owner, reply_or_answer, respond_query, safe_answer

__all__ = [
    "ACTION_RE",
    "ARG_RE",
    "CALLBACK_ACTIONS",
    "DOMAIN_RE",
    "MAX_CALLBACK_BYTES",
    "api_get",
    "api_post",
    "build_callback",
    "close_http_session",
    "handle_expired_callback",
    "is_allowed",
    "new_request_id",
    "parse_callback",
    "reject_non_owner",
    "reply_or_answer",
    "resolve_uid",
    "respond_query",
    "safe_answer",
]

