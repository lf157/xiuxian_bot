"""Extra settlement services (shop/signin/breakthrough etc.).

Keep this module free of imports from core.server to avoid circular imports.
Each function returns (response_dict, http_status).
"""

from __future__ import annotations

import hashlib
import time
import psycopg2.errors
from typing import Any, Dict, Optional, Tuple

from core.database.connection import (
    get_user_by_id,
    update_user,
    execute,
    fetch_one,
    fetch_all,
    db_transaction,
    refresh_user_vitals,
    spend_user_stamina_tx,
)
from core.services.quests_service import increment_quest
from core.services.realm_trials_service import is_realm_trial_complete, get_or_create_realm_trial
from core.database.migrations import reserve_request, save_response
from core.services.metrics_service import log_event, log_economy_ledger
from core.services.story_service import track_story_action
from core.utils.timeutil import local_day_key, midnight_timestamp
from core.game.items import (
    get_item_by_id,
    Quality,
    generate_material,
    generate_pill,
    generate_skill_book,
    generate_equipment,
    can_buy_item,
    SHOP_ITEMS,
    get_shop_offer,
    calculate_shop_price,
)
from core.config import config
from core.game.secret_realms import get_secret_realm_attempts_left


def _config_raw() -> Dict[str, Any]:
    raw = config.raw
    return raw if isinstance(raw, dict) else {}


def _pill_buff_cfg() -> Dict[str, Any]:
    cfg = (_config_raw().get("balance", {}) or {}).get("pill_buffs", {}) or {}
    return {
        "cultivation_sprint": cfg.get("cultivation_sprint", {}) or {"duration_seconds": 7200, "exp_mult": 1.35},
        "realm_drop": cfg.get("realm_drop", {}) or {"duration_seconds": 3600, "drop_mul": 1.35},
        "breakthrough_protect": cfg.get("breakthrough_protect", {}) or {"duration_seconds": 3600, "success_bonus": 0.05, "exp_loss_mult": 0.5, "weak_seconds_mult": 0.0},
    }


def _cfg_float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _cfg_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _breakthrough_cfg() -> Dict[str, Any]:
    cfg = (_config_raw().get("balance", {}) or {}).get("breakthrough", {}) or {}
    return {
        "fire_bonus": _cfg_float(cfg.get("fire_bonus"), 0.03),
        "steady_bonus": _cfg_float(cfg.get("steady_bonus"), 0.10),
        "spirit_density_bonus_scale": _cfg_float(cfg.get("spirit_density_bonus_scale"), 0.20),
        "post_breakthrough_restore_ratio": min(1.0, max(0.0, _cfg_float(cfg.get("post_breakthrough_restore_ratio"), 0.30))),
        "stamina_cost": max(1, _cfg_int(cfg.get("stamina_cost"), 1)),
        "protect_material_base": max(0, _cfg_int(cfg.get("protect_material_base"), 2)),
        "protect_material_per_10_rank": max(0, _cfg_int(cfg.get("protect_material_per_10_rank"), 1)),
        "desperate_exp_penalty_add": max(0.0, _cfg_float(cfg.get("desperate_exp_penalty_add"), 0.05)),
        "desperate_exp_penalty_cap": max(0.0, _cfg_float(cfg.get("desperate_exp_penalty_cap"), 0.30)),
        "desperate_weak_seconds_add": max(0, _cfg_int(cfg.get("desperate_weak_seconds_add"), 1800)),
        "desperate_success_gold_bonus": max(0, _cfg_int(cfg.get("desperate_success_gold_bonus"), 1)),
        "desperate_success_copper_min": max(0, _cfg_int(cfg.get("desperate_success_copper_min"), 50)),
        "desperate_success_copper_cost_divisor": max(1, _cfg_int(cfg.get("desperate_success_copper_cost_divisor"), 5)),
        "tribulation_flat_penalty": max(0.0, min(0.95, _cfg_float(cfg.get("tribulation_flat_penalty"), 0.10))),
        "tribulation_rate_multiplier": max(0.05, min(1.0, _cfg_float(cfg.get("tribulation_rate_multiplier"), 0.70))),
        "tribulation_extra_cost_multiplier": max(1.0, _cfg_float(cfg.get("tribulation_extra_cost_multiplier"), 1.20)),
        "tribulation_extra_stamina": max(0, _cfg_int(cfg.get("tribulation_extra_stamina"), 1)),
        "tribulation_fail_exp_penalty_add": max(0.0, _cfg_float(cfg.get("tribulation_fail_exp_penalty_add"), 0.05)),
        "tribulation_fail_weak_seconds_add": max(0, _cfg_int(cfg.get("tribulation_fail_weak_seconds_add"), 1200)),
    }


def _steady_breakthrough_pill_candidates(bt_cfg: Dict[str, Any]) -> list[Dict[str, Any]]:
    default_bonus = float(bt_cfg.get("steady_bonus", 0.10) or 0.10)
    return [
        {"item_id": "super_breakthrough_pill", "item_name": "超级突破丹", "bonus": 0.50},
        {"item_id": "advanced_breakthrough_pill", "item_name": "高级突破丹", "bonus": 0.20},
        {"item_id": "breakthrough_pill", "item_name": "突破丹", "bonus": default_bonus},
    ]


def _pill_breaks_tribulation_limit(item_id: str | None) -> bool:
    return str(item_id or "").strip() in {"advanced_breakthrough_pill", "super_breakthrough_pill"}


def _pick_steady_breakthrough_pill(*, user_id: str, bt_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for candidate in _steady_breakthrough_pill_candidates(bt_cfg):
        row = fetch_one(
            "SELECT id, quantity FROM items WHERE user_id = %s AND item_id = %s AND item_type = 'pill' AND quantity >= 1 ORDER BY id ASC LIMIT 1",
            (user_id, candidate["item_id"]),
        )
        if row:
            chosen = candidate.copy()
            chosen["db_id"] = int(row.get("id") or 0)
            chosen["quantity"] = int(row.get("quantity") or 0)
            return chosen
    return None


def _protect_material_need(rank: int, bt_cfg: Dict[str, Any]) -> int:
    base = int(bt_cfg.get("protect_material_base", 2) or 2)
    per_10 = int(bt_cfg.get("protect_material_per_10_rank", 1) or 1)
    return base + max(0, int(rank or 1) // 10) * per_10


def _is_tribulation_breakthrough(*, current_realm: Dict[str, Any], next_realm: Dict[str, Any]) -> bool:
    if not current_realm or not next_realm:
        return False
    try:
        sub_level = int(current_realm.get("sub_level", 0) or 0)
    except (TypeError, ValueError):
        sub_level = 0
    if sub_level == 4:
        return True
    return "圆满" in str(current_realm.get("name") or "")


def _apply_tribulation_rate_adjustment(*, rate: float, is_tribulation: bool, bt_cfg: Dict[str, Any]) -> float:
    value = min(1.0, max(0.0, float(rate or 0.0)))
    if not is_tribulation:
        return value
    flat_penalty = float(bt_cfg.get("tribulation_flat_penalty", 0.10) or 0.10)
    multiplier = float(bt_cfg.get("tribulation_rate_multiplier", 0.70) or 0.70)
    value = max(0.0, value - flat_penalty)
    value = min(1.0, max(0.0, value * multiplier))
    return value


def _build_breakthrough_congrats_message(*, new_realm: str, display_name: str = "") -> str:
    clean_name = str(display_name or "").strip().replace("\n", "").replace("\r", "")
    target = f"{clean_name} 道友" if clean_name else "道友"
    return f"灵光一闪！恭喜 {target}，修为精进，成功突破至【{new_realm}】！"


def _format_signed_ratio_percent(ratio: float) -> str:
    value = float(ratio or 0.0) * 100.0
    rounded = round(value, 2)
    if abs(rounded) < 1e-9:
        return "0%"
    sign = "+" if rounded > 0 else "-"
    abs_value = abs(rounded)
    if abs(abs_value - round(abs_value)) < 1e-9:
        return f"{sign}{int(round(abs_value))}%"
    if abs(abs_value * 10 - round(abs_value * 10)) < 1e-9:
        return f"{sign}{abs_value:.1f}%"
    return f"{sign}{abs_value:.2f}%"


def _resolve_breakthrough_environment(*, user_id: str, user: Dict[str, Any], bt_cfg: Dict[str, Any]) -> Dict[str, Any]:
    from core.game.maps import get_map, get_spirit_density

    current_map = str(user.get("current_map") or "canglan_city")
    map_info = get_map(current_map) or {}
    location_name = str(map_info.get("name") or current_map)
    spirit_density = float(get_spirit_density(current_map) or 1.0)

    density_scale = float(bt_cfg.get("spirit_density_bonus_scale", 0.20) or 0.20)
    location_bonus = max(-0.08, min(0.12, (spirit_density - 1.0) * density_scale))

    seed = f"{local_day_key()}:{user_id}"
    roll = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
    if roll < 8:
        fortune_label, fortune_bonus = "大凶", -0.06
    elif roll < 25:
        fortune_label, fortune_bonus = "小凶", -0.03
    elif roll < 65:
        fortune_label, fortune_bonus = "平", 0.00
    elif roll < 88:
        fortune_label, fortune_bonus = "吉", 0.04
    else:
        fortune_label, fortune_bonus = "大吉", 0.08

    return {
        "current_map": current_map,
        "location_name": location_name,
        "spirit_density": spirit_density,
        "location_bonus": float(location_bonus),
        "fortune_label": fortune_label,
        "fortune_bonus": float(fortune_bonus),
    }


def _realm_trial_requirement_payload(trial: Dict[str, Any]) -> Dict[str, Any]:
    hunt_target = max(0, int((trial or {}).get("hunt_target", 0) or 0))
    hunt_progress = max(0, int((trial or {}).get("hunt_progress", 0) or 0))
    secret_target = max(0, int((trial or {}).get("secret_target", 0) or 0))
    secret_progress = max(0, int((trial or {}).get("secret_progress", 0) or 0))
    return {
        "hunt": {
            "progress": hunt_progress,
            "target": hunt_target,
            "remaining": max(0, hunt_target - hunt_progress),
        },
        "secret": {
            "progress": secret_progress,
            "target": secret_target,
            "remaining": max(0, secret_target - secret_progress),
        },
    }


def _realm_trial_requirement_text(trial: Dict[str, Any]) -> str:
    req = _realm_trial_requirement_payload(trial)
    parts = []
    hunt = req["hunt"]
    if int(hunt.get("target", 0) or 0) > 0:
        parts.append(f"狩猎 {hunt['progress']}/{hunt['target']}（还差 {hunt['remaining']}）")
    secret = req["secret"]
    if int(secret.get("target", 0) or 0) > 0:
        parts.append(f"秘境 {secret['progress']}/{secret['target']}（还差 {secret['remaining']}）")
    return "，".join(parts) if parts else "当前境界无额外试炼要求"


def _current_period_key(period: str) -> str:
    day_key = local_day_key()
    if period == "week":
        return f"week:{day_key // 7}"
    return f"day:{day_key}"


def _format_ratio_percent(ratio: float) -> str:
    pct = max(0.0, float(ratio or 0.0) * 100.0)
    rounded = round(pct, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return f"{int(round(rounded))}%"
    if abs(rounded * 10 - round(rounded * 10)) < 1e-9:
        return f"{rounded:.1f}%"
    return f"{rounded:.2f}%"


def _format_weak_penalty_text(weak_seconds: int) -> str:
    seconds = max(0, int(weak_seconds or 0))
    if seconds <= 0:
        return "不进入虚弱状态"
    if seconds % 60 == 0:
        return f"进入虚弱状态{seconds // 60}分钟"
    return f"进入虚弱状态{seconds}秒"


def _active_buff_value(*, now: int, until: Any, value: Any, default: float = 0.0) -> float:
    """Return buff value only when it is currently active."""
    try:
        until_ts = int(until or 0)
    except (TypeError, ValueError):
        until_ts = 0
    if until_ts <= int(now or 0):
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ── 突破关联道具 ──

_BREAKTHROUGH_EFFECT_DESC: Dict[str, str] = {
    "breakthrough_pill": "提升突破成功率 +10%",
    "advanced_breakthrough_pill": "提升突破成功率 +20%，可豁免天雷劫压制",
    "super_breakthrough_pill": "提升突破成功率 +50%，可豁免天雷劫压制",
    "breakthrough_guard_pill": "突破保护：一段时间内突破失败不进虚弱状态，额外 +5% 成功率",
}


def _get_breakthrough_related_items(user_id: str) -> list:
    """查询用户背包中与突破相关的道具列表。

    匹配规则：item_id 包含 "breakthrough" 关键字（覆盖已知及将来新增道具）。

    返回格式: [{"item_id": ..., "item_name": ..., "quantity": N, "effect": "..."}]
    """
    try:
        rows = fetch_all(
            "SELECT item_id, item_name, SUM(quantity) AS quantity "
            "FROM items WHERE user_id = %s AND item_id LIKE %s "
            "GROUP BY item_id, item_name "
            "HAVING SUM(quantity) > 0 "
            "ORDER BY item_id",
            (user_id, "%breakthrough%"),
        )
    except Exception:
        rows = []

    result = []
    for row in rows:
        iid = str(row.get("item_id") or "")
        qty = int(row.get("quantity", 0) or 0)
        if qty <= 0:
            continue
        item_name = str(row.get("item_name") or "")
        if not item_name:
            defn = get_item_by_id(iid)
            item_name = str((defn or {}).get("name", iid))
        effect = _BREAKTHROUGH_EFFECT_DESC.get(iid, "")
        if not effect:
            defn = get_item_by_id(iid)
            if defn:
                val = defn.get("value", 0)
                if defn.get("effect") == "breakthrough" and val:
                    effect = f"提升突破成功率 +{val}%"
                elif defn.get("effect") == "breakthrough_protect":
                    effect = "突破保护：失败不进虚弱状态"
                else:
                    effect = "辅助突破"
            else:
                effect = "辅助突破"
        result.append({
            "item_id": iid,
            "item_name": item_name,
            "quantity": qty,
            "effect": effect,
        })
    return result


def get_breakthrough_preview(
    *,
    user_id: str,
    use_pill: bool = False,
    strategy: str = "steady",
) -> Tuple[Dict[str, Any], int]:
    from core.game.realms import get_next_realm, calculate_breakthrough_cost, get_realm_by_id

    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404

    current_rank = int(user.get("rank", 1) or 1)
    current_realm = get_realm_by_id(current_rank) or {"name": "当前境界"}
    next_realm = get_next_realm(current_rank)
    if not next_realm:
        return {
            "success": False,
            "code": "MAX",
            "message": "你已站上当前世界的修行尽头。",
            "preview": {
                "strategy": "normal",
                "strategy_name": "普通冲关",
                "preview_text": "你已站上当前世界的修行尽头。",
                "strategy_notes": "",
            },
        }, 200

    strategy = (strategy or "normal").strip().lower()
    if strategy not in ("steady", "protect", "desperate"):
        strategy = "steady"
    if use_pill and strategy not in ("steady", "protect", "desperate"):
        strategy = "steady"

    now = int(time.time())
    protect_cfg = _pill_buff_cfg()["breakthrough_protect"]
    protect_until = int(user.get("breakthrough_protect_until", 0) or 0)
    protect_active = protect_until > now
    boost_until = int(user.get("breakthrough_boost_until", 0) or 0)
    boost_pct = float(user.get("breakthrough_boost_pct", 0) or 0)
    boost_active = boost_until > now and boost_pct > 0

    bt_cfg = _breakthrough_cfg()
    is_tribulation = _is_tribulation_breakthrough(current_realm=current_realm, next_realm=next_realm)
    if is_tribulation:
        # 圆满关口仅开放渡劫突破单一路线，避免前端残留按钮导致分流。
        strategy = "steady"
    tribulation_flat_penalty = float(bt_cfg.get("tribulation_flat_penalty", 0.10) or 0.10) if is_tribulation else 0.0
    tribulation_rate_multiplier = float(bt_cfg.get("tribulation_rate_multiplier", 0.70) or 0.70) if is_tribulation else 1.0
    tribulation_cost_multiplier = float(bt_cfg.get("tribulation_extra_cost_multiplier", 1.20) or 1.20) if is_tribulation else 1.0
    tribulation_extra_stamina = int(bt_cfg.get("tribulation_extra_stamina", 1) or 1) if is_tribulation else 0
    env_ctx = _resolve_breakthrough_environment(user_id=user_id, user=user, bt_cfg=bt_cfg)
    location_bonus = float(env_ctx.get("location_bonus", 0.0) or 0.0)
    fortune_bonus = float(env_ctx.get("fortune_bonus", 0.0) or 0.0)
    base_cost = calculate_breakthrough_cost(current_rank)
    protect_need = _protect_material_need(current_rank, bt_cfg) if strategy == "protect" else 0
    base_total_cost = int(base_cost + protect_need)
    if is_tribulation:
        cost = int(max(base_total_cost, round(base_total_cost * tribulation_cost_multiplier)))
    else:
        cost = base_total_cost
    tribulation_extra_cost = max(0, int(cost - base_total_cost))
    stamina_cost = int(bt_cfg.get("stamina_cost", 1) or 1) + tribulation_extra_stamina
    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    fire_bonus = float(bt_cfg.get("fire_bonus", 0.03) or 0.03)
    steady_default_bonus = float(bt_cfg.get("steady_bonus", 0.10) or 0.10)
    steady_pill = _pick_steady_breakthrough_pill(user_id=user_id, bt_cfg=bt_cfg) if strategy == "steady" else None
    steady_pill_bonus = float((steady_pill or {}).get("bonus", 0.0) or 0.0)
    tribulation_rate_ignored = bool(
        is_tribulation
        and strategy == "steady"
        and _pill_breaks_tribulation_limit((steady_pill or {}).get("item_id"))
    )
    tribulation_rate_active = bool(is_tribulation and not tribulation_rate_ignored)
    rate_parts = [f"基础成功率 {int(base_rate * 100)}%"]
    shown_rate = base_rate
    if user.get("element") == "火":
        shown_rate = min(1.0, shown_rate + fire_bonus)
        rate_parts.append(f"火灵根 +{_format_ratio_percent(fire_bonus)}")

    if protect_active:
        protect_bonus = float(protect_cfg.get("success_bonus", 0.05))
        if protect_bonus > 0:
            shown_rate = min(1.0, shown_rate + protect_bonus)
            rate_parts.append(f"保护丹 +{int(protect_bonus * 100)}%")
    if boost_active:
        shown_rate = min(1.0, shown_rate + boost_pct / 100.0)
        rate_parts.append(f"聚灵增益 +{int(boost_pct)}%")
    if abs(location_bonus) > 1e-9:
        shown_rate = min(1.0, max(0.0, shown_rate + location_bonus))
        rate_parts.append(f"地脉灵气 {_format_signed_ratio_percent(location_bonus)}")
    if abs(fortune_bonus) > 1e-9:
        shown_rate = min(1.0, max(0.0, shown_rate + fortune_bonus))
        rate_parts.append(f"今日运势 {_format_signed_ratio_percent(fortune_bonus)}")
    strategy_name = "渡劫突破" if is_tribulation else {
        "steady": "稳妥突破",
        "protect": "护脉突破",
        "desperate": "生死突破",
    }.get(strategy, "稳妥突破")

    extra_cost_text = "无额外材料"
    if strategy == "steady":
        if steady_pill:
            shown_rate = min(1.0, shown_rate + steady_pill_bonus)
            rate_parts.append(f"{steady_pill['item_name']} +{_format_ratio_percent(steady_pill_bonus)}")
            extra_cost_text = f"额外消耗: {steady_pill['item_name']} x1"
        elif boost_active:
            extra_cost_text = "额外消耗: 无（已激活聚灵增益）"
        else:
            rate_parts.append("当前无可用突破丹")
            extra_cost_text = "额外消耗: 需要突破丹/高级突破丹/超级突破丹 x1"
    elif strategy == "protect":
        extra_cost_text = f"额外消耗: 下品灵石 x{protect_need}"
        rate_parts.append("护脉: 失败不进虚弱")
    elif strategy == "desperate":
        extra_cost_text = "额外效果: 成功额外奖励，失败惩罚更重"

    base_for_notes = float(next_realm.get("break_rate", 0.0) or 0.0)
    if user.get("element") == "火":
        base_for_notes = min(1.0, base_for_notes + fire_bonus)
    if protect_active:
        base_for_notes = min(1.0, base_for_notes + float(protect_cfg.get("success_bonus", 0.05)))
    if boost_active:
        base_for_notes = min(1.0, base_for_notes + boost_pct / 100.0)
    base_for_notes = min(1.0, max(0.0, base_for_notes + location_bonus + fortune_bonus))
    steady_notes_bonus = steady_pill_bonus if steady_pill else (0.0 if boost_active else steady_default_bonus)
    steady_rate = _apply_tribulation_rate_adjustment(
        rate=min(1.0, base_for_notes + steady_notes_bonus),
        is_tribulation=tribulation_rate_active,
        bt_cfg=bt_cfg,
    )
    protect_rate = _apply_tribulation_rate_adjustment(
        rate=base_for_notes,
        is_tribulation=is_tribulation,
        bt_cfg=bt_cfg,
    )
    desperate_rate = _apply_tribulation_rate_adjustment(
        rate=base_for_notes,
        is_tribulation=is_tribulation,
        bt_cfg=bt_cfg,
    )
    shown_rate = _apply_tribulation_rate_adjustment(
        rate=shown_rate,
        is_tribulation=tribulation_rate_active,
        bt_cfg=bt_cfg,
    )
    if tribulation_rate_active:
        rate_parts.append(f"天雷劫压制 {_format_signed_ratio_percent(-tribulation_flat_penalty)}")
        rate_parts.append(f"雷劫强度倍率 x{tribulation_rate_multiplier:.2f}")
    elif is_tribulation and tribulation_rate_ignored:
        rate_parts.append(f"{(steady_pill or {}).get('item_name', '高阶突破丹')}破劫：天雷劫压制豁免")

    title_text = "⛈️ *渡劫突破·天雷劫*" if is_tribulation else "⚡ *突破预告*"
    mode_text = "圆满渡劫（天雷劫）" if is_tribulation else "常规破境"
    tribulation_line = ""
    if is_tribulation:
        if tribulation_rate_active:
            tribulation_line = (
                f"雷劫压制: 固定{_format_signed_ratio_percent(-tribulation_flat_penalty)}，"
                f"再乘以 {tribulation_rate_multiplier:.2f} 倍\n"
                f"雷劫附加消耗: +{tribulation_extra_cost} 下品灵石，+{tribulation_extra_stamina} 点精力\n"
            )
        else:
            tribulation_line = (
                f"雷劫压制: 已被{(steady_pill or {}).get('item_name', '高阶突破丹')}豁免\n"
                f"雷劫附加消耗: +{tribulation_extra_cost} 下品灵石，+{tribulation_extra_stamina} 点精力\n"
            )

    preview_text = (
        f"{title_text}\n"
        f"策略: *{strategy_name}*\n"
        f"关卡类型: *{mode_text}*\n"
        f"你将从 *{current_realm.get('name', '当前境界')}* 冲击 *{next_realm.get('name', '下一境界')}*。\n"
        f"所在地: *{env_ctx.get('location_name', env_ctx.get('current_map', '未知'))}*"
        f"（灵气×{float(env_ctx.get('spirit_density', 1.0) or 1.0):.2f}，地脉{_format_signed_ratio_percent(location_bonus)}）\n"
        f"今日运势: *{env_ctx.get('fortune_label', '平')}*（{_format_signed_ratio_percent(fortune_bonus)}）\n"
        f"{tribulation_line}"
        f"消耗: {cost:,} 下品灵石\n"
        f"额外消耗: {stamina_cost} 点精力\n"
        f"{extra_cost_text}\n"
        f"预计成功率: *{int(shown_rate * 100)}%*\n"
        f"加成构成: {' ｜ '.join(rate_parts)}"
    )
    if is_tribulation:
        strategy_notes = (
            f"当前为【{current_realm.get('name', '圆满境')}】圆满关口，仅开放 *渡劫突破*。\n"
            f"渡劫突破：消耗下品灵石 + 突破丹系道具 x1，成功率约 *{int(steady_rate * 100)}%*。\n"
            "说明：渡劫成功率由灵根、增益、地脉、运势共同决定；"
            "高级/超级突破丹可豁免雷劫成功率压制，不含保底机制。"
        )
    else:
        strategy_notes = (
            f"稳妥突破：消耗下品灵石 + 突破丹系道具 x1，成功率约 *{int(steady_rate * 100)}%*，失败损失减半\n"
            f"护脉突破：消耗下品灵石（含附加 x{protect_need}），成功率约 *{int(protect_rate * 100)}%*，失败不进虚弱\n"
            f"生死突破：只消耗下品灵石，成功率约 *{int(desperate_rate * 100)}%*，成功有额外奖励，失败惩罚更重\n"
            "说明：成功率可通过突破丹、灵石增益、地脉、运势等多种方式提升。"
        )

    # ── 用户当前资源快照 ──
    user_copper = int(user.get("copper", 0) or 0)
    user_exp = int(user.get("exp", 0) or 0)
    next_exp = int(next_realm.get("exp", 0) or 0)
    try:
        user_stamina = float(user.get("stamina", 0) or 0)
    except (TypeError, ValueError):
        user_stamina = 0.0
    # 查询聚元石持有
    try:
        _ss_row = fetch_one(
            "SELECT SUM(quantity) AS qty FROM items WHERE user_id = %s AND item_id = 'spirit_stone'",
            (user_id,),
        )
        user_spirit_stone = int((_ss_row or {}).get("qty", 0) or 0)
    except Exception:
        user_spirit_stone = 0

    # 资源充足性检查
    _resource_ok = True
    _resource_hints: list = []
    if user_exp < next_exp and next_exp > 0:
        _resource_ok = False
        _resource_hints.append(f"修为不足（需{next_exp:,}，当前{user_exp:,}）")
    if user_copper < cost:
        _resource_ok = False
        _resource_hints.append(f"下品灵石不足（需{cost:,}，当前{user_copper:,}）")
    if user_stamina < stamina_cost:
        _resource_ok = False
        _resource_hints.append(f"精力不足（需{stamina_cost}，当前{int(user_stamina)}）")
    if strategy == "protect" and user_spirit_stone < protect_need:
        _resource_ok = False
        _resource_hints.append(f"聚元石不足（需{protect_need}，当前{user_spirit_stone}）")
    if strategy == "steady" and not steady_pill and not boost_active:
        _resource_ok = False
        _resource_hints.append("缺少突破丹（需突破丹/高级突破丹/超级突破丹 x1）")

    # buff 状态
    protect_remaining = max(0, protect_until - now) if protect_active else 0
    boost_remaining = max(0, boost_until - now) if boost_active else 0

    return {
        "success": True,
        "preview": {
            "strategy": strategy,
            "strategy_name": strategy_name,
            "current_rank": current_rank,
            "current_realm": current_realm.get("name", "当前境界"),
            "next_realm": next_realm.get("name", "下一境界"),
            "cost_copper": int(cost),
            "base_cost_copper": int(base_cost),
            "extra_cost_copper": int(protect_need if strategy == "protect" else 0),
            "protect_material_need": int(protect_need),
            "is_tribulation": bool(is_tribulation),
            "tribulation_name": "天雷劫" if is_tribulation else "",
            "tribulation_flat_penalty": float(tribulation_flat_penalty if is_tribulation else 0.0),
            "tribulation_rate_multiplier": float(tribulation_rate_multiplier if is_tribulation else 1.0),
            "tribulation_rate_ignored": bool(tribulation_rate_ignored),
            "tribulation_extra_cost_copper": int(tribulation_extra_cost),
            "tribulation_extra_stamina": int(tribulation_extra_stamina),
            "stamina_cost": stamina_cost,
            "success_rate": float(shown_rate),
            "success_rate_pct": int(shown_rate * 100),
            "current_map": env_ctx.get("current_map"),
            "location_name": env_ctx.get("location_name"),
            "spirit_density": float(env_ctx.get("spirit_density", 1.0) or 1.0),
            "location_bonus": float(location_bonus),
            "fortune_label": env_ctx.get("fortune_label"),
            "fortune_bonus": float(fortune_bonus),
            "steady_pill_item_id": steady_pill.get("item_id") if steady_pill else "",
            "steady_pill_name": steady_pill.get("item_name") if steady_pill else "",
            "steady_pill_bonus": float(steady_pill_bonus if steady_pill else 0.0),
            "rate_parts": rate_parts,
            "preview_text": preview_text,
            "strategy_notes": strategy_notes,
            "related_items": _get_breakthrough_related_items(user_id),
            # 用户资源快照
            "user_copper": user_copper,
            "user_stamina": int(user_stamina),
            "user_spirit_stone": user_spirit_stone,
            "user_exp": user_exp,
            "next_exp": next_exp,
            # buff 状态
            "protect_buff_active": protect_active,
            "protect_buff_remaining": protect_remaining,
            "boost_buff_active": boost_active,
            "boost_buff_remaining": boost_remaining,
            "boost_buff_pct": float(boost_pct if boost_active else 0.0),
            # 资源检查结果
            "resource_ok": _resource_ok,
            "resource_hints": _resource_hints,
        },
    }, 200


def get_shop_remaining_limit(user_id: str, item_id: str, offer: Dict[str, Any]) -> int | None:
    limit = offer.get("limit")
    period = offer.get("limit_period")
    if not limit or not period:
        return None
    period_key = _current_period_key(period)
    try:
        row = fetch_one(
            "SELECT quantity FROM shop_purchase_limits WHERE user_id = %s AND item_id = %s AND period_key = %s",
            (user_id, item_id, period_key),
        )
    except psycopg2.OperationalError:
        # Older databases may not have the shop limit table yet. Treat as unlimited
        # so the shop UI can still load until startup migrations create the table.
        return None
    bought = int(row.get("quantity", 0) or 0) if row else 0
    return max(0, int(limit) - bought)


def _reserve_shop_limit(
    cur: object,
    *,
    user_id: str,
    item_id: str,
    period_key: str,
    quantity: int,
    limit: int,
) -> bool:
    if quantity <= 0:
        return True
    if limit <= 0 or quantity > limit:
        return False

    cur.execute(
        """UPDATE shop_purchase_limits
           SET quantity = quantity + %s
           WHERE user_id = %s AND item_id = %s AND period_key = %s
             AND quantity + %s <= %s""",
        (quantity, user_id, item_id, period_key, quantity, limit),
    )
    if cur.rowcount == 1:
        return True

    try:
        cur.execute(
            "INSERT INTO shop_purchase_limits (user_id, item_id, period_key, quantity) VALUES (%s, %s, %s, %s)",
            (user_id, item_id, period_key, quantity),
        )
        return True
    except psycopg2.errors.UniqueViolation:
        cur.execute(
            """UPDATE shop_purchase_limits
               SET quantity = quantity + %s
               WHERE user_id = %s AND item_id = %s AND period_key = %s
                 AND quantity + %s <= %s""",
            (quantity, user_id, item_id, period_key, quantity, limit),
        )
        return cur.rowcount == 1


def settle_shop_buy(
    *,
    user_id: str,
    item_id: str,
    quantity: int,
    currency: str | None = None,
    request_id: str | None = None,
) -> Tuple[Dict[str, Any], int]:
    if request_id:
        status, cached = reserve_request(request_id, user_id=user_id, action="shop_buy")
        if status == "cached" and cached:
            return cached, 200
        if status == "in_progress":
            return {
                "success": False,
                "code": "REQUEST_IN_PROGRESS",
                "message": "请求处理中，请稍后重试",
            }, 409

    def _dedup_return(resp: Dict[str, Any], http_status: int) -> Tuple[Dict[str, Any], int]:
        if request_id:
            save_response(request_id, user_id, "shop_buy", resp)
        return resp, http_status

    user = get_user_by_id(user_id)
    if not user:
        return _dedup_return({"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404)

    quantity = int(quantity or 1)
    if quantity <= 0 or quantity > 999:
        return _dedup_return({"success": False, "code": "INVALID", "message": "quantity invalid"}, 400)

    user_copper = int(user.get("copper", 0) or 0)
    user_gold = int(user.get("gold", 0) or 0)
    user_spirit_high = int(user.get("spirit_high", 0) or 0)

    item_info = get_shop_offer(item_id, currency)
    if not item_info:
        return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "物品不存在"}, 404)
    currency = str(item_info.get("currency", currency or "") or "").strip().lower()

    if currency == "spirit_high":
        min_rank = int(item_info.get("min_rank", 1) or 1)
        if int(user.get("rank", 1) or 1) < min_rank:
            from core.game.realms import format_realm_display
            return _dedup_return(
                {
                    "success": False,
                    "code": "FORBIDDEN",
                    "message": f"境界不足，需达到{format_realm_display(min_rank)}才可购买",
                },
                400,
            )
        total_price = int(item_info.get("price", 0) or 0) * quantity
        if user_spirit_high < total_price:
            return _dedup_return(
                {
                    "success": False,
                    "code": "FORBIDDEN",
                    "message": f"上品灵石不足，需要 {total_price}",
                },
                400,
            )
    else:
        can_buy_ok, resolved_currency, msg = can_buy_item(
            item_id,
            user_copper,
            user_gold,
            user_rank=int(user.get("rank", 1) or 1),
            preferred_currency=currency,
            quantity=quantity,
        )
        if not can_buy_ok:
            return _dedup_return({"success": False, "code": "FORBIDDEN", "message": msg}, 400)
        currency = resolved_currency
        item_info = get_shop_offer(item_id, currency)
        if not item_info:
            return _dedup_return({"success": False, "code": "NOT_FOUND", "message": "物品不存在"}, 404)

    remaining_limit = get_shop_remaining_limit(user_id, item_id, item_info)
    if remaining_limit is not None and quantity > remaining_limit:
        return _dedup_return({"success": False, "code": "LIMIT", "message": f"超出限购，剩余可买 {remaining_limit}"}, 400)

    pricing = calculate_shop_price(int(item_info["price"]), currency, quantity)
    total_price = int(pricing["base_total"])
    extra_fee = int(pricing["extra_fee"])
    actual_cost = int(pricing["actual_total"])
    limit = int(item_info.get("limit", 0) or 0)
    limit_period = str(item_info.get("limit_period", "") or "")

    # 生成物品数据（在事务外计算，事务内仅做DB操作）
    base_item = get_item_by_id(item_id)
    generated_item = None
    if base_item:
        from core.game.items import ItemType
        if base_item.get("type") == ItemType.PILL:
            generated_item = generate_pill(item_id, quantity)
        elif base_item.get("type") == ItemType.MATERIAL:
            generated_item = generate_material(item_id, quantity)
        elif base_item.get("type") == ItemType.SKILL_BOOK:
            generated_item = generate_skill_book(item_id, quantity)
        else:
            generated_item = generate_equipment(base_item, Quality.COMMON, 1)
            generated_item["quantity"] = quantity

    try:
        # ---- 单事务原子购买 ----
        with db_transaction() as cur:
            # 1. 扣除货币（条件更新防负数）
            if currency == "copper":
                cur.execute(
                    "UPDATE users SET copper = copper - %s WHERE user_id = %s AND copper >= %s",
                    (actual_cost, user_id, actual_cost),
                )
            elif currency == "spirit_high":
                cur.execute(
                    "UPDATE users SET spirit_high = spirit_high - %s WHERE user_id = %s AND spirit_high >= %s",
                    (actual_cost, user_id, actual_cost),
                )
            else:
                cur.execute(
                    "UPDATE users SET gold = gold - %s WHERE user_id = %s AND gold >= %s",
                    (actual_cost, user_id, actual_cost),
                )
            if cur.rowcount == 0:
                raise ValueError("INSUFFICIENT")

            # 2. 添加物品
            if generated_item:
                cur.execute(
                    """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                       quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                       first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_id, generated_item.get("item_id"), generated_item.get("item_name"),
                     generated_item.get("item_type"), generated_item.get("quality", "common"),
                     generated_item.get("quantity", 1), generated_item.get("level", 1),
                     generated_item.get("attack_bonus", 0), generated_item.get("defense_bonus", 0),
                     generated_item.get("hp_bonus", 0), generated_item.get("mp_bonus", 0),
                     generated_item.get("first_round_reduction_pct", 0), generated_item.get("crit_heal_pct", 0),
                     generated_item.get("element_damage_pct", 0), generated_item.get("low_hp_shield_pct", 0)),
                )

            if remaining_limit is not None:
                period_key = _current_period_key(limit_period)
                if not _reserve_shop_limit(
                    cur,
                    user_id=user_id,
                    item_id=item_id,
                    period_key=period_key,
                    quantity=quantity,
                    limit=limit,
                ):
                    raise ValueError("LIMIT")
    except ValueError as exc:
        reason = str(exc)
        if reason == "LIMIT":
            current_remaining = get_shop_remaining_limit(user_id, item_id, item_info)
            log_event(
                "shop_buy",
                user_id=user_id,
                success=False,
                request_id=request_id,
                rank=int(user.get("rank", 1) or 1),
                reason="LIMIT",
                meta={"item_id": item_id, "quantity": quantity},
            )
            return _dedup_return({
                "success": False,
                "code": "LIMIT",
                "message": f"超出限购，剩余可买 {int(current_remaining or 0)}",
            }, 400)
        log_event(
            "shop_buy",
            user_id=user_id,
            success=False,
            request_id=request_id,
            rank=int(user.get("rank", 1) or 1),
            reason="INSUFFICIENT",
            meta={"item_id": item_id, "quantity": quantity},
        )
        return _dedup_return({"success": False, "code": "INSUFFICIENT", "message": "余额不足，购买失败"}, 400)

    increment_quest(user_id, "daily_shop")

    item_name = item_info.get("name")
    if not item_name:
        base_info = get_item_by_id(item_id) or {}
        item_name = str(base_info.get("name") or item_id)
    item_payload = item_info.copy()
    item_payload.setdefault("name", item_name)

    log_event(
        "shop_buy",
        user_id=user_id,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={"item_id": item_id, "quantity": quantity, "currency": currency},
    )
    log_economy_ledger(
        user_id=user_id,
        module="shop",
        action="shop_buy",
        delta_copper=-actual_cost if currency == "copper" else 0,
        delta_gold=-actual_cost if currency == "gold" else 0,
        currency=currency,
        item_id=item_id,
        qty=quantity,
        shown_price=total_price,
        actual_price=actual_cost,
        success=True,
        request_id=request_id,
        rank=int(user.get("rank", 1) or 1),
        meta={
            "limit": item_info.get("limit"),
            "limit_period": item_info.get("limit_period"),
            "delta_spirit_high": -actual_cost if currency == "spirit_high" else 0,
        },
    )
    return _dedup_return({
        "success": True,
        "message": f"购买成功！获得 {item_name} x{quantity}",
        "item": item_payload,
        "quantity": quantity,
        "price": total_price,
        "base_price": total_price,
        "extra_fee": extra_fee,
        "actual_price": actual_cost,
        "currency": currency,
        "remaining_limit": get_shop_remaining_limit(user_id, item_id, item_payload),
    }, 200)


def settle_signin(*, user_id: str) -> Tuple[Dict[str, Any], int]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404

    from core.game.signin import do_signin
    today_start = midnight_timestamp()
    rewards: Dict[str, Any] = {}
    message = ""

    # ---- 单事务原子签到（事务内防重 + 发奖） ----
    with db_transaction() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404

        snapshot = dict(row)
        ok, result, message = do_signin(snapshot)
        if not ok:
            rewards = result.get("rewards") if isinstance(result, dict) else None
            log_event(
                "signin",
                user_id=user_id,
                success=True,
                rank=int(snapshot.get("rank", 1) or 1),
                meta={"already_signed": True},
            )
            return {"success": True, "already_signed": True, "message": message, "rewards": rewards}, 200

        rewards = result["rewards"]
        updates = result["updates"]

        new_sign = int(updates.get("sign", 1) or 1)
        new_last_sign = int(updates.get("last_sign_timestamp", int(time.time())) or int(time.time()))
        new_consecutive = int(updates.get("consecutive_sign_days", 0) or 0)
        new_max_signin = int(
            updates.get(
                "max_signin_days",
                max(
                    int(snapshot.get("max_signin_days", 0) or 0),
                    int(snapshot.get("consecutive_sign_days", 0) or 0),
                    new_consecutive,
                ),
            )
            or 0
        )
        month_bonus = rewards.get("month_bonus") or {}
        copper_bonus = int(rewards.get("copper", 0) or 0) + int(month_bonus.get("copper", 0) or 0)
        exp_bonus = int(rewards.get("exp", 0) or 0) + int(month_bonus.get("exp", 0) or 0)
        gold_bonus = int(rewards.get("gold", 0) or 0) + int(month_bonus.get("gold", 0) or 0)

        cur.execute(
            """UPDATE users
               SET sign = %s,
                   last_sign_timestamp = %s,
                   consecutive_sign_days = %s,
                   max_signin_days = %s,
                   signin_month_key = %s,
                   signin_month_days = %s,
                   signin_month_claim_bits = %s,
                   copper = copper + %s,
                   exp = exp + %s,
                   gold = gold + %s
               WHERE user_id = %s
                 AND COALESCE(last_sign_timestamp, 0) < %s""",
            (
                new_sign,
                new_last_sign,
                new_consecutive,
                new_max_signin,
                updates.get("signin_month_key", ""),
                int(updates.get("signin_month_days", 0) or 0),
                int(updates.get("signin_month_claim_bits", 0) or 0),
                copper_bonus,
                exp_bonus,
                gold_bonus,
                user_id,
                today_start,
            ),
        )
        if int(cur.rowcount or 0) == 0:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            latest_row = cur.fetchone()
            latest = dict(latest_row) if latest_row else snapshot
            latest_ok, latest_result, latest_message = do_signin(latest)
            if latest_ok:
                latest_message = "签到状态已变化，请重试"
            if not latest_ok:
                reward_preview = latest_result.get("rewards") if isinstance(latest_result, dict) else None
                log_event(
                    "signin",
                    user_id=user_id,
                    success=True,
                    rank=int(latest.get("rank", 1) or 1),
                    meta={"already_signed": True},
                )
                return {"success": True, "already_signed": True, "message": latest_message, "rewards": reward_preview}, 200
            return {"success": False, "code": "FAILED", "message": latest_message}, 400

        signin_item = None
        if rewards.get("item"):
            item_info = rewards["item"]
            base_item = get_item_by_id(item_info["id"])
            if base_item:
                item_type = getattr(base_item.get("type"), "value", base_item.get("type"))
                if item_type == "material":
                    signin_item = generate_material(item_info["id"], item_info["quantity"])
                elif item_type == "pill":
                    signin_item = generate_pill(item_info["id"], item_info["quantity"])
                elif item_type == "skill_book":
                    signin_item = generate_skill_book(item_info["id"], item_info["quantity"])
                else:
                    signin_item = generate_equipment(base_item, Quality.COMMON, 1)
                    signin_item["quantity"] = int(item_info.get("quantity", 1) or 1)

        if signin_item:
            cur.execute(
                """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                   quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                   first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, signin_item.get("item_id"), signin_item.get("item_name"),
                 signin_item.get("item_type"), signin_item.get("quality", "common"),
                 signin_item.get("quantity", 1), signin_item.get("level", 1),
                 signin_item.get("attack_bonus", 0), signin_item.get("defense_bonus", 0),
                 signin_item.get("hp_bonus", 0), signin_item.get("mp_bonus", 0),
                 signin_item.get("first_round_reduction_pct", 0), signin_item.get("crit_heal_pct", 0),
                 signin_item.get("element_damage_pct", 0), signin_item.get("low_hp_shield_pct", 0)),
            )

        month_item = None
        if month_bonus.get("item"):
            item_info = month_bonus["item"]
            base_item = get_item_by_id(item_info["id"])
            if base_item:
                item_type = getattr(base_item.get("type"), "value", base_item.get("type"))
                if item_type == "material":
                    month_item = generate_material(item_info["id"], item_info["quantity"])
                elif item_type == "pill":
                    month_item = generate_pill(item_info["id"], item_info["quantity"])
                elif item_type == "skill_book":
                    month_item = generate_skill_book(item_info["id"], item_info["quantity"])
                else:
                    month_item = generate_equipment(base_item, Quality.COMMON, 1)
                    month_item["quantity"] = int(item_info.get("quantity", 1) or 1)
        if month_item:
            cur.execute(
                """INSERT INTO items (user_id, item_id, item_name, item_type, quality,
                   quantity, level, attack_bonus, defense_bonus, hp_bonus, mp_bonus,
                   first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, month_item.get("item_id"), month_item.get("item_name"),
                 month_item.get("item_type"), month_item.get("quality", "common"),
                 month_item.get("quantity", 1), month_item.get("level", 1),
                 month_item.get("attack_bonus", 0), month_item.get("defense_bonus", 0),
                 month_item.get("hp_bonus", 0), month_item.get("mp_bonus", 0),
                 month_item.get("first_round_reduction_pct", 0), month_item.get("crit_heal_pct", 0),
                 month_item.get("element_damage_pct", 0), month_item.get("low_hp_shield_pct", 0)),
            )

    increment_quest(user_id, "daily_signin")
    story_update = []
    try:
        story_update = track_story_action(user_id, "signin")
    except Exception:
        story_update = []
    log_event(
        "signin",
        user_id=user_id,
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={"already_signed": False},
    )
    month_bonus = rewards.get("month_bonus") or {}
    log_economy_ledger(
        user_id=user_id,
        module="signin",
        action="signin",
        delta_copper=int(rewards.get("copper", 0) or 0) + int(month_bonus.get("copper", 0) or 0),
        delta_gold=int(rewards.get("gold", 0) or 0) + int(month_bonus.get("gold", 0) or 0),
        delta_exp=int(rewards.get("exp", 0) or 0) + int(month_bonus.get("exp", 0) or 0),
        success=True,
        rank=int(user.get("rank", 1) or 1),
        meta={
            "day": rewards.get("day_in_cycle"),
            "consecutive_days": rewards.get("consecutive_days"),
            "month_days": rewards.get("month_days"),
        },
    )
    return {"success": True, "message": message, "rewards": rewards, "story_update": story_update}, 200


def settle_breakthrough(
    *,
    user_id: str,
    use_pill: bool,
    strategy: str = "steady",
) -> Tuple[Dict[str, Any], int]:
    from core.game.realms import (
        get_next_realm,
        can_breakthrough,
        calculate_breakthrough_cost,
        attempt_breakthrough,
        calculate_user_stats,
        get_realm_by_id,
    )
    from core.utils.number import format_stamina_value

    user = get_user_by_id(user_id)
    if not user:
        log_event("breakthrough", user_id=user_id, success=False, reason="USER_NOT_FOUND")
        return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404

    current_rank = user.get("rank", 1)
    rank = int(current_rank or 1)
    if not is_realm_trial_complete(user_id, rank):
        trial = get_or_create_realm_trial(user_id, rank) or {}
        requirement_text = _realm_trial_requirement_text(trial)
        return {
            "success": False,
            "code": "REALM_TRIAL",
            "message": f"需完成当前境界试炼后方可突破：{requirement_text}",
            "trial": trial,
            "trial_requirements": _realm_trial_requirement_payload(trial),
        }, 400
    now = int(time.time())
    protect_cfg = _pill_buff_cfg()["breakthrough_protect"]
    protect_until = int(user.get("breakthrough_protect_until", 0) or 0)
    protect_active = protect_until > now
    boost_until = int(user.get("breakthrough_boost_until", 0) or 0)
    boost_pct = float(user.get("breakthrough_boost_pct", 0) or 0)
    boost_active = boost_until > now and boost_pct > 0
    strategy = (strategy or "normal").strip().lower()
    if strategy not in ("normal", "steady", "protect", "desperate"):
        strategy = "normal"
    if use_pill and strategy == "normal":
        strategy = "steady"

    if not can_breakthrough(user.get("exp", 0), current_rank):
        log_event(
            "breakthrough",
            user_id=user_id,
            success=False,
            rank=rank,
            reason="INSUFFICIENT_EXP",
            meta={"strategy": strategy},
        )
        return {"success": False, "code": "INSUFFICIENT_EXP", "message": "修为不足，无法突破"}, 400

    bt_cfg = _breakthrough_cfg()
    current_realm = get_realm_by_id(current_rank) or {"name": "当前境界"}
    next_realm = get_next_realm(current_rank)
    if not next_realm:
        log_event(
            "breakthrough",
            user_id=user_id,
            success=False,
            rank=rank,
            reason="MAX",
            meta={"strategy": strategy},
        )
        return {"success": False, "code": "MAX", "message": "你已达到最高境界！"}, 400
    is_tribulation = _is_tribulation_breakthrough(current_realm=current_realm, next_realm=next_realm)
    if is_tribulation:
        # 圆满关口统一为渡劫突破，忽略其他策略入参。
        strategy = "steady"
    tribulation_flat_penalty = float(bt_cfg.get("tribulation_flat_penalty", 0.10) or 0.10) if is_tribulation else 0.0
    tribulation_rate_multiplier = float(bt_cfg.get("tribulation_rate_multiplier", 0.70) or 0.70) if is_tribulation else 1.0
    tribulation_cost_multiplier = float(bt_cfg.get("tribulation_extra_cost_multiplier", 1.20) or 1.20) if is_tribulation else 1.0
    tribulation_extra_stamina = int(bt_cfg.get("tribulation_extra_stamina", 1) or 1) if is_tribulation else 0

    base_cost = calculate_breakthrough_cost(current_rank)
    protect_material_need = _protect_material_need(current_rank, bt_cfg) if strategy == "protect" else 0
    base_total_cost = int(base_cost + protect_material_need)
    if is_tribulation:
        cost = int(max(base_total_cost, round(base_total_cost * tribulation_cost_multiplier)))
    else:
        cost = base_total_cost
    tribulation_extra_cost = max(0, int(cost - base_total_cost))
    if user.get("copper", 0) < cost:
        log_event(
            "breakthrough",
            user_id=user_id,
            success=False,
            rank=rank,
            reason="INSUFFICIENT_COPPER",
            meta={
                "strategy": strategy,
                "cost": cost,
                "base_cost": base_cost,
                "extra_cost": protect_material_need,
                "is_tribulation": bool(is_tribulation),
                "tribulation_extra_cost": tribulation_extra_cost,
            },
        )
        return {"success": False, "code": "INSUFFICIENT_COPPER", "message": f"下品灵石不足，需要 {cost} 下品灵石"}, 400

    base_rate = float(next_realm.get("break_rate", 0.0) or 0.0)
    env_ctx = _resolve_breakthrough_environment(user_id=user_id, user=user, bt_cfg=bt_cfg)
    location_bonus = float(env_ctx.get("location_bonus", 0.0) or 0.0)
    fortune_bonus = float(env_ctx.get("fortune_bonus", 0.0) or 0.0)
    consume_item_id = None
    consume_item_name = ""
    consume_item_type = None
    consume_item_qty = 0
    pill_bonus = 0.0
    if strategy == "steady":
        steady_pill = _pick_steady_breakthrough_pill(user_id=user_id, bt_cfg=bt_cfg)
        if steady_pill:
            consume_item_id = str(steady_pill.get("item_id") or "")
            consume_item_name = str(steady_pill.get("item_name") or "")
            consume_item_type = "pill"
            consume_item_qty = 1
            pill_bonus = float(steady_pill.get("bonus", 0.0) or 0.0)

    item_row = None
    if consume_item_id:
        item_row = fetch_one(
            "SELECT * FROM items WHERE user_id = %s AND item_id = %s AND item_type = %s AND quantity >= %s ORDER BY id ASC",
            (user_id, consume_item_id, consume_item_type, consume_item_qty),
        )
        if not item_row:
            if boost_active and consume_item_type == "pill":
                consume_item_id = None
                consume_item_name = ""
                consume_item_type = None
                consume_item_qty = 0
                pill_bonus = 0.0
            else:
                log_event(
                    "breakthrough",
                    user_id=user_id,
                    success=False,
                    rank=rank,
                    reason="INSUFFICIENT_ITEM",
                    meta={"strategy": strategy, "item_id": consume_item_id, "cost": cost, "base_cost": base_cost, "extra_cost": protect_material_need},
                )
                item_name = consume_item_name or "突破材料"
                return {"success": False, "code": "INSUFFICIENT_ITEM", "message": f"{item_name}不足，无法使用当前冲关策略"}, 400
    elif strategy == "steady" and not boost_active:
        log_event(
            "breakthrough",
            user_id=user_id,
            success=False,
            rank=rank,
            reason="INSUFFICIENT_ITEM",
            meta={
                "strategy": strategy,
                "item_id": "breakthrough_family",
                "cost": cost,
                "base_cost": base_cost,
                "extra_cost": protect_material_need,
            },
        )
        return {
            "success": False,
            "code": "INSUFFICIENT_ITEM",
            "message": "突破丹不足，需持有突破丹/高级突破丹/超级突破丹或先激活聚灵增益",
        }, 400

    fire_bonus = float(bt_cfg.get("fire_bonus", 0.03) or 0.03)
    shown_rate = base_rate
    if user.get("element") == "火":
        shown_rate = min(1.0, shown_rate + fire_bonus)
    if protect_active:
        shown_rate = min(1.0, shown_rate + float(protect_cfg.get("success_bonus", 0.05)))
    if boost_active:
        shown_rate = min(1.0, shown_rate + float(boost_pct) / 100.0)
    if abs(location_bonus) > 1e-9:
        shown_rate = min(1.0, max(0.0, shown_rate + location_bonus))
    if abs(fortune_bonus) > 1e-9:
        shown_rate = min(1.0, max(0.0, shown_rate + fortune_bonus))
    if pill_bonus > 0:
        shown_rate = min(1.0, shown_rate + pill_bonus)
    tribulation_rate_ignored = bool(
        is_tribulation
        and strategy == "steady"
        and _pill_breaks_tribulation_limit(consume_item_id)
    )
    shown_rate = _apply_tribulation_rate_adjustment(
        rate=shown_rate,
        is_tribulation=bool(is_tribulation and not tribulation_rate_ignored),
        bt_cfg=bt_cfg,
    )
    stamina_cost = int(bt_cfg.get("stamina_cost", 1) or 1) + tribulation_extra_stamina

    try:
        current_stamina = float(user.get("stamina", 0) or 0)
    except (TypeError, ValueError):
        current_stamina = 0.0
    if current_stamina < stamina_cost:
        log_event(
            "breakthrough",
            user_id=user_id,
            success=False,
            rank=rank,
            reason="INSUFFICIENT_STAMINA",
            meta={"strategy": strategy},
        )
        return {
            "success": False,
            "code": "INSUFFICIENT_STAMINA",
            "message": f"精力不足，突破需要 {stamina_cost} 点精力",
            "stamina": format_stamina_value(current_stamina),
            "stamina_cost": stamina_cost,
        }, 400

    use_pill_effective = pill_bonus > 0
    attempt_base_rate = base_rate
    if user.get("element") == "火":
        attempt_base_rate += fire_bonus
    if use_pill_effective:
        attempt_base_rate += pill_bonus
    extra_bonus = shown_rate - attempt_base_rate
    ok, message = attempt_breakthrough(
        user,
        use_pill_effective,
        extra_bonus=extra_bonus,
        forced_success_rate=shown_rate,
    )

    new_rank = next_realm["id"]

    if ok:
        # 突破成功中品灵石奖励（按境界阶段）
        gold_reward = 0
        if new_rank >= 30:
            gold_reward = 5    # 大乘+
        elif new_rank >= 18:
            gold_reward = 5    # 化神+
        elif new_rank >= 14:
            gold_reward = 3    # 元婴
        elif new_rank >= 10:
            gold_reward = 2    # 金丹
        elif new_rank >= 6:
            gold_reward = 1    # 筑基
        if strategy == "desperate":
            gold_reward += int(bt_cfg.get("desperate_success_gold_bonus", 1) or 1)
        copper_reward = 0
        if strategy == "desperate":
            copper_reward = max(
                int(bt_cfg.get("desperate_success_copper_min", 50) or 50),
                cost // int(bt_cfg.get("desperate_success_copper_cost_divisor", 5) or 5),
            )

        new_stats = calculate_user_stats({"rank": new_rank, "element": user.get("element")})
        restored_hp = int(new_stats["max_hp"])
        restored_mp = int(new_stats["max_mp"])

        # ---- 单事务原子突破成功 ----
        try:
            with db_transaction() as cur:
                if item_row:
                    if int(item_row.get("quantity", 0) or 0) == consume_item_qty:
                        cur.execute("DELETE FROM items WHERE id = %s AND quantity = %s", (item_row["id"], consume_item_qty))
                    else:
                        cur.execute(
                            "UPDATE items SET quantity = quantity - %s WHERE id = %s AND quantity >= %s",
                            (consume_item_qty, item_row["id"], consume_item_qty),
                        )
                    if int(cur.rowcount or 0) != 1:
                        raise ValueError("INSUFFICIENT_ITEM")

                if not spend_user_stamina_tx(cur, user_id, stamina_cost, now=now):
                    raise ValueError("INSUFFICIENT_STAMINA")

                if protect_active:
                    cur.execute(
                        """UPDATE users SET
                           rank = %s, copper = copper - %s + %s, gold = gold + %s,
                           max_hp = %s, max_mp = %s, hp = %s, mp = %s,
                           attack = %s, defense = %s,
                           weak_until = 0, breakthrough_pity = 0, breakthrough_protect_until = 0
                           WHERE user_id = %s AND copper >= %s""",
                        (
                            new_rank,
                            cost,
                            copper_reward,
                            gold_reward,
                            new_stats["max_hp"],
                            new_stats["max_mp"],
                            restored_hp,
                            restored_mp,
                            new_stats["attack"],
                            new_stats["defense"],
                            user_id,
                            cost,
                        ),
                    )
                else:
                    cur.execute(
                        """UPDATE users SET
                           rank = %s, copper = copper - %s + %s, gold = gold + %s,
                           max_hp = %s, max_mp = %s, hp = %s, mp = %s,
                           attack = %s, defense = %s,
                           weak_until = 0, breakthrough_pity = 0
                           WHERE user_id = %s AND copper >= %s""",
                        (
                            new_rank,
                            cost,
                            copper_reward,
                            gold_reward,
                            new_stats["max_hp"],
                            new_stats["max_mp"],
                            restored_hp,
                            restored_mp,
                            new_stats["attack"],
                            new_stats["defense"],
                            user_id,
                            cost,
                        ),
                    )
                if int(cur.rowcount or 0) != 1:
                    raise ValueError("INSUFFICIENT_COPPER")

                cur.execute(
                    """INSERT INTO breakthrough_logs
                       (user_id, from_rank, to_rank, success, exp_lost, timestamp)
                       VALUES (%s, %s, %s, 1, 0, %s)""",
                    (user_id, current_rank, new_rank, int(time.time())),
                )
        except ValueError as exc:
            reason = str(exc)
            if reason == "INSUFFICIENT_STAMINA":
                latest = get_user_by_id(user_id) or {}
                log_event("breakthrough", user_id=user_id, success=False, rank=rank, reason=reason, meta={"strategy": strategy})
                return {
                    "success": False,
                    "code": "INSUFFICIENT_STAMINA",
                    "message": f"精力不足，突破需要 {stamina_cost} 点精力",
                    "stamina": format_stamina_value((latest or {}).get("stamina", 0)),
                    "stamina_cost": stamina_cost,
                }, 400
            if reason == "INSUFFICIENT_COPPER":
                latest = get_user_by_id(user_id) or {}
                log_event("breakthrough", user_id=user_id, success=False, rank=rank, reason=reason, meta={"strategy": strategy, "cost": cost})
                return {
                    "success": False,
                    "code": "INSUFFICIENT_COPPER",
                    "message": f"下品灵石不足，需要 {cost} 下品灵石",
                    "copper": int((latest or {}).get("copper", 0) or 0),
                }, 400
            if reason == "INSUFFICIENT_ITEM":
                item_name = consume_item_name or "突破材料"
                log_event(
                    "breakthrough",
                    user_id=user_id,
                    success=False,
                    rank=rank,
                    reason=reason,
                    meta={"strategy": strategy, "item_id": consume_item_id, "cost": cost, "base_cost": base_cost, "extra_cost": protect_material_need},
                )
                return {"success": False, "code": "INSUFFICIENT_ITEM", "message": f"{item_name}不足，无法使用当前冲关策略"}, 400
            raise

        resp = {
            "success": True,
            "message": message,
            "new_rank": new_rank,
            "new_realm": next_realm["name"] if next_realm else "未知",
            "cost": cost,
            "success_rate": shown_rate,
            "strategy": strategy,
            "is_tribulation": bool(is_tribulation),
            "tribulation_name": "天雷劫" if is_tribulation else "",
            "tribulation_flat_penalty": float(tribulation_flat_penalty if is_tribulation else 0.0),
            "tribulation_rate_multiplier": float(tribulation_rate_multiplier if is_tribulation else 1.0),
            "tribulation_rate_ignored": bool(tribulation_rate_ignored),
            "tribulation_extra_cost_copper": int(tribulation_extra_cost),
            "tribulation_extra_stamina": int(tribulation_extra_stamina),
            "location_name": env_ctx.get("location_name"),
            "spirit_density": float(env_ctx.get("spirit_density", 1.0) or 1.0),
            "location_bonus": float(location_bonus),
            "fortune_label": env_ctx.get("fortune_label"),
            "fortune_bonus": float(fortune_bonus),
            "event_title": "天雷劫已渡，道基蜕变" if is_tribulation else "天劫已破，道心更进一步",
            "event_flavor": (
                f"你顶住了从【{current_realm.get('name', '当前境界')}】到【{next_realm['name']}】的天雷洗礼，"
                "经脉重铸，根基更坚。"
                if is_tribulation
                else f"你熬过了从【{current_realm.get('name', '当前境界')}】到【{next_realm['name']}】的瓶颈，气海与经脉一同蜕变。"
            ),
            "next_goal": "建议优先补齐渡劫资源，再规划下一阶段修炼与秘境路线。" if is_tribulation else "建议立刻查看新解锁内容，并准备下一阶段的修炼、秘境和炼丹路线。",
            "stamina_cost": stamina_cost,

            "post_breakthrough_hp": restored_hp,
            "post_breakthrough_mp": restored_mp,
        }
        resp["congrats_message"] = _build_breakthrough_congrats_message(
            new_realm=str(resp.get("new_realm", "未知境界") or "未知境界"),
            display_name=str(user.get("in_game_username") or user.get("username") or ""),
        )
        if protect_active:
            resp["protect_pill_used"] = True
            resp["message"] += "\n🛡️ 突破保护丹已生效。"
        if gold_reward > 0:
            resp["gold_reward"] = gold_reward
            resp["message"] += f"\n🟩 额外获得 {gold_reward} 中品灵石！"
        if copper_reward > 0:
            resp["copper_reward"] = copper_reward
            resp["message"] += f"\n🟦 生死破境额外获得 {copper_reward} 下品灵石！"
        if strategy == "steady" and item_row:
            resp["strategy_cost_text"] = f"消耗{(consume_item_name or '突破丹')} x1"
        elif strategy == "protect" and protect_material_need > 0:
            resp["strategy_cost_text"] = f"护脉附加消耗下品灵石 x{protect_material_need}"
        story_update = []
        try:
            story_update = track_story_action(user_id, "breakthrough_success")
        except Exception:
            story_update = []
        resp["story_update"] = story_update
        log_event(
            "breakthrough",
            user_id=user_id,
            success=True,
            rank=rank,
            meta={
                "strategy": strategy,
                "breakthrough_success": True,
                "from_rank": current_rank,
                "to_rank": new_rank,
                "shown_rate": shown_rate,
                "protect_active": protect_active,
                "boost_active": boost_active,
                "location_bonus": location_bonus,
                "fortune_bonus": fortune_bonus,
                "is_tribulation": bool(is_tribulation),
                "tribulation_flat_penalty": tribulation_flat_penalty if is_tribulation else 0.0,
                "tribulation_rate_multiplier": tribulation_rate_multiplier if is_tribulation else 1.0,
                "tribulation_rate_ignored": bool(tribulation_rate_ignored),
                "tribulation_extra_cost": tribulation_extra_cost,
                "tribulation_extra_stamina": tribulation_extra_stamina,
                "item_id": consume_item_id,
                "item_qty": consume_item_qty,
            },
        )
        log_economy_ledger(
            user_id=user_id,
            module="breakthrough",
            action="breakthrough",
            delta_copper=-cost + copper_reward,
            delta_gold=gold_reward,
            delta_stamina=-stamina_cost,
            item_id=consume_item_id,
            qty=consume_item_qty if consume_item_id else None,
            success=True,
            rank=rank,
            meta={
                "strategy": strategy,
                "breakthrough_success": True,
                "from_rank": current_rank,
                "to_rank": new_rank,
                "shown_rate": shown_rate,
                "location_bonus": location_bonus,
                "fortune_bonus": fortune_bonus,
                "is_tribulation": bool(is_tribulation),
                "tribulation_flat_penalty": tribulation_flat_penalty if is_tribulation else 0.0,
                "tribulation_rate_multiplier": tribulation_rate_multiplier if is_tribulation else 1.0,
                "tribulation_rate_ignored": bool(tribulation_rate_ignored),
                "tribulation_extra_cost": tribulation_extra_cost,
                "tribulation_extra_stamina": tribulation_extra_stamina,
            },
        )
        return resp, 200

    # ---- 突破失败 ----
    app_cfg = _config_raw()
    exp_lost_pct = float(app_cfg.get("balance", {}).get("breakthrough", {}).get("fail_exp_loss_pct", 0.05))
    if strategy == "steady":
        exp_lost_pct *= 0.5
    elif strategy == "desperate":
        exp_lost_pct = min(
            float(bt_cfg.get("desperate_exp_penalty_cap", 0.30) or 0.30),
            exp_lost_pct + float(bt_cfg.get("desperate_exp_penalty_add", 0.05) or 0.05),
        )
    if is_tribulation:
        exp_lost_pct = min(
            0.95,
            exp_lost_pct + float(bt_cfg.get("tribulation_fail_exp_penalty_add", 0.05) or 0.05),
        )
    weak_seconds = int(app_cfg.get("balance", {}).get("breakthrough", {}).get("weak_seconds", 3600))
    if strategy == "protect":
        weak_seconds = 0
    elif strategy == "desperate":
        weak_seconds += int(bt_cfg.get("desperate_weak_seconds_add", 1800) or 1800)
    if is_tribulation:
        weak_seconds += int(bt_cfg.get("tribulation_fail_weak_seconds_add", 1200) or 1200)
    if protect_active:
        exp_lost_pct *= float(protect_cfg.get("exp_loss_mult", 0.5))
        weak_seconds = int(weak_seconds * float(protect_cfg.get("weak_seconds_mult", 0.0)))
    exp_lost = int(user.get("exp", 0) * exp_lost_pct)
    weak_until = int(time.time()) + weak_seconds

    # ---- 单事务原子突破失败 ----
    try:
        with db_transaction() as cur:
            if item_row:
                if int(item_row.get("quantity", 0) or 0) == consume_item_qty:
                    cur.execute("DELETE FROM items WHERE id = %s AND quantity = %s", (item_row["id"], consume_item_qty))
                else:
                    cur.execute(
                        "UPDATE items SET quantity = quantity - %s WHERE id = %s AND quantity >= %s",
                        (consume_item_qty, item_row["id"], consume_item_qty),
                    )
                if int(cur.rowcount or 0) != 1:
                    raise ValueError("INSUFFICIENT_ITEM")

            if not spend_user_stamina_tx(cur, user_id, stamina_cost, now=now):
                raise ValueError("INSUFFICIENT_STAMINA")

            if protect_active:
                cur.execute(
                    """UPDATE users SET
                       exp = GREATEST(0, exp - %s), copper = copper - %s,
                       weak_until = %s, breakthrough_pity = 0, breakthrough_protect_until = 0
                       WHERE user_id = %s AND copper >= %s""",
                    (
                        exp_lost,
                        cost,
                        weak_until,
                        user_id,
                        cost,
                    ),
                )
            else:
                cur.execute(
                    """UPDATE users SET
                       exp = GREATEST(0, exp - %s), copper = copper - %s,
                       weak_until = %s, breakthrough_pity = 0
                       WHERE user_id = %s AND copper >= %s""",
                    (
                        exp_lost,
                        cost,
                        weak_until,
                        user_id,
                        cost,
                    ),
                )
            if int(cur.rowcount or 0) != 1:
                raise ValueError("INSUFFICIENT_COPPER")

            cur.execute(
                """INSERT INTO breakthrough_logs
                   (user_id, from_rank, to_rank, success, exp_lost, timestamp)
                   VALUES (%s, %s, %s, 0, %s, %s)""",
                (user_id, current_rank, current_rank, exp_lost, int(time.time())),
            )
    except ValueError as exc:
        reason = str(exc)
        if reason == "INSUFFICIENT_STAMINA":
            latest = get_user_by_id(user_id) or {}
            log_event("breakthrough", user_id=user_id, success=False, rank=rank, reason=reason, meta={"strategy": strategy})
            return {
                "success": False,
                "code": "INSUFFICIENT_STAMINA",
                "message": f"精力不足，突破需要 {stamina_cost} 点精力",
                "stamina": format_stamina_value((latest or {}).get("stamina", 0)),
                "stamina_cost": stamina_cost,
            }, 400
        if reason == "INSUFFICIENT_COPPER":
            latest = get_user_by_id(user_id) or {}
            log_event("breakthrough", user_id=user_id, success=False, rank=rank, reason=reason, meta={"strategy": strategy, "cost": cost})
            return {
                "success": False,
                "code": "INSUFFICIENT_COPPER",
                "message": f"下品灵石不足，需要 {cost} 下品灵石",
                "copper": int((latest or {}).get("copper", 0) or 0),
            }, 400
        if reason == "INSUFFICIENT_ITEM":
            item_name = consume_item_name or "突破材料"
            log_event(
                "breakthrough",
                user_id=user_id,
                success=False,
                rank=rank,
                reason=reason,
                meta={"strategy": strategy, "item_id": consume_item_id, "cost": cost, "base_cost": base_cost, "extra_cost": protect_material_need},
            )
            return {"success": False, "code": "INSUFFICIENT_ITEM", "message": f"{item_name}不足，无法使用当前冲关策略"}, 400
        raise

    realm_name = current_realm.get("name", "当前境界")
    penalty_prefix = "渡劫失败，遭天雷反噬，" if is_tribulation else "突破失败，"
    penalty_message = penalty_prefix + f"损失{_format_ratio_percent(exp_lost_pct)}修为，{_format_weak_penalty_text(weak_seconds)}"

    if strategy == "steady":
        if consume_item_id:
            strategy_cost_text = f"已消耗{consume_item_name or '突破丹'} x1"
        else:
            strategy_cost_text = "稳妥突破：本次未消耗突破丹（由聚灵增益替代）"
    elif strategy == "protect" and protect_material_need > 0:
        strategy_cost_text = f"已额外消耗下品灵石 x{protect_material_need}"
    else:
        strategy_cost_text = ""

    resp = {
        "success": False,
        "code": "BREAKTHROUGH_FAILED",
        "message": penalty_message + f"\n本次判定成功率：{int(shown_rate * 100)}%\n\n本次突破按实时成功率判定，不含保底机制。",
        "exp_lost": exp_lost,
        "weak_seconds": weak_seconds,
        "cost": cost,
        "success_rate": shown_rate,
        "realm": realm_name,
        "strategy": strategy,
        "is_tribulation": bool(is_tribulation),
        "tribulation_name": "天雷劫" if is_tribulation else "",
        "tribulation_flat_penalty": float(tribulation_flat_penalty if is_tribulation else 0.0),
        "tribulation_rate_multiplier": float(tribulation_rate_multiplier if is_tribulation else 1.0),
        "tribulation_rate_ignored": bool(tribulation_rate_ignored),
        "tribulation_extra_cost_copper": int(tribulation_extra_cost),
        "tribulation_extra_stamina": int(tribulation_extra_stamina),
        "location_name": env_ctx.get("location_name"),
        "spirit_density": float(env_ctx.get("spirit_density", 1.0) or 1.0),
        "location_bonus": float(location_bonus),
        "fortune_label": env_ctx.get("fortune_label"),
        "fortune_bonus": float(fortune_bonus),
        "stamina_cost": stamina_cost,
        "event_title": "天雷劫未渡，道心仍可重铸" if is_tribulation else "天劫未过，但道心未碎",
        "event_flavor": "本次雷劫过于凶险，但你已摸清劫雷节奏，调整准备后仍可再战。" if is_tribulation else "这次冲关虽然折戟，但你已积累了更清晰的破境经验，调整状态后可再次尝试。",
        "next_goal": "建议补足丹药、灵石与精力后再渡劫，并尽量选择高灵气地脉。" if is_tribulation else "建议先恢复状态，补齐突破丹或下品灵石，再择时再次冲关。",
        "strategy_cost_text": strategy_cost_text,
    }
    if protect_active:
        resp["protect_pill_used"] = True
        resp["message"] += "\n🛡️ 突破保护丹生效，本次失败惩罚降低。"
    log_event(
        "breakthrough",
        user_id=user_id,
        success=False,
        rank=rank,
        meta={
            "strategy": strategy,
            "breakthrough_success": False,
            "from_rank": current_rank,
            "to_rank": current_rank,
            "shown_rate": shown_rate,
            "exp_lost": exp_lost,
            "protect_active": protect_active,
            "boost_active": boost_active,
            "location_bonus": location_bonus,
            "fortune_bonus": fortune_bonus,
            "is_tribulation": bool(is_tribulation),
            "tribulation_flat_penalty": tribulation_flat_penalty if is_tribulation else 0.0,
            "tribulation_rate_multiplier": tribulation_rate_multiplier if is_tribulation else 1.0,
            "tribulation_rate_ignored": bool(tribulation_rate_ignored),
            "tribulation_extra_cost": tribulation_extra_cost,
            "tribulation_extra_stamina": tribulation_extra_stamina,
            "item_id": consume_item_id,
            "item_qty": consume_item_qty,
        },
    )
    log_economy_ledger(
        user_id=user_id,
        module="breakthrough",
        action="breakthrough",
        delta_copper=-cost,
        delta_exp=-exp_lost,
        delta_stamina=-stamina_cost,
        item_id=consume_item_id,
        qty=consume_item_qty if consume_item_id else None,
        success=True,
        rank=rank,
        meta={
            "strategy": strategy,
            "breakthrough_success": False,
            "business_success": False,
            "from_rank": current_rank,
            "to_rank": current_rank,
            "shown_rate": shown_rate,
            "location_bonus": location_bonus,
            "fortune_bonus": fortune_bonus,
            "is_tribulation": bool(is_tribulation),
            "tribulation_flat_penalty": tribulation_flat_penalty if is_tribulation else 0.0,
            "tribulation_rate_multiplier": tribulation_rate_multiplier if is_tribulation else 1.0,
            "tribulation_rate_ignored": bool(tribulation_rate_ignored),
            "tribulation_extra_cost": tribulation_extra_cost,
            "tribulation_extra_stamina": tribulation_extra_stamina,
        },
    )
    return resp, 400


def _consume_one_item_tx(cur: object, *, user_id: str, item_id: str) -> bool:
    cur.execute(
        "SELECT id, quantity FROM items WHERE user_id = %s AND item_id = %s AND quantity > 0 ORDER BY id ASC LIMIT 1",
        (user_id, item_id),
    )
    row = cur.fetchone()
    if not row:
        return False
    row_id = int(row["id"])
    qty = int(row["quantity"] or 0)
    if qty > 1:
        cur.execute(
            "UPDATE items SET quantity = quantity - 1 WHERE id = %s AND user_id = %s AND quantity > 0",
            (row_id, user_id),
        )
    else:
        cur.execute(
            "DELETE FROM items WHERE id = %s AND user_id = %s AND quantity = 1",
            (row_id, user_id),
        )
    return int(cur.rowcount or 0) == 1


# ---------------------------------------------------------------------------
# settle_use_item -- dict-dispatch 重构
#
# 每个 handler 签名统一:
#   def _handle_xxx(*, cur, user_id, user, base_item, now) -> Dict[str, Any]
#
# handler 内部通过 cur 做 SQL 写操作（已在事务内）。
# 返回 payload dict，由外层包装成 (payload, status_code) 元组。
# 如需提前返回错误，抛出 _UseItemAbort(payload, status) 即可。
# ---------------------------------------------------------------------------

class _UseItemAbort(Exception):
    """Handler 需要提前返回错误时抛出。"""
    def __init__(self, payload: Dict[str, Any], status: int):
        self.payload = payload
        self.status = status


def _handle_exp(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    value = base_item.get("value", 0)
    cur.execute("UPDATE users SET exp = exp + %s WHERE user_id = %s", (value, user_id))
    return {
        "success": True,
        "message": f"使用成功！获得 {value} 修为",
        "effect": "exp",
        "value": value,
        "effect_type": "instant",
        "effect_description": f"修为+{value}",
        "effect_duration": 0,
        "effect_value": value,
    }


def _handle_hp(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    value_pct = float(base_item.get("value_pct", 0) or 0)
    heal_amount = max(1, int(round(int(user.get("max_hp", 100) or 100) * value_pct)))
    cur.execute(
        "UPDATE users SET hp = LEAST(max_hp, hp + %s), vitals_updated_at = %s WHERE user_id = %s",
        (heal_amount, now, user_id),
    )
    return {
        "success": True,
        "message": f"使用成功！恢复 {heal_amount} HP",
        "effect": "hp",
        "value": heal_amount,
        "effect_type": "instant",
        "effect_description": f"恢复{heal_amount}HP",
        "effect_duration": 0,
        "effect_value": heal_amount,
    }


def _handle_mp(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    value_pct = float(base_item.get("value_pct", 0) or 0)
    recover_amount = max(1, int(round(int(user.get("max_mp", 50) or 50) * value_pct)))
    cur.execute(
        "UPDATE users SET mp = LEAST(max_mp, mp + %s), vitals_updated_at = %s WHERE user_id = %s",
        (recover_amount, now, user_id),
    )
    return {
        "success": True,
        "message": f"使用成功！恢复 {recover_amount} MP",
        "effect": "mp",
        "value": recover_amount,
        "effect_type": "instant",
        "effect_description": f"恢复{recover_amount}MP",
        "effect_duration": 0,
        "effect_value": recover_amount,
    }


def _handle_full_restore(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    cur.execute("UPDATE users SET hp = max_hp, mp = max_mp, vitals_updated_at = %s WHERE user_id = %s", (now, user_id))
    return {
        "success": True,
        "message": "使用成功！完全恢复HP和MP",
        "effect": "full_restore",
        "value": 0,
        "effect_type": "instant",
        "effect_description": "完全恢复HP和MP",
        "effect_duration": 0,
        "effect_value": 0,
    }


def _handle_attack_buff(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    value = int(base_item.get("value", 0) or 0)
    duration = int(base_item.get("duration", 3600) or 3600)
    if value <= 0:
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "此物品无法使用"}, 400)
    current_until = int(user.get("attack_buff_until", 0) or 0)
    active_val = int(_active_buff_value(now=now, until=current_until, value=user.get("attack_buff_value", 0), default=0.0))
    new_until = max(current_until if current_until > now else 0, now + duration)
    new_val = max(active_val, value)
    cur.execute(
        "UPDATE users SET attack_buff_until = %s, attack_buff_value = %s WHERE user_id = %s",
        (new_until, new_val, user_id),
    )
    duration_minutes = duration // 60
    return {
        "success": True,
        "message": f"使用成功！攻击+{new_val}（{duration_minutes}分钟内有效）",
        "effect": "attack_buff",
        "value": new_val,
        "effect_type": "buff",
        "effect_description": f"攻击+{new_val}",
        "effect_duration": duration,
        "effect_value": new_val,
        "buff_until": new_until,
    }


def _handle_defense_buff(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    value = int(base_item.get("value", 0) or 0)
    duration = int(base_item.get("duration", 3600) or 3600)
    if value <= 0:
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "此物品无法使用"}, 400)
    current_until = int(user.get("defense_buff_until", 0) or 0)
    active_val = int(_active_buff_value(now=now, until=current_until, value=user.get("defense_buff_value", 0), default=0.0))
    new_until = max(current_until if current_until > now else 0, now + duration)
    new_val = max(active_val, value)
    cur.execute(
        "UPDATE users SET defense_buff_until = %s, defense_buff_value = %s WHERE user_id = %s",
        (new_until, new_val, user_id),
    )
    duration_minutes = duration // 60
    return {
        "success": True,
        "message": f"使用成功！防御+{new_val}（{duration_minutes}分钟内有效）",
        "effect": "defense_buff",
        "value": new_val,
        "effect_type": "buff",
        "effect_description": f"防御+{new_val}",
        "effect_duration": duration,
        "effect_value": new_val,
        "buff_until": new_until,
    }


def _handle_cultivation_buff(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    """cultivation_sprint / cultivation_buff 共用逻辑。"""
    effect = base_item.get("effect")
    if user.get("state"):
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "修炼中无法使用，请先结算修炼"}, 400)
    buffs = _pill_buff_cfg()
    cfg = buffs["cultivation_sprint"]
    duration = int(base_item.get("duration", cfg.get("duration_seconds", 7200)) or 7200)
    bonus_pct = int(base_item.get("value", 0) or 0)
    if bonus_pct <= 0:
        exp_mult = float(cfg.get("exp_mult", 1.35))
        bonus_pct = int(round((exp_mult - 1) * 100))
    current_until = int(user.get("cultivation_boost_until", 0) or 0)
    active_pct = _active_buff_value(now=now, until=current_until, value=user.get("cultivation_boost_pct", 0), default=0.0)
    new_until = max(current_until if current_until > now else 0, now + duration)
    new_pct = max(active_pct, bonus_pct)
    item_name = str(base_item.get("name", "") or "")
    if not item_name:
        item_name = "悟道丹" if effect == "cultivation_buff" else "修炼冲刺丹"
    duration_minutes = duration // 60
    cur.execute(
        "UPDATE users SET cultivation_boost_until = %s, cultivation_boost_pct = %s WHERE user_id = %s",
        (new_until, new_pct, user_id),
    )
    return {
        "success": True,
        "message": f"使用成功！{item_name}生效，修炼收益+{int(new_pct)}%（{duration_minutes}分钟内有效）",
        "effect": effect,
        "value": int(new_pct),
        "effect_type": "buff",
        "effect_description": f"修炼收益+{int(new_pct)}%",
        "effect_duration": duration,
        "effect_value": int(new_pct),
        "buff_until": new_until,
    }


def _handle_realm_drop_boost(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    if get_secret_realm_attempts_left(user) <= 0:
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "今日秘境次数已用尽，无法使用"}, 400)
    buffs = _pill_buff_cfg()
    cfg = buffs["realm_drop"]
    duration = int(cfg.get("duration_seconds", 3600))
    drop_mul = float(cfg.get("drop_mul", 1.35))
    bonus_pct = int(round((drop_mul - 1) * 100))
    current_until = int(user.get("realm_drop_boost_until", 0) or 0)
    new_until = max(current_until, now + duration)
    cur.execute("UPDATE users SET realm_drop_boost_until = %s WHERE user_id = %s", (new_until, user_id))
    return {
        "success": True,
        "message": f"使用成功！秘境掉落丹生效，秘境掉落+{bonus_pct}%（{duration // 60}分钟内有效）",
        "effect": "realm_drop_boost",
        "value": bonus_pct,
        "effect_type": "buff",
        "effect_description": f"秘境掉落+{bonus_pct}%",
        "effect_duration": duration,
        "effect_value": bonus_pct,
        "buff_until": new_until,
    }


def _handle_breakthrough_protect(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    buffs = _pill_buff_cfg()
    cfg = buffs["breakthrough_protect"]
    duration = int(cfg.get("duration_seconds", 3600))
    exp_loss_mult = float(cfg.get("exp_loss_mult", 0.5))
    bonus_pct = int(round((1 - exp_loss_mult) * 100))
    current_until = int(user.get("breakthrough_protect_until", 0) or 0)
    new_until = max(current_until, now + duration)
    cur.execute("UPDATE users SET breakthrough_protect_until = %s WHERE user_id = %s", (new_until, user_id))
    return {
        "success": True,
        "message": f"使用成功！突破保护丹生效，下一次突破失败惩罚降低{bonus_pct}%（{duration // 60}分钟内有效）",
        "effect": "breakthrough_protect",
        "value": bonus_pct,
        "effect_type": "buff",
        "effect_description": f"突破失败惩罚降低{bonus_pct}%",
        "effect_duration": duration,
        "effect_value": bonus_pct,
        "buff_until": new_until,
    }


def _handle_spirit_array(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    bonus_pct = int(base_item.get("value", 0) or 0)
    duration = int(base_item.get("duration", 3600) or 3600)
    mp_pct = float(base_item.get("value_pct", 0) or 0)
    if bonus_pct <= 0:
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "此物品无法使用"}, 400)
    current_until = int(user.get("breakthrough_boost_until", 0) or 0)
    active_pct = _active_buff_value(now=now, until=current_until, value=user.get("breakthrough_boost_pct", 0), default=0.0)
    new_until = max(current_until if current_until > now else 0, now + duration)
    new_pct = max(active_pct, bonus_pct)
    recover_amount = 0
    if mp_pct > 0:
        recover_amount = max(1, int(round(int(user.get("max_mp", 50) or 50) * mp_pct)))
    message = f"使用成功！聚灵阵已启动，当前聚灵增益+{int(new_pct)}%"
    if recover_amount > 0:
        message += f"，恢复 {recover_amount} MP"
    message += f"（{duration // 60}分钟内有效）"
    if recover_amount > 0:
        cur.execute(
            "UPDATE users SET breakthrough_boost_until = %s, breakthrough_boost_pct = %s, mp = LEAST(max_mp, mp + %s), vitals_updated_at = %s WHERE user_id = %s",
            (new_until, new_pct, recover_amount, now, user_id),
        )
    else:
        cur.execute(
            "UPDATE users SET breakthrough_boost_until = %s, breakthrough_boost_pct = %s WHERE user_id = %s",
            (new_until, new_pct, user_id),
        )
    return {
        "success": True,
        "message": message,
        "effect": "spirit_array",
        "value": int(new_pct),
        "mp_recovered": recover_amount,
        "effect_type": "buff",
        "effect_description": f"聚灵增益+{int(new_pct)}%" + (f"，恢复{recover_amount}MP" if recover_amount > 0 else ""),
        "effect_duration": duration,
        "effect_value": int(new_pct),
        "buff_until": new_until,
    }


def _handle_breakthrough(*, cur, user_id, user, base_item, now) -> Dict[str, Any]:
    bonus_pct = int(base_item.get("value", 0) or 0)
    duration = int(base_item.get("duration", 3600) or 3600)
    if bonus_pct <= 0:
        raise _UseItemAbort({"success": False, "code": "INVALID", "message": "此物品无法使用"}, 400)
    current_until = int(user.get("breakthrough_boost_until", 0) or 0)
    active_pct = _active_buff_value(now=now, until=current_until, value=user.get("breakthrough_boost_pct", 0), default=0.0)
    new_until = max(current_until if current_until > now else 0, now + duration)
    new_pct = max(active_pct, bonus_pct)
    cur.execute(
        "UPDATE users SET breakthrough_boost_until = %s, breakthrough_boost_pct = %s WHERE user_id = %s",
        (new_until, new_pct, user_id),
    )
    return {
        "success": True,
        "message": f"使用成功！突破成功率+{int(new_pct)}%（{duration // 60}分钟内有效）",
        "effect": "breakthrough",
        "value": int(new_pct),
        "effect_type": "buff",
        "effect_description": f"突破成功率+{int(new_pct)}%",
        "effect_duration": duration,
        "effect_value": int(new_pct),
        "buff_until": new_until,
    }


# 效果名 -> 处理函数 映射表
_ITEM_EFFECT_HANDLERS: Dict[str, Any] = {
    "exp": _handle_exp,
    "hp": _handle_hp,
    "mp": _handle_mp,
    "full_restore": _handle_full_restore,
    "attack_buff": _handle_attack_buff,
    "defense_buff": _handle_defense_buff,
    "cultivation_sprint": _handle_cultivation_buff,
    "cultivation_buff": _handle_cultivation_buff,
    "realm_drop_boost": _handle_realm_drop_boost,
    "breakthrough_protect": _handle_breakthrough_protect,
    "spirit_array": _handle_spirit_array,
    "breakthrough": _handle_breakthrough,
}


def settle_use_item(*, user_id: str, item_id: str) -> Tuple[Dict[str, Any], int]:
    refresh_user_vitals(user_id)
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "code": "USER_NOT_FOUND", "message": "User not found"}, 404

    if not item_id:
        return {"success": False, "code": "MISSING_PARAMS", "message": "Missing item_id"}, 400

    base_item = get_item_by_id(item_id)
    if not base_item:
        return {"success": False, "code": "NOT_FOUND", "message": "未知物品"}, 400

    effect = base_item.get("effect")
    now = int(time.time())

    handler = _ITEM_EFFECT_HANDLERS.get(effect)
    if handler is None:
        return {"success": False, "code": "INVALID", "message": "此物品无法使用"}, 400

    try:
        with db_transaction() as cur:
            if not _consume_one_item_tx(cur, user_id=user_id, item_id=item_id):
                raise ValueError("NOT_FOUND")
            result = handler(cur=cur, user_id=user_id, user=user, base_item=base_item, now=now)
    except _UseItemAbort as e:
        return e.payload, e.status
    except ValueError:
        return {"success": False, "code": "NOT_FOUND", "message": "物品不存在或数量不足"}, 400

    return result, 200
