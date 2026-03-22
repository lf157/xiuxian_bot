
from typing import Dict, Any, Optional, List, Tuple
from core.database.connection import (
    fetch_one,
    fetch_all,
    execute,
    DatabaseError,
)

ADMIN_IDS = []

GROUP_LABELS = {
    "resources": "资源",
    "progress": "修炼",
    "combat": "战斗",
    "activity": "活跃",
    "pvp": "PVP",
}

FIELD_SPECS: List[Dict[str, Any]] = [
    {"field": "copper", "label": "下品灵石", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "gold", "label": "中品灵石", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "spirit_high", "label": "上品灵石", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "spirit_exquisite", "label": "精品灵石", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "spirit_supreme", "label": "极品灵石", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "immortal_flawed", "label": "残缺仙晶", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "immortal_low", "label": "下品仙晶", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "immortal_mid", "label": "中品仙晶", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "immortal_high", "label": "上品仙晶", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "immortal_supreme", "label": "极品仙晶", "group": "resources", "value_type": "int", "non_negative": True},
    {"field": "exp", "label": "修为", "group": "progress", "value_type": "int", "non_negative": True},
    {"field": "rank", "label": "境界等级", "group": "progress", "value_type": "int", "non_negative": True},
    {"field": "breakthrough_pity", "label": "突破计数", "group": "progress", "value_type": "int", "non_negative": True},
    {"field": "asc_reduction", "label": "突破损失减免", "group": "progress", "value_type": "int", "non_negative": True},
    {"field": "hp", "label": "当前气血", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "mp", "label": "当前灵力", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "max_hp", "label": "气血上限", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "max_mp", "label": "灵力上限", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "attack", "label": "攻击", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "defense", "label": "防御", "group": "combat", "value_type": "int", "non_negative": True},
    {"field": "crit_rate", "label": "暴击率", "group": "combat", "value_type": "float", "non_negative": True},
    {"field": "stamina", "label": "精力", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "dy_times", "label": "打野次数", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "hunts_today", "label": "今日狩猎次数", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "sign", "label": "签到状态", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "consecutive_sign_days", "label": "连续签到天数", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "secret_realm_attempts", "label": "秘境次数", "group": "activity", "value_type": "int", "non_negative": True},
    {"field": "pvp_rating", "label": "PVP 评分", "group": "pvp", "value_type": "int", "non_negative": True},
    {"field": "pvp_wins", "label": "PVP 胜场", "group": "pvp", "value_type": "int", "non_negative": True},
    {"field": "pvp_losses", "label": "PVP 负场", "group": "pvp", "value_type": "int", "non_negative": True},
    {"field": "pvp_draws", "label": "PVP 平局", "group": "pvp", "value_type": "int", "non_negative": True},
    {"field": "pvp_daily_count", "label": "今日 PVP 次数", "group": "pvp", "value_type": "int", "non_negative": True},
]
FIELD_SPEC_MAP: Dict[str, Dict[str, Any]] = {item["field"]: item for item in FIELD_SPECS}

# 保留旧常量名，兼容可能存在的外部引用
MODIFIABLE_FIELDS = frozenset(FIELD_SPEC_MAP.keys())


def load_admin_ids(admin_list: List[str]) -> None:
    global ADMIN_IDS
    ADMIN_IDS = admin_list

def is_admin(user_id: str) -> bool:
    return user_id in ADMIN_IDS

def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        return fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    except Exception as e:
        raise DatabaseError(str(e))


def _get_users_columns() -> set[str]:
    rows = fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = 'public'"
    )
    return {str(r.get("column_name") or "").strip() for r in rows if r.get("column_name")}


def get_modifiable_fields() -> List[Dict[str, Any]]:
    columns = _get_users_columns()
    fields: List[Dict[str, Any]] = []
    for item in FIELD_SPECS:
        field = item["field"]
        if field not in columns:
            continue
        fields.append({
            "field": field,
            "label": item.get("label", field),
            "group": item.get("group", "misc"),
            "group_label": GROUP_LABELS.get(item.get("group", "misc"), item.get("group", "misc")),
            "value_type": item.get("value_type", "int"),
            "non_negative": bool(item.get("non_negative", False)),
        })
    return fields


def _parse_value(value: Any, value_type: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError("布尔值不是有效数值")
    if value_type == "float":
        return float(value)
    return int(value)


def modify_user_field(user_id: str, field: str, action: str, value: int | float) -> Tuple[bool, str]:
    try:
        spec = FIELD_SPEC_MAP.get(field)
        if not spec:
            return False, f"字段 {field} 不允许修改"

        users_columns = _get_users_columns()
        if field not in users_columns:
            return False, f"字段 {field} 在当前数据库中不存在"

        user = fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

        if not user:
            return False, f"用户 {user_id} 不存在"

        in_game_username = user.get("in_game_username", user_id)
        action = str(action or "").strip().lower()
        if action not in {"set", "add", "minus"}:
            return False, f"不支持的操作类型：{action}"

        try:
            parsed_value = _parse_value(value, str(spec.get("value_type", "int")))
        except (TypeError, ValueError):
            return False, f"数值无效：{value}"

        if parsed_value < 0:
            return False, "操作数值不能为负数"

        non_negative = bool(spec.get("non_negative", False))

        if action == "set":
            execute(
                f"UPDATE users SET {field} = ? WHERE user_id = ?",
                (parsed_value, user_id),
            )
            return True, f"已将 {in_game_username} 的 {field} 设置为 {parsed_value}"

        if action == "add":
            execute(
                f"UPDATE users SET {field} = COALESCE({field}, 0) + ? WHERE user_id = ?",
                (parsed_value, user_id),
            )
            return True, f"已为 {in_game_username} 的 {field} 增加 {parsed_value}"

        if non_negative:
            execute(
                f"UPDATE users SET {field} = GREATEST(COALESCE({field}, 0) - ?, 0) WHERE user_id = ?",
                (parsed_value, user_id),
            )
            return True, f"已从 {in_game_username} 的 {field} 扣除 {parsed_value}（最低为 0）"

        execute(
            f"UPDATE users SET {field} = COALESCE({field}, 0) - ? WHERE user_id = ?",
            (parsed_value, user_id),
        )
        return True, f"已从 {in_game_username} 的 {field} 扣除 {parsed_value}"

    except Exception as e:
        return False, f"数据库错误: {str(e)}"

def modify_user_exp(user_id: str, action: str, value: int) -> Tuple[bool, str]:
    return modify_user_field(user_id, "exp", action, value)

def modify_user_copper(user_id: str, action: str, value: int) -> Tuple[bool, str]:
    return modify_user_field(user_id, "copper", action, value)

def modify_user_gold(user_id: str, action: str, value: int) -> Tuple[bool, str]:
    return modify_user_field(user_id, "gold", action, value)

def modify_user_rank(user_id: str, action: str, value: int) -> Tuple[bool, str]:
    return modify_user_field(user_id, "rank", action, value)

def get_all_users(limit: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
    try:
        return fetch_all(
            "SELECT * FROM users LIMIT ? OFFSET ?",
            (limit, skip),
        )
    except Exception as e:
        raise DatabaseError(str(e))

def search_users(query: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
    try:
        if "in_game_username" in query:
            regex = query["in_game_username"].get("$regex") if isinstance(query["in_game_username"], dict) else query["in_game_username"]
            pattern = f"%{regex}%"
            return fetch_all(
                "SELECT * FROM users WHERE in_game_username LIKE ? LIMIT ?",
                (pattern, limit),
            )
        if "user_id" in query:
            return fetch_all(
                "SELECT * FROM users WHERE user_id = ? LIMIT ?",
                (query["user_id"], limit),
            )
        return []
    except Exception as e:
        raise DatabaseError(str(e))

def get_user_inventory(user_id: str, page: int = 1, items_per_page: int = 10) -> Tuple[List[Dict[str, Any]], int]:
    try:
        count_row = fetch_one(
            "SELECT COUNT(*) as c FROM items WHERE user_id = ?",
            (user_id,),
        )
        total_items = count_row["c"] if count_row else 0
        total_pages = (total_items + items_per_page - 1) // items_per_page

        items = fetch_all(
            "SELECT * FROM items WHERE user_id = ? LIMIT ? OFFSET ?",
            (user_id, items_per_page, (page - 1) * items_per_page),
        )

        return items, max(1, total_pages)
    except Exception as e:
        raise DatabaseError(str(e))
