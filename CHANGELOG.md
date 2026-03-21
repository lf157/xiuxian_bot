# 项目变更日志（Changelog）

- 最后更新：2026-03-21 18:26 (UTC+8)
- 本轮修复完成时间：2026-03-21 18:26 (UTC+8)
- 维护规则：新记录写在最前；每条记录必须包含“记录时间、影响范围、修改摘要”。

## 2026-03-21

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
