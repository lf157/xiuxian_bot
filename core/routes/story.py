"""Mainline story routes."""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.routes._helpers import (
    error,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.services.story_service import claim_story_chapter, get_story_status


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

