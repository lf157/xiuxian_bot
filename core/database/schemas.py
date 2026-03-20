"""
core/database/schemas.py
数据模型定义，与实际数据库表结构同步。
本文件作为文档和验证参考，create_tables() 中的 DDL 为权威定义。

最后同步日期: 2026-03-16
对应 schema_version: 6
"""

from typing import Dict, Any, Union, List, Optional


# ── users 表字段定义 ──
USER_SCHEMA = {
    # 身份信息
    "user_id": str,               # 主键，纯数字字符串
    "in_game_username": str,      # 游戏内显示名
    "lang": str,                  # 语言偏好 (默认 "CHS")
    "element": Optional[str],     # 五行元素 (金/木/水/火/土)
    "created_at": int,            # 注册时间戳

    # 平台ID
    "telegram_id": Optional[str],

    # 游戏属性
    "state": int,                 # 0=空闲, 1=修炼中
    "exp": int,                   # 修为
    "rank": int,                  # 境界等级 (1-32)
    "dy_times": int,              # 成功狩猎次数
    "copper": int,                # 下品灵石
    "gold": int,                  # 中品灵石
    "spirit_high": int,
    "spirit_exquisite": int,
    "spirit_supreme": int,
    "immortal_flawed": int,
    "immortal_low": int,
    "immortal_mid": int,
    "immortal_high": int,
    "immortal_supreme": int,
    "asc_reduction": int,         # 突破减免
    "sign": int,                  # 签到相关

    # 战斗属性
    "hp": int,
    "mp": int,
    "max_hp": int,
    "max_mp": int,
    "attack": int,
    "defense": int,
    "crit_rate": float,

    # 状态
    "weak_until": int,            # 虚弱结束时间戳
    "breakthrough_pity": int,     # 突破保底心魔值

    # 签到
    "last_sign_timestamp": int,
    "consecutive_sign_days": int,
    "max_signin_days": int,

    # 秘境
    "secret_realm_attempts": int,
    "secret_realm_last_reset": int,

    # 装备栏
    "equipped_weapon": Optional[str],
    "equipped_armor": Optional[str],
    "equipped_accessory1": Optional[str],
    "equipped_accessory2": Optional[str],

    # 冷却时间
    "last_hunt_time": int,
    "hunts_today": int,
    "hunts_today_reset": int,
    "last_secret_time": int,
    "last_quest_claim_time": int,
    "last_enhance_time": int,
    "cultivation_boost_until": int,
    "cultivation_boost_pct": float,
    "realm_drop_boost_until": int,
    "breakthrough_protect_until": int,
    "attack_buff_until": int,
    "attack_buff_value": int,
    "defense_buff_until": int,
    "defense_buff_value": int,
    "breakthrough_boost_until": int,
    "breakthrough_boost_pct": float,

    # PVP
    "pvp_rating": int,
    "pvp_wins": int,
    "pvp_losses": int,
    "pvp_draws": int,
    "pvp_daily_count": int,
    "pvp_daily_reset": int,
    "pvp_season_id": Optional[str],

    # 体力/状态恢复
    "stamina": int,
    "stamina_updated_at": int,
    "vitals_updated_at": int,

    # 聊天与抽卡日限
    "chat_energy_today": float,
    "chat_energy_reset": int,
    "gacha_free_today": int,
    "gacha_paid_today": int,
    "gacha_daily_reset": int,
    "daily_cultivate_stone_day": int,
    "daily_cultivate_stone_claimed": int,

    # 玩法评分
    "secret_loot_score": int,
    "alchemy_output_score": int,
}

USER_DEFAULT_VALUES = {
    "lang": "CHS",
    "state": 0,
    "exp": 0,
    "rank": 1,
    "dy_times": 0,
    "copper": 0,
    "gold": 0,
    "spirit_high": 0,
    "spirit_exquisite": 0,
    "spirit_supreme": 0,
    "immortal_flawed": 0,
    "immortal_low": 0,
    "immortal_mid": 0,
    "immortal_high": 0,
    "immortal_supreme": 0,
    "asc_reduction": 0,
    "sign": 0,
    "element": None,
    "hp": 100,
    "mp": 50,
    "max_hp": 100,
    "max_mp": 50,
    "attack": 10,
    "defense": 5,
    "crit_rate": 0.05,
    "weak_until": 0,
    "breakthrough_pity": 0,
    "created_at": 0,
    "last_sign_timestamp": 0,
    "consecutive_sign_days": 0,
    "max_signin_days": 0,
    "secret_realm_attempts": 0,
    "secret_realm_last_reset": 0,
    "last_hunt_time": 0,
    "hunts_today": 0,
    "hunts_today_reset": 0,
    "last_secret_time": 0,
    "last_quest_claim_time": 0,
    "last_enhance_time": 0,
    "cultivation_boost_until": 0,
    "cultivation_boost_pct": 0,
    "realm_drop_boost_until": 0,
    "breakthrough_protect_until": 0,
    "attack_buff_until": 0,
    "attack_buff_value": 0,
    "defense_buff_until": 0,
    "defense_buff_value": 0,
    "breakthrough_boost_until": 0,
    "breakthrough_boost_pct": 0,
    "pvp_rating": 1000,
    "pvp_wins": 0,
    "pvp_losses": 0,
    "pvp_draws": 0,
    "pvp_daily_count": 0,
    "pvp_daily_reset": 0,
    "pvp_season_id": None,
    "stamina": 24,
    "stamina_updated_at": 0,
    "vitals_updated_at": 0,
    "chat_energy_today": 0,
    "chat_energy_reset": 0,
    "gacha_free_today": 0,
    "gacha_paid_today": 0,
    "gacha_daily_reset": 0,
    "daily_cultivate_stone_day": 0,
    "daily_cultivate_stone_claimed": 0,
    "secret_loot_score": 0,
    "alchemy_output_score": 0,
}

# ── items 表 ──
ITEM_SCHEMA = {
    "id": int,                    # 自增主键
    "user_id": str,               # 所属用户
    "item_id": str,               # 物品定义ID
    "item_name": str,             # 显示名
    "item_type": str,             # weapon/armor/accessory/pill/material
    "quality": str,               # common/uncommon/rare/epic/legendary/spirit/holy
    "quantity": int,              # 数量
    "level": int,                 # 物品等级
    "attack_bonus": int,
    "defense_bonus": int,
    "hp_bonus": int,
    "mp_bonus": int,
    "first_round_reduction_pct": float,
    "crit_heal_pct": float,
    "element_damage_pct": float,
    "low_hp_shield_pct": float,
    "enhance_level": int,         # 强化等级
}

# ── timings 表 ──
TIMING_SCHEMA = {
    "id": int,
    "user_id": str,
    "start_time": int,
    "type": str,                  # "cultivation"
    "base_gain": int,
}

# ── battle_logs 表 ──
BATTLE_LOG_SCHEMA = {
    "id": int,
    "user_id": str,
    "monster_id": str,
    "victory": int,
    "rounds": int,
    "exp_gained": int,
    "copper_gained": int,
    "gold_gained": int,
    "timestamp": int,
}

# ── user_skills 表 ──
USER_SKILL_SCHEMA = {
    "id": int,
    "user_id": str,
    "skill_id": str,
    "equipped": int,
    "learned_at": int,
}

# ── user_quests 表 ──
USER_QUEST_SCHEMA = {
    "id": int,
    "user_id": str,
    "quest_id": str,
    "progress": int,
    "goal": int,
    "claimed": int,
    "assigned_date": str,
}

# ── pvp_records 表 ──
PVP_RECORD_SCHEMA = {
    "id": int,
    "challenger_id": str,
    "defender_id": str,
    "winner_id": Optional[str],
    "rounds": int,
    "challenger_rating_before": int,
    "defender_rating_before": int,
    "challenger_rating_after": int,
    "defender_rating_after": int,
    "rewards_json": Optional[str],
    "timestamp": int,
}

# ── friends 表 ──
FRIENDS_SCHEMA = {
    "id": int,
    "user_id": str,
    "friend_id": str,
    "created_at": int,
}

# ── friend_requests 表 ──
FRIEND_REQUEST_SCHEMA = {
    "id": int,
    "from_user_id": str,
    "to_user_id": str,
    "status": str,
    "created_at": int,
}

# ── sects 表 ──
SECT_SCHEMA = {
    "id": int,
    "sect_id": str,
    "name": str,
    "description": str,
    "leader_id": str,
    "level": int,
    "exp": int,
    "fund_copper": int,
    "fund_gold": int,
    "max_members": int,
    "war_wins": int,
    "war_losses": int,
    "last_war_time": int,
    "created_at": int,
}

# ── sect_members 表 ──
SECT_MEMBER_SCHEMA = {
    "id": int,
    "sect_id": str,
    "user_id": str,
    "role": str,
    "contribution": int,
    "joined_at": int,
}

# ── sect_quests 表 ──
SECT_QUEST_SCHEMA = {
    "id": int,
    "sect_id": str,
    "quest_type": str,
    "target": int,
    "progress": int,
    "reward_copper": int,
    "reward_exp": int,
    "assigned_date": str,
    "completed": int,
    "claimed": int,
}

# ── sect_wars 表 ──
SECT_WAR_SCHEMA = {
    "id": int,
    "attacker_sect_id": str,
    "defender_sect_id": str,
    "winner_sect_id": str,
    "power_a": int,
    "power_b": int,
    "created_at": int,
}

# ── alchemy_logs 表 ──
ALCHEMY_LOG_SCHEMA = {
    "id": int,
    "user_id": str,
    "recipe_id": str,
    "success": int,
    "created_at": int,
    "result_item_id": str,
    "quantity": int,
}

# ── gacha_pity 表 ──
GACHA_PITY_SCHEMA = {
    "id": int,
    "user_id": str,
    "banner_id": int,
    "pity_count": int,
    "sr_pity_count": int,
    "total_pulls": int,
}

# ── gacha_logs 表 ──
GACHA_LOG_SCHEMA = {
    "id": int,
    "user_id": str,
    "banner_id": int,
    "item_id": str,
    "rarity": str,
    "created_at": int,
}

# ── user_achievements 表 ──
ACHIEVEMENT_SCHEMA = {
    "id": int,
    "user_id": str,
    "achievement_id": str,
    "claimed": int,
    "completed_at": int,
}

# ── world_boss_state 表 ──
WORLD_BOSS_SCHEMA = {
    "id": int,
    "boss_id": str,
    "hp": int,
    "max_hp": int,
    "last_reset": int,
    "last_defeated": int,
}

# ── event_claims 表 ──
EVENT_CLAIM_SCHEMA = {
    "id": int,
    "user_id": str,
    "event_id": str,
    "last_claim": int,
    "claims": int,
}

# ── world_boss_attacks 表 ──
WORLD_BOSS_ATTACK_SCHEMA = {
    "id": int,
    "user_id": str,
    "last_attack_day": int,
    "attacks_today": int,
}

# ── audit_logs 表 ──
AUDIT_LOG_SCHEMA = {
    "id": int,
    "module": str,
    "action": str,
    "user_id": str,
    "target_user_id": str,
    "success": int,
    "detail_json": str,
    "created_at": int,
}

# ── bounty_orders 表 ──
BOUNTY_ORDER_SCHEMA = {
    "id": int,
    "poster_user_id": str,
    "wanted_item_id": str,
    "wanted_item_name": str,
    "wanted_quantity": int,
    "reward_spirit_low": int,
    "description": str,
    "status": str,
    "claimer_user_id": str,
    "created_at": int,
    "claimed_at": int,
    "completed_at": int,
    "cancelled_at": int,
}

COLLECTIONS = {
    "USERS": "users",
    "ITEMS": "items",
    "TIMINGS": "timings",
    "BATTLE_LOGS": "battle_logs",
    "BREAKTHROUGH_LOGS": "breakthrough_logs",
    "USER_SKILLS": "user_skills",
    "USER_QUESTS": "user_quests",
    "CODEX_MONSTERS": "codex_monsters",
    "CODEX_ITEMS": "codex_items",
    "REQUEST_DEDUP": "request_dedup",
    "AUDIT_LOGS": "audit_logs",
    "BOUNTY_ORDERS": "bounty_orders",
}


def create_default_user(user_id: str, username: str = None) -> Dict[str, Any]:
    user_data = USER_DEFAULT_VALUES.copy()
    user_data["user_id"] = user_id
    user_data["in_game_username"] = username if username else user_id
    return user_data
