"""装备 / 强化 / 锻造路由。"""

from flask import Blueprint, request, jsonify

from core.routes._helpers import (
    error,
    success,
    log_action,
    parse_json_payload,
    resolve_actor_path_user_id,
    resolve_actor_user_id,
)
from core.config import config
from core.database.connection import (
    get_user_by_id,
    get_user_items,
    fetch_one,
    update_user,
    get_item_by_db_id,
)
from core.game.items import get_item_by_id
from core.services.settlement import settle_enhance
from core.services.forge_service import forge as do_forge, forge_catalog, forge_targeted, decompose_item
from core.services.stats_service import recalculate_user_combat_stats

equipment_bp = Blueprint("equipment", __name__)


@equipment_bp.route("/api/items/<user_id>", methods=["GET"])
def user_items(user_id):
    """获取用户物品"""
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    items = get_user_items(user_id)
    return success(items=items)


@equipment_bp.route("/api/equip", methods=["POST"])
def equip_item():
    """装备物品"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_db_id = data.get("item_id")
    log_action("equip", user_id=user_id, item_id=item_db_id)

    if not item_db_id:
        return error("ERROR", "Missing parameters", 400)

    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)

    # 获取物品
    item = fetch_one("SELECT * FROM items WHERE id = ? AND user_id = ?", (item_db_id, user_id))
    if not item:
        return error("ERROR", "物品不存在", 400)

    item_type = item.get("item_type")
    base_item = get_item_by_id(item.get("item_id"))
    min_rank = int((base_item or {}).get("min_rank", 1) or 1)
    if int(user.get("rank", 1) or 1) < min_rank:
        from core.game.realms import format_realm_display
        return error("ERROR", f"境界不足，需达到{format_realm_display(min_rank)}才能装备该物品", 400)

    # 确定装备槽位
    slot_map = {
        "weapon": "equipped_weapon",
        "armor": "equipped_armor",
        "accessory": "equipped_accessory1",
    }

    if item_type not in slot_map:
        return error("ERROR", "此物品无法装备", 400)

    slot = slot_map[item_type]
    if item_type == "accessory":
        if str(user.get("equipped_accessory1")) == str(item_db_id) or str(user.get("equipped_accessory2")) == str(item_db_id):
            return error("ERROR", "该饰品已装备", 400)
        # auto-fill accessory2 if accessory1 is already occupied
        if user.get("equipped_accessory1") and not user.get("equipped_accessory2"):
            slot = "equipped_accessory2"

    # 装备新物品到槽位
    update_user(user_id, {slot: item_db_id})

    # 用realm base + all equipped items重新计算属性
    recalculate_user_combat_stats(user_id, reset_current=False)

    return success(
        message=f"已装备 {item['item_name']}",
        slot=slot,
        bonuses={
            "attack": item.get("attack_bonus", 0),
            "defense": item.get("defense_bonus", 0),
            "hp": item.get("hp_bonus", 0),
            "mp": item.get("mp_bonus", 0),
        },
    )


@equipment_bp.route("/api/unequip", methods=["POST"])
def unequip_item():
    """卸下装备"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    slot = data.get("slot")
    log_action("unequip", user_id=user_id, slot=slot)

    if not slot:
        return error("ERROR", "Missing parameters", 400)

    valid_slots = ("equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2")
    if slot not in valid_slots:
        return error("ERROR", "无效的装备槽位", 400)

    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)

    current_item_id = user.get(slot)
    if not current_item_id:
        return error("ERROR", "该槽位没有装备", 400)

    item = get_item_by_db_id(current_item_id)
    item_name = item.get("item_name", "未知") if item else "未知"

    # 清空槽位
    update_user(user_id, {slot: None})

    # 重新计算属性
    recalculate_user_combat_stats(user_id, reset_current=False)

    return success(message=f"已卸下 {item_name}")


@equipment_bp.route("/api/enhance", methods=["POST"])
def enhance_item():
    """强化装备"""
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_db_id = data.get("item_id")
    request_id = data.get("request_id")
    strategy = data.get("strategy", "steady")
    log_action("enhance", user_id=user_id, request_id=request_id, item_id=item_db_id)

    if not item_db_id:
        return error("MISSING_PARAMS", "Missing parameters", 400)

    try:
        item_db_id_int = int(item_db_id)
    except (TypeError, ValueError):
        return error("INVALID", "Invalid item_id", 400)

    resp, http_status = settle_enhance(
        user_id=user_id,
        item_db_id=item_db_id_int,
        request_id=request_id,
        enhance_cooldown_seconds=config.enhance_cooldown,
        strategy=strategy,
    )

    # if enhanced item is equipped, recalc combat stats
    if resp.get("success") and resp.get("enhance_success", True):
        try:
            user = get_user_by_id(user_id)
            for slot in ("equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2"):
                if str(user.get(slot)) == str(item_db_id):
                    recalculate_user_combat_stats(user_id, reset_current=False)
                    break
        except Exception:
            pass

    return jsonify(resp), http_status


@equipment_bp.route("/api/forge/<user_id>", methods=["GET"])
def forge_status(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    cfg = config.get_nested("balance", "forge", default={}) or {}
    high_cfg = cfg.get("high_invest", {}) or {}
    material_item_id = str(cfg.get("material_item_id", "iron_ore"))
    material_item = get_item_by_id(material_item_id) or get_item_by_id({
        "ironore": "iron_ore",
        "iron-ore": "iron_ore",
        "iron ore": "iron_ore",
    }.get(material_item_id.lower(), material_item_id))
    material_item_name = str((material_item or {}).get("name") or ("铁矿石" if material_item_id.lower() in {"ironore", "iron-ore", "iron ore"} else material_item_id))
    return success(
        enabled=bool(cfg.get("enabled", True)),
        cost_copper=int(cfg.get("base_cost_copper", 500)),
        material_item_id=material_item_id,
        material_item_name=material_item_name,
        material_need=int(cfg.get("material_need", 8)),
        modes={
            "normal": {"enabled": True},
            "high": {
                "enabled": bool(high_cfg.get("enabled", True)),
                "cost_mult": float(high_cfg.get("cost_mult", 2.5) or 2.5),
                "material_mult": float(high_cfg.get("material_mult", 2.0) or 2.0),
            },
        },
    )


@equipment_bp.route("/api/forge", methods=["POST"])
def forge_post():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    mode = data.get("mode", "normal")
    request_id = data.get("request_id")
    log_action("forge", user_id=user_id, mode=mode, request_id=request_id)
    cfg = config.get_nested("balance", "forge", default={}) or {}
    resp, http_status = do_forge(user_id=user_id, cfg=cfg, mode=mode, request_id=request_id)
    return jsonify(resp), http_status


@equipment_bp.route("/api/forge/catalog/<user_id>", methods=["GET"])
def forge_catalog_get(user_id):
    _, auth_error = resolve_actor_path_user_id(user_id)
    if auth_error:
        return auth_error
    user = get_user_by_id(user_id)
    if not user:
        return error("ERROR", "User not found", 404)
    return success(items=forge_catalog(user_id))


@equipment_bp.route("/api/forge/targeted", methods=["POST"])
def forge_targeted_post():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_id = data.get("item_id")
    request_id = data.get("request_id")
    log_action("forge_targeted", user_id=user_id, item_id=item_id, request_id=request_id)
    if not item_id:
        return error("MISSING_PARAMS", "Missing params", 400)
    cfg = config.get_nested("balance", "forge", default={}) or {}
    resp, http_status = forge_targeted(user_id=user_id, item_id=item_id, cfg=cfg, request_id=request_id)
    return jsonify(resp), http_status


@equipment_bp.route("/api/decompose", methods=["POST"])
def decompose_post():
    data, payload_error = parse_json_payload()
    if payload_error:
        return payload_error
    user_id, auth_error = resolve_actor_user_id(data)
    if auth_error:
        return auth_error
    item_db_id = data.get("item_id")
    log_action("decompose", user_id=user_id, item_id=item_db_id)
    if not item_db_id:
        return error("MISSING_PARAMS", "Missing params", 400)
    try:
        item_db_id = int(item_db_id)
    except (TypeError, ValueError):
        return error("INVALID", "Invalid item_id", 400)
    resp, http_status = decompose_item(user_id=user_id, item_db_id=item_db_id)
    return jsonify(resp), http_status
