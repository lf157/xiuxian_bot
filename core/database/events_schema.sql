-- ============================================================
-- 万级随机事件系统 · PostgreSQL 建表 + 种子数据
-- 文件: core/database/events_schema.sql
-- ============================================================

-- ============================================================
-- 表1: events 事件主表
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    category         TEXT NOT NULL,        -- combat/adventure/social/crafting/daily/enlightenment/tribulation/karma
    sub_category     TEXT NOT NULL,        -- beast/evil_cultivator/cave/market_trade...
    rarity           TEXT DEFAULT 'common', -- common/uncommon/rare/epic/legendary/mythic
    realm_min        INTEGER DEFAULT 0,    -- 最低境界 (1=炼气一层, 10=金丹, 15=元婴...)
    realm_max        INTEGER DEFAULT 99,
    location_type    TEXT,                 -- forest/mountain/desert/market/sect/cave/plain/ruin/any
    template_key     TEXT NOT NULL,        -- 指向 texts/ 中的文案节点
    base_weight      REAL DEFAULT 1.0,
    cooldown_events  INTEGER DEFAULT 10,   -- 触发后间隔多少次才可再次触发
    required_tags    JSONB DEFAULT '[]',   -- 前置蝴蝶效应标签
    excluded_tags    JSONB DEFAULT '[]',   -- 排斥标签
    produced_tags    JSONB DEFAULT '[]',   -- 事件触发后产出的标签
    is_chain_event   BOOLEAN DEFAULT FALSE,
    chain_id         TEXT,
    chain_stage      INTEGER,
    created_at       TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 表2: event_choices 选项表
-- ============================================================
CREATE TABLE IF NOT EXISTS event_choices (
    id               BIGSERIAL PRIMARY KEY,
    event_id         TEXT REFERENCES events(event_id) ON DELETE CASCADE,
    choice_index     INTEGER NOT NULL,
    choice_text      TEXT NOT NULL,         -- 按钮显示文字
    style            TEXT,                  -- bold/cautious/cunning/kind/cruel
    template_key     TEXT,                  -- 选项结果文案 key
    skill_check      JSONB DEFAULT '{}',    -- {"attribute":"神识","base_rate":0.6}
    success_rewards  JSONB DEFAULT '{}',    -- {"exp":100,"spirit_stone":5,"items":["half_jade"]}
    failure_penalty  JSONB DEFAULT '{}',    -- {"hp_loss":20}
    success_tags     JSONB DEFAULT '[]',
    failure_tags     JSONB DEFAULT '[]'
);

-- ============================================================
-- 表3: player_butterfly_tags 玩家蝴蝶效应标签
-- ============================================================
CREATE TABLE IF NOT EXISTS player_butterfly_tags (
    player_id        BIGINT NOT NULL,       -- 对应 players.id
    tag              TEXT NOT NULL,
    acquired_at      TIMESTAMP DEFAULT NOW(),
    expires_at       TIMESTAMP,             -- NULL = 永久
    PRIMARY KEY (player_id, tag)
);

-- ============================================================
-- 表4: player_event_queue 蝴蝶效应待触发队列
-- ============================================================
CREATE TABLE IF NOT EXISTS player_event_queue (
    id               BIGSERIAL PRIMARY KEY,
    player_id        BIGINT NOT NULL,
    event_id         TEXT REFERENCES events(event_id),
    trigger_after    INTEGER NOT NULL,      -- 距离当前还需几次随机事件后触发
    created_at       TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 表5: player_event_cooldown 事件冷却
-- ============================================================
CREATE TABLE IF NOT EXISTS player_event_cooldown (
    player_id        BIGINT NOT NULL,
    event_id         TEXT NOT NULL,
    remaining_events INTEGER NOT NULL,      -- 还需多少次事件后才能再触发
    updated_at       TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (player_id, event_id)
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_events_category    ON events(category, sub_category);
CREATE INDEX IF NOT EXISTS idx_events_realm       ON events(realm_min, realm_max);
CREATE INDEX IF NOT EXISTS idx_events_rarity      ON events(rarity);
CREATE INDEX IF NOT EXISTS idx_events_location    ON events(location_type);
CREATE INDEX IF NOT EXISTS idx_events_chain       ON events(chain_id, chain_stage) WHERE is_chain_event;
CREATE INDEX IF NOT EXISTS idx_events_tags_req    ON events USING GIN(required_tags);
CREATE INDEX IF NOT EXISTS idx_events_tags_exc    ON events USING GIN(excluded_tags);
CREATE INDEX IF NOT EXISTS idx_choices_event      ON event_choices(event_id);
CREATE INDEX IF NOT EXISTS idx_player_btags       ON player_butterfly_tags(player_id);
CREATE INDEX IF NOT EXISTS idx_player_queue       ON player_event_queue(player_id, trigger_after);

-- ============================================================
-- 种子数据: INSERT INTO events
-- 分类: combat / adventure / social / enlightenment / tribulation / karma / daily
-- ============================================================

BEGIN;

-- ==================== COMBAT: beast ====================
INSERT INTO events VALUES
-- 一. 妖兽战斗 (forest/mountain/plain, realm 1-6, common)
('CMB_BEAST_001','combat','beast','common',   1, 6, 'forest',  'combat/beast_encounter::wolf_demon',    1.2,10,'[]','[]','["初斗妖兽"]',             FALSE,NULL,NULL,NOW()),
('CMB_BEAST_002','combat','beast','common',   1, 5, 'forest',  'combat/beast_encounter::wolf_demon',    1.0,10,'["初斗妖兽"]','[]','["狼妖老手"]',    FALSE,NULL,NULL,NOW()),
('CMB_BEAST_003','combat','beast','uncommon', 2, 8, 'mountain','combat/beast_encounter::rock_serpent',  0.9,15,'[]','[]','["斩蛇之人"]',              FALSE,NULL,NULL,NOW()),
('CMB_BEAST_004','combat','beast','uncommon', 3, 8, 'mountain','combat/beast_encounter::thunder_hawk',  0.8,15,'[]','[]','["雷翎猎手"]',              FALSE,NULL,NULL,NOW()),
('CMB_BEAST_005','combat','beast','rare',     4, 10,'forest',  'combat/beast_encounter::fire_ape',      0.5,20,'[]','[]','["赤猿克星"]',              FALSE,NULL,NULL,NOW()),
('CMB_BEAST_006','combat','beast','rare',     3, 9, 'plain',   'combat/beast_encounter::ice_serpent',   0.5,20,'[]','[]','["冰蛟屠者"]',              FALSE,NULL,NULL,NOW()),
('CMB_BEAST_007','combat','beast','epic',     5, 12,'any',     'combat/beast_encounter::beast_horde',   0.2,30,'[]','[]','["妖兽潮幸存者"]',          FALSE,NULL,NULL,NOW()),
('CMB_BEAST_008','combat','beast','common',   1, 4, 'plain',   'combat/beast_encounter::wolf_demon',    1.1,8, '[]','["狼妖老手"]','["初斗妖兽"]',   FALSE,NULL,NULL,NOW()),
('CMB_BEAST_009','combat','beast','uncommon', 2, 7, 'cave',    'combat/beast_encounter::rock_serpent',  0.7,15,'[]','[]','["洞穴探者"]',              FALSE,NULL,NULL,NOW()),
('CMB_BEAST_010','combat','beast','rare',     6, 14,'mountain','combat/beast_encounter::thunder_hawk',  0.4,25,'[]','[]','["鹰王猎手"]',              FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== COMBAT: evil_cultivator ====================
INSERT INTO events VALUES
('CMB_EVIL_001','combat','evil_cultivator','rare',     3, 8, NULL,    'combat/evil_cultivator::blood_shadow',   0.6,30,'[]','[]','["血影追踪"]',        FALSE,NULL,NULL,NOW()),
('CMB_EVIL_002','combat','evil_cultivator','uncommon', 2, 7, 'forest','combat/evil_cultivator::poison_witch',   0.7,20,'[]','[]','["断毒手"]',           FALSE,NULL,NULL,NOW()),
('CMB_EVIL_003','combat','evil_cultivator','rare',     4,10, NULL,    'combat/evil_cultivator::sect_hunter',    0.5,30,'[]','[]','["猎杀幸存者"]',       FALSE,NULL,NULL,NOW()),
('CMB_EVIL_004','combat','evil_cultivator','epic',     6,14, NULL,    'combat/evil_cultivator::blood_shadow',   0.2,50,'["血影追踪"]','[]','["血影宿敌"]',FALSE,NULL,NULL,NOW()),
('CMB_EVIL_005','combat','evil_cultivator','uncommon', 1, 5, 'market','combat/evil_cultivator::poison_witch',   0.6,15,'[]','[]','["市井邪修"]',         FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== COMBAT: ambush ====================
INSERT INTO events VALUES
('CMB_AMBS_001','combat','ambush','common',   1, 6, 'forest', 'combat/ambush::roadside_ambush',  1.0,12,'[]','[]','["遇袭幸存"]',       FALSE,NULL,NULL,NOW()),
('CMB_AMBS_002','combat','ambush','uncommon', 2, 8, 'ruin',   'combat/ambush::formation_trap',   0.8,18,'[]','[]','["破阵专家"]',       FALSE,NULL,NULL,NOW()),
('CMB_AMBS_003','combat','ambush','rare',     4,10, 'camp',   'combat/ambush::night_raid',       0.5,25,'[]','[]','["夜袭应对者"]',     FALSE,NULL,NULL,NOW()),
('CMB_AMBS_004','combat','ambush','uncommon', 2, 7, 'road',   'combat/ambush::roadside_ambush',  0.9,15,'["遇袭幸存"]','[]','["警觉修士"]',FALSE,NULL,NULL,NOW()),
('CMB_AMBS_005','combat','ambush','epic',     5,12, NULL,     'combat/ambush::night_raid',       0.3,35,'["猎杀幸存者"]','[]','["夜战王者"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== COMBAT: tribulation_beast ====================
INSERT INTO events VALUES
('CMB_TRIB_001','combat','tribulation_beast','rare',     5,12,'any','combat/tribulation_beast::thunder_dragon', 0.4,40,'[]','[]','["渡劫感悟"]',      FALSE,NULL,NULL,NOW()),
('CMB_TRIB_002','combat','tribulation_beast','epic',    10,20,'any','combat/tribulation_beast::thunder_dragon', 0.2,60,'["渡劫感悟"]','[]','["雷龙屠者"]',FALSE,NULL,NULL,NOW()),
('CMB_TRIB_003','combat','tribulation_beast','legendary',4,20,'any','combat/tribulation_beast::heart_demon_beast',0.1,80,'[]','[]','["心魔斩断"]',    FALSE,NULL,NULL,NOW()),
('CMB_TRIB_004','combat','tribulation_beast','epic',     8,18,'any','combat/tribulation_beast::heart_demon_beast',0.15,60,'[]','["心魔斩断"]','["心魔困扰"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== ADVENTURE: cave ====================
INSERT INTO events VALUES
('ADV_CAVE_001','adventure','cave','uncommon',0, 5,'mountain','adventure/ancient_cave::spirit_spring', 0.9,20,'[]','[]','["灵泉记忆"]',           FALSE,NULL,NULL,NOW()),
('ADV_CAVE_002','adventure','cave','rare',    1, 6,'desert',  'adventure/ancient_cave::bone_jade',    0.5,30,'[]','[]','["枯骨玉佩"]',           FALSE,NULL,NULL,NOW()),
('ADV_CAVE_003','adventure','cave','epic',    3,10,'any',     'adventure/ancient_cave::time_rift',    0.2,50,'["灵泉记忆","枯骨玉佩"]','[]','["时空裂隙"]',FALSE,NULL,NULL,NOW()),
('ADV_CAVE_004','adventure','cave','rare',    2, 8,'mountain','adventure/ancient_cave::spirit_spring', 0.6,25,'["灵泉记忆"]','[]','["灵泉常客"]', FALSE,NULL,NULL,NOW()),
('ADV_CAVE_005','adventure','cave','legendary',5,15,'any',    'adventure/ancient_cave::time_rift',    0.1,70,'["时空裂隙"]','[]','["时空旅者"]',  FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== ADVENTURE: secret_realm ====================
INSERT INTO events VALUES
('ADV_SECR_001','adventure','secret_realm','rare',  3, 8,'any','adventure/secret_realm::star_meteor_realm', 0.5,35,'[]','[]','["星陨入场"]',       FALSE,NULL,NULL,NOW()),
('ADV_SECR_002','adventure','secret_realm','epic',  6,15,'any','adventure/secret_realm::ancient_battlefield',0.2,60,'[]','[]','["古战场历练者"]', FALSE,NULL,NULL,NOW()),
('ADV_SECR_003','adventure','secret_realm','rare',  4,10,'any','adventure/secret_realm::star_meteor_realm', 0.4,40,'["星陨入场"]','[]','["星陨老手"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== ADVENTURE: meteorite ====================
INSERT INTO events VALUES
('ADV_METR_001','adventure','meteorite','uncommon',1, 6,'any',    'adventure/meteorite::falling_star',0.8,20,'[]','[]','["星辰碎铁持有者"]', FALSE,NULL,NULL,NOW()),
('ADV_METR_002','adventure','meteorite','rare',    4, 9,'mountain','adventure/meteorite::star_beast', 0.5,30,'[]','[]','["星兽猎手"]',       FALSE,NULL,NULL,NOW()),
('ADV_METR_003','adventure','meteorite','uncommon',2, 7,'plain',   'adventure/meteorite::falling_star',0.7,18,'["星辰碎铁持有者"]','[]','["星辰收藏家"]',FALSE,NULL,NULL,NOW()),
('ADV_METR_004','adventure','meteorite','epic',    5,12,'any',    'adventure/meteorite::star_beast',  0.25,45,'["星兽猎手"]','[]','["星力觉醒"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== ADVENTURE: fallen_immortal ====================
INSERT INTO events VALUES
('ADV_IMMO_001','adventure','fallen_immortal','epic',     5,15,'mountain','adventure/fallen_immortal::immortal_manor', 0.2,60,'[]','[]','["仙府缘起"]',      FALSE,NULL,NULL,NOW()),
('ADV_IMMO_002','adventure','fallen_immortal','legendary',8,20,'any',     'adventure/fallen_immortal::immortal_ghost', 0.1,80,'[]','[]','["仙人遗愿"]',      FALSE,NULL,NULL,NOW()),
('ADV_IMMO_003','adventure','fallen_immortal','epic',     6,18,'ruin',    'adventure/fallen_immortal::immortal_manor', 0.15,70,'["仙府缘起"]','[]','["仙府探索者"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== SOCIAL: market_trade ====================
INSERT INTO events VALUES
('SOC_MRKT_001','social','market_trade','uncommon',1,10,'market','social/market_trade::mystery_vendor',    0.8,15,'[]','[]','["神秘摊贩相遇"]', FALSE,NULL,NULL,NOW()),
('SOC_MRKT_002','social','market_trade','uncommon',2, 8,'market','social/market_trade::auction_house',     0.9,10,'[]','[]','["拍卖老手"]',     FALSE,NULL,NULL,NOW()),
('SOC_MRKT_003','social','market_trade','common',  1, 6,'road',  'social/market_trade::wandering_merchant',1.2, 8,'[]','[]','[]',             FALSE,NULL,NULL,NOW()),
('SOC_MRKT_004','social','market_trade','rare',    3,10,'market','social/market_trade::mystery_vendor',    0.4,30,'["神秘摊贩相遇"]','[]','["摊贩宿命"]',FALSE,NULL,NULL,NOW()),
('SOC_MRKT_005','social','market_trade','common',  1, 8,'market','social/market_trade::wandering_merchant',1.0,10,'[]','[]','[]',             FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== SOCIAL: sect_conflict ====================
INSERT INTO events VALUES
('SOC_SECT_001','social','sect_conflict','common',  1, 8,'field', 'social/sect_conflict::resource_dispute',      1.0,10,'[]','[]','["宗门争端"]',  FALSE,NULL,NULL,NOW()),
('SOC_SECT_002','social','sect_conflict','uncommon',3,10,'sect',  'social/sect_conflict::inner_sect_politics',   0.7,20,'[]','[]','["内门靠山"]',  FALSE,NULL,NULL,NOW()),
('SOC_SECT_003','social','sect_conflict','common',  2, 8,'any',   'social/sect_conflict::inter_sect_challenge',  1.0,12,'[]','[]','[]',          FALSE,NULL,NULL,NOW()),
('SOC_SECT_004','social','sect_conflict','rare',    4,12,'sect',  'social/sect_conflict::inner_sect_politics',   0.4,30,'["宗门争端"]','[]','["派系领袖"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== SOCIAL: romance ====================
INSERT INTO events VALUES
('SOC_ROMN_001','social','romance','uncommon',2, 8,'any',  'social/romance::first_encounter',0.6,20,'[]','["道侣相遇"]','["道侣相遇"]',FALSE,NULL,NULL,NOW()),
('SOC_ROMN_002','social','romance','uncommon',3,10,'any',  'social/romance::mutual_help',    0.7,15,'["道侣相遇"]','[]','["羁绊深化"]', FALSE,NULL,NULL,NOW()),
('SOC_ROMN_003','social','romance','rare',    4,12,'any',  'social/romance::growing_bond',   0.4,25,'["羁绊深化"]','[]','["道侣确立"]', FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== ENLIGHTENMENT: breakthrough ====================
INSERT INTO events VALUES
('ENL_BKTG_001','enlightenment','breakthrough','common',  1,20,'any','enlightenment/breakthrough::meditation',  1.2, 5,'[]','[]','[]',              FALSE,NULL,NULL,NOW()),
('ENL_BKTG_002','enlightenment','breakthrough','uncommon',1,20,'any','enlightenment/breakthrough::insight',      0.8,10,'[]','[]','["顿悟者"]',     FALSE,NULL,NULL,NOW()),
('ENL_BKTG_003','enlightenment','breakthrough','rare',    2,20,'any','enlightenment/breakthrough::breakthrough', 0.4,20,'["顿悟者"]','[]','["境界精通"]',FALSE,NULL,NULL,NOW()),
('ENL_BKTG_004','enlightenment','breakthrough','epic',    5,20,'any','enlightenment/breakthrough::breakthrough', 0.15,40,'[]','[]','["大道先行者"]', FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== TRIBULATION: lightning ====================
INSERT INTO events VALUES
('TRB_LTNG_001','tribulation','lightning','rare',      8,15,'any','tribulation/lightning::approach',   0.4,50,'[]','[]','["渡劫在望"]',  FALSE,NULL,NULL,NOW()),
('TRB_LTNG_002','tribulation','lightning','epic',      9,16,'any','tribulation/lightning::tribulation', 0.2,60,'["渡劫在望"]','[]','["渡劫幸存"]',FALSE,NULL,NULL,NOW()),
('TRB_LTNG_003','tribulation','lightning','legendary', 10,17,'any','tribulation/lightning::outcome',    0.1,80,'["渡劫幸存"]','[]','["雷劫超度者"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== KARMA: deeds ====================
INSERT INTO events VALUES
('KRM_GOOD_001','karma','good_karma','common',  1,20,'any','karma/deeds::good_karma',  1.0, 8,'[]','[]','["善缘+1"]',    FALSE,NULL,NULL,NOW()),
('KRM_GOOD_002','karma','good_karma','uncommon',1,20,'any','karma/deeds::good_karma',  0.7,12,'["善缘+1"]','[]','["功德积累"]',FALSE,NULL,NULL,NOW()),
('KRM_BADK_001','karma','bad_karma', 'common',  1,20,'any','karma/deeds::bad_karma',   0.8,10,'[]','[]','["业障+1"]',    FALSE,NULL,NULL,NOW()),
('KRM_BADK_002','karma','bad_karma', 'uncommon',1,20,'any','karma/deeds::bad_karma',   0.5,15,'["业障+1"]','[]','["业障深重"]',FALSE,NULL,NULL,NOW()),
('KRM_KFCT_001','karma','karma_effect','rare',  3,20,'any','karma/deeds::karma_effect',0.4,20,'["功德积累"]','[]','["天道眷顾"]',FALSE,NULL,NULL,NOW()),
('KRM_KFCT_002','karma','karma_effect','rare',  3,20,'any','karma/deeds::karma_effect',0.4,20,'["业障深重"]','[]','["业报清算"]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== DAILY: activities ====================
INSERT INTO events VALUES
('DAI_MORN_001','daily','morning',  'common',  1,20,'any','daily/activities::morning',  1.5, 3,'[]','[]','[]',FALSE,NULL,NULL,NOW()),
('DAI_ALCH_001','daily','alchemist','common',  2,20,'sect','daily/activities::alchemist',1.0, 5,'[]','[]','[]',FALSE,NULL,NULL,NOW()),
('DAI_MRKT_001','daily','market',  'common',  1,20,'market','daily/activities::market', 1.2, 4,'[]','[]','[]',FALSE,NULL,NULL,NOW()),
('DAI_REST_001','daily','rest',    'common',  1,20,'any','daily/activities::rest',      1.3, 3,'[]','[]','[]',FALSE,NULL,NULL,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== 蝴蝶效应链: 枯骨道人 ====================
INSERT INTO events VALUES
('CHN_BONE_S1', 'adventure','bone_sage','rare',  0, 5,'desert', 'adventure/ancient_cave::bone_jade',              0.4,999,'[]','["枯骨完结"]','[]',             TRUE,'CHAIN_BONE_SAGE',1,NOW()),
('CHN_BONE_S2A','social',   'bone_sage','rare',  1, 6,'market', 'adventure/bone_sage_chain::stage2a_market',      0.0,999,'["枯骨玉佩"]','[]','[]',              TRUE,'CHAIN_BONE_SAGE',2,NOW()),
('CHN_BONE_S2B','adventure','bone_sage','rare',  0, 6,NULL,     'adventure/bone_sage_chain::stage2b_bury',        0.0,999,'["枯骨安葬"]','[]','[]',              TRUE,'CHAIN_BONE_SAGE',2,NOW()),
('CHN_BONE_S3', 'adventure','bone_sage','epic',  2, 8,NULL,     'adventure/bone_sage_chain::stage3_confrontation',0.0,999,'["枯骨玉佩","残魂记忆","道人遗志"]','[]','["枯骨完结"]',TRUE,'CHAIN_BONE_SAGE',3,NOW())
ON CONFLICT (event_id) DO NOTHING;

-- ==================== 蝴蝶效应链: 流星因果 ====================
INSERT INTO events VALUES
('CHN_STAR_S1','adventure','meteor_chain','uncommon',1, 6,'any',    'adventure/meteorite::falling_star',          0.8,20,'[]','[]','[]',                TRUE,'CHAIN_STAR_FATE',1,NOW()),
('CHN_STAR_S2','adventure','meteor_chain','rare',    3, 9,'any',    'adventure/meteorite::star_beast',            0.0,30,'["星辰碎铁持有者"]','[]','["星力觉醒"]',TRUE,'CHAIN_STAR_FATE',2,NOW()),
('CHN_STAR_S3','adventure','meteor_chain','epic',    5,14,'mountain','adventure/secret_realm::star_meteor_realm', 0.0,50,'["星力觉醒","星陨入场"]','[]','["星辰传人"]',TRUE,'CHAIN_STAR_FATE',3,NOW())
ON CONFLICT (event_id) DO NOTHING;

COMMIT;

-- ============================================================
-- 选项种子数据
-- ============================================================

BEGIN;

-- ADV_CAVE_002 (枯骨玉佩) 的四个选项
INSERT INTO event_choices (event_id, choice_index, choice_text, style, template_key, skill_check, success_rewards, failure_penalty, success_tags, failure_tags) VALUES
('ADV_CAVE_002',1,'⚔️ 取走玉佩',    'bold',    'adventure/ancient_cave::bone_jade::take',
 '{"attribute":"神识","base_rate":0.7}',
 '{"items":["half_jade_pendant"]}',
 '{"hp_loss_pct":0.15}',
 '["枯骨玉佩","贪念微起"]','["玉佩反噬"]'),
('ADV_CAVE_002',2,'🙏 为其安葬',    'kind',    'adventure/ancient_cave::bone_jade::bury',
 '{}',
 '{"karma":10,"exp":50}',
 '{}',
 '["枯骨安葬","善缘+1"]','[]'),
('ADV_CAVE_002',3,'👁 灵识探查残魂','cunning', 'adventure/ancient_cave::bone_jade::probe',
 '{"attribute":"神识","base_rate":0.4}',
 '{"exp":200,"clue":"陆衍清"}',
 '{"hp_loss_pct":0.25}',
 '["残魂记忆","道人遗志"]','["神识受创"]'),
('ADV_CAVE_002',4,'🚶 无视离去',    'cautious','adventure/ancient_cave::bone_jade::leave',
 '{}','{}','{}',
 '["枯骨无视"]','[]')
ON CONFLICT DO NOTHING;

-- ADV_METR_001 (流星坠落) 的三个选项
INSERT INTO event_choices (event_id, choice_index, choice_text, style, template_key, skill_check, success_rewards, failure_penalty, success_tags, failure_tags) VALUES
('ADV_METR_001',1,'🏃 立刻前往查看','bold',    'adventure/meteorite::falling_star::rush_in',
 '{"attribute":"速度","base_rate":0.65}',
 '{"items":["star_iron_fragment"]}',
 '{"hp_loss_pct":0.1}',
 '["星辰碎铁持有者"]','[]'),
('ADV_METR_001',2,'🔭 先远远观察',  'cautious','adventure/meteorite::falling_star::observe_first',
 '{"attribute":"神识","base_rate":0.7}',
 '{"items":["star_dust_x3"]}',
 '{}',
 '[]','[]'),
('ADV_METR_001',3,'😴 与我无关',    'cautious','adventure/meteorite::falling_star::ignore',
 '{}',
 '{"mentality":5}',
 '{}',
 '[]','["错过流星"]')
ON CONFLICT DO NOTHING;

-- SOC_MRKT_001 (神秘摊贩) 的四个选项
INSERT INTO event_choices (event_id, choice_index, choice_text, style, template_key, skill_check, success_rewards, failure_penalty, success_tags, failure_tags) VALUES
('SOC_MRKT_001',1,'⬅️ 选左边',    'bold',    'social/market_trade::mystery_vendor::choose_left',
 '{"base_rate":0.5}','{"items":["century_herb"]}','{}','[]','[]'),
('SOC_MRKT_001',2,'⬆️ 选中间',    'cautious','social/market_trade::mystery_vendor::choose_middle',
 '{"base_rate":0.4}','{"items":["skill_scroll_fragment"]}','{}','[]','[]'),
('SOC_MRKT_001',3,'➡️ 选右边',    'cunning', 'social/market_trade::mystery_vendor::choose_right',
 '{"base_rate":0.2}','{"items":["mystery_pearl"]}','{}','["神秘摊贩相遇"]','[]'),
('SOC_MRKT_001',4,'🚶 不选，离开','cautious','social/market_trade::mystery_vendor::leave',
 '{}','{"mentality":3}','{}','[]','[]')
ON CONFLICT DO NOTHING;

COMMIT;
