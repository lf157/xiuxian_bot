"""
数据库 Schema 定义与表创建

包含所有 DDL（CREATE TABLE）、索引创建、数据去重等操作。
从 connection.py 中拆分而来。
"""

import logging
from typing import Optional

from core.constants import DEFAULT_STAMINA_MAX
from core.database.connection import get_db

logger = logging.getLogger("Database")


def _ensure_user_platform_columns(conn: Optional[object] = None) -> None:
    if conn is None:
        conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = 'public'"
        )
        existing_columns = {row[0] for row in cur.fetchall()}
        changed = False
        for col_name, sql in [
            ("telegram_id", "ALTER TABLE users ADD COLUMN telegram_id TEXT"),
        ]:
            if col_name not in existing_columns:
                cur.execute(sql)
                changed = True
        if changed:
            conn.commit()


def _ensure_request_dedup_schema(conn: object) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'request_dedup' AND table_schema = 'public'"
        )
        table_exists = cur.fetchone()
        if not table_exists:
            cur.execute(
                """
                CREATE TABLE request_dedup (
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER,
                    response_json TEXT,
                    PRIMARY KEY(request_id, user_id, action)
                )
                """
            )
            return

        # 在 PostgreSQL 中不需要检查 PK 结构迁移，建表时已正确定义
        return


# ── Battle session persistence ──

def ensure_battle_session_table(conn: Optional[object] = None) -> None:
    if conn is None:
        conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS battle_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_battle_sessions_user ON battle_sessions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_battle_sessions_expires ON battle_sessions(expires_at)")
    conn.commit()


# ── 索引管理 ──

def _dedupe_users_for_unique_username(cur) -> int:
    cur.execute(
        """
        SELECT in_game_username
        FROM users
        WHERE in_game_username IS NOT NULL AND in_game_username != ''
        GROUP BY in_game_username
        HAVING COUNT(1) > 1
        ORDER BY in_game_username
        """
    )
    duplicate_usernames = cur.fetchall()
    renamed_count = 0
    for row in duplicate_usernames:
        username = str(row[0] or "").strip()
        if not username:
            continue
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE in_game_username = %s
            ORDER BY created_at ASC, user_id ASC
            """,
            (username,),
        )
        users = cur.fetchall()
        # Keep canonical row and rename the rest.
        for idx, user_row in enumerate(users):
            if idx == 0:
                continue
            user_id = str(user_row[0] or "")
            base_name = (username[:12] or "user").strip() or "user"
            suffix = (user_id[-6:] or "dup")
            candidate = f"{base_name}_{suffix}"
            serial = 1
            while cur.execute(
                "SELECT 1 FROM users WHERE in_game_username = %s AND user_id != %s LIMIT 1",
                (candidate, user_id),
            ).fetchone():
                serial += 1
                candidate = f"{base_name}_{suffix}_{serial}"
            cur.execute(
                "UPDATE users SET in_game_username = %s WHERE user_id = %s",
                (candidate, user_id),
            )
            renamed_count += int(cur.rowcount or 0)
    return renamed_count


def _dedupe_users_for_unique_telegram_id(cur) -> int:
    cur.execute(
        """
        SELECT telegram_id
        FROM users
        WHERE telegram_id IS NOT NULL AND telegram_id != ''
        GROUP BY telegram_id
        HAVING COUNT(1) > 1
        ORDER BY telegram_id
        """
    )
    duplicate_platform_ids = cur.fetchall()
    cleared_count = 0
    for row in duplicate_platform_ids:
        telegram_id = str(row[0] or "").strip()
        if not telegram_id:
            continue
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE telegram_id = %s
            ORDER BY created_at ASC, user_id ASC
            """,
            (telegram_id,),
        )
        users = cur.fetchall()
        # Keep canonical row and clear the rest.
        for idx, user_row in enumerate(users):
            if idx == 0:
                continue
            user_id = str(user_row[0] or "")
            cur.execute(
                "UPDATE users SET telegram_id = '' WHERE user_id = %s AND telegram_id = %s",
                (user_id, telegram_id),
            )
            cleared_count += int(cur.rowcount or 0)
    return cleared_count


def _create_indexes(cur) -> None:
    """创建所有必需的数据库索引（幂等操作）"""
    # 历史版本允许同一技能重复学习；先去重再补唯一约束，避免建索引失败。
    cur.execute(
        """
        DELETE FROM user_skills
        WHERE id NOT IN (
            SELECT MIN(id) FROM user_skills GROUP BY user_id, skill_id
        )
        """
    )
    # 历史版本可能写入同一天重复任务；保留最早一条再加唯一索引。
    cur.execute(
        """
        DELETE FROM user_quests
        WHERE id NOT IN (
            SELECT MIN(id) FROM user_quests GROUP BY user_id, quest_id, assigned_date
        )
        """
    )
    # 历史版本可能存在同日宗门任务重复；先把重复聚合到保留行，再清理多余行。
    cur.execute(
        """
        UPDATE sect_quests
        SET target = (
                SELECT MAX(q2.target)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            ),
            progress = (
                SELECT MAX(q2.progress)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            ),
            reward_copper = (
                SELECT MAX(q2.reward_copper)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            ),
            reward_exp = (
                SELECT MAX(q2.reward_exp)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            ),
            completed = (
                SELECT MAX(q2.completed)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            ),
            claimed = (
                SELECT MAX(q2.claimed)
                FROM sect_quests q2
                WHERE q2.sect_id = sect_quests.sect_id
                  AND q2.quest_type = sect_quests.quest_type
                  AND q2.assigned_date = sect_quests.assigned_date
            )
        WHERE id IN (
            SELECT MIN(id)
            FROM sect_quests
            GROUP BY sect_id, quest_type, assigned_date
            HAVING COUNT(1) > 1
        )
        """
    )
    cur.execute(
        """
        DELETE FROM sect_quests
        WHERE id NOT IN (
            SELECT MIN(id) FROM sect_quests GROUP BY sect_id, quest_type, assigned_date
        )
        """
    )
    # 旧数据中可能存在同一玩家重复 pending 申请；保留最早一条，其他置为 rejected。
    cur.execute(
        """
        UPDATE sect_branch_requests
        SET status = 'rejected',
            decided_at = EXTRACT(EPOCH FROM NOW())::INTEGER,
            decided_by = 'system_dedupe'
        WHERE status = 'pending'
          AND id NOT IN (
              SELECT MIN(id)
              FROM sect_branch_requests
              WHERE status = 'pending'
              GROUP BY parent_sect_id, applicant_user_id
          )
        """
    )
    # 旧数据中同一对玩家可能存在多条 pending 论道请求；保留最早一条。
    cur.execute(
        """
        UPDATE social_chat_requests
        SET status = 'rejected',
            responded_at = EXTRACT(EPOCH FROM NOW())::INTEGER
        WHERE status = 'pending'
          AND id NOT IN (
              SELECT MIN(id)
              FROM social_chat_requests
              WHERE status = 'pending'
              GROUP BY
                  CASE WHEN from_user_id < to_user_id THEN from_user_id ELSE to_user_id END,
                  CASE WHEN from_user_id < to_user_id THEN to_user_id ELSE from_user_id END
          )
        """
    )

    indexes = [
        # 平台ID索引（部分索引，排除空值）
        "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id) WHERE telegram_id IS NOT NULL AND telegram_id != ''",
        # 关联表索引
        "CREATE INDEX IF NOT EXISTS idx_items_user_id ON items(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_user_item_type ON items(user_id, item_id, item_type)",
        "CREATE INDEX IF NOT EXISTS idx_user_skills_user_id ON user_skills(user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_skills_user_skill ON user_skills(user_id, skill_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_quests_user_date ON user_quests(user_id, assigned_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_quests_user_quest_day ON user_quests(user_id, quest_id, assigned_date)",
        "CREATE INDEX IF NOT EXISTS idx_realm_trials_user_rank ON user_realm_trials(user_id, realm_id)",
        "CREATE INDEX IF NOT EXISTS idx_growth_snapshots_user_day ON user_growth_snapshots(user_id, day_key)",
        "CREATE INDEX IF NOT EXISTS idx_timings_user_id ON timings(user_id)",
        # 日志索引
        "CREATE INDEX IF NOT EXISTS idx_battle_logs_user_ts ON battle_logs(user_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_breakthrough_logs_user_id ON breakthrough_logs(user_id)",
        # 清理索引
        "CREATE INDEX IF NOT EXISTS idx_request_dedup_created ON request_dedup(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_battle_sessions_user ON battle_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_battle_sessions_expires ON battle_sessions(expires_at)",
        # codex 索引
        "CREATE INDEX IF NOT EXISTS idx_codex_monsters_user ON codex_monsters(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_codex_items_user ON codex_items(user_id)",
        # PVP 索引
        "CREATE INDEX IF NOT EXISTS idx_pvp_challenger ON pvp_records(challenger_id)",
        "CREATE INDEX IF NOT EXISTS idx_pvp_defender ON pvp_records(defender_id)",
        "CREATE INDEX IF NOT EXISTS idx_pvp_timestamp ON pvp_records(timestamp)",
        # 好友索引
        "CREATE INDEX IF NOT EXISTS idx_friends_user ON friends(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_friend_req_to ON friend_requests(to_user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_chat_req_to ON social_chat_requests(to_user_id, status)",
        # 宗门索引
        "CREATE INDEX IF NOT EXISTS idx_sect_members_sect ON sect_members(sect_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_members_user ON sect_members(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_quests_sect_date ON sect_quests(sect_id, assigned_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sect_quests_unique_daily ON sect_quests(sect_id, quest_type, assigned_date)",
        "CREATE INDEX IF NOT EXISTS idx_sect_quest_claims_quest ON sect_quest_claims(quest_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_quest_claims_user ON sect_quest_claims(user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sect_quest_claims_unique ON sect_quest_claims(quest_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_wars_attacker ON sect_wars(attacker_sect_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_branches_parent ON sect_branches(parent_sect_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_branches_leader ON sect_branches(leader_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_branch_members_branch ON sect_branch_members(branch_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_branch_members_user ON sect_branch_members(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sect_branch_requests_parent ON sect_branch_requests(parent_sect_id, status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sect_branch_req_pending_unique ON sect_branch_requests(parent_sect_id, applicant_user_id) WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_social_chat_from_to ON social_chat_requests(from_user_id, to_user_id) WHERE status = 'pending'",
        # 炼丹索引
        "CREATE INDEX IF NOT EXISTS idx_alchemy_logs_user ON alchemy_logs(user_id)",
        # 掉落保底索引
        "CREATE INDEX IF NOT EXISTS idx_drop_pity_user_key ON drop_pity(user_id, pity_key)",
        # 抽卡索引
        "CREATE INDEX IF NOT EXISTS idx_gacha_pity_user ON gacha_pity(user_id, banner_id)",
        "CREATE INDEX IF NOT EXISTS idx_gacha_logs_user ON gacha_logs(user_id, banner_id)",
        # 成就索引
        "CREATE INDEX IF NOT EXISTS idx_achievements_user ON user_achievements(user_id)",
        # 世界BOSS索引
        "CREATE INDEX IF NOT EXISTS idx_worldboss_id ON world_boss_state(boss_id)",
        # 活动索引
        "CREATE INDEX IF NOT EXISTS idx_event_claims_user ON event_claims(user_id, event_id)",
        "CREATE INDEX IF NOT EXISTS idx_event_points_user ON event_points(user_id, event_id)",
        "CREATE INDEX IF NOT EXISTS idx_event_point_logs_user_event ON event_point_logs(user_id, event_id)",
        "CREATE INDEX IF NOT EXISTS idx_event_exchange_claims_user ON event_exchange_claims(user_id, event_id, exchange_id)",
        # 世界BOSS攻击索引
        "CREATE INDEX IF NOT EXISTS idx_worldboss_attacks_user ON world_boss_attacks(user_id)",
        # 商店轮换限购索引
        "CREATE INDEX IF NOT EXISTS idx_shop_limits_user_period ON shop_purchase_limits(user_id, period_key)",
        # Story progression indexes
        "CREATE INDEX IF NOT EXISTS idx_story_unlocks_user_order ON user_story_unlocks(user_id, chapter_order)",
        "CREATE INDEX IF NOT EXISTS idx_story_unlocks_user_claimed ON user_story_unlocks(user_id, claimed)",
        # Audit and bounty indexes
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_bounty_status_created ON bounty_orders(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_bounty_poster ON bounty_orders(poster_user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_bounty_claimer ON bounty_orders(claimer_user_id, created_at)",
        # Metrics & reports
        "CREATE INDEX IF NOT EXISTS idx_event_logs_ts ON event_logs(ts)",
        "CREATE INDEX IF NOT EXISTS idx_event_logs_user_event ON event_logs(user_id, event)",
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_ts ON economy_ledger(ts)",
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_user ON economy_ledger(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_module_action ON economy_ledger(module, action)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_reports_unique ON daily_reports(report_date, report_type)",
        "CREATE INDEX IF NOT EXISTS idx_guardrail_alerts_date_metric ON guardrail_alerts(report_date, metric)",
    ]
    for sql in indexes:
        cur.execute(sql)

    renamed_usernames = _dedupe_users_for_unique_username(cur)
    if renamed_usernames > 0:
        logger.warning(f"Renamed {renamed_usernames} duplicate usernames before creating unique index")

    cleared_platform_ids = _dedupe_users_for_unique_telegram_id(cur)
    if cleared_platform_ids > 0:
        logger.warning(f"Cleared {cleared_platform_ids} duplicate telegram_id values before creating unique index")

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(in_game_username) WHERE in_game_username IS NOT NULL AND in_game_username != ''"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id_unique ON users(telegram_id) WHERE telegram_id IS NOT NULL AND telegram_id != ''"
    )
    logger.info(f"Ensured {len(indexes)} indexes exist")


# ── 建表 ──

def create_tables(conn: Optional[object] = None) -> None:
    """创建数据表"""
    if conn is None:
        conn = get_db()
    cur = conn.cursor()

    # Schema meta
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cur.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version', '6') ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value")

    # 用户表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            in_game_username TEXT,
            lang TEXT DEFAULT 'CHS',
            state INTEGER DEFAULT 0,
            exp INTEGER DEFAULT 0,
            rank INTEGER DEFAULT 1,
            current_map TEXT DEFAULT 'canglan_city',
            visited_maps TEXT DEFAULT '[]',
            dy_times INTEGER DEFAULT 0,
            copper INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 0,
            spirit_high INTEGER DEFAULT 0,
            spirit_exquisite INTEGER DEFAULT 0,
            spirit_supreme INTEGER DEFAULT 0,
            immortal_flawed INTEGER DEFAULT 0,
            immortal_low INTEGER DEFAULT 0,
            immortal_mid INTEGER DEFAULT 0,
            immortal_high INTEGER DEFAULT 0,
            immortal_supreme INTEGER DEFAULT 0,
            asc_reduction INTEGER DEFAULT 0,
            sign INTEGER DEFAULT 0,
            element TEXT,
            hp INTEGER DEFAULT 100,
            mp INTEGER DEFAULT 50,
            max_hp INTEGER DEFAULT 100,
            max_mp INTEGER DEFAULT 50,
            attack INTEGER DEFAULT 10,
            defense INTEGER DEFAULT 5,
            crit_rate REAL DEFAULT 0.05,
            weak_until INTEGER DEFAULT 0,
            breakthrough_pity INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            last_sign_timestamp INTEGER DEFAULT 0,
            consecutive_sign_days INTEGER DEFAULT 0,
            max_signin_days INTEGER DEFAULT 0,
            signin_month_key TEXT DEFAULT '',
            signin_month_days INTEGER DEFAULT 0,
            signin_month_claim_bits INTEGER DEFAULT 0,
            secret_realm_attempts INTEGER DEFAULT 0,
            secret_realm_last_reset INTEGER DEFAULT 0,
            secret_realm_resets_today INTEGER DEFAULT 0,
            secret_realm_reset_day INTEGER DEFAULT 0,
            equipped_weapon TEXT,
            equipped_armor TEXT,
            equipped_accessory1 TEXT,
            equipped_accessory2 TEXT,
            last_hunt_time INTEGER DEFAULT 0,
            hunts_today INTEGER DEFAULT 0,
            hunts_today_reset INTEGER DEFAULT 0,
            last_secret_time INTEGER DEFAULT 0,
            last_quest_claim_time INTEGER DEFAULT 0,
            last_enhance_time INTEGER DEFAULT 0,
            cultivation_boost_until INTEGER DEFAULT 0,
            cultivation_boost_pct REAL DEFAULT 0,
            realm_drop_boost_until INTEGER DEFAULT 0,
            breakthrough_protect_until INTEGER DEFAULT 0,
            attack_buff_until INTEGER DEFAULT 0,
            attack_buff_value INTEGER DEFAULT 0,
            defense_buff_until INTEGER DEFAULT 0,
            defense_buff_value INTEGER DEFAULT 0,
            breakthrough_boost_until INTEGER DEFAULT 0,
            breakthrough_boost_pct REAL DEFAULT 0,
            pvp_rating INTEGER DEFAULT 1000,
            pvp_wins INTEGER DEFAULT 0,
            pvp_losses INTEGER DEFAULT 0,
            pvp_draws INTEGER DEFAULT 0,
            pvp_daily_count INTEGER DEFAULT 0,
            pvp_daily_reset INTEGER DEFAULT 0,
            pvp_season_id TEXT,
            stamina INTEGER DEFAULT 24,
            stamina_updated_at INTEGER DEFAULT 0,
            vitals_updated_at INTEGER DEFAULT 0,
            chat_energy_today REAL DEFAULT 0,
            chat_energy_reset INTEGER DEFAULT 0,
            gacha_free_today INTEGER DEFAULT 0,
            gacha_paid_today INTEGER DEFAULT 0,
            gacha_daily_reset INTEGER DEFAULT 0,
            daily_cultivate_stone_day INTEGER DEFAULT 0,
            daily_cultivate_stone_claimed INTEGER DEFAULT 0,
            telegram_id TEXT,
            tower_floor INTEGER DEFAULT 0,
            tower_last_attempt_day INTEGER DEFAULT 0,
            tower_resets_today INTEGER DEFAULT 0,
            garden_level INTEGER DEFAULT 1,
            garden_exp INTEGER DEFAULT 0,
            garden_last_water TIMESTAMPTZ
        )
        """
    )

    # 药园灵田表
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

    # 修炼/活动计时表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS timings (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            start_time INTEGER,
            type TEXT,
            base_gain INTEGER
        )
        """
    )

    # 物品表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            item_id TEXT,
            item_name TEXT,
            item_type TEXT,
            quality TEXT DEFAULT 'common',
            quantity INTEGER DEFAULT 1,
            level INTEGER DEFAULT 1,
            attack_bonus INTEGER DEFAULT 0,
            defense_bonus INTEGER DEFAULT 0,
            hp_bonus INTEGER DEFAULT 0,
            mp_bonus INTEGER DEFAULT 0,
            first_round_reduction_pct REAL DEFAULT 0,
            crit_heal_pct REAL DEFAULT 0,
            element_damage_pct REAL DEFAULT 0,
            low_hp_shield_pct REAL DEFAULT 0
        )
        """
    )

    # 战斗记录表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS battle_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            monster_id TEXT,
            victory INTEGER,
            rounds INTEGER,
            exp_gained INTEGER DEFAULT 0,
            copper_gained INTEGER DEFAULT 0,
            gold_gained INTEGER DEFAULT 0,
            timestamp INTEGER
        )
        """
    )

    # 突破记录表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS breakthrough_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            from_rank INTEGER,
            to_rank INTEGER,
            success INTEGER,
            exp_lost INTEGER DEFAULT 0,
            timestamp INTEGER
        )
        """
    )

    # 技能表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_skills (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            skill_id TEXT,
            equipped INTEGER DEFAULT 0,
            learned_at INTEGER DEFAULT 0,
            UNIQUE(user_id, skill_id)
        )
        """
    )
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_skills' AND table_schema = 'public'")
    existing_skill_cols = {row[0] for row in cur.fetchall()}
    for col_name, sql in [
        ("skill_level", "ALTER TABLE user_skills ADD COLUMN skill_level INTEGER DEFAULT 1"),
        ("mastery_exp", "ALTER TABLE user_skills ADD COLUMN mastery_exp INTEGER DEFAULT 0"),
        ("last_used_at", "ALTER TABLE user_skills ADD COLUMN last_used_at INTEGER DEFAULT 0"),
    ]:
        if col_name not in existing_skill_cols:
            cur.execute(sql)

    # 兼容旧库：补充新增列
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = 'public'")
    existing_columns = {row[0] for row in cur.fetchall()}
    for col_sql in [
        ("secret_realm_attempts", "ALTER TABLE users ADD COLUMN secret_realm_attempts INTEGER DEFAULT 0"),
        ("secret_realm_last_reset", "ALTER TABLE users ADD COLUMN secret_realm_last_reset INTEGER DEFAULT 0"),
        ("secret_realm_resets_today", "ALTER TABLE users ADD COLUMN secret_realm_resets_today INTEGER DEFAULT 0"),
        ("secret_realm_reset_day", "ALTER TABLE users ADD COLUMN secret_realm_reset_day INTEGER DEFAULT 0"),
        ("last_hunt_time", "ALTER TABLE users ADD COLUMN last_hunt_time INTEGER DEFAULT 0"),
        ("hunts_today", "ALTER TABLE users ADD COLUMN hunts_today INTEGER DEFAULT 0"),
        ("hunts_today_reset", "ALTER TABLE users ADD COLUMN hunts_today_reset INTEGER DEFAULT 0"),
        ("last_secret_time", "ALTER TABLE users ADD COLUMN last_secret_time INTEGER DEFAULT 0"),
        ("last_quest_claim_time", "ALTER TABLE users ADD COLUMN last_quest_claim_time INTEGER DEFAULT 0"),
        ("last_enhance_time", "ALTER TABLE users ADD COLUMN last_enhance_time INTEGER DEFAULT 0"),
        ("cultivation_boost_until", "ALTER TABLE users ADD COLUMN cultivation_boost_until INTEGER DEFAULT 0"),
        ("cultivation_boost_pct", "ALTER TABLE users ADD COLUMN cultivation_boost_pct REAL DEFAULT 0"),
        ("realm_drop_boost_until", "ALTER TABLE users ADD COLUMN realm_drop_boost_until INTEGER DEFAULT 0"),
        ("breakthrough_protect_until", "ALTER TABLE users ADD COLUMN breakthrough_protect_until INTEGER DEFAULT 0"),
        ("attack_buff_until", "ALTER TABLE users ADD COLUMN attack_buff_until INTEGER DEFAULT 0"),
        ("attack_buff_value", "ALTER TABLE users ADD COLUMN attack_buff_value INTEGER DEFAULT 0"),
        ("defense_buff_until", "ALTER TABLE users ADD COLUMN defense_buff_until INTEGER DEFAULT 0"),
        ("defense_buff_value", "ALTER TABLE users ADD COLUMN defense_buff_value INTEGER DEFAULT 0"),
        ("breakthrough_boost_until", "ALTER TABLE users ADD COLUMN breakthrough_boost_until INTEGER DEFAULT 0"),
        ("breakthrough_boost_pct", "ALTER TABLE users ADD COLUMN breakthrough_boost_pct REAL DEFAULT 0"),
        ("breakthrough_pity", "ALTER TABLE users ADD COLUMN breakthrough_pity INTEGER DEFAULT 0"),
        ("pvp_rating", "ALTER TABLE users ADD COLUMN pvp_rating INTEGER DEFAULT 1000"),
        ("pvp_wins", "ALTER TABLE users ADD COLUMN pvp_wins INTEGER DEFAULT 0"),
        ("pvp_losses", "ALTER TABLE users ADD COLUMN pvp_losses INTEGER DEFAULT 0"),
        ("pvp_draws", "ALTER TABLE users ADD COLUMN pvp_draws INTEGER DEFAULT 0"),
        ("pvp_daily_count", "ALTER TABLE users ADD COLUMN pvp_daily_count INTEGER DEFAULT 0"),
        ("pvp_daily_reset", "ALTER TABLE users ADD COLUMN pvp_daily_reset INTEGER DEFAULT 0"),
        ("secret_loot_score", "ALTER TABLE users ADD COLUMN secret_loot_score INTEGER DEFAULT 0"),
        ("alchemy_output_score", "ALTER TABLE users ADD COLUMN alchemy_output_score INTEGER DEFAULT 0"),
        ("pvp_season_id", "ALTER TABLE users ADD COLUMN pvp_season_id TEXT"),
        ("current_map", "ALTER TABLE users ADD COLUMN current_map TEXT DEFAULT 'canglan_city'"),
        ("visited_maps", "ALTER TABLE users ADD COLUMN visited_maps TEXT DEFAULT '[]'"),
        ("stamina", f"ALTER TABLE users ADD COLUMN stamina INTEGER DEFAULT {DEFAULT_STAMINA_MAX}"),
        ("stamina_updated_at", "ALTER TABLE users ADD COLUMN stamina_updated_at INTEGER DEFAULT 0"),
        ("vitals_updated_at", "ALTER TABLE users ADD COLUMN vitals_updated_at INTEGER DEFAULT 0"),
        ("chat_energy_today", "ALTER TABLE users ADD COLUMN chat_energy_today REAL DEFAULT 0"),
        ("chat_energy_reset", "ALTER TABLE users ADD COLUMN chat_energy_reset INTEGER DEFAULT 0"),
        ("gacha_free_today", "ALTER TABLE users ADD COLUMN gacha_free_today INTEGER DEFAULT 0"),
        ("gacha_paid_today", "ALTER TABLE users ADD COLUMN gacha_paid_today INTEGER DEFAULT 0"),
        ("gacha_daily_reset", "ALTER TABLE users ADD COLUMN gacha_daily_reset INTEGER DEFAULT 0"),
        ("spirit_high", "ALTER TABLE users ADD COLUMN spirit_high INTEGER DEFAULT 0"),
        ("spirit_exquisite", "ALTER TABLE users ADD COLUMN spirit_exquisite INTEGER DEFAULT 0"),
        ("spirit_supreme", "ALTER TABLE users ADD COLUMN spirit_supreme INTEGER DEFAULT 0"),
        ("immortal_flawed", "ALTER TABLE users ADD COLUMN immortal_flawed INTEGER DEFAULT 0"),
        ("immortal_low", "ALTER TABLE users ADD COLUMN immortal_low INTEGER DEFAULT 0"),
        ("immortal_mid", "ALTER TABLE users ADD COLUMN immortal_mid INTEGER DEFAULT 0"),
        ("immortal_high", "ALTER TABLE users ADD COLUMN immortal_high INTEGER DEFAULT 0"),
        ("immortal_supreme", "ALTER TABLE users ADD COLUMN immortal_supreme INTEGER DEFAULT 0"),
        ("daily_cultivate_stone_day", "ALTER TABLE users ADD COLUMN daily_cultivate_stone_day INTEGER DEFAULT 0"),
        ("daily_cultivate_stone_claimed", "ALTER TABLE users ADD COLUMN daily_cultivate_stone_claimed INTEGER DEFAULT 0"),
        ("telegram_id", "ALTER TABLE users ADD COLUMN telegram_id TEXT"),
        ("max_signin_days", "ALTER TABLE users ADD COLUMN max_signin_days INTEGER DEFAULT 0"),
        ("signin_month_key", "ALTER TABLE users ADD COLUMN signin_month_key TEXT DEFAULT ''"),
        ("signin_month_days", "ALTER TABLE users ADD COLUMN signin_month_days INTEGER DEFAULT 0"),
        ("signin_month_claim_bits", "ALTER TABLE users ADD COLUMN signin_month_claim_bits INTEGER DEFAULT 0"),
        ("tower_floor", "ALTER TABLE users ADD COLUMN tower_floor INTEGER DEFAULT 0"),
        ("tower_last_attempt_day", "ALTER TABLE users ADD COLUMN tower_last_attempt_day INTEGER DEFAULT 0"),
        ("tower_resets_today", "ALTER TABLE users ADD COLUMN tower_resets_today INTEGER DEFAULT 0"),
    ]:
        if col_sql[0] not in existing_columns:
            cur.execute(col_sql[1])
    # --- Quest tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_quests (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            quest_id TEXT,
            progress INTEGER DEFAULT 0,
            goal INTEGER DEFAULT 1,
            claimed INTEGER DEFAULT 0,
            assigned_date TEXT,
            UNIQUE(user_id, quest_id, assigned_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_realm_trials (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            realm_id INTEGER NOT NULL,
            hunt_target INTEGER DEFAULT 0,
            hunt_progress INTEGER DEFAULT 0,
            secret_target INTEGER DEFAULT 0,
            secret_progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            completed_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(user_id, realm_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_growth_snapshots (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            day_key INTEGER NOT NULL,
            exp INTEGER DEFAULT 0,
            power INTEGER DEFAULT 0,
            affix_score INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(user_id, day_key)
        )
        """
    )

    # --- Enhancement columns ---
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'items' AND table_schema = 'public'")
    item_columns = {row[0] for row in cur.fetchall()}
    if "enhance_level" not in item_columns:
        cur.execute("ALTER TABLE items ADD COLUMN enhance_level INTEGER DEFAULT 0")
    for col_sql in [
        ("first_round_reduction_pct", "ALTER TABLE items ADD COLUMN first_round_reduction_pct REAL DEFAULT 0"),
        ("crit_heal_pct", "ALTER TABLE items ADD COLUMN crit_heal_pct REAL DEFAULT 0"),
        ("element_damage_pct", "ALTER TABLE items ADD COLUMN element_damage_pct REAL DEFAULT 0"),
        ("low_hp_shield_pct", "ALTER TABLE items ADD COLUMN low_hp_shield_pct REAL DEFAULT 0"),
    ]:
        if col_sql[0] not in item_columns:
            cur.execute(col_sql[1])

    # --- Codex tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS codex_monsters (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            monster_id TEXT,
            first_seen_at INTEGER,
            last_seen_at INTEGER,
            kills INTEGER DEFAULT 0,
            UNIQUE(user_id, monster_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS codex_items (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            item_id TEXT,
            first_seen_at INTEGER,
            last_seen_at INTEGER,
            total_obtained INTEGER DEFAULT 0,
            UNIQUE(user_id, item_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS drop_pity (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            pity_key TEXT NOT NULL,
            streak INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(user_id, pity_key)
        )
        """
    )

    # --- PVP records ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pvp_records (
            id SERIAL PRIMARY KEY,
            challenger_id TEXT NOT NULL,
            defender_id TEXT NOT NULL,
            winner_id TEXT,
            rounds INTEGER DEFAULT 0,
            challenger_rating_before INTEGER DEFAULT 1000,
            defender_rating_before INTEGER DEFAULT 1000,
            challenger_rating_after INTEGER DEFAULT 1000,
            defender_rating_after INTEGER DEFAULT 1000,
            rewards_json TEXT,
            timestamp INTEGER NOT NULL
        )
        """
    )

    # --- Friends tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS friends (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            friend_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, friend_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS friend_requests (
            id SERIAL PRIMARY KEY,
            from_user_id TEXT NOT NULL,
            to_user_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            UNIQUE(from_user_id, to_user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS social_chat_requests (
            id SERIAL PRIMARY KEY,
            from_user_id TEXT NOT NULL,
            to_user_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            responded_at INTEGER DEFAULT 0
        )
        """
    )

    # --- Sect tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sects (
            id SERIAL PRIMARY KEY,
            sect_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            leader_id TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            fund_copper INTEGER DEFAULT 0,
            fund_gold INTEGER DEFAULT 0,
            max_members INTEGER DEFAULT 20,
            war_wins INTEGER DEFAULT 0,
            war_losses INTEGER DEFAULT 0,
            last_war_time INTEGER DEFAULT 0,
            cultivation_buff_pct REAL DEFAULT 10,
            stat_buff_pct REAL DEFAULT 5,
            battle_reward_buff_pct REAL DEFAULT 10,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_members (
            id SERIAL PRIMARY KEY,
            sect_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            contribution INTEGER DEFAULT 0,
            joined_at INTEGER NOT NULL,
            UNIQUE(user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_quests (
            id SERIAL PRIMARY KEY,
            sect_id TEXT NOT NULL,
            quest_type TEXT NOT NULL,
            target INTEGER DEFAULT 0,
            progress INTEGER DEFAULT 0,
            reward_copper INTEGER DEFAULT 0,
            reward_exp INTEGER DEFAULT 0,
            assigned_date TEXT,
            completed INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            UNIQUE(sect_id, quest_type, assigned_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_quest_claims (
            id SERIAL PRIMARY KEY,
            quest_id INTEGER NOT NULL,
            sect_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            claimed_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(quest_id, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_wars (
            id SERIAL PRIMARY KEY,
            attacker_sect_id TEXT NOT NULL,
            defender_sect_id TEXT NOT NULL,
            winner_sect_id TEXT NOT NULL,
            power_a INTEGER DEFAULT 0,
            power_b INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_branches (
            id SERIAL PRIMARY KEY,
            branch_id TEXT UNIQUE NOT NULL,
            parent_sect_id TEXT NOT NULL,
            name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            leader_user_id TEXT NOT NULL,
            max_members INTEGER DEFAULT 5,
            description TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_branch_requests (
            id SERIAL PRIMARY KEY,
            parent_sect_id TEXT NOT NULL,
            applicant_user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            cost_copper INTEGER DEFAULT 0,
            cost_gold INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            decided_at INTEGER DEFAULT 0,
            decided_by TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sect_branch_members (
            id SERIAL PRIMARY KEY,
            branch_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            contribution INTEGER DEFAULT 0,
            joined_at INTEGER NOT NULL,
            UNIQUE(user_id)
        )
        """
    )

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sects' AND table_schema = 'public'")
    existing_sect_columns = {row[0] for row in cur.fetchall()}
    for col_sql in [
        ("cultivation_buff_pct", "ALTER TABLE sects ADD COLUMN cultivation_buff_pct REAL DEFAULT 10"),
        ("stat_buff_pct", "ALTER TABLE sects ADD COLUMN stat_buff_pct REAL DEFAULT 5"),
        ("battle_reward_buff_pct", "ALTER TABLE sects ADD COLUMN battle_reward_buff_pct REAL DEFAULT 10"),
    ]:
        if col_sql[0] not in existing_sect_columns:
            cur.execute(col_sql[1])

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sect_branches' AND table_schema = 'public'")
    existing_branch_columns = {row[0] for row in cur.fetchall()}
    for col_sql in [
        ("max_members", "ALTER TABLE sect_branches ADD COLUMN max_members INTEGER DEFAULT 5"),
    ]:
        if col_sql[0] not in existing_branch_columns:
            cur.execute(col_sql[1])

    # --- Alchemy logs ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alchemy_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            success INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            result_item_id TEXT,
            quantity INTEGER DEFAULT 1
        )
        """
    )

    # --- Gacha tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gacha_pity (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            banner_id INTEGER NOT NULL,
            pity_count INTEGER DEFAULT 0,
            sr_pity_count INTEGER DEFAULT 0,
            total_pulls INTEGER DEFAULT 0,
            UNIQUE(user_id, banner_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gacha_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            banner_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )

    # --- Achievements ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_achievements (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            achievement_id TEXT NOT NULL,
            claimed INTEGER DEFAULT 0,
            completed_at INTEGER DEFAULT 0,
            UNIQUE(user_id, achievement_id)
        )
        """
    )

    # --- World boss ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS world_boss_state (
            id SERIAL PRIMARY KEY,
            boss_id TEXT UNIQUE NOT NULL,
            hp INTEGER DEFAULT 0,
            max_hp INTEGER DEFAULT 0,
            last_reset INTEGER DEFAULT 0,
            last_defeated INTEGER DEFAULT 0
        )
        """
    )

    # --- Event claims ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_claims (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            last_claim INTEGER DEFAULT 0,
            claims INTEGER DEFAULT 0,
            UNIQUE(user_id, event_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_points (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            points_total INTEGER DEFAULT 0,
            points_spent INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            UNIQUE(user_id, event_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_point_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            delta_points INTEGER NOT NULL,
            source TEXT NOT NULL,
            meta_json TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_exchange_claims (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            exchange_id TEXT NOT NULL,
            period_key TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            UNIQUE(user_id, event_id, exchange_id, period_key)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS world_boss_attacks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            last_attack_day INTEGER DEFAULT 0,
            attacks_today INTEGER DEFAULT 0,
            UNIQUE(user_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_purchase_limits (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            period_key TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            UNIQUE(user_id, item_id, period_key)
        )
        """
    )

    # --- Idempotency dedup table ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS request_dedup (
            request_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at INTEGER,
            response_json TEXT,
            PRIMARY KEY(request_id, user_id, action)
        )
        """
    )

    # --- Metrics & reports ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_logs (
            id SERIAL PRIMARY KEY,
            ts INTEGER NOT NULL,
            user_id TEXT,
            event TEXT NOT NULL,
            success INTEGER DEFAULT 1,
            rank INTEGER,
            request_id TEXT,
            reason TEXT,
            meta_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS economy_ledger (
            id SERIAL PRIMARY KEY,
            ts INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            rank INTEGER,
            module TEXT NOT NULL,
            action TEXT NOT NULL,
            currency TEXT,
            item_id TEXT,
            qty INTEGER,
            delta_copper INTEGER DEFAULT 0,
            delta_gold INTEGER DEFAULT 0,
            delta_exp INTEGER DEFAULT 0,
            delta_stamina INTEGER DEFAULT 0,
            shown_price INTEGER,
            actual_price INTEGER,
            success INTEGER DEFAULT 1,
            reason TEXT,
            request_id TEXT,
            meta_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            id SERIAL PRIMARY KEY,
            report_date TEXT NOT NULL,
            report_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(report_date, report_type)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guardrail_alerts (
            id SERIAL PRIMARY KEY,
            report_date TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            lower_bound REAL,
            upper_bound REAL,
            level TEXT DEFAULT 'warn',
            detail_json TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )

    _ensure_request_dedup_schema(conn)

    # --- Turn-battle session persistence ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS battle_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )

    # --- NPC 记忆与道心系统 ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS npc_memories (
            id SERIAL PRIMARY KEY,
            npc_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            affinity INTEGER DEFAULT 0,
            impression TEXT DEFAULT '陌生人',
            interaction_count INTEGER DEFAULT 0,
            last_interaction_at INTEGER DEFAULT 0,
            flags TEXT DEFAULT '{}',
            interactions TEXT DEFAULT '[]',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(npc_id, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS player_dao (
            id SERIAL PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            dao_heng INTEGER DEFAULT 0,
            dao_ni INTEGER DEFAULT 0,
            dao_yan INTEGER DEFAULT 0,
            mentality INTEGER DEFAULT 100,
            active_technique_id TEXT,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS player_techniques (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            technique_id TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            proficiency INTEGER DEFAULT 0,
            learned_at INTEGER NOT NULL,
            UNIQUE(user_id, technique_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_tier TEXT NOT NULL,
            event_trigger TEXT,
            result TEXT,
            narrative TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS world_state (
            id SERIAL PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            value TEXT DEFAULT '{}',
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_state (
            user_id TEXT PRIMARY KEY,
            next_chapter_order INTEGER DEFAULT 1,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_counters (
            user_id TEXT PRIMARY KEY,
            signin_count INTEGER DEFAULT 0,
            cultivate_count INTEGER DEFAULT 0,
            hunt_victory_count INTEGER DEFAULT 0,
            secret_realm_count INTEGER DEFAULT 0,
            breakthrough_success_count INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_story_unlocks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            narrative TEXT DEFAULT '',
            reward_json TEXT DEFAULT '{}',
            trigger_event TEXT,
            unlocked_at INTEGER NOT NULL,
            claimed INTEGER DEFAULT 0,
            claimed_at INTEGER DEFAULT 0,
            UNIQUE(user_id, chapter_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            module TEXT NOT NULL,
            action TEXT NOT NULL,
            user_id TEXT DEFAULT '',
            target_user_id TEXT DEFAULT '',
            success INTEGER DEFAULT 1,
            detail_json TEXT DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bounty_orders (
            id SERIAL PRIMARY KEY,
            poster_user_id TEXT NOT NULL,
            wanted_item_id TEXT NOT NULL,
            wanted_item_name TEXT NOT NULL,
            wanted_quantity INTEGER NOT NULL,
            reward_spirit_low INTEGER NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            claimer_user_id TEXT,
            created_at INTEGER NOT NULL,
            claimed_at INTEGER DEFAULT 0,
            completed_at INTEGER DEFAULT 0,
            cancelled_at INTEGER DEFAULT 0
        )
        """
    )

    # --- 创建索引 ---
    _create_indexes(cur)

    # 清理已废弃的交易功能遗留结构，不再保留兼容。
    cur.execute("DROP INDEX IF EXISTS idx_trade_offers_from")
    cur.execute("DROP INDEX IF EXISTS idx_trade_offers_to")
    cur.execute("DROP TABLE IF EXISTS trade_offers")
    conn.commit()
    logger.info("Database tables created/verified")

