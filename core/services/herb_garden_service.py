"""
core/services/herb_garden_service.py
药园（宗门灵田）业务逻辑。

移植自 my_farm/db.py + bot.py，适配 psycopg2 同步事务。
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.database.connection import (
    db_transaction,
    fetch_all,
    fetch_one,
    execute,
    get_db,
    get_user_by_id,
)
from core.game.herb_garden import (
    SPIRIT_HERBS,
    GARDEN_LEVELS,
    PEST_EVENT_TYPES,
    MAX_GARDEN_LEVEL,
    WATER_SPEEDUP_PCT,
    WATER_COOLDOWN_SECONDS,
    PEST_DEATH_MINUTES,
    get_herb,
    get_garden_level_info,
    get_remaining_minutes,
    format_time,
    format_time_short,
)

logger = logging.getLogger("HerbGardenService")

# ── Schema 初始化（幂等，首次调用时执行一次）──

_GARDEN_SCHEMA_READY = False
_GARDEN_SCHEMA_LOCK = threading.Lock()


def _ensure_garden_schema() -> None:
    """创建 herb_garden_plots 表，并为 users 表追加 garden_level / garden_exp 列。"""
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS herb_garden_plots (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            plot_index INT NOT NULL,
            herb_name TEXT DEFAULT '',
            planted_at TIMESTAMPTZ,
            growth_minutes DOUBLE PRECISION DEFAULT 0,
            water_count INTEGER DEFAULT 0,
            watered BOOLEAN DEFAULT FALSE,
            has_pest BOOLEAN DEFAULT FALSE,
            pest_type TEXT DEFAULT '',
            pest_at TIMESTAMPTZ,
            is_dead BOOLEAN DEFAULT FALSE,
            UNIQUE(user_id, plot_index)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_herb_garden_plots_user ON herb_garden_plots(user_id)"
    )

    # users 表追加药园列（幂等）
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'users' AND table_schema = 'public'"
    )
    existing = {row[0] for row in cur.fetchall()}
    for col_name, sql in [
        ("garden_level", "ALTER TABLE users ADD COLUMN garden_level INTEGER DEFAULT 1"),
        ("garden_exp", "ALTER TABLE users ADD COLUMN garden_exp INTEGER DEFAULT 0"),
        ("garden_last_water", "ALTER TABLE users ADD COLUMN garden_last_water TIMESTAMPTZ"),
    ]:
        if col_name not in existing:
            cur.execute(sql)

    conn.commit()


def _ensure_garden_schema_once() -> None:
    global _GARDEN_SCHEMA_READY
    if _GARDEN_SCHEMA_READY:
        return
    with _GARDEN_SCHEMA_LOCK:
        if _GARDEN_SCHEMA_READY:
            return
        _ensure_garden_schema()
        _GARDEN_SCHEMA_READY = True


# ── 内部辅助 ──

def _init_plots_for_user(cur, user_id: str, plot_count: int) -> None:
    """为用户初始化田地行（幂等）。"""
    for i in range(plot_count):
        cur.execute(
            "INSERT INTO herb_garden_plots (user_id, plot_index) "
            "VALUES (%s, %s) ON CONFLICT (user_id, plot_index) DO NOTHING",
            (user_id, i),
        )


def _get_user_garden_fields(user: Dict[str, Any]) -> Dict[str, Any]:
    """从 user dict 提取药园相关字段。"""
    return {
        "garden_level": int(user.get("garden_level") or 1),
        "garden_exp": int(user.get("garden_exp") or 0),
        "copper": int(user.get("copper") or 0),
    }


# ── 公开 API ──

def get_garden(user_id: str) -> Dict[str, Any]:
    """获取药园概览：等级、经验、田地数、灵石余额。"""
    _ensure_garden_schema_once()
    user = get_user_by_id(user_id)
    if not user:
        return {"error": "USER_NOT_FOUND"}

    garden = _get_user_garden_fields(user)
    level_info = get_garden_level_info(garden["garden_level"])
    plots = get_plots(user_id)

    return {
        "user_id": user_id,
        "garden_level": garden["garden_level"],
        "garden_exp": garden["garden_exp"],
        "exp_next": level_info["exp_next"],
        "max_plots": level_info["plots"],
        "copper": garden["copper"],
        "plots": plots,
    }


def get_plots(user_id: str) -> List[Dict[str, Any]]:
    """获取所有田地状态。"""
    _ensure_garden_schema_once()
    user = get_user_by_id(user_id)
    if not user:
        return []

    level = int(user.get("garden_level") or 1)
    level_info = get_garden_level_info(level)
    expected_plots = level_info["plots"]

    # 确保田地行已初始化
    rows = fetch_all(
        "SELECT * FROM herb_garden_plots WHERE user_id = %s ORDER BY plot_index ASC",
        (user_id,),
    )
    if len(rows) < expected_plots:
        with db_transaction() as cur:
            _init_plots_for_user(cur, user_id, expected_plots)
        rows = fetch_all(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s ORDER BY plot_index ASC",
            (user_id,),
        )

    result = []
    for r in rows:
        if r["plot_index"] >= expected_plots:
            continue
        herb_name = (r.get("herb_name") or "").strip()
        herb_cfg = get_herb(herb_name) if herb_name else None
        remaining = -1
        if herb_name and herb_cfg and r.get("planted_at") and not r.get("is_dead"):
            gm = r.get("growth_minutes") or herb_cfg["growth_minutes"]
            remaining = get_remaining_minutes(r["planted_at"], gm)

        plot_data = {
            "plot_index": r["plot_index"],
            "herb_name": herb_name,
            "planted_at": r.get("planted_at").isoformat() if r.get("planted_at") else None,
            "growth_minutes": r.get("growth_minutes") or (herb_cfg["growth_minutes"] if herb_cfg else 0),
            "water_count": r.get("water_count", 0),
            "watered": bool(r.get("watered")),
            "has_pest": bool(r.get("has_pest")),
            "pest_type": r.get("pest_type", ""),
            "is_dead": bool(r.get("is_dead")),
            "remaining_minutes": round(remaining, 1) if remaining >= 0 else None,
            "is_mature": remaining == 0 if remaining >= 0 else False,
            "emoji": herb_cfg["emoji"] if herb_cfg else None,
        }
        result.append(plot_data)

    return result


def plant_herb(user_id: str, plot_index: int, herb_name: str) -> Dict[str, Any]:
    """在指定田地种植灵植。扣除灵石。"""
    _ensure_garden_schema_once()
    herb = get_herb(herb_name)
    if not herb:
        return {"success": False, "code": "UNKNOWN_HERB", "message": f"未知灵植: {herb_name}"}

    with db_transaction() as cur:
        # 获取用户（事务内锁定）
        cur.execute("SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        user = cur.fetchone()
        if not user:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}

        copper = int(user[_col_index(cur, "copper")] or 0) if not isinstance(user, dict) else int(user.get("copper") or 0)
        # handle DictCursor
        if hasattr(user, 'keys'):
            copper = int(user.get("copper") or 0)
        else:
            copper = int(user[_find_col_idx(cur, "copper")] or 0)

        if copper < herb["seed_cost"]:
            return {
                "success": False,
                "code": "INSUFFICIENT_COPPER",
                "message": f"灵石不足！种植{herb_name}需要 {herb['seed_cost']} 下品灵石，当前余额 {copper}",
            }

        garden_level = _get_dict_val(user, cur, "garden_level", 1)
        level_info = get_garden_level_info(garden_level)

        if plot_index < 0 or plot_index >= level_info["plots"]:
            return {"success": False, "code": "INVALID_PLOT", "message": f"无效的田地编号（0-{level_info['plots'] - 1}）"}

        # 确保田地行存在
        _init_plots_for_user(cur, user_id, level_info["plots"])

        # 检查田地是否为空
        cur.execute(
            "SELECT herb_name, is_dead FROM herb_garden_plots WHERE user_id = %s AND plot_index = %s",
            (user_id, plot_index),
        )
        plot_row = cur.fetchone()
        if plot_row:
            phn = plot_row[0] if not hasattr(plot_row, 'keys') else plot_row.get("herb_name", "")
            p_dead = plot_row[1] if not hasattr(plot_row, 'keys') else plot_row.get("is_dead", False)
            if phn and phn.strip() and not p_dead:
                return {"success": False, "code": "PLOT_NOT_EMPTY", "message": "该田地已有灵植，请先收获或清除"}

        # 扣灵石
        cur.execute(
            "UPDATE users SET copper = copper - %s WHERE user_id = %s",
            (herb["seed_cost"], user_id),
        )

        # 种植
        cur.execute(
            """
            UPDATE herb_garden_plots
            SET herb_name = %s, planted_at = NOW(), growth_minutes = %s,
                water_count = 0, watered = FALSE,
                has_pest = FALSE, pest_type = '', pest_at = NULL,
                is_dead = FALSE
            WHERE user_id = %s AND plot_index = %s
            """,
            (herb_name, herb["growth_minutes"], user_id, plot_index),
        )

    growth_text = format_time(herb["growth_minutes"])
    return {
        "success": True,
        "herb_name": herb_name,
        "emoji": herb["emoji"],
        "plot_index": plot_index,
        "seed_cost": herb["seed_cost"],
        "growth_time": growth_text,
        "message": f"成功种植 {herb['emoji']} {herb_name}！花费 {herb['seed_cost']} 灵石，预计 {growth_text} 后成熟",
    }


def harvest_herb(user_id: str, plot_index: int) -> Dict[str, Any]:
    """收获指定田地的成熟灵植。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}

        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND plot_index = %s FOR UPDATE",
            (user_id, plot_index),
        )
        plot = cur.fetchone()
        if not plot:
            return {"success": False, "code": "INVALID_PLOT", "message": "无效的田地编号"}

        herb_name, planted_at, is_dead, has_pest, gm = _extract_plot_fields(plot, cur)

        if not herb_name:
            return {"success": False, "code": "EMPTY_PLOT", "message": "该田地没有灵植"}
        if is_dead:
            return {"success": False, "code": "DEAD_HERB", "message": "该灵植已枯死，请先清除（remove-dead）"}
        if has_pest:
            return {"success": False, "code": "HAS_PEST", "message": "该灵植有虫害，请先除虫再收获"}

        herb = get_herb(herb_name)
        if not herb:
            return {"success": False, "code": "UNKNOWN_HERB", "message": "灵植配置异常"}

        effective_gm = gm or herb["growth_minutes"]
        remaining = get_remaining_minutes(planted_at, effective_gm)
        if remaining > 0:
            return {
                "success": False,
                "code": "NOT_MATURE",
                "message": f"灵植尚未成熟，还需 {format_time(remaining)}",
            }

        # 收获：加灵石 + 加经验
        reward = herb["harvest_reward"]
        exp_gain = max(1, math.ceil(herb["seed_cost"] / 2))

        cur.execute("UPDATE users SET copper = copper + %s WHERE user_id = %s", (reward, user_id))
        cur.execute("UPDATE users SET garden_exp = COALESCE(garden_exp, 0) + %s WHERE user_id = %s", (exp_gain, user_id))

        # 清空田地
        cur.execute(
            """
            UPDATE herb_garden_plots
            SET herb_name = '', planted_at = NULL, growth_minutes = 0,
                water_count = 0, watered = FALSE,
                has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE
            WHERE user_id = %s AND plot_index = %s
            """,
            (user_id, plot_index),
        )

        # 检查升级
        level_up = _check_level_up_tx(cur, user_id)

    result = {
        "success": True,
        "herb_name": herb_name,
        "emoji": herb["emoji"],
        "plot_index": plot_index,
        "reward": reward,
        "exp_gain": exp_gain,
        "message": f"收获 {herb['emoji']} {herb_name}，获得 {reward} 灵石，经验 +{exp_gain}",
    }
    if level_up:
        result["level_up"] = level_up
        result["message"] += f"\n药园升级到 Lv.{level_up}！"
    return result


def harvest_all(user_id: str) -> Dict[str, Any]:
    """一键收获所有成熟灵植。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}

        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s ORDER BY plot_index ASC FOR UPDATE",
            (user_id,),
        )
        plots = cur.fetchall()

        total_reward = 0
        total_exp = 0
        harvested = []
        pest_blocked = []

        for plot in plots:
            herb_name, planted_at, is_dead, has_pest, gm = _extract_plot_fields(plot, cur)
            if not herb_name or is_dead:
                continue
            herb = get_herb(herb_name)
            if not herb:
                continue
            effective_gm = gm or herb["growth_minutes"]
            remaining = get_remaining_minutes(planted_at, effective_gm)

            if remaining > 0:
                continue

            if has_pest:
                pest_blocked.append({"herb_name": herb_name, "emoji": herb["emoji"]})
                continue

            reward = herb["harvest_reward"]
            exp_gain = max(1, math.ceil(herb["seed_cost"] / 2))
            total_reward += reward
            total_exp += exp_gain
            pi = _get_dict_val(plot, cur, "plot_index", 0)
            harvested.append({
                "herb_name": herb_name,
                "emoji": herb["emoji"],
                "reward": reward,
                "exp_gain": exp_gain,
                "plot_index": pi,
            })

            # 清空田地
            cur.execute(
                """
                UPDATE herb_garden_plots
                SET herb_name = '', planted_at = NULL, growth_minutes = 0,
                    water_count = 0, watered = FALSE,
                    has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE
                WHERE user_id = %s AND plot_index = %s
                """,
                (user_id, pi),
            )

        if not harvested:
            msg = "没有成熟的灵植可以收获"
            if pest_blocked:
                msg += f"（{len(pest_blocked)} 株灵植被虫害阻止，请先除虫）"
            return {"success": False, "code": "NOTHING_TO_HARVEST", "message": msg}

        cur.execute("UPDATE users SET copper = copper + %s WHERE user_id = %s", (total_reward, user_id))
        cur.execute("UPDATE users SET garden_exp = COALESCE(garden_exp, 0) + %s WHERE user_id = %s", (total_exp, user_id))

        level_up = _check_level_up_tx(cur, user_id)

    result = {
        "success": True,
        "harvested": harvested,
        "total_reward": total_reward,
        "total_exp": total_exp,
        "count": len(harvested),
        "message": f"收获 {len(harvested)} 株灵植，共获得 {total_reward} 灵石，经验 +{total_exp}",
    }
    if pest_blocked:
        result["pest_blocked"] = pest_blocked
    if level_up:
        result["level_up"] = level_up
        result["message"] += f"\n药园升级到 Lv.{level_up}！"
    return result


def water_plot(user_id: str, plot_index: int) -> Dict[str, Any]:
    """浇水，减少指定田地 20% 的生长时间。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}

        # 检查浇水冷却
        last_water = _get_dict_val(user_row, cur, "garden_last_water", None)
        if last_water:
            if isinstance(last_water, str):
                last_water = datetime.fromisoformat(last_water)
            if last_water.tzinfo is None:
                last_water = last_water.replace(tzinfo=timezone.utc)
            diff = (datetime.now(timezone.utc) - last_water).total_seconds()
            if diff < WATER_COOLDOWN_SECONDS:
                remain_sec = WATER_COOLDOWN_SECONDS - diff
                remain_min = math.ceil(remain_sec / 60)
                return {
                    "success": False,
                    "code": "WATER_COOLDOWN",
                    "message": f"浇水冷却中，还需等待 {remain_min} 分钟",
                }

        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND plot_index = %s FOR UPDATE",
            (user_id, plot_index),
        )
        plot = cur.fetchone()
        if not plot:
            return {"success": False, "code": "INVALID_PLOT", "message": "无效的田地编号"}

        herb_name, planted_at, is_dead, has_pest, gm = _extract_plot_fields(plot, cur)
        if not herb_name:
            return {"success": False, "code": "EMPTY_PLOT", "message": "该田地没有灵植"}
        if is_dead:
            return {"success": False, "code": "DEAD_HERB", "message": "灵植已枯死"}

        herb = get_herb(herb_name)
        if not herb:
            return {"success": False, "code": "UNKNOWN_HERB", "message": "灵植配置异常"}

        effective_gm = gm or herb["growth_minutes"]
        remaining = get_remaining_minutes(planted_at, effective_gm)
        if remaining <= 0:
            return {"success": False, "code": "ALREADY_MATURE", "message": "灵植已成熟，无需浇水"}

        # 浇水：将 planted_at 往前推，等效加速
        speedup_minutes = effective_gm * WATER_SPEEDUP_PCT
        cur.execute(
            """
            UPDATE herb_garden_plots
            SET planted_at = planted_at - make_interval(secs => %s),
                water_count = water_count + 1,
                watered = TRUE
            WHERE user_id = %s AND plot_index = %s
            """,
            (speedup_minutes * 60, user_id, plot_index),
        )
        cur.execute(
            "UPDATE users SET garden_last_water = NOW() WHERE user_id = %s",
            (user_id,),
        )

    return {
        "success": True,
        "plot_index": plot_index,
        "herb_name": herb_name,
        "speedup_minutes": round(speedup_minutes, 1),
        "message": f"浇水成功！{herb_name} 加速 {round(speedup_minutes, 1)} 分钟（{int(WATER_SPEEDUP_PCT * 100)}%）",
    }


def water_all(user_id: str) -> Dict[str, Any]:
    """一键浇水所有正在生长的灵植。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            return {"success": False, "code": "USER_NOT_FOUND", "message": "用户不存在"}

        # 检查浇水冷却
        last_water = _get_dict_val(user_row, cur, "garden_last_water", None)
        if last_water:
            if isinstance(last_water, str):
                last_water = datetime.fromisoformat(last_water)
            if last_water.tzinfo is None:
                last_water = last_water.replace(tzinfo=timezone.utc)
            diff = (datetime.now(timezone.utc) - last_water).total_seconds()
            if diff < WATER_COOLDOWN_SECONDS:
                remain_min = math.ceil((WATER_COOLDOWN_SECONDS - diff) / 60)
                return {"success": False, "code": "WATER_COOLDOWN", "message": f"浇水冷却中，还需等待 {remain_min} 分钟"}

        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s ORDER BY plot_index ASC FOR UPDATE",
            (user_id,),
        )
        plots = cur.fetchall()
        watered_count = 0

        for plot in plots:
            herb_name, planted_at, is_dead, has_pest, gm = _extract_plot_fields(plot, cur)
            if not herb_name or is_dead:
                continue
            herb = get_herb(herb_name)
            if not herb:
                continue
            effective_gm = gm or herb["growth_minutes"]
            remaining = get_remaining_minutes(planted_at, effective_gm)
            if remaining <= 0:
                continue

            speedup_minutes = effective_gm * WATER_SPEEDUP_PCT
            pi = _get_dict_val(plot, cur, "plot_index", 0)
            cur.execute(
                """
                UPDATE herb_garden_plots
                SET planted_at = planted_at - make_interval(secs => %s),
                    water_count = water_count + 1,
                    watered = TRUE
                WHERE user_id = %s AND plot_index = %s
                """,
                (speedup_minutes * 60, user_id, pi),
            )
            watered_count += 1

        if watered_count == 0:
            return {"success": False, "code": "NOTHING_TO_WATER", "message": "没有需要浇水的灵植"}

        cur.execute("UPDATE users SET garden_last_water = NOW() WHERE user_id = %s", (user_id,))

    return {
        "success": True,
        "watered_count": watered_count,
        "message": f"浇水成功！为 {watered_count} 块田地浇了水，生长加速 {int(WATER_SPEEDUP_PCT * 100)}%",
    }


def remove_pest(user_id: str, plot_index: int) -> Dict[str, Any]:
    """除虫（清除指定田地的虫害）。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND plot_index = %s FOR UPDATE",
            (user_id, plot_index),
        )
        plot = cur.fetchone()
        if not plot:
            return {"success": False, "code": "INVALID_PLOT", "message": "无效的田地编号"}

        has_pest = _get_dict_val(plot, cur, "has_pest", False)
        if not has_pest:
            return {"success": False, "code": "NO_PEST", "message": "该田地没有虫害"}

        cur.execute(
            """
            UPDATE herb_garden_plots
            SET has_pest = FALSE, pest_type = '', pest_at = NULL
            WHERE user_id = %s AND plot_index = %s
            """,
            (user_id, plot_index),
        )

    return {
        "success": True,
        "plot_index": plot_index,
        "message": "除虫成功！灵植恢复正常生长",
    }


def remove_pest_all(user_id: str) -> Dict[str, Any]:
    """一键除虫：清除所有田地的虫害。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND has_pest = TRUE FOR UPDATE",
            (user_id,),
        )
        pest_plots = cur.fetchall()
        if not pest_plots:
            return {"success": False, "code": "NO_PEST", "message": "药园很干净，没有虫害"}

        cur.execute(
            """
            UPDATE herb_garden_plots
            SET has_pest = FALSE, pest_type = '', pest_at = NULL
            WHERE user_id = %s AND has_pest = TRUE
            """,
            (user_id,),
        )

    return {
        "success": True,
        "cleaned_count": len(pest_plots),
        "message": f"除虫成功！清理了 {len(pest_plots)} 块田地的虫害",
    }


def remove_dead(user_id: str, plot_index: int) -> Dict[str, Any]:
    """清除指定枯死灵植。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND plot_index = %s FOR UPDATE",
            (user_id, plot_index),
        )
        plot = cur.fetchone()
        if not plot:
            return {"success": False, "code": "INVALID_PLOT", "message": "无效的田地编号"}

        is_dead = _get_dict_val(plot, cur, "is_dead", False)
        if not is_dead:
            return {"success": False, "code": "NOT_DEAD", "message": "该灵植没有枯死"}

        cur.execute(
            """
            UPDATE herb_garden_plots
            SET herb_name = '', planted_at = NULL, growth_minutes = 0,
                water_count = 0, watered = FALSE,
                has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE
            WHERE user_id = %s AND plot_index = %s
            """,
            (user_id, plot_index),
        )

    return {"success": True, "plot_index": plot_index, "message": "枯死灵植已清除，田地恢复可用"}


def remove_dead_all(user_id: str) -> Dict[str, Any]:
    """一键清除所有枯死灵植。"""
    _ensure_garden_schema_once()

    with db_transaction() as cur:
        cur.execute(
            "SELECT * FROM herb_garden_plots WHERE user_id = %s AND is_dead = TRUE FOR UPDATE",
            (user_id,),
        )
        dead_plots = cur.fetchall()
        if not dead_plots:
            return {"success": False, "code": "NO_DEAD", "message": "没有枯死的灵植"}

        cur.execute(
            """
            UPDATE herb_garden_plots
            SET herb_name = '', planted_at = NULL, growth_minutes = 0,
                water_count = 0, watered = FALSE,
                has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE
            WHERE user_id = %s AND is_dead = TRUE
            """,
            (user_id,),
        )

    return {
        "success": True,
        "cleared_count": len(dead_plots),
        "message": f"清除了 {len(dead_plots)} 株枯死灵植，田地已恢复",
    }


def get_garden_status(user_id: str) -> Dict[str, Any]:
    """获取药园整体状态（含文本概览）。"""
    garden = get_garden(user_id)
    if "error" in garden:
        return garden

    plots = garden["plots"]
    mature = sum(1 for p in plots if p.get("is_mature"))
    growing = sum(1 for p in plots if p.get("herb_name") and not p.get("is_dead") and not p.get("is_mature") and p.get("remaining_minutes") is not None and p["remaining_minutes"] > 0)
    dead = sum(1 for p in plots if p.get("is_dead"))
    pest = sum(1 for p in plots if p.get("has_pest"))
    empty = sum(1 for p in plots if not p.get("herb_name"))

    garden["summary"] = {
        "mature": mature,
        "growing": growing,
        "dead": dead,
        "pest": pest,
        "empty": empty,
    }
    return garden


def get_herb_list() -> List[Dict[str, Any]]:
    """返回所有可种植灵植的列表（用于 API）。"""
    result = []
    for name, cfg in SPIRIT_HERBS.items():
        result.append({
            "name": name,
            "emoji": cfg["emoji"],
            "seed_cost": cfg["seed_cost"],
            "harvest_reward": cfg["harvest_reward"],
            "growth_minutes": cfg["growth_minutes"],
            "type": cfg["type"],
            "growth_time_text": format_time(cfg["growth_minutes"]),
        })
    return result


# ── 事务内升级检查 ──

def _check_level_up_tx(cur, user_id: str) -> Optional[int]:
    """在事务内检查药园是否升级，如果升级则更新。返回新等级或 None。"""
    cur.execute("SELECT garden_level, garden_exp FROM users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        return None

    if hasattr(row, 'keys'):
        level = int(row.get("garden_level") or 1)
        exp = int(row.get("garden_exp") or 0)
    else:
        level = int(row[0] or 1)
        exp = int(row[1] or 0)

    if level >= MAX_GARDEN_LEVEL:
        return None

    level_info = get_garden_level_info(level)
    if exp < level_info["exp_next"]:
        return None

    new_level = level + 1
    new_info = get_garden_level_info(new_level)
    cur.execute(
        "UPDATE users SET garden_level = %s WHERE user_id = %s",
        (new_level, user_id),
    )
    # 确保新田地行存在
    _init_plots_for_user(cur, user_id, new_info["plots"])
    return new_level


# ── Dict/tuple 兼容辅助 ──

def _get_dict_val(row, cur, key: str, default=None):
    """从 DictRow 或 tuple 中安全取值。"""
    if row is None:
        return default
    if hasattr(row, 'keys'):
        return row.get(key, default)
    # tuple fallback: 通过 cursor.description 查找
    if hasattr(cur, 'description') and cur.description:
        desc = cur.description
        if hasattr(desc, '__iter__'):
            for i, col in enumerate(desc):
                col_name = col[0] if isinstance(col, (tuple, list)) else getattr(col, 'name', None)
                if col_name == key:
                    return row[i] if i < len(row) else default
    return default


def _extract_plot_fields(plot, cur) -> tuple:
    """从 plot 行中提取常用字段。"""
    herb_name = _get_dict_val(plot, cur, "herb_name", "") or ""
    herb_name = herb_name.strip() if herb_name else ""
    planted_at = _get_dict_val(plot, cur, "planted_at", None)
    is_dead = bool(_get_dict_val(plot, cur, "is_dead", False))
    has_pest = bool(_get_dict_val(plot, cur, "has_pest", False))
    gm = _get_dict_val(plot, cur, "growth_minutes", 0)
    return herb_name, planted_at, is_dead, has_pest, gm
