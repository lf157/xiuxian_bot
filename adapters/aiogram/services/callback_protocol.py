"""Callback protocol helpers for aiogram adapter.

Protocol format:
    <domain>:<action>[:arg1[:arg2...]]
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_CALLBACK_BYTES = 64
DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")
ACTION_RE = re.compile(r"^[a-z0-9_]+$")
ARG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Explicit allow-list from the design spec (v3, section 4.3).
CALLBACK_ACTIONS: dict[str, set[str]] = {
    "menu": {"home", "register", "stat", "back"},
    "cul": {"start", "status", "end"},
    "hunt": {"list", "start", "act_normal", "act_skill", "exit", "settle"},
    "break": {"preview", "help_toggle", "confirm", "cancel"},
    "bag": {"page", "use", "detail"},
    "gear": {"page", "equip", "enhance", "decompose", "equipped_view", "unequip", "detail"},
    "skill": {"list", "learn", "equip", "unequip", "detail"},
    "shop": {"currency", "page", "buy", "back", "noop"},
    "alchemy": {"menu", "craft", "batch", "back"},
    "forge": {"menu", "craft", "enhance", "back"},
    "secret": {"list", "realm", "path", "choice", "act_normal", "act_skill", "exit", "settle"},
    "quest": {"list", "detail", "claim"},
    "event": {"list", "detail", "claim"},
    "boss": {"menu", "attack", "rank"},
    "bounty": {"menu", "refresh", "claim"},
    "story": {"menu", "chapter", "node", "claim"},
    "rank": {"menu", "realm", "combat", "wealth"},
    "social": {"menu", "chat", "dao", "friend", "reply"},
    "pvp": {"menu", "match", "duel", "history", "claim_daily", "refresh"},
    "sect": {"menu", "create", "info", "members", "contribute", "donate", "train", "leave"},
    "admin": {"menu", "test", "lookup", "modify", "preset", "confirm", "cancel"},
}


def _is_valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(value))


def _is_valid_action(value: str) -> bool:
    return bool(ACTION_RE.fullmatch(value))


def _is_valid_arg(value: str) -> bool:
    return bool(ARG_RE.fullmatch(value))


def is_allowed(domain: str, action: str) -> bool:
    actions = CALLBACK_ACTIONS.get(domain)
    return bool(actions and action in actions)


def parse_callback(data: str) -> tuple[str, str, list[str]] | None:
    raw = str(data or "").strip()
    if not raw:
        return None
    if len(raw.encode("utf-8")) > MAX_CALLBACK_BYTES:
        return None
    parts = raw.split(":")
    if len(parts) < 2:
        return None
    domain = parts[0].strip()
    action = parts[1].strip()
    args = [part.strip() for part in parts[2:]]
    if not _is_valid_domain(domain) or not _is_valid_action(action):
        return None
    if not is_allowed(domain, action):
        return None
    if any(not arg or not _is_valid_arg(arg) for arg in args):
        return None
    return domain, action, args


def build_callback(domain: str, action: str, *args: str | int) -> str:
    d = str(domain or "").strip()
    a = str(action or "").strip()
    if not _is_valid_domain(d):
        raise ValueError(f"invalid callback domain: {domain!r}")
    if not _is_valid_action(a):
        raise ValueError(f"invalid callback action: {action!r}")
    if not is_allowed(d, a):
        raise ValueError(f"unsupported callback action: {d}:{a}")
    parts: list[str] = [d, a]
    for arg in args:
        value = str(arg).strip()
        if not value or not _is_valid_arg(value):
            raise ValueError(f"invalid callback arg: {arg!r}")
        parts.append(value)
    data = ":".join(parts)
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback exceeds {MAX_CALLBACK_BYTES} bytes: {data!r}")
    return data


def normalize_callbacks(values: Iterable[str]) -> list[str]:
    """Drop invalid callback strings and keep valid values."""
    result: list[str] = []
    for value in values:
        parsed = parse_callback(value)
        if parsed is None:
            continue
        domain, action, args = parsed
        result.append(build_callback(domain, action, *args))
    return result

