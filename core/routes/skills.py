"""技能路由。"""

import psycopg2.errors
import time

from flask import Blueprint

from core.routes._helpers import (
    error,
    success,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.database.connection import (
    get_user_by_id,
    db_transaction,
    get_user_skills,
    set_equipped_skill,
    unequip_all_skills,
    unequip_skill,
    has_skill,
)
from core.game.skills import get_skill, get_unlockable_skills
from core.services.skills_service import use_skill_book, mastery_required, SKILL_MAX_LEVEL

skills_bp = Blueprint("skills", __name__)


@skills_bp.route("/api/skills/<user_id>", methods=["GET"])
def skills_list(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    learned = get_user_skills(user_id)
    for row in learned:
        level = int(row.get("skill_level", 1) or 1)
        row["skill_level"] = level
        row["mastery_required"] = 0 if level >= SKILL_MAX_LEVEL else mastery_required(level)
        row["max_level"] = SKILL_MAX_LEVEL
        skill_def = get_skill(str(row.get("skill_id") or ""))
        if skill_def:
            row.setdefault("name", skill_def.get("name", ""))
            row.setdefault("desc", skill_def.get("desc", ""))
            row.setdefault("type", skill_def.get("type", ""))
            row.setdefault("unlock_rank", skill_def.get("unlock_rank", 1))
    unlockable = get_unlockable_skills(
        int(user.get("rank", 1) or 1),
        user_element=user.get("element"),
    )
    return success(learned=learned, unlockable=unlockable)


@skills_bp.route("/api/skills/learn", methods=["POST"])
def skill_learn():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    skill_id = data.get("skill_id")
    log_action("skill_learn", user_id=user_id, skill_id=skill_id)
    if not user_id or not skill_id:
        return error("ERROR", "Missing parameters", 400)
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    if has_skill(user_id, skill_id):
        return error("ERROR", "已学会该技能", 400)
    skill = get_skill(skill_id)
    if not skill:
        return error("ERROR", "技能不存在", 404)
    if skill.get("element"):
        user_element = user.get("element")
        if not user_element:
            return error("ERROR", "未选择五行，无法学习该技能", 400)
        if str(user_element) != str(skill.get("element")):
            return error("ERROR", "五行不符，无法学习该技能", 400)
    if user.get("rank", 1) < skill["unlock_rank"]:
        return error("ERROR", "境界不足，无法学习", 400)
    cost_copper = int(skill.get("cost_copper", 0) or 0)
    cost_gold = int(skill.get("cost_gold", 0) or 0)
    if user.get("copper", 0) < cost_copper or user.get("gold", 0) < cost_gold:
        return error("ERROR", "资源不足，无法学习", 400)

    try:
        with db_transaction() as cur:
            cur.execute(
                """
                UPDATE users
                SET copper = copper - %s, gold = gold - %s
                WHERE user_id = %s AND copper >= %s AND gold >= %s
                """,
                (cost_copper, cost_gold, user_id, cost_copper, cost_gold),
            )
            if int(cur.rowcount or 0) == 0:
                return error("ERROR", "资源不足，无法学习", 400)
            cur.execute(
                "INSERT INTO user_skills (user_id, skill_id, equipped, learned_at, skill_level, mastery_exp, last_used_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (user_id, skill_id, 0, int(time.time()), 1, 0, 0),
            )
    except psycopg2.errors.UniqueViolation:
        return error("ERROR", "已学会该技能", 400)

    if skill.get("type") == "active":
        set_equipped_skill(user_id, skill_id)

    return success(message=f"学会技能：{skill['name']}")


@skills_bp.route("/api/skills/equip", methods=["POST"])
def skill_equip():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    skill_id = data.get("skill_id")
    log_action("skill_equip", user_id=user_id, skill_id=skill_id)
    if not user_id or not skill_id:
        return error("ERROR", "Missing parameters", 400)
    if not has_skill(user_id, skill_id):
        return error("ERROR", "尚未学会该技能", 400)
    set_equipped_skill(user_id, skill_id)
    skill = get_skill(skill_id)
    if skill and skill.get("type") == "active":
        return success(message="已装备主动技能（最多同时装备两个）")
    return success(message="已装备技能")


@skills_bp.route("/api/skills/unequip", methods=["POST"])
def skill_unequip():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    skill_id = data.get("skill_id")
    log_action("skill_unequip", user_id=user_id, skill_id=skill_id)
    if not user_id:
        return error("ERROR", "Missing user_id", 400)
    if skill_id:
        unequip_skill(user_id, skill_id)
        return success(message="已卸下技能")
    else:
        unequip_all_skills(user_id)
        return success(message="已卸下所有技能")


@skills_bp.route("/api/skills/upgrade", methods=["POST"])
def skill_upgrade():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    skill_id = data.get("skill_id")
    book_id = data.get("book_id", "skill_book_basic")
    log_action("skill_upgrade", user_id=user_id, skill_id=skill_id, book_id=book_id)
    if not user_id or not skill_id:
        return error("ERROR", "Missing parameters", 400)
    resp, status = use_skill_book(user_id=user_id, skill_id=skill_id, book_id=book_id)
    if resp.get("success"):
        return success(**resp), status
    return error(resp.get("code", "ERROR"), resp.get("message", "失败"), status)
