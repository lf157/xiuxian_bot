"""Mainline story routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.routes._helpers import (
    error,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.services.story_service import (
    claim_story_chapter,
    get_available_volume_chapters,
    get_chapter_next_lines,
    get_story_status,
    reset_chapter_progress,
)


story_bp = Blueprint("story", __name__)


@story_bp.route("/api/story/<user_id>", methods=["GET"])
def story_status(user_id: str):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    resp, http_status = get_story_status(user_id)
    return jsonify(resp), http_status


@story_bp.route("/api/story/claim", methods=["POST"])
def story_claim():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    chapter_id = str(data.get("chapter_id") or "").strip() or None
    log_action("story_claim", user_id=user_id, chapter_id=chapter_id or "")
    resp, http_status = claim_story_chapter(user_id, chapter_id)
    if not resp.get("success") and resp.get("code") == "NO_PENDING":
        return error("NO_PENDING", "No claimable story chapter", 404)
    return jsonify(resp), http_status


# ── Volume story endpoints ──────────────────────────────────


@story_bp.route("/api/story/volumes/<user_id>", methods=["GET"])
def volume_chapters(user_id: str):
    """List available volume chapters for the player."""
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    resp, http_status = get_available_volume_chapters(user_id)
    return jsonify(resp), http_status


@story_bp.route("/api/story/read", methods=["POST"])
def story_read():
    """Read next N lines of a volume chapter."""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    chapter_id = str(data.get("chapter_id") or "").strip()
    if not chapter_id:
        return error("MISSING_CHAPTER", "chapter_id is required", 400)
    count = min(max(int(data.get("count", 5) or 5), 1), 20)
    resp, http_status = get_chapter_next_lines(user_id, chapter_id, count)
    return jsonify(resp), http_status


@story_bp.route("/api/story/reread", methods=["POST"])
def story_reread():
    """Reset reading progress for a chapter."""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    chapter_id = str(data.get("chapter_id") or "").strip()
    if not chapter_id:
        return error("MISSING_CHAPTER", "chapter_id is required", 400)
    resp, http_status = reset_chapter_progress(user_id, chapter_id)
    return jsonify(resp), http_status

