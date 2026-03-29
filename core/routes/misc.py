"""排行榜等杂项路由。"""

from flask import Blueprint, request

from core.routes._helpers import success
from core.database.connection import fetch_all, get_user_by_id, get_item_by_db_id, fetch_one, execute
from core.game.leaderboards import leaderboard_entry, get_stage_goal
from core.game.items import calculate_equipment_score
from core.game.maps import get_map
from core.game.realms import get_realm_by_id
from core.services.sect_service import get_user_sect_buffs
from core.utils.timeutil import local_day_key

misc_bp = Blueprint("misc", __name__)


def _calc_affix_score(user: dict) -> int:
    score = 0
    for slot in ("equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2"):
        db_id = user.get(slot)
        if not db_id:
            continue
        item = get_item_by_db_id(db_id)
        if not item:
            continue
        base_score = calculate_equipment_score(item)
        fr = float(item.get("first_round_reduction_pct", 0.0) or 0.0)
        ch = float(item.get("crit_heal_pct", 0.0) or 0.0)
        ed = float(item.get("element_damage_pct", 0.0) or 0.0)
        lh = float(item.get("low_hp_shield_pct", 0.0) or 0.0)
        affix_bonus = int(round((fr + ch + ed + lh) * 1000))
        score += base_score + affix_bonus
    return int(score)


def _ensure_growth_snapshot(user: dict, power: int, affix_score: int, day_key: int) -> None:
    execute(
        """INSERT INTO user_growth_snapshots (user_id, day_key, exp, power, affix_score, updated_at)
           VALUES (?, ?, ?, ?, ?, CAST(strftime('%s','now') AS INTEGER))
           ON CONFLICT(user_id, day_key) DO UPDATE SET
             exp = excluded.exp,
             power = excluded.power,
             affix_score = excluded.affix_score,
             updated_at = excluded.updated_at""",
        (user.get("user_id"), int(day_key), int(user.get("exp", 0) or 0), int(power or 0), int(affix_score or 0)),
    )


def _enrich_leaderboard_entry(entry: dict, user_row: dict) -> None:
    rank = int(entry.get("rank", user_row.get("rank", 1)) or 1)
    realm_name = (get_realm_by_id(rank) or {}).get("name", "未知")

    current_map = str(user_row.get("current_map") or "canglan_city")
    current_map_name = current_map
    try:
        map_info = get_map(current_map)
        if map_info:
            current_map_name = str(map_info.get("name") or current_map)
    except Exception:
        pass

    sect_name = None
    user_id = str(entry.get("user_id") or "")
    if user_id:
        try:
            buffs = get_user_sect_buffs(user_id)
            if buffs.get("in_sect"):
                sect_name = buffs.get("sect_name")
        except Exception:
            pass

    entry["realm_name"] = realm_name
    entry["current_map"] = current_map
    entry["current_map_name"] = current_map_name
    entry["sect_name"] = sect_name


@misc_bp.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    mode = request.args.get("mode", "power")
    user_id = request.args.get("user_id")
    stage_only = (request.args.get("stage_only") or "").lower() in ("1", "true", "yes")
    stage_goal = None
    if user_id:
        user = get_user_by_id(str(user_id))
        if user:
            stage_goal = get_stage_goal(int(user.get("rank", 1) or 1))
            if mode in ("stage", "auto", "recommended"):
                mode = stage_goal.get("recommended_mode", "power")
                stage_only = True
    elif mode in ("stage", "auto", "recommended"):
        mode = "power"

    users = fetch_all("SELECT * FROM users")
    user_index = {str(u.get("user_id")): u for u in users}
    entries = [leaderboard_entry(u) for u in users]
    if mode in ("affix_score", "growth_7d"):
        day_key = local_day_key()
        for user, entry in zip(users, entries):
            affix_score = _calc_affix_score(user)
            entry["affix_score"] = affix_score
            if mode == "growth_7d":
                _ensure_growth_snapshot(user, entry.get("power", 0), affix_score, day_key)
                past = fetch_one(
                    "SELECT exp FROM user_growth_snapshots WHERE user_id = ? AND day_key = ?",
                    (user.get("user_id"), int(day_key) - 7),
                )
                past_exp = int((past or {}).get("exp", 0) or 0)
                entry["growth_7d"] = int(entry.get("exp", 0) or 0) - past_exp
    if stage_goal and stage_only:
        min_rank = int(stage_goal.get("min_rank", 1) or 1)
        max_rank = int(stage_goal.get("max_rank", 999) or 999)
        entries = [e for e in entries if min_rank <= int(e.get("rank", 1) or 1) <= max_rank]
    if mode == "exp_growth":
        mode = "exp"
    if mode == "exp":
        entries = sorted(entries, key=lambda x: (x["rank"], x["exp"]), reverse=True)
    elif mode == "wealth":
        entries = sorted(entries, key=lambda x: (x.get("wealth", 0), x.get("rank", 1)), reverse=True)
    elif mode == "affix_score":
        entries = sorted(entries, key=lambda x: (x.get("affix_score", 0), x.get("rank", 1)), reverse=True)
    elif mode == "growth_7d":
        entries = sorted(entries, key=lambda x: (x.get("growth_7d", 0), x.get("rank", 1)), reverse=True)
    elif mode == "realm_loot":
        entries = sorted(entries, key=lambda x: (x["realm_loot"], x["rank"]), reverse=True)
    elif mode == "alchemy_output":
        entries = sorted(entries, key=lambda x: (x["alchemy_output"], x["rank"]), reverse=True)
    elif mode == "hunt":
        entries = sorted(entries, key=lambda x: x["dy_times"], reverse=True)
    else:
        entries = sorted(entries, key=lambda x: x["power"], reverse=True)

    top_entries = entries[:20]
    for entry in top_entries:
        user_row = user_index.get(str(entry.get("user_id") or ""), {})
        _enrich_leaderboard_entry(entry, user_row)

    return success(mode=mode, entries=top_entries, stage_goal=stage_goal)
