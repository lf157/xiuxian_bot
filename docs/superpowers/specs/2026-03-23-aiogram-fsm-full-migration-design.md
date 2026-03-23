# aiogram + FSM 全量接管 Telegram 交互（去除嵌套 if-else）设计稿

**日期:** 2026-03-23
**版本:** v3（补齐全域迁移、回调枚举与切换收口）
**状态:** 设计已获口头确认，待用户审阅文档后进入计划阶段
**范围:** `adapters/aiogram/*`、`core/config.py`、`config.json`（`adapters/telegram/bot.py`仅作迁移参考）

## 1. 背景与目标
当前 `aiogram` 运行链路仍通过 `adapters/aiogram/legacy_bridge.py` 转发至 `adapters/telegram/bot.py` 的超长 `callback_handler`，核心分发依赖嵌套 if-else。

本次目标：
- 全量迁移到原生 `aiogram Router + FSM`。
- 运行链路彻底下线 `legacy_bridge`。
- 以“按域拆分 handlers/states/services”替代超长 if-else。
- 保持玩家可见体验尽量一致（菜单习惯、文案风格、命令入口），并延续“储物袋/灵装”命名。
- 启用新 callback 协议，不兼容历史消息旧按钮。
- FSM 存储直接使用 `RedisStorage`。

## 2. 已确认约束（用户决定）
- 全量替换，不分一期二期。
- 新 callback 协议，不兼容历史消息旧按钮。
- 彻底下线 `legacy_bridge`，不保留回滚开关。
- 使用现有工作区：
  `C:\Users\Administrator\.config\superpowers\worktrees\xiuxian_bot\storage-bag-lingzhuang`
- 允许并要求子代理并行实现，必须避免写冲突。
- 本轮不做测试执行（用户明确指令）。

## 3. 目标架构

### 3.1 运行入口
- `adapters/aiogram/bot.py`
  - 初始化 `Bot`、`Dispatcher`。
  - 使用 `RedisStorage` + `FSMStrategy.USER_IN_CHAT`。
  - 仅加载新聚合 router（`adapters/aiogram/handlers/__init__.py` 导出的 `root_router`）。
  - 不导入、不注册 `legacy_bridge`。

### 3.2 分层职责
- `adapters/aiogram/handlers/`：按业务域拆 router，禁全局 if-else 分发。
- `adapters/aiogram/states/`：按业务域拆 `StatesGroup`。
- `adapters/aiogram/services/`：HTTP/API、uid 解析、callback 解析、公共错误与导航行为。
- `adapters/aiogram/ui.py`：统一键盘构造与文案渲染。

### 3.3 业务域拆分（全量）
- `menu_account.py`：主菜单、注册、状态、统一 home/back。
- `cultivation.py`：修炼。
- `hunt.py`：狩猎战斗。
- `breakthrough.py`：突破预览与执行。
- `inventory_equipment.py`：储物袋、灵装、已佩戴、装备/强化/分解/卸下、道具使用。
- `skills.py`：技能学习/装备/卸下。
- `shop_alchemy_forge.py`：商店、炼丹、锻造。
- `secret_realms.py`：秘境（路线、事件、战斗、结算）。
- `social_pvp_sect.py`：社交、PVP、宗门。
- `story_events_quests.py`：剧情、活动、任务、签到、世界BOSS、悬赏。
- `admin.py`：超管 `/test` 与管理回调。

## 4. Callback 协议规范（严格）

### 4.1 Grammar
- 格式：`<domain>:<action>[:arg1[:arg2]]`
- `domain/action`：`[a-z0-9_]+`
- `arg`：`[A-Za-z0-9_\-]+`（禁止空白、中文、分隔符嵌套）
- 最大长度：**64 bytes**（Telegram 限制）
  - 对可能超长参数（如复杂 session key）使用短 token + FSM/context 映射。

### 4.2 未知/过期回调处理（强制）
- 所有 callback 必须先 `answer_callback_query`（避免客户端转圈）。
- 解析失败/未知 action/过期按钮统一行为：
  1. `answer_callback_query(text="按钮已过期，请重新打开菜单", show_alert=False)`
  2. 编辑当前消息或新发一条：`"该消息按钮已失效，请点击下方主菜单继续。"`
  3. 附 `menu:home` 导航键盘。

### 4.3 动作清单（显式枚举，禁通配符）
- `menu`：`home`、`register`、`stat`、`back`
- `cul`：`start`、`status`、`end`
- `hunt`：`list`、`start`、`act_normal`、`act_skill`、`exit`、`settle`
- `break`：`preview`、`help_toggle`、`confirm`、`cancel`
- `bag`：`page`、`use`、`detail`
- `gear`：`page`、`equip`、`enhance`、`decompose`、`equipped_view`、`unequip`、`detail`
- `skill`：`list`、`learn`、`equip`、`unequip`、`detail`
- `shop`：`currency`、`page`、`buy`、`back`、`noop`
- `alchemy`：`menu`、`craft`、`batch`、`back`
- `forge`：`menu`、`craft`、`enhance`、`back`
- `secret`：`list`、`realm`、`path`、`choice`、`act_normal`、`act_skill`、`exit`、`settle`
- `quest`：`list`、`detail`、`claim`
- `event`：`list`、`detail`、`claim`
- `boss`：`menu`、`attack`、`rank`
- `bounty`：`menu`、`refresh`、`claim`
- `story`：`menu`、`chapter`、`node`、`claim`
- `rank`：`menu`、`realm`、`combat`、`wealth`
- `social`：`menu`、`chat`、`dao`、`friend`、`reply`
- `pvp`：`menu`、`match`、`duel`、`history`、`claim_daily`、`refresh`
- `sect`：`menu`、`create`、`info`、`members`、`contribute`、`donate`、`train`、`leave`
- `admin`：`menu`、`test`、`lookup`、`modify`、`preset`、`confirm`、`cancel`

实施约束：
- 所有 handler 仅接收上述 `<domain>:<action>` 明确组合。
- 未列出的 action 统一按“未知/过期回调”处理（见 4.2）。

## 5. FSM 设计与状态机表

### 5.1 通用规则
- Strategy：`USER_IN_CHAT`。
- 每域只存最小必要字段（`uid`、`session_id`、`page`、`strategy` 等）。
- 跨域进入时清理上一个域的运行态 key。
- 任何异常回退到“域入口”并重置该域运行态。

### 5.2 全域状态迁移表

- `menu_account`：`idle -> menu_home -> viewing_stat|registering -> menu_home`
  - 事件：`menu:home/register/stat/back`
  - 异常：账号缺失时仅允许 `registering`，其他动作回 `menu_home`
- `cultivation`：`idle -> cultivating -> reward_preview -> idle|menu_home`
  - 事件：`cul:start/status/end`
  - 异常：修炼会话异常时清理 `cultivation_session_id`，回 `idle`
- `hunt`：`selecting_monster -> in_battle -> settlement -> selecting_monster|menu_home`
  - 事件：`hunt:list/start/act_normal/act_skill/settle/exit`
  - 异常：会话失效时 `clear(hunt_session_id)`，回 `selecting_monster`
- `breakthrough`：`selecting_strategy -> confirm -> result -> selecting_strategy|menu_home`
  - 事件：`break:preview/help_toggle/confirm/cancel`
  - 异常：预览失败保留 `selecting_strategy` 并提示
- `inventory_equipment`：`bag_browsing <-> gear_browsing <-> equipped_view`
  - 事件：`bag:page/use/detail`、`gear:page/equip/enhance/decompose/equipped_view/unequip/detail`
  - 规则：翻页不切主状态，仅更新 `page`；执行动作后回 `gear_browsing`
- `skills`：`listing -> learning|equipping -> listing`
  - 事件：`skill:list/learn/equip/unequip/detail`
  - 异常：技能不存在时保留 `listing` 并刷新列表
- `shop_alchemy_forge`：`selecting_currency -> shop_browsing -> alchemy_panel|forge_panel -> shop_browsing`
  - 事件：`shop:currency/page/buy/back/noop`、`alchemy:menu/craft/batch/back`、`forge:menu/craft/enhance/back`
  - 规则：购买/合成/锻造后刷新当前页，不离开当前子面板
- `secret_realms`：`selecting_realm -> selecting_path -> in_event_choice|in_battle -> settlement -> selecting_realm|menu_home`
  - 事件：`secret:list/realm/path/choice/act_normal/act_skill/settle/exit`
  - 异常：`secret_session_id` 无效时回 `selecting_realm`
- `social_pvp_sect`：
  - `social`：`social_menu -> social_chat|dao_dialog -> social_menu`
  - `pvp`：`pvp_menu -> matching -> duel -> settlement -> pvp_menu`
  - `sect`：`sect_menu -> sect_members|sect_contribution|sect_training -> sect_menu`
  - 事件：`social:menu/chat/dao/friend/reply`、`pvp:menu/match/duel/history/claim_daily/refresh`、`sect:menu/create/info/members/contribute/donate/train/leave`
  - 异常：任一子域异常回对应 `*_menu`
- `story_events_quests`：
  - `quest`：`quest_menu -> quest_detail -> quest_menu`
  - `event`：`event_menu -> event_detail -> event_menu`
  - `story`：`story_menu -> chapter_view -> node_view -> story_menu`
  - `boss/bounty`：`boss_menu|bounty_menu -> action_result -> boss_menu|bounty_menu`
  - 事件：`quest:list/detail/claim`、`event:list/detail/claim`、`story:menu/chapter/node/claim`、`boss:menu/attack/rank`、`bounty:menu/refresh/claim`
  - 异常：目标条目无效时回各自 `*_menu`
- `admin`：`admin_menu -> target_lookup -> modify_preview -> confirm_apply|cancel_back -> admin_menu`
  - 事件：`admin:menu/test/lookup/modify/preset/confirm/cancel`
  - 异常：权限不足或目标无效时回 `admin_menu`

## 6. RedisStorage 接入规范

### 6.1 配置优先级
1. 环境变量 `XXBOT_REDIS_URL`（若存在优先）
2. `config.json.redis.url`
3. `config.json.redis.host/port/db/password` 组装 URL

### 6.2 配置键定义
- `redis.enabled`（bool，必须为 `true`）
- `redis.url`（string，可选，优先）
- `redis.host`（default: `127.0.0.1`）
- `redis.port`（default: `6379`）
- `redis.db`（default: `0`）
- `redis.password`（default: 空）
- `redis.fsm_key_prefix`（default: `xxbot:fsm:v2:`）
- `redis.purge_legacy_fsm_prefixes`（default: `false`）

### 6.3 依赖与生命周期
- 新增依赖：`redis>=5.0`（aiogram redis storage）
- 启动：创建 `RedisStorage`，连接不可用则 adapter 启动失败（fail-fast）
- 关闭：adapter shutdown 时显式关闭 storage/redis 连接
- FSM 状态 TTL：默认不主动设置（由业务流程显式清理运行态）

### 6.4 旧态切换与清理策略（一次性迁移）
- 新版本默认前缀：`redis.fsm_key_prefix = xxbot:fsm:v2:`（与历史 `xxbot:fsm:` 隔离）。
- 启动时只读取 `v2` 前缀，不回读历史前缀，确保“新协议+新状态”一致。
- 提供受控清理开关：`redis.purge_legacy_fsm_prefixes`（default: `false`）。
- 当开关为 `true` 时，启动阶段执行一次 `SCAN xxbot:fsm:*` 删除历史键，但排除 `xxbot:fsm:v2:*`。
- 清理完成后记录日志：`legacy_fsm_purge_deleted=<count>`，用于发布后核对。

## 7. legacy_bridge 下线收口清单（强制）
1. `adapters/aiogram/bot.py` 不再 import/include `legacy_bridge`。
2. `adapters/aiogram/__init__.py`、`handlers/__init__.py` 不暴露 legacy router。
3. 全仓搜索 `legacy_bridge`：仅允许历史文档/CHANGELOG 提及，不得存在运行时引用。
4. 全仓搜索 `from adapters.telegram import bot as legacy_bot`：不得出现在 aiogram 运行链路。
5. `start.py`/adapter 启动逻辑仅指向 aiogram 原生入口。

## 8. 公共接口契约（并行开发防漂移）

### 8.1 services 契约
- `resolve_uid(tg_user_id) -> str|None`
- `api_get(path, params=None, actor_uid=None) -> dict`
- `api_post(path, payload, actor_uid=None, request_id=None) -> dict`
- `new_request_id() -> str`
- `safe_answer(query, text=None, show_alert=False)`
- `respond_query(query, text, reply_markup=None, parse_mode=None, fallback_to_send=True)`

### 8.2 ui 契约
- `main_menu_keyboard(registered: bool)`
- 各域 `*_keyboard(...)`
- 各域 `format_*_panel/result(...)`
- 统一“返回主菜单”键文案与位置规则

### 8.3 变更权限
- 公共契约只能由主控或 Agent A 修改。
- 其他子代理只调用，不重定义。

## 9. UX 不变量（验收）
1. 主菜单仍为多入口结构，且包含“储物袋/灵装”。
2. 命令习惯保持：`/xian_start`、`/xian_stat`、`/xian_cul`、`/xian_hunt`、`/xian_break`、`/xian_shop`、`/xian_bag` 等。
3. 关键流程文案风格保持：状态卡、狩猎战斗结算、突破预览/结果、秘境事件。
4. 导航体验保持：关键面板均有“返回/主菜单”。
5. 旧按钮失效时有明确提示，不出现静默无响应。

### 9.1 命令兼容矩阵（精确）
- `/xian_start`、`/start` -> `menu_account.home`
- `/xian_register`、`/register` -> `menu_account.register`
- `/xian_stat`、`/xian_status`、`/stat`、`/status` -> `menu_account.stat`
- `/xian_cul`、`/xian_cultivate`、`/cul`、`/cultivate` -> `cultivation.entry`
- `/xian_hunt`、`/hunt` -> `hunt.entry`
- `/xian_break`、`/xian_breakthrough`、`/break`、`/breakthrough` -> `breakthrough.entry`
- `/xian_shop`、`/shop` -> `shop_alchemy_forge.shop_entry`
- `/xian_bag`、`/xian_inventory`、`/bag`、`/inventory` -> `inventory_equipment.bag_entry`
- `/xian_quest`、`/xian_quests`、`/xian_task`、`/quest`、`/quests`、`/task` -> `story_events_quests.quest_entry`
- `/xian_skills`、`/xian_skill`、`/skills`、`/skill` -> `skills.entry`
- `/xian_secret`、`/xian_mystic`、`/secret`、`/mystic` -> `secret_realms.entry`
- `/xian_rank`、`/xian_leaderboard`、`/rank`、`/leaderboard` -> `story_events_quests.rank_entry`
- `/xian_pvp`、`/pvp` -> `social_pvp_sect.pvp_entry`
- `/xian_chat`、`/xian_dao`、`/chat`、`/dao` -> `social_pvp_sect.social_entry`
- `/xian_sect`、`/sect` -> `social_pvp_sect.sect_entry`
- `/xian_alchemy`、`/alchemy` -> `shop_alchemy_forge.alchemy_entry`
- `/xian_currency`、`/currency` -> `shop_alchemy_forge.currency_entry`
- `/xian_convert`、`/convert` -> `shop_alchemy_forge.convert_entry`
- `/xian_achievements`、`/xian_ach`、`/achievements`、`/ach` -> `story_events_quests.achievements_entry`
- `/xian_codex`、`/codex` -> `story_events_quests.codex_entry`
- `/xian_events`、`/events` -> `story_events_quests.events_entry`
- `/xian_bounty`、`/bounty` -> `story_events_quests.bounty_entry`
- `/xian_worldboss`、`/xian_boss`、`/worldboss`、`/boss` -> `story_events_quests.worldboss_entry`
- `/xian_guide`、`/xian_realms`、`/guide`、`/realms` -> `story_events_quests.guide_entry`
- `/xian_version`、`/version` -> `menu_account.version_entry`
- `/test`、`/xian_test` -> `admin.test_entry`
- `/xian_give_low` -> `admin.give_currency_low_entry`（超管权限校验）
- `/xian_give_mid` -> `admin.give_currency_mid_entry`（超管权限校验）
- `/xian_give_high` -> `admin.give_currency_high_entry`（超管权限校验）
- `/xian_give_uhigh` -> `admin.give_currency_uhigh_entry`（超管权限校验）
- `/xian_give_xhigh` -> `admin.give_currency_xhigh_entry`（超管权限校验）

## 10. 并行子代理写入边界
- Agent A：`aiogram/bot.py`、`services/*`、Redis config 读取
- Agent B：`handlers/menu_account.py`、`states/common.py`
- Agent C：`handlers/cultivation.py`、`handlers/hunt.py`、`states/combat.py`
- Agent D：`handlers/breakthrough.py`、`handlers/inventory_equipment.py`、`handlers/skills.py`、`states/inventory.py`
- Agent E：`handlers/shop_alchemy_forge.py`、`handlers/story_events_quests.py`、`states/economy.py`
- Agent F：`handlers/secret_realms.py`、`states/realms.py`
- Agent G：`handlers/social_pvp_sect.py`、`handlers/admin.py`、`states/social_admin.py`
- 主控：`handlers/__init__.py`、`ui.py`、收口与文档

冲突规则：
- 任何子代理不得编辑其他 agent 归属文件。
- 公共契约文件变更需主控协调后再落地。

## 11. 一次性切换方案（无回滚）
- 并行域开发完成后，主控统一注册新 routers。
- 同次变更移除 `legacy_bridge` 运行依赖。
- 不提供运行时 fallback/开关。

## 12. 配置迁移说明
- 开发/生产均建议优先使用 `redis.url`（可由环境变量覆盖）。
- 敏感信息（密码/URL）不写死到仓库，优先走环境变量。
- `redis.enabled=false` 视为配置错误（本方案要求强制 Redis FSM）。

## 13. 验收标准
1. aiogram 运行链路完全不可达 legacy_bridge。
2. 全部主流程由原生 aiogram handlers + FSM 接管。
3. 运行分发不再依赖 telegram 侧超长 if-else。
4. UX 不变量满足，含“储物袋/灵装”命名一致。
5. 新 callback 协议生效；旧按钮点击有统一失效提示。
6. 无测试执行要求（遵循用户指令），但需完成 13.1 的静态集成检查并全部通过。

### 13.1 静态集成检查（不执行测试）
1. 运行链路去桥接检查：
   - 命令：`rg -n "legacy_bridge|from adapters.telegram import bot as legacy_bot" adapters/aiogram`
   - 通过标准：无运行时引用；仅允许文档或变更日志出现。
2. Router 注册完整性检查：
   - 命令：`rg -n "include_router\\(|root_router" adapters/aiogram/handlers adapters/aiogram/bot.py`
   - 通过标准：`root_router` 注册全部业务域 router，`bot.py` 仅 include 新 root router。
3. Callback 枚举一致性检查：
   - 命令：`rg -n "CallbackData|callback_data=|parse_callback" adapters/aiogram`
   - 通过标准：解析器与键盘构造仅出现 4.3 已定义 domain/action。
4. RedisStorage 强制接入检查：
   - 命令：`rg -n "RedisStorage|fsm_key_prefix|purge_legacy_fsm_prefixes" adapters/aiogram core/config.py config.json`
   - 通过标准：aiogram 使用 `RedisStorage`；默认前缀为 `xxbot:fsm:v2:`；存在旧态清理开关读取。
5. 命令入口映射检查：
   - 命令：`rg -n "xian_|Command|router.message\\(Command" adapters/aiogram`
   - 通过标准：9.1 中命令均可在 aiogram 侧找到对应入口处理函数。

## 14. 非目标
- 不做旧 callback 兼容层。
- 不做回滚开关。
- 不引入 Web 侧功能改造。
- 不执行自动化测试。
