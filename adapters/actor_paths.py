"""Shared actor-user path extraction patterns for adapters."""

from __future__ import annotations

import re
from typing import List, Pattern


# Keep a single source of truth to avoid drift between adapter implementations.
_RAW_PATTERNS = (
    r"^/api/(?:stat|goals|codex|items|skills|quests|weekly|events/status|secret-realms|hunt/status|gacha/pity|gacha/status|pvp/opponents|pvp/records|sect/member|sect/buffs|convert/options|achievements|story)/([^/?#]+)$",
    r"^/api/(?:cultivate/status|signin|forge|forge/catalog)/([^/?#]+)$",
)


def compiled_actor_path_patterns() -> List[Pattern[str]]:
    return [re.compile(raw) for raw in _RAW_PATTERNS]
