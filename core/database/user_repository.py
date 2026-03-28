"""
用户数据仓库 - 用户 CRUD 操作

包含用户查询、更新、体力/生命值恢复等操作。
从 connection.py 中拆分而来。
"""

import logging
import time
from typing import Optional, Dict, Any

from core.constants import (
    DEFAULT_STAMINA_MAX,
    DEFAULT_STAMINA_REGEN_SECONDS,
    DEFAULT_VITALS_REGEN_SECONDS,
    DEFAULT_VITALS_REGEN_PCT,
)
from core.database.connection import fetch_one, execute
from core.database.schema import _ensure_user_platform_columns

logger = logging.getLogger("Database")


def fetch_schema_version() -> int:
    try:
        row = fetch_one("SELECT value FROM schema_meta WHERE key='schema_version'")
        if not row:
            return 0
        return int(row.get("value") or 0)
    except Exception:
        return 0


VALID_PLATFORMS = frozenset({"telegram"})


def get_user_by_platform(platform: str, platform_id: str) -> Optional[Dict[str, Any]]:
    """根据平台ID获取用户（白名单校验防止SQL注入）"""
    if platform not in VALID_PLATFORMS:
        return None
    _ensure_user_platform_columns()
    column = f"{platform}_id"
    return fetch_one(f"SELECT * FROM users WHERE {column} = %s", (platform_id,))


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """根据用户ID获取用户"""
    return fetch_one("SELECT * FROM users WHERE user_id = %s", (user_id,))


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """根据游戏名精确获取用户"""
    return fetch_one("SELECT * FROM users WHERE in_game_username = %s", (username,))


# 允许通过 update_user() 修改的列（白名单防止SQL注入）
VALID_USER_COLUMNS = frozenset({
    "in_game_username", "lang", "state", "exp", "rank", "dy_times",
    "current_map", "visited_maps",
    "copper", "gold", "spirit_high", "spirit_exquisite", "spirit_supreme",
    "immortal_flawed", "immortal_low", "immortal_mid", "immortal_high", "immortal_supreme",
    "asc_reduction", "sign", "element",
    "hp", "mp", "max_hp", "max_mp", "attack", "defense", "crit_rate",
    "weak_until", "breakthrough_pity", "last_sign_timestamp",
    "consecutive_sign_days", "max_signin_days", "signin_month_key", "signin_month_days", "signin_month_claim_bits",
    "secret_realm_attempts", "secret_realm_last_reset",
    "secret_realm_resets_today", "secret_realm_reset_day",
    "equipped_weapon", "equipped_armor", "equipped_accessory1", "equipped_accessory2",
    "last_hunt_time", "hunts_today", "hunts_today_reset",
    "last_secret_time", "last_quest_claim_time", "last_enhance_time",
    "cultivation_boost_until", "cultivation_boost_pct", "realm_drop_boost_until", "breakthrough_protect_until",
    "attack_buff_until", "attack_buff_value", "defense_buff_until", "defense_buff_value",
    "breakthrough_boost_until", "breakthrough_boost_pct",
    "pvp_rating", "pvp_wins", "pvp_losses", "pvp_draws", "pvp_daily_count",
    "pvp_daily_reset", "pvp_season_id", "stamina", "stamina_updated_at",
    "vitals_updated_at",
    "chat_energy_today", "chat_energy_reset",
    "gacha_free_today", "gacha_paid_today", "gacha_daily_reset",
    "daily_cultivate_stone_day", "daily_cultivate_stone_claimed",
    "secret_loot_score", "alchemy_output_score",
    "tower_floor", "tower_last_attempt_day", "tower_resets_today",
    "garden_level", "garden_exp", "garden_last_water",
})


def update_user(user_id: str, updates: Dict[str, Any]) -> bool:
    """更新用户数据（列名白名单校验）"""
    if not updates:
        return False

    # 过滤非法列名
    safe_updates = {k: v for k, v in updates.items() if k in VALID_USER_COLUMNS}
    if not safe_updates:
        logger.warning(f"update_user: all keys rejected by whitelist: {list(updates.keys())}")
        return False

    set_clause = ", ".join([f"{k} = %s" for k in safe_updates.keys()])
    values = list(safe_updates.values()) + [user_id]

    try:
        execute(f"UPDATE users SET {set_clause} WHERE user_id = %s", tuple(values))
        return True
    except Exception as e:
        logger.error(f"Update user error: {e}")
        return False


def refresh_user_stamina(
    user_id: str,
    *,
    now: Optional[int] = None,
    max_stamina: int = DEFAULT_STAMINA_MAX,
    regen_seconds: int = DEFAULT_STAMINA_REGEN_SECONDS,
) -> Optional[Dict[str, Any]]:
    user = get_user_by_id(user_id)
    if not user:
        return None
    now = int(time.time()) if now is None else int(now)
    raw_stamina = user.get("stamina")
    try:
        current = float(max_stamina if raw_stamina is None else raw_stamina)
    except (TypeError, ValueError):
        current = float(max_stamina)
    updated_at = int(user.get("stamina_updated_at", 0) or 0)
    current = max(0.0, min(float(max_stamina), current))
    if updated_at <= 0:
        updated_at = now
    if current >= max_stamina:
        if int(user.get("stamina_updated_at", 0) or 0) != updated_at:
            execute("UPDATE users SET stamina = %s, stamina_updated_at = %s WHERE user_id = %s", (max_stamina, updated_at, user_id))
        user["stamina"] = max_stamina
        user["stamina_updated_at"] = updated_at
        return user
    elapsed = max(0, now - updated_at)
    recovered = elapsed // max(1, regen_seconds)
    if recovered <= 0:
        user["stamina"] = current
        user["stamina_updated_at"] = updated_at
        return user
    new_stamina = min(float(max_stamina), current + float(int(recovered)))
    remainder = elapsed % max(1, regen_seconds)
    new_updated_at = now if new_stamina >= max_stamina else now - remainder
    execute("UPDATE users SET stamina = %s, stamina_updated_at = %s WHERE user_id = %s", (new_stamina, new_updated_at, user_id))
    user["stamina"] = new_stamina
    user["stamina_updated_at"] = new_updated_at
    return user


def spend_user_stamina(
    user_id: str,
    amount: int = 1,
    *,
    now: Optional[int] = None,
    max_stamina: int = DEFAULT_STAMINA_MAX,
    regen_seconds: int = DEFAULT_STAMINA_REGEN_SECONDS,
) -> tuple[bool, Optional[Dict[str, Any]]]:
    user = refresh_user_stamina(user_id, now=now, max_stamina=max_stamina, regen_seconds=regen_seconds)
    if not user:
        return False, None
    amount = max(1, int(amount or 1))
    raw_stamina = user.get("stamina")
    try:
        current = float(max_stamina if raw_stamina is None else raw_stamina)
    except (TypeError, ValueError):
        current = float(max_stamina)
    if current < amount:
        return False, user
    now = int(time.time()) if now is None else int(now)
    updated_at = int(user.get("stamina_updated_at", 0) or now)
    if current >= max_stamina:
        updated_at = now
    remaining = current - float(amount)
    execute("UPDATE users SET stamina = %s, stamina_updated_at = %s WHERE user_id = %s", (remaining, updated_at, user_id))
    user["stamina"] = remaining
    user["stamina_updated_at"] = updated_at
    return True, user


def spend_user_stamina_tx(
    cur: object,
    user_id: str,
    amount: int = 1,
    *,
    now: Optional[int] = None,
    max_stamina: int = DEFAULT_STAMINA_MAX,
) -> bool:
    """在事务中扣除精力（需调用方提前 refresh_user_stamina）。"""
    amount = max(1, int(amount or 1))
    now = int(time.time()) if now is None else int(now)
    cur.execute(
        """
        UPDATE users
        SET stamina = stamina - %s,
            stamina_updated_at = CASE WHEN stamina >= %s THEN %s ELSE stamina_updated_at END
        WHERE user_id = %s AND stamina >= %s
        """,
        (amount, int(max_stamina), now, user_id, amount),
    )
    return int(cur.rowcount or 0) > 0


def refresh_user_vitals(
    user_id: str,
    *,
    now: Optional[int] = None,
    regen_seconds: int = DEFAULT_VITALS_REGEN_SECONDS,
    regen_pct: float = DEFAULT_VITALS_REGEN_PCT,
) -> Optional[Dict[str, Any]]:
    user = get_user_by_id(user_id)
    if not user:
        return None
    now = int(time.time()) if now is None else int(now)
    effective_regen_seconds = max(1, int(regen_seconds or DEFAULT_VITALS_REGEN_SECONDS))
    effective_regen_pct = float(regen_pct or DEFAULT_VITALS_REGEN_PCT)
    try:
        from core.config import config as app_config

        if int(regen_seconds or 0) == int(DEFAULT_VITALS_REGEN_SECONDS):
            effective_regen_seconds = max(
                1,
                int(
                    app_config.get_nested(
                        "battle",
                        "mp",
                        "regen_seconds",
                        default=effective_regen_seconds,
                    )
                    or effective_regen_seconds
                ),
            )
        if float(regen_pct or 0.0) == float(DEFAULT_VITALS_REGEN_PCT):
            effective_regen_pct = float(
                app_config.get_nested("battle", "mp", "regen_pct", default=effective_regen_pct)
                or effective_regen_pct
            )
    except Exception:
        pass
    effective_regen_pct = max(0.0, effective_regen_pct)

    hp = max(0, int(user.get("hp", user.get("max_hp", 100)) or 0))
    mp = max(0, int(user.get("mp", user.get("max_mp", 50)) or 0))
    max_hp = max(1, int(user.get("max_hp", 100) or 100))
    max_mp = max(1, int(user.get("max_mp", 50) or 50))
    updated_at = int(user.get("vitals_updated_at", 0) or 0)
    if updated_at <= 0:
        updated_at = now
    if hp >= max_hp and mp >= max_mp:
        clamped = hp > max_hp or mp > max_mp
        if int(user.get("vitals_updated_at", 0) or 0) != updated_at or clamped:
            if clamped:
                execute(
                    "UPDATE users SET hp = %s, mp = %s, vitals_updated_at = %s WHERE user_id = %s",
                    (max_hp, max_mp, updated_at, user_id),
                )
            else:
                execute("UPDATE users SET vitals_updated_at = %s WHERE user_id = %s", (updated_at, user_id))
        user["hp"] = max_hp
        user["mp"] = max_mp
        user["vitals_updated_at"] = updated_at
        return user

    elapsed = max(0, now - updated_at)
    recovered = elapsed // effective_regen_seconds
    if recovered <= 0:
        user["hp"] = hp
        user["mp"] = mp
        user["vitals_updated_at"] = updated_at
        return user

    hp_step = max(1, int(round(max_hp * effective_regen_pct)))
    mp_step = max(1, int(round(max_mp * effective_regen_pct)))
    new_hp = min(max_hp, hp + hp_step * int(recovered))
    new_mp = min(max_mp, mp + mp_step * int(recovered))
    remainder = elapsed % effective_regen_seconds
    new_updated_at = now if (new_hp >= max_hp and new_mp >= max_mp) else now - remainder
    execute(
        "UPDATE users SET hp = %s, mp = %s, vitals_updated_at = %s WHERE user_id = %s",
        (new_hp, new_mp, new_updated_at, user_id),
    )
    user["hp"] = new_hp
    user["mp"] = new_mp
    user["vitals_updated_at"] = new_updated_at
    return user


