"""用户相关路由。"""

from flask import Blueprint, request, jsonify

from core.routes._helpers import (
    error,
    success,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
)
from core.database.connection import get_user_by_id
from core.utils.account_status import get_user_status
from core.services.account_service import register_account
from core.services.story_service import get_story_status, track_story_action
from core.services.codex_service import (
    list_monsters as codex_list_monsters,
    list_items as codex_list_items,
)

user_bp = Blueprint("user", __name__)


@user_bp.route("/api/user/lookup", methods=["GET"])
def lookup_user():
    platform = request.args.get("platform")
    platform_id = request.args.get("platform_id")
    if not platform or not platform_id:
        return error("ERROR", "Missing parameters", 400)

    from core.database.connection import get_user_by_platform
    user = get_user_by_platform(platform, platform_id)
    if not user:
        return error("ERROR", "User not found", 404)

    return success(
        user_id=user["user_id"],
        username=user.get("in_game_username"),
        lang=user.get("lang", "CHS"),
        element=user.get("element"),
        rank=user.get("rank", 1),
    )


@user_bp.route("/api/register", methods=["POST"])
def register_user():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    platform = data.get("platform")
    platform_id = data.get("platform_id")
    username = data.get("username") or f"修士{(platform_id or '')[:6]}"
    lang = "CHS"
    element = data.get("element")

    log_action("register", user_id=f"{platform}:{platform_id}", element=element, lang=lang)

    resp, http_status = register_account(
        platform=platform,
        platform_id=platform_id,
        username=username,
        element=element,
        lang=lang,
    )
    if resp.get("success") and resp.get("user_id"):
        try:
            track_story_action(resp["user_id"], "status_check", amount=0)
            story_resp, _ = get_story_status(resp["user_id"])
            pending = ((story_resp or {}).get("story") or {}).get("pending_claims") or []
            if pending:
                resp["story_intro"] = pending[0]
        except Exception:
            pass
    return jsonify(resp), http_status


@user_bp.route("/api/stat/<user_id>", methods=["GET"])
def user_status(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    status = get_user_status(user_id)
    if not status:
        return error("ERROR", "User not found", 404)
    return success(status=status)


@user_bp.route("/api/codex/<user_id>", methods=["GET"])
def codex_get(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    kind = (request.args.get("kind") or "monsters").lower()
    if kind == "items":
        return success(kind="items", items=codex_list_items(user_id)[:50])
    return success(kind="monsters", monsters=codex_list_monsters(user_id)[:50])
