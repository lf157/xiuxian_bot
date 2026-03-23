# 项目变更日志（Changelog）

- 最后更新：2026-03-23 11:48 (UTC+8)
- 本轮修复完成时间：2026-03-23 11:48 (UTC+8)
- 维护规则：新记录写在最前；每条记录必须包含“记录时间、影响范围、修改摘要”。

## 2026-03-23

### [54] aiogram + FSM 全量迁移（移除 legacy_bridge，接入 RedisStorage）
- 记录时间：2026-03-23 11:48 (UTC+8)
- 影响范围：`adapters/aiogram/bot.py`、`adapters/aiogram/handlers/*`、`adapters/aiogram/states/*`、`adapters/aiogram/services/*`、`adapters/aiogram/ui.py`、`core/config.py`、`config.json`、`requirements.txt`、`pyproject.toml`。
- 修改摘要：
  - aiogram 入口改为原生 `root_router`，运行链路彻底移除 `legacy_bridge` 与 `p1` 兼容层。
  - 全量按域拆分 handlers/states：菜单、修炼、狩猎、突破、储物袋/灵装、技能、商店/炼丹/锻造、秘境、社交/PVP/宗门、剧情/活动/任务、管理。
  - callback 协议统一为显式白名单：`<domain>:<action>[:args...]`，并新增 64-byte/regex 语法校验与过期按钮统一兜底。
  - FSM 存储强制切换为 `RedisStorage`，默认前缀 `xxbot:fsm:v2:`，并提供受控历史 key 清理开关 `redis.purge_legacy_fsm_prefixes`。
  - 主界面命名与入口统一为“储物袋/灵装”；命令矩阵在 aiogram 侧全量接管，保留 `xian_` 系列入口。

### [53] 主界面新增“灵装”入口 + 背包改名“储物袋”并完成装备拆分
- 记录时间：2026-03-23 09:47 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`core/game/realms.py`。
- 修改摘要：
  - 主菜单从“🎒 背包”调整为“🎒 储物袋”，并新增一级入口“👕 灵装”。
  - 新增 `equipment` 回调别名并复用 `equipbag_0` 逻辑，历史按钮链路保持兼容。
  - 储物袋面板去装备化：仅显示可堆叠物品，不再展示装备数量与装备入口。
  - 装备相关文案统一为“灵装”，包括灵装面板、强化/分解返回链路与已装备页跳转文案。
  - 修复 `core/game/realms.py` 中全角引号导致的语法错误，恢复可解析状态。

## 2026-03-22

### [52] 突破预览鉴权链路修复（背包用药后文案与成功率不再回退旧模板）
- 记录时间：2026-03-22 20:32 (UTC+8)
- 影响范围：`adapters/actor_paths.py`、`adapters/telegram/bot.py`、`tests/test_actor_path_patterns.py`。
- 修改摘要：
  - 修复 Telegram 端获取 `/api/breakthrough/preview/<uid>` 时未注入 `X-Actor-User-Id` 导致 401 的问题。
  - 根因：actor path 白名单缺少 `breakthrough/preview` 与 `realm-trial`，预览请求被后端鉴权拒绝后回退到 Bot 本地旧模板（固定“突破丹 +10%”）。
  - 已补齐 actor path 规则，并在预览请求参数中显式携带 `user_id`，双保险确保走服务端真实预览。
  - 新增回归测试覆盖上述两条路径的 actor 提取。

### [51] 聚灵阵突破文案同步 + 商店“全部”展示完整商品
- 记录时间：2026-03-22 20:28 (UTC+8)
- 影响范围：`core/services/settlement_extra.py`、`adapters/telegram/bot.py`。
- 修改摘要：
  - 突破预览“加成构成”中激活类加成文案由“突破增益”统一为“聚灵增益”，与聚灵阵玩法文案一致。
  - 稳妥突破在已激活增益时的提示文案同步为“已激活聚灵增益”。
  - 万宝楼“全部/分类”去除前 10 条截断，改为列出完整商品列表与完整购买按钮。
  - “全部”分类采用精简条目展示，避免商品数量上升时消息超长。

### [50] 高级/超级突破丹修复与上品灵石商店接入
- 记录时间：2026-03-22 20:24 (UTC+8)
- 影响范围：`core/services/settlement_extra.py`、`core/game/items.py`、`core/routes/shop.py`、`adapters/telegram/bot.py`、`tests/test_shop_economy_fixes.py`、`tests/test_breakthrough_failure_message_consistency.py`。
- 修改摘要：
  - 修复“高级突破丹加成未体现在突破”的问题：稳妥突破现在会自动优先消耗 `超级突破丹(+50%)` / `高级突破丹(+20%)` / `突破丹(+配置值)`，并同步到预览成功率与实际结算。
  - 新增 `超级突破丹`（`super_breakthrough_pill`）：突破增益 +50%，时效 60 分钟。
  - 新增 `spirit_high`（上品灵石）商店货币通道，`超级突破丹` 定价 `100 上品灵石`。
  - 购买链路支持 `spirit_high`：`/api/shop/buy`、结算扣费、TG 商店展示与购买回调均已接入。
  - 聚灵阵（下/中/上）同步上架万宝阁（中品灵石货架），并加入万宝阁日/周轮换位，便于在万宝阁直接购买。
  - `高级突破丹` 日限货架库存/限购从 `1` 提升到 `10`。
  - 新增回归测试覆盖：高级库存配置、上品灵石购买超级突破丹、稳妥突破丹药优先级选择。

### [49] 新增聚灵阵商品（上/中/下）并接入灵力恢复 + 突破增益
- 记录时间：2026-03-22 20:20 (UTC+8)
- 影响范围：`core/game/items.py`、`core/services/settlement_extra.py`、`tests/test_shop_economy_fixes.py`。
- 修改摘要：
  - 新增三档商品：`下品聚灵阵`、`中品聚灵阵`、`上品聚灵阵`，支持商店常驻与轮换售卖。
  - 新增道具效果 `spirit_array`：使用后激活突破成功率增益，并立即恢复部分 MP（灵力）。
  - 聚灵阵增益沿用突破增益字段（`breakthrough_boost_until/breakthrough_boost_pct`），与原有突破丹加成逻辑兼容，不会覆盖更高已生效增益。
  - 增加回归测试，覆盖聚灵阵使用后的 MP 恢复与突破增益行为。

### [48] `/test` “目标自己”被历史回复目标覆盖修复
- 记录时间：2026-03-22 20:08 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`tests/test_admin_target_resolution.py`。
- 修改摘要：
  - 修复管理面板在回调刷新时误读 `reply_to_message` 覆盖当前目标的问题。
  - 现在仅命令入口会采纳回复目标，按钮回调（含“目标自己”）不再被旧回复关系重置。
  - 新增回归测试覆盖该场景。

### [47] `/test` 面板示例命令修正（去除写死 TG_ID）
- 记录时间：2026-03-22 20:06 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`。
- 修改摘要：
  - 管理面板“快速示例”不再写死 `8516652120`，改为当前目标或占位符 `<UID|TG_ID>`。
  - 避免超管误抄示例后改到他人账号，导致“修为没变化”的误判。

### [46] `/test` 目标解析修复（优先 TG_ID 映射游戏UID）+ 预设防连点
- 记录时间：2026-03-22 20:04 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`tests/test_admin_target_resolution.py`。
- 修改摘要：
  - 修复超管面板目标解析：数字目标现在优先走 `platform_id(telegram)` 映射，成功时强制使用游戏 UID，避免误把 TG_ID 当作 `user_id` 写入。
  - `/xian_give_*` 命令目标解析统一复用同一逻辑，保持与 `/test` 一致。
  - 管理面板预设按钮增加短窗防连点（1.8 秒），群内限流/延迟场景下避免重复执行导致“+1w 实际多加”。
  - 新增回归测试：覆盖 TG 映射优先与防连点行为。

### [45] 秘境按钮“无响应”日志定位与回调限流降噪修复
- 记录时间：2026-03-22 19:58 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`。
- 修改摘要：
  - 日志确认秘境接口正常（`/api/secret-realms/*` 返回 200），无响应主因是 Telegram 群内 `Flood control` 限流导致编辑/兜底消息发送失败。
  - 回调 `_safe_edit` 在命中 `retry after` 时改为立即停止后续二次编辑与补发，避免继续放大限流。
  - 保留限流日志并尝试回调提示“操作过于频繁，请稍后再试”，提高可感知性。

### [44] 商店数量上限与冷却配置修复（支持 1-999，狩猎/秘境冷却关闭）
- 记录时间：2026-03-22 19:55 (UTC+8)
- 影响范围：`core/services/settlement_extra.py`、`config.json`、`tests/test_shop_quantity_limit.py`。
- 修改摘要：
  - 修复商店购买数量校验与前端提示不一致问题：后端上限由 `99` 调整为 `999`，输入 `100` 不再触发 `quantity invalid`。
  - 关闭狩猎与秘境冷却：`cooldowns.hunt=0`、`cooldowns.secret_realm=0`。
  - 新增数量上限回归测试，覆盖“100 可过、1000 拒绝”。

### [43] 取消狩猎冷却（`cooldowns.hunt=0`）
- 记录时间：2026-03-22 19:45 (UTC+8)
- 影响范围：`config.json`。
- 修改摘要：
  - 将 `cooldowns.hunt` 从 `30` 调整为 `0`，后端狩猎冷却判定不再触发“请等待 X 秒”。
  - 该配置对 `/api/hunt/status` 与实际狩猎结算共用，生效后面板与结算行为一致。

### [42] 突破成功率判定对齐修复（预览值与实际 RNG 完全一致）
- 记录时间：2026-03-22 19:40 (UTC+8)
- 影响范围：`core/services/settlement_extra.py`、`core/game/realms.py`、`tests/test_breakthrough_failure_message_consistency.py`。
- 修改摘要：
  - `settle_breakthrough` 调用 `attempt_breakthrough` 时新增传入 `forced_success_rate=shown_rate`，实际随机判定直接使用结算阶段已展示的最终成功率。
  - `attempt_breakthrough` 新增可选参数 `forced_success_rate`，在保留前置校验逻辑（修为/材料）前提下，避免重复计算导致展示与结算偏差。
  - `settlement_extra` 去除模块级配置快照，改为实时读取 `config.raw`，避免运行期配置重载后与其它模块读取源不一致。
  - 新增回归测试覆盖 `forced_success_rate` 行为，并补充“结算传递展示成功率到最终判定”的测试。

### [41] `/test` 扩展大量预设按钮（超管一键改资源/修为/境界/状态）
- 记录时间：2026-03-22 19:07 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`。
- 修改摘要：
  - `/test` 面板新增多组一键预设按钮：下/中/上/精品/极品灵石、修为、境界、精力、破境计数、满血满蓝、狩猎清零、PVP日清等。
  - 新增目标快捷按钮：`目标自己`、`清空目标`，并保留 `刷新` 与 `手动输入`。
  - 回调执行逻辑支持 `admin_test_quick_*` 预设直改主库，执行成功后自动保留/刷新当前目标并回显结果。
  - `/test` 命令参数扩展：支持 `UID 字段 值`（沿用当前操作），并与 `UID 操作 字段 值`、回复目标模式兼容。

### [40] Telegram 超管 `/test` 管理面板接入（无需 Web 即可改资源/修为/等级）
- 记录时间：2026-03-22 19:00 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`adapters/aiogram/legacy_bridge.py`。
- 修改摘要：
  - 新增超管命令 `/test`（兼容 `/xian_test`）作为游戏内管理员面板入口，支持 `set/add/minus` 修改玩家字段并直接写主库。
  - 新增 Telegram 回调面板：可在按钮中切换“设置/增加/扣减”，并通过 ForceReply 输入执行修改（支持回复目标或显式 UID/TG_ID）。
  - 支持命令直改：`/test <UID|TG_ID> <set|add|minus> <field> <value>`，并保留目标解析（UID/TG_ID 双通道）。
  - aiogram 兼容桥补充 `test/xian_test` 路由，确保当前运行模式下 `/test` 指令可触达 legacy 逻辑。

### [39] 管理员面板扩展为全资源/修为/境界管理（动态字段 + 实时回显）
- 记录时间：2026-03-22 18:42 (UTC+8)
- 影响范围：`core/admin/user_management.py`、`web_local/app.py`、`web_local/templates/admin.html`、`web_local/static/css/admin.css`、`web_local/i18n/zh.json`。
- 修改摘要：
  - 管理端可编辑字段扩展为资源、修炼、战斗、活跃、PVP 多分组，覆盖下中上品灵石、仙晶、修为、境界、精力、气血灵力、攻防、PVP统计等核心字段。
  - 新增后端接口 `/admin/field_options`，前端动态拉取当前数据库可编辑字段，只展示真实存在列，避免历史库结构不一致导致失败。
  - `/admin/modify_user` 成功后回传最新用户快照，管理页新增“玩家关键数据总览”并在修改后实时刷新。
  - 强化管理员写入安全：字段白名单、字段存在校验、数值解析校验、非负字段减法下限保护（最低 0）。

## 2026-03-21

### [38] MiniApp 狩猎参数缺失修复（默认补齐 monster_id）
- 记录时间：2026-03-21 20:24 (UTC+8)
- 影响范围：`xiuxian-web/src/views/Hunt.vue`、`xiuxian-web/src/api/client.ts`。
- 修改摘要：
  - 修复 MiniApp 点击“狩猎”时报 `MISSING_PARAMS` 的问题：前端请求中 `monster_id` 为空。
  - `Hunt.vue` 新增默认怪物探测逻辑：先调用 `/api/monsters` 获取可挑战目标，缓存 `monster_id`，再发起回合战斗和快速狩猎。
  - `api/client.ts` 中 `hunt` / `turnStart` 调整为显式传递 `monster_id`，并为 `getMonsters` 增加可选 `user_id` 参数。
  - 前端产物已重新构建并同步到 `/var/www/xiuxian-web`。

### [37] MiniApp 按钮兼容策略调整（禁止降级到外部网页）
- 记录时间：2026-03-21 20:13 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`adapters/aiogram/legacy_bridge.py`、`tests/test_aiogram_legacy_bridge.py`。
- 修改摘要：
  - 针对 `BUTTON_TYPE_INVALID`，不再把 `web_app` 按钮降级为 URL 网页按钮，改为降级到 `miniapp_private_hint` 回调提示（引导私聊打开 MiniApp）。
  - Telegram 发送入口 `_reply_text` 新增按钮降级重试，避免 `/start` 直接回“服务器错误，请稍后重试”。
  - Chat Menu 按钮在配置了 `miniapp.url` 时改为 `MenuButtonWebApp`，提供稳定的 MiniApp 入口。
  - 更新桥接回归测试，覆盖“web_app 失败后降级为回调提示”的行为。

### [36] WebApp 按钮兼容降级（修复 BUTTON_TYPE_INVALID 导致的“服务器错误”）
- 记录时间：2026-03-21 20:04 (UTC+8)
- 影响范围：`adapters/aiogram/legacy_bridge.py`、`tests/test_aiogram_legacy_bridge.py`。
- 修改摘要：
  - 兼容桥发送/编辑消息时，若 Telegram 返回 `BUTTON_TYPE_INVALID`，自动将 `web_app` 按钮降级为同 URL 的普通按钮后重试。
  - 覆盖 `_CompatBot.send_message`、`_CompatMessage.reply_text`、`_CompatCallbackQuery.edit_message_text` 三条链路，避免 /start 等入口直接报“服务器错误，请稍后重试”。
  - 新增回归测试，验证 `web_app` 保留、可降级与失败后自动重试。

### [35] aiogram 兼容桥修复 WebApp 按钮丢失（进入修仙世界不再退化为状态）
- 记录时间：2026-03-21 19:55 (UTC+8)
- 影响范围：`adapters/aiogram/legacy_bridge.py`、`tests/test_aiogram_legacy_bridge.py`。
- 修改摘要：
  - 兼容桥 `InlineKeyboardButton` 转换补齐 `web_app` 字段，正确映射为 aiogram 的 `WebAppInfo`。
  - 修复“🏯 进入修仙世界”按钮被降级为 `main_menu` 回调后只刷新状态、不打开 MiniApp 的问题。
  - 新增回归测试覆盖 `web_app` 透传，防止后续迁移回归。

### [34] MiniApp 404 修复（Nginx root 与部署目录自动同步）
- 记录时间：2026-03-21 19:12 (UTC+8)
- 影响范围：`setup.sh`、`/etc/nginx/sites-available/xiuxian`、`/etc/nginx/sites-available/default`。
- 修改摘要：
  - 修复线上 Nginx `root` 指向 `/var/www/xiuxian-web/dist` 但实际部署到 `/var/www/xiuxian-web` 导致首页 404 的问题。
  - `setup.sh` 新增 root 同步步骤：读取 `xiuxian-web/deploy.sh` 的 `WEB_DIR`，自动对齐 Nginx 站点路径并重载。
  - 追加备份逻辑，调整 Nginx 配置前自动生成带时间戳的备份文件。

### [33] setup 一键部署改为调用 xiuxian-web build/deploy 链路
- 记录时间：2026-03-21 18:26 (UTC+8)
- 影响范围：`setup.sh`。
- 修改摘要：
  - `setup.sh` 改为总控脚本：依赖检查后自动执行 `xiuxian-web/build.sh` 与 `xiuxian-web/deploy.sh`。
  - 支持 `all` / `update-web` / `update-gw` 参数透传到 `deploy.sh`。
  - Go 版本策略调整为 `max(1.25.6, xiuxian-web/gateway/go.mod)`，版本不足时自动升级。

### [32] 按钮“无响应”兜底修复（回调超时可见提示）
- 记录时间：2026-03-21 10:55 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`adapters/aiogram/legacy_bridge.py`、`tests/test_telegram_panel_ownership.py`、`tests/test_aiogram_legacy_bridge.py`。
- 修改摘要：
  - 回调应答 `_safe_answer` 改为返回状态；当 Telegram 返回“query is too old”时，不再静默失败。
  - 对“不是你的面板/面板已失效”两类拦截场景，新增聊天消息兜底回复，避免用户体感“点了没反应”。
  - aiogram 兼容桥接补齐 `reply_to_message` 透传，修复 owner 推断丢失导致的权限判定不稳定。
  - 新增对应单测，覆盖回调超时时的可见反馈和桥接字段透传。

### [31] Telegram 面板所有权校验加固（修复可点击他人面板）
- 记录时间：2026-03-21 10:23 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`tests/test_telegram_panel_ownership.py`。
- 修改摘要：
  - 回调入口新增“无缓存时的面板 owner 推断”（优先用 `reply_to_message.from_user.id`，私聊回退 `chat.id`）。
  - owner 推断成功后立即绑定到 `panel_owners`，并在回调处理前统一拒绝非 owner 点击。
  - `_safe_edit` 成功编辑后同步回填 owner 绑定，避免消息编辑链路丢失权限信息。
  - 新增 3 条测试覆盖 owner 推断与越权点击拦截。

### [30] Core 启动导入错误修复（恢复 execute_query 兼容别名）
- 记录时间：2026-03-21 10:07 (UTC+8)
- 影响范围：`core/database/connection.py`。
- 修改摘要：
  - 补回 `execute_query` 兼容函数并复用现有 `execute` 实现，修复路由模块导入时的符号缺失。
  - 消除 `cannot import name 'execute_query' from core.database.connection` 导致的 Core 启动失败。
  - 让 `travel/sect/audit` 路由注册可正常完成，避免 Telegram 侧出现 `127.0.0.1:11450` 连接失败连锁报错。

### [29] 商店支持输入数量批量购买
- 记录时间：2026-03-21 02:07 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`。
- 修改摘要：
  - 商店点击商品后不再直接购买 1 个，改为弹出输入框让玩家输入购买数量（1-999）。
  - 新增购买数量输入状态处理，支持“取消购买”、非法数量重试、下单后返回商店分类。
  - 增加商店分类记忆，批量购买后“返回商店”保持当前分类视图。

### [28] 炼丹/锻造 Internal Server Error 修复（结构自检 + 异常兜底）
- 记录时间：2026-03-21 01:55 (UTC+8)
- 影响范围：`core/services/alchemy_service.py`、`core/services/forge_service.py`。
- 修改摘要：
  - 炼丹与锻造服务新增数据库结构自检（缺表/缺列自动补齐），降低历史库结构不一致导致的 500。
  - 炼丹新增异常兜底，返回业务错误码 `ALCHEMY_SERVER_ERROR`，避免直接抛出通用内部错误。
  - 锻造/定向锻造/分解新增异常兜底，分别返回 `FORGE_SERVER_ERROR`、`FORGE_TARGETED_SERVER_ERROR`、`DECOMPOSE_SERVER_ERROR`。
  - 锻造图鉴查询新增保护，异常时返回空列表，避免面板链路因单点异常中断。

### [27] 背包与装备面板拆分优化 + 成就奖励展示补全
- 记录时间：2026-03-21 01:22 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`。
- 修改摘要：
  - 背包中非装备物品按同类合并显示数量；装备独立为“装备背包”分页展示。
  - 装备不合并，单件保留“装备/强化/分解”按钮，且操作后可直接回到装备背包。
  - 已装备面板与背包跳转链路调整为“物品背包/装备背包”双入口。
  - 成就面板补充每条成就奖励显示；领取成就后新增奖励明细回显。

### [26] aiogram 适配器切换与兼容桥接接入
- 记录时间：2026-03-21 00:40 (UTC+8)
- 影响范围：`config.json`、`adapters/aiogram/bot.py`、`adapters/aiogram/legacy_bridge.py`、`adapters/aiogram/__init__.py`、`adapters/aiogram/ui.py`、`adapters/aiogram/services/api_client.py`、`adapters/aiogram/handlers/p1.py`、`adapters/aiogram/states/p1.py` 及对应 `__init__.py`。
- 修改摘要：
  - 适配器开关切换为 `aiogram=true`、`telegram=false`，避免双轮询抢占同一 Token。
  - aiogram 主入口改为加载 legacy bridge 路由，以兼容承接原 Telegram 逻辑。
  - 新增 aiogram 目录结构与基础模块（服务层、状态机、UI、P1 handlers）作为后续原生 FSM 迁移基底。

## 2026-03-20

### [25] 狩猎失败新增虚弱状态（可配置）
- 记录时间：2026-03-20 12:38 (UTC+8)
- 影响范围：`core/services/turn_battle_service.py`、`core/services/settlement.py`、`config.json`、`web_local/static/i18n/config_fields_zh.json`、`tests/test_combat_service_fixes.py`。
- 修改摘要：
  - 狩猎战斗失败后，角色保留 1 点 HP 的同时进入虚弱状态（写入 `weak_until`）。
  - 新增配置项 `balance.hunt.defeat_weak_seconds`（默认 `1800` 秒），支持 Web 配置页调整。
  - 失败返回文案增加虚弱时长提示，并在响应中返回 `weak_seconds`、`weak_until`。
  - 补充回归测试，覆盖“狩猎失败会进入虚弱状态”。

### [24] 资源称呼统一（铜/金 -> 下品灵石/中品灵石）
- 记录时间：2026-03-20 12:31 (UTC+8)
- 影响范围：`adapters/telegram/bot.py`、`core/game/quests.py`、`core/services/balance_service.py`、`core/services/settlement.py`、`core/database/schemas.py`、`web_local/templates/config.html`、`web_local/templates/admin.html`、`web_local/templates/database.html`、`web_local/i18n/zh.json`、`web_local/static/i18n/config_fields_zh.json`。
- 修改摘要：
  - Telegram 玩家可见文案统一替换：抽奖、商店、宗门资金、任务奖励中的“铜/金/金币池”改为“下品灵石/中品灵石”。
  - Web 管理端字段与配置翻译统一替换：`铜币/金币/元宝` 改为 `下品灵石/中品灵石`。
  - 任务状态格式化文案同步替换，避免旧称呼在非 Telegram 展示链路残留。
  - 相关注释文案同步改为灵石称呼；`social_service` 中保留旧称呼仅作输入兼容，不参与展示。

### [23] 狩猎“已有进行中的战斗”误报与卡会话修复
- 记录时间：2026-03-20 12:17 (UTC+8)
- 影响范围：`core/services/turn_battle_service.py`、`tests/test_combat_service_fixes.py`。
- 修改摘要：
  - 狩猎开始时若已存在同类型会话，改为直接恢复原会话并返回战斗面板数据，不再报错打断。
  - 修复狩猎结算异常时会话残留问题：异常时强制清理会话，避免后续一直提示“已有进行中的战斗”。
  - 补充测试覆盖“重复开局恢复会话”和“结算异常后可重新开局”。

### [22] 突破成功后状态恢复规则调整（不再回满）
- 记录时间：2026-03-20 12:11 (UTC+8)
- 影响范围：`core/services/settlement_extra.py`、`config.json`、`web_local/static/i18n/config_fields_zh.json`。
- 修改摘要：
  - 突破成功后 HP/MP 改为“当前值 + 新上限 x 恢复比例”并封顶，不再直接回满。
  - 新增配置项 `balance.breakthrough.post_breakthrough_restore_ratio`（默认 `0.3`）。
  - Web 配置中文字段映射补齐，支持在前端调整突破后恢复比例。

### [21] 技能蓝耗体系重构（动态耗蓝 + 回蓝收紧）
- 记录时间：2026-03-20 12:01 (UTC+8)
- 影响范围：`core/game/skills.py`、`core/game/combat.py`、`core/services/turn_battle_service.py`、`core/database/connection.py`、`config.json`、`web_local/static/i18n/config_fields_zh.json`、`adapters/telegram/bot.py`。
- 修改摘要：
  - 主动技能蓝耗改为“基础耗蓝 + 按最大MP比例动态提高（取较高值）”。
  - 新增蓝耗档位配置（基础/爆发/终极），并接入 Web 可配字段映射。
  - 回元术/灵气转换回蓝数值下调，抑制高境界无限蓝循环。
  - HP/MP 自动恢复改为配置驱动，默认从每分钟 10% 下调至 3%，Telegram 文案改为动态显示配置值。

### [20] 论道防重复修复（同目标日内仅可发起一次）
- 记录时间：2026-03-20 11:40 (UTC+8)
- 影响范围：`core/services/social_service.py`、`tests/test_social_sect_review_fixes.py`。
- 修改摘要：
  - 论道发起增加“同一发起者 -> 同一目标”日内唯一校验。
  - 增加事务内 `pg_advisory_xact_lock`，修复并发下同目标重复发起。
  - 重复发起返回统一错误码：`CHAT_TARGET_DAILY_LIMIT`。

### [19] 变更日志规范化与开发流程手册补齐
- 记录时间：2026-03-20 11:33 (UTC+8)
- 影响范围：`CHANGELOG.md`、`开发修改流程手册.md`、日志文件命名规范。
- 修改摘要：
  - 将本地临时日志 `LOCAL_BUGFIX_LOG.md` 正式更名为 `CHANGELOG.md`。
  - 日志结构统一为“最新在前”，并补齐每条记录的时间字段。
  - 新增《开发修改流程手册》，强制约束“新增功能可配置项接入 Web + 变更写入日志”的流程。

### [18] 可配置项扩展到 Web（运营参数集中管理）
- 记录时间：2026-03-20 11:20 (UTC+8)
- 影响范围：`config.json`、核心服务、Telegram 适配层、Web 配置字段映射。
- 修改摘要：
  - 新增并接入 `gacha`、`pvp`、`social`、`sect`、`secret_realm`、`economy`、`battle.kernel`、`events.world_boss` 等配置组。
  - 关键结算逻辑改为读配置，降低硬编码导致的维护成本。
  - 抽奖/宗门/突破相关展示文案改为与配置值联动，避免“显示值”和“实际值”不一致。

### [17] 文案与配置值联动（突破失败）
- 记录时间：2026-03-20 10:40 (UTC+8)
- 影响范围：突破结算与提示文案。
- 修改摘要：
  - 移除“损失10%/虚弱1小时”写死文案。
  - 失败提示按实际 `exp_lost_pct` 与 `weak_seconds` 动态生成。

### [16] 突破硬保底阈值支持前端配置
- 记录时间：2026-03-20 10:10 (UTC+8)
- 影响范围：突破保底策略、Web 配置。
- 修改摘要：
  - 硬保底阈值支持通过 `config.json` 调整。
  - Web 中文字段映射补齐，可直接在配置页维护。

## 2026-03-19

### [15] 测试脚本 PostgreSQL 兼容改造（tests + scripts）
- 记录时间：2026-03-19 22:30 (UTC+8)
- 影响范围：测试基建与冒烟脚本。
- 修改摘要：
  - 测试流程改为 PostgreSQL 兼容，补齐清库安全保护。
  - 冒烟脚本统一走 PostgreSQL 连接配置。
  - 文档增加 PostgreSQL 运行说明。

### [14] 世界Boss/活动审查问题修复（并发与健壮性）
- 记录时间：2026-03-19 21:10 (UTC+8)
- 影响范围：世界Boss、活动积分兑换、事件引擎。
- 修改摘要：
  - 世界Boss 攻击改为行锁流程，修复并发重复结算。
  - 活动积分兑换改为并发安全结算，补齐参数校验。
  - 事件引擎条件判断增强类型容错。

### [13] 社交/宗门/任务审查问题修复（原子性与输入校验）
- 记录时间：2026-03-19 19:40 (UTC+8)
- 影响范围：论道、悬赏、宗门、成就、Telegram 交互。
- 修改摘要：
  - 关键流程改为事务内回滚，减少半成功状态。
  - 宗门与悬赏输入校验增强，修复字符串布尔误判等问题。
  - 成就查询与领取流程优化，避免重复查询与到账异常。

### [12] 经济系统审查问题修复（并发/幂等/配置安全）
- 记录时间：2026-03-19 18:20 (UTC+8)
- 影响范围：转化、炼丹、抽卡、锻造、货币兑换。
- 修改摘要：
  - 扣材与保底流程改为并发安全操作。
  - 关键接口接入 `request_id` 幂等。
  - 配置校验增强并统一业务成功语义埋点。

### [11] 战斗/狩猎审查问题修复（稳定性与交互完整性）
- 记录时间：2026-03-19 16:50 (UTC+8)
- 影响范围：回合战、会话管理、Telegram 秘境交互。
- 修改摘要：
  - 会话并发保护增强，ID 改为高熵生成。
  - 回合 action 支持幂等，修复重复点击推进问题。
  - 秘境交互补齐分支流程，统一 `/hunt` 入口链路。

### [10] 修炼/突破审查问题修复（并发与提示一致性）
- 记录时间：2026-03-19 15:30 (UTC+8)
- 影响范围：修炼、突破、试炼进度。
- 修改摘要：
  - 修炼开始/结束改为原子流程，降低重复结算风险。
  - 试炼进度更新改为并发安全，异常不再静默。
  - 增加突破预览接口，降低前后端规则漂移。

### [9] 世界 Boss 文案与血量调整
- 记录时间：2026-03-19 14:40 (UTC+8)
- 影响范围：Boss 配置与交互文案。
- 修改摘要：
  - 奖励文案统一为“下品灵石/中品灵石”。
  - 世界 Boss 最大血量下调并支持状态同步。

### [8] 材料/道具显示中文统一
- 记录时间：2026-03-19 14:10 (UTC+8)
- 影响范围：锻造、转化、悬赏、抽奖显示层。
- 修改摘要：
  - 清理内部 ID 直出，统一显示中文名称。

### [7] 论道修为收益优化
- 记录时间：2026-03-19 13:40 (UTC+8)
- 影响范围：社交结算、Telegram 论道反馈。
- 修改摘要：
  - 论道收益改为随境界增长，不再固定低值。
  - 面板增加双方实际收益展示。

### [6] 境界试炼拦截提示优化
- 记录时间：2026-03-19 13:20 (UTC+8)
- 影响范围：突破入口提示。
- 修改摘要：
  - 条件不足时明确提示缺失任务与剩余进度。

### [5] 免费抽奖逻辑修复
- 记录时间：2026-03-19 12:55 (UTC+8)
- 影响范围：抽奖服务、Telegram 抽奖交互。
- 修改摘要：
  - 免费次数用尽后不再自动回退为付费抽奖。

### [4] 突破失败虚弱机制完善
- 记录时间：2026-03-19 12:30 (UTC+8)
- 影响范围：状态面板、宗门加成、战斗。
- 修改摘要：
  - 虚弱状态展示补齐剩余时间与 debuff。
  - 虚弱期间属性临时下降，结束后自动恢复。

### [3] 狩猎面板精简展示
- 记录时间：2026-03-19 12:05 (UTC+8)
- 影响范围：狩猎列表与 Telegram 面板。
- 修改摘要：
  - 随境界提升仅显示最新可挑战怪物，减少面板拥挤。

### [2] 世界 Boss 状态获取失败
- 记录时间：2026-03-19 11:40 (UTC+8)
- 影响范围：活动与世界 Boss 服务。
- 修改摘要：
  - 修复旧表结构与并发首访冲突问题。

### [1] 狩猎奖励按怪物强度缩放
- 记录时间：2026-03-19 11:10 (UTC+8)
- 影响范围：平衡、结算、回合战斗。
- 修改摘要：
  - 奖励计算改为同时参考境界与怪物强度。
