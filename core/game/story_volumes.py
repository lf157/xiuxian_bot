"""Volume story loader – parses volumes/*.yaml into line-by-line scenes.

Each chapter is flattened into a list of *lines*, where each line is a short
piece of text shown to the player one at a time (like a visual novel).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_VOLUMES_DIR = Path(__file__).resolve().parent.parent.parent / "texts" / "story" / "volumes"

# ---------------------------------------------------------------------------
# Low-level YAML loading
# ---------------------------------------------------------------------------

def _load_all_volumes() -> Dict[str, Any]:
    """Load every volume_*.yaml and return {volume_key: parsed_dict}."""
    result: Dict[str, Any] = {}
    if not _VOLUMES_DIR.is_dir():
        return result
    for fpath in sorted(_VOLUMES_DIR.glob("volume_*.yaml")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k.startswith("volume_") and isinstance(v, dict):
                        result[k] = v
        except Exception:
            pass
    return result


@lru_cache(maxsize=1)
def _cached_volumes() -> Dict[str, Any]:
    return _load_all_volumes()


def reload_volumes() -> None:
    """Force reload (call after hot-editing YAML in dev)."""
    _cached_volumes.cache_clear()


# ---------------------------------------------------------------------------
# Line builder – converts a chapter's scenes into a flat list of display lines
# ---------------------------------------------------------------------------

_LINE_PAUSE = "···"  # visual pause marker (optional)


def _split_text_block(text: str) -> List[str]:
    """Split a multi-line text block into display lines.

    Rules:
    - Blank lines become pause markers
    - Each non-blank paragraph is one display line
    """
    raw = text.strip()
    if not raw:
        return []
    paragraphs = re.split(r"\n\s*\n", raw)
    lines: List[str] = []
    for p in paragraphs:
        cleaned = p.strip()
        if cleaned:
            # collapse internal newlines within a paragraph into spaces
            # but keep Chinese text readable – just strip leading spaces per line
            merged = "\n".join(l.strip() for l in cleaned.splitlines() if l.strip())
            lines.append(merged)
    return lines


def _scene_to_lines(scene: Dict[str, Any]) -> List[Dict[str, str]]:
    """Convert one scene dict to a list of {type, speaker?, text} line dicts."""
    scene_type = scene.get("type", "narration")
    result: List[Dict[str, str]] = []

    if scene_type == "narration":
        for para in _split_text_block(scene.get("text", "")):
            result.append({"type": "narration", "text": para})

    elif scene_type == "dialogue":
        for line in scene.get("lines", []):
            speaker = line.get("speaker")
            text = (line.get("text") or "").strip()
            if not text:
                continue
            for para in _split_text_block(text):
                entry: Dict[str, str] = {"type": "dialogue", "text": para}
                if speaker:
                    entry["speaker"] = speaker
                result.append(entry)

    elif scene_type == "choice":
        choices = scene.get("choices", [])
        for i, c in enumerate(choices, 1):
            label = c.get("label", "...")
            result.append({"type": "choice", "text": f"{i}. {label}"})

    return result


def _chapter_to_lines(chapter: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flatten all scenes of a chapter into ordered display lines."""
    lines: List[Dict[str, str]] = []
    scenes = chapter.get("scenes", [])
    if not scenes:
        # chapter may only have summary / title
        summary = chapter.get("summary", "")
        if summary:
            lines.append({"type": "narration", "text": summary})
        return lines
    for scene in scenes:
        lines.extend(_scene_to_lines(scene))
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_chapter_ids() -> List[str]:
    """Return all chapter IDs in order across all volumes."""
    ids: List[str] = []
    for _vkey, vol in sorted(_cached_volumes().items()):
        chapters = vol.get("chapters") or {}
        for ch_key in chapters:
            ids.append(ch_key)
    return ids


def get_volume_chapter_list() -> List[Dict[str, Any]]:
    """Return a flat list of {volume, chapter_id, title, trigger, realm_range}."""
    result: List[Dict[str, Any]] = []
    for vkey, vol in sorted(_cached_volumes().items()):
        realm_range = vol.get("realm_range", [1, 36])
        vol_title = vol.get("title", vkey)
        chapters = vol.get("chapters") or {}
        for ch_key, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            result.append({
                "volume": vkey,
                "volume_title": vol_title,
                "chapter_id": ch_key,
                "title": ch_data.get("title", ch_key),
                "trigger": ch_data.get("trigger", {}),
                "realm_range": realm_range,
                "has_scenes": bool(ch_data.get("scenes")),
            })
    return result


def get_chapter_lines(chapter_id: str) -> Optional[List[Dict[str, str]]]:
    """Get the flattened display lines for a given chapter_id.

    Returns None if chapter not found, or a list of line dicts:
      [{"type": "narration"|"dialogue"|"choice", "text": "...", "speaker": "..."}]
    """
    for _vkey, vol in _cached_volumes().items():
        chapters = vol.get("chapters") or {}
        if chapter_id in chapters:
            ch = chapters[chapter_id]
            if isinstance(ch, dict):
                return _chapter_to_lines(ch)
    return None


def get_chapter_info(chapter_id: str) -> Optional[Dict[str, Any]]:
    """Get chapter metadata (title, trigger, map, rewards) without lines."""
    for vkey, vol in _cached_volumes().items():
        chapters = vol.get("chapters") or {}
        if chapter_id in chapters:
            ch = chapters[chapter_id]
            if isinstance(ch, dict):
                return {
                    "volume": vkey,
                    "volume_title": vol.get("title", vkey),
                    "chapter_id": chapter_id,
                    "title": ch.get("title", chapter_id),
                    "trigger": ch.get("trigger", {}),
                    "map": ch.get("map"),
                    "rewards": ch.get("rewards"),
                    "summary": ch.get("summary", ""),
                }
    return None


def format_line_for_display(line: Dict[str, str]) -> str:
    """Format a single line dict into a display string for Telegram."""
    ltype = line.get("type", "narration")
    text = line.get("text", "")
    speaker = line.get("speaker")

    if ltype == "dialogue" and speaker:
        return f"*{speaker}*：{text}"
    elif ltype == "choice":
        return f"  {text}"
    else:
        return text
