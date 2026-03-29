"""区域移动路由。"""

import json

from flask import Blueprint, jsonify

from core.routes._helpers import (
    error,
    success,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.database.connection import fetch_one, execute_query, db_transaction
from core.game.maps import (
    get_map,
    get_maps_by_tier,
    get_world_tier,
    get_adjacent_maps,
    calc_travel_cost,
    check_travel_requirements,
    get_first_visit_text,
    get_area_actions,
)


travel_bp = Blueprint("travel", __name__)


@travel_bp.route("/api/travel", methods=["POST"])
def travel():
    """移动到相邻区域。

    请求体: {"user_id": "...", "to_map": "east_forest"}
    """
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error

    to_map_id = data.get("to_map")
    if not to_map_id:
        return error("MISSING_PARAMS", "缺少 to_map 参数", 400)

    log_action("travel", user_id=user_id, to_map=to_map_id)

    user = fetch_one("SELECT * FROM users WHERE user_id = %s", (user_id,))
    if not user:
        return error("NOT_FOUND", "用户不存在", 404)

    current_map = user.get("current_map", "canglan_city")
    rank = int(user.get("rank", 1) or 1)
    stamina = float(user.get("stamina", 0) or 0)
    dao_heng = float(user.get("dao_heng", 0) or 0)
    dao_ni = float(user.get("dao_ni", 0) or 0)
    dao_yan = float(user.get("dao_yan", 0) or 0)

    # 检查是否在修炼等状态中
    if user.get("state"):
        return error("BUSY", "你正处于修炼/战斗状态，无法移动", 400)

    # 检查相邻和移动消耗
    cost = calc_travel_cost(current_map, to_map_id)
    if not cost["can_travel"]:
        return error("CANNOT_TRAVEL", cost["reason"], 400)

    # 检查精力
    if stamina < cost["stamina_cost"]:
        return error("NO_STAMINA", f"精力不足，需要{cost['stamina_cost']}点精力", 400)

    # 检查目标地图的进入条件
    can_enter, reason = check_travel_requirements(to_map_id, rank, dao_heng, dao_ni, dao_yan)
    if not can_enter:
        return error("REQUIREMENTS_NOT_MET", reason, 400)

    # 检查是否首次到达
    visited_maps_raw = user.get("visited_maps") or "[]"
    try:
        visited_maps = json.loads(visited_maps_raw) if isinstance(visited_maps_raw, str) else visited_maps_raw
    except Exception:
        visited_maps = []

    first_visit = to_map_id not in visited_maps

    # 执行移动
    with db_transaction() as cur:
        cur.execute(
            "UPDATE users SET current_map = %s, stamina = stamina - %s WHERE user_id = %s",
            (to_map_id, cost["stamina_cost"], user_id)
        )
        # 记录首次到达
        if first_visit:
            new_visited = visited_maps + [to_map_id]
            cur.execute(
                "UPDATE users SET visited_maps = %s WHERE user_id = %s",
                (json.dumps(new_visited), user_id)
            )

    to_map = get_map(to_map_id)
    first_visit_text = get_first_visit_text(to_map_id) if first_visit else None
    actions = get_area_actions(to_map_id)

    return jsonify({
        "success": True,
        "from_map": current_map,
        "to_map": to_map_id,
        "to_name": to_map["name"] if to_map else to_map_id,
        "to_desc": to_map.get("desc", "") if to_map else "",
        "stamina_cost": cost["stamina_cost"],
        "first_visit": first_visit,
        "first_visit_text": first_visit_text,
        "actions": actions,
    }), 200


@travel_bp.route("/api/travel/map/<user_id>", methods=["GET"])
def travel_map(user_id: str):
    """返回玩家当前世界层级的大地图数据。"""
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error

    user = fetch_one("SELECT * FROM users WHERE user_id = %s", (user_id,))
    if not user:
        return error("NOT_FOUND", "用户不存在", 404)

    current_map_id = str(user.get("current_map") or "canglan_city")
    current = get_map(current_map_id)
    if not current:
        return error("NOT_FOUND", "当前地图不存在", 404)

    rank = int(user.get("rank", 1) or 1)
    stamina = float(user.get("stamina", 0) or 0)
    dao_heng = float(user.get("dao_heng", 0) or 0)
    dao_ni = float(user.get("dao_ni", 0) or 0)
    dao_yan = float(user.get("dao_yan", 0) or 0)

    visited_maps_raw = user.get("visited_maps") or "[]"
    try:
        visited_maps = json.loads(visited_maps_raw) if isinstance(visited_maps_raw, str) else visited_maps_raw
    except Exception:
        visited_maps = []
    if not isinstance(visited_maps, list):
        visited_maps = []
    visited_set = {str(mid) for mid in visited_maps}

    current_tier = int(current.get("world_tier", 1) or 1)
    tier_info = get_world_tier(current_tier) or {"tier": current_tier, "name": "未知", "desc": ""}
    adjacent_ids = {m["id"] for m in get_adjacent_maps(current_map_id)}

    map_nodes = []
    tier_maps = get_maps_by_tier(current_tier)
    for m in tier_maps:
        map_id = str(m["id"])
        can_enter, reason = check_travel_requirements(map_id, rank, dao_heng, dao_ni, dao_yan)
        is_current = map_id == current_map_id
        is_adjacent = map_id in adjacent_ids
        cost = calc_travel_cost(current_map_id, map_id) if is_adjacent and not is_current else {
            "can_travel": False,
            "stamina_cost": 0,
            "time_desc": "",
            "reason": "",
        }

        map_nodes.append({
            "id": map_id,
            "name": m.get("name", map_id),
            "desc": m.get("desc", ""),
            "region": m.get("region", ""),
            "region_name": m.get("region_name", ""),
            "world_tier": int(m.get("world_tier", current_tier) or current_tier),
            "spirit_density": float(m.get("spirit_density", 1.0) or 1.0),
            "min_realm": int(m.get("min_realm", 1) or 1),
            "is_current": is_current,
            "is_adjacent": is_adjacent,
            "visited": map_id in visited_set,
            "unlocked": bool(can_enter),
            "unlock_reason": "" if can_enter else reason,
            "can_travel": bool(can_enter and is_adjacent and cost.get("can_travel", False) and stamina >= float(cost.get("stamina_cost", 0) or 0)),
            "travel_cost": int(cost.get("stamina_cost", 0) or 0),
            "travel_time_desc": str(cost.get("time_desc", "") or ""),
            "travel_block_reason": str(cost.get("reason", "") or ""),
            "adjacent_ids": [a["id"] for a in get_adjacent_maps(map_id)],
            "actions": get_area_actions(map_id),
        })

    map_nodes.sort(key=lambda x: (x["region_name"], x["name"]))
    return success(
        world={
            "tier": current_tier,
            "name": tier_info.get("name", "未知"),
            "desc": tier_info.get("desc", ""),
        },
        player={
            "user_id": user_id,
            "rank": rank,
            "stamina": stamina,
            "dao_heng": dao_heng,
            "dao_ni": dao_ni,
            "dao_yan": dao_yan,
            "current_map": current_map_id,
            "current_map_name": current.get("name", current_map_id),
        },
        maps=map_nodes,
    )


@travel_bp.route("/api/travel/info", methods=["GET"])
def travel_info():
    """获取从当前位置到目标位置的移动信息（不实际移动）。"""
    from flask import request as req
    from_map = req.args.get("from")
    to_map = req.args.get("to")
    if not from_map or not to_map:
        return error("MISSING_PARAMS", "缺少 from/to 参数", 400)

    cost = calc_travel_cost(from_map, to_map)
    return jsonify({"success": True, "travel_info": cost}), 200


@travel_bp.route("/api/area/actions/<map_id>", methods=["GET"])
def area_actions(map_id: str):
    """获取某个区域的可执行操作列表。"""
    actions = get_area_actions(map_id)
    map_data = get_map(map_id)
    if not map_data:
        return error("NOT_FOUND", "区域不存在", 404)
    return jsonify({
        "success": True,
        "map_id": map_id,
        "map_name": map_data["name"],
        "actions": actions,
    }), 200
