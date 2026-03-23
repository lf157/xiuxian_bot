# aiogram + FSM 全量接管迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Telegram 交互从 `adapters/telegram/bot.py` 的嵌套分发彻底迁移到原生 `aiogram Router + FSM + RedisStorage`，移除 `legacy_bridge` 运行链路。

**Architecture:** 采用“按业务域拆分 handler/state + 共享 services/ui 契约”的结构，所有 callback 走显式 `<domain>:<action>` 白名单。入口层强制使用 `RedisStorage`，通过 `xxbot:fsm:v2:` 前缀隔离旧态并支持受控清理历史 FSM key。命令入口在 aiogram 侧保持旧命令集合兼容，但旧消息按钮不兼容。

**Tech Stack:** Python 3.11, aiogram 3.x, aiohttp, redis-py (RedisStorage), ripgrep

---

## File Map

### Core Entry / Config
- Modify: `adapters/aiogram/bot.py`
- Modify: `core/config.py`
- Modify: `config.json`
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

### aiogram Routers
- Create: `adapters/aiogram/handlers/menu_account.py`
- Create: `adapters/aiogram/handlers/cultivation.py`
- Create: `adapters/aiogram/handlers/hunt.py`
- Create: `adapters/aiogram/handlers/breakthrough.py`
- Create: `adapters/aiogram/handlers/inventory_equipment.py`
- Create: `adapters/aiogram/handlers/skills.py`
- Create: `adapters/aiogram/handlers/shop_alchemy_forge.py`
- Create: `adapters/aiogram/handlers/secret_realms.py`
- Create: `adapters/aiogram/handlers/social_pvp_sect.py`
- Create: `adapters/aiogram/handlers/story_events_quests.py`
- Create: `adapters/aiogram/handlers/admin.py`
- Modify: `adapters/aiogram/handlers/__init__.py`

### aiogram FSM States
- Create: `adapters/aiogram/states/common.py`
- Create: `adapters/aiogram/states/combat.py`
- Create: `adapters/aiogram/states/inventory.py`
- Create: `adapters/aiogram/states/economy.py`
- Create: `adapters/aiogram/states/realms.py`
- Create: `adapters/aiogram/states/social_admin.py`
- Modify: `adapters/aiogram/states/__init__.py`

### aiogram Shared Services/UI
- Create: `adapters/aiogram/services/callback_protocol.py`
- Create: `adapters/aiogram/services/navigation.py`
- Modify: `adapters/aiogram/services/api_client.py`
- Modify: `adapters/aiogram/services/__init__.py`
- Modify: `adapters/aiogram/ui.py`

### Deletions / Docs
- Delete: `adapters/aiogram/legacy_bridge.py`
- Delete: `adapters/aiogram/handlers/p1.py`
- Delete: `adapters/aiogram/states/p1.py`
- Modify: `CHANGELOG.md`

## Parallel Ownership Matrix (Hard Boundary)
- Agent A: `adapters/aiogram/bot.py`, `adapters/aiogram/services/*`, `core/config.py`, `config.json`, `requirements.txt`, `pyproject.toml`
- Agent B: `adapters/aiogram/handlers/menu_account.py`, `adapters/aiogram/states/common.py`
- Agent C: `adapters/aiogram/handlers/cultivation.py`, `adapters/aiogram/handlers/hunt.py`, `adapters/aiogram/states/combat.py`
- Agent D: `adapters/aiogram/handlers/breakthrough.py`, `adapters/aiogram/handlers/inventory_equipment.py`, `adapters/aiogram/handlers/skills.py`, `adapters/aiogram/states/inventory.py`
- Agent E: `adapters/aiogram/handlers/shop_alchemy_forge.py`, `adapters/aiogram/handlers/story_events_quests.py`, `adapters/aiogram/states/economy.py`
- Agent F: `adapters/aiogram/handlers/secret_realms.py`, `adapters/aiogram/states/realms.py`
- Agent G: `adapters/aiogram/handlers/social_pvp_sect.py`, `adapters/aiogram/handlers/admin.py`, `adapters/aiogram/states/social_admin.py`
- Main Controller Only: `adapters/aiogram/handlers/__init__.py`, `adapters/aiogram/states/__init__.py`, `adapters/aiogram/ui.py`, deletions, `CHANGELOG.md`

Conflict rule:
- Agents must not edit files outside their ownership.
- Shared contract changes (`callback_protocol.py`, `navigation.py`, `api_client.py`, `ui.py`) only by Agent A or Main Controller.
- Task 4-9 的域 handler 只消费 `ui.py` 契约，不直接修改 `ui.py`。

## Task 1: 建立 callback 协议与通用导航契约（Agent A）

**Files:**
- Create: `adapters/aiogram/services/callback_protocol.py`
- Create: `adapters/aiogram/services/navigation.py`
- Modify: `adapters/aiogram/services/__init__.py`
- Modify: `adapters/aiogram/services/api_client.py`

- [ ] **Step 1: 创建显式 callback 白名单与解析器**

```python
CALLBACK_ACTIONS: dict[str, set[str]] = {
    "menu": {"home", "register", "stat", "back"},
    "cul": {"start", "status", "end"},
    # ...按设计稿 4.3 全量枚举
}

def parse_callback(data: str) -> tuple[str, str, list[str]] | None:
    # 限制: len(data.encode("utf-8")) <= 64
    # domain/action: [a-z0-9_]+
    # arg: [A-Za-z0-9_-]+
    # 仅允许 <domain>:<action>[:arg...]
    ...
```

- [ ] **Step 2: 实现未知/过期按钮统一响应函数**

```python
async def handle_expired_callback(query: CallbackQuery) -> None:
    await safe_answer(query, text="按钮已过期，请重新打开菜单", show_alert=False)
    await respond_query(query, "该消息按钮已失效，请点击下方主菜单继续。", reply_markup=main_menu_keyboard(registered=True))
```

- [ ] **Step 3: API client 补齐 spec 契约签名**

```python
async def api_get(path, params=None, actor_uid=None) -> dict: ...
async def api_post(path, payload, actor_uid=None, request_id=None) -> dict: ...
```

- [ ] **Step 4: 运行静态编译检查**

Run: `python -m compileall adapters/aiogram/services`
Expected: 无 `SyntaxError`

- [ ] **Step 5: Commit**

```bash
git add adapters/aiogram/services
git commit -m "refactor(aiogram): add callback protocol and navigation contracts"
```

## Task 2: RedisStorage 强制接入与配置读取（Agent A）

**Files:**
- Modify: `adapters/aiogram/bot.py`
- Modify: `core/config.py`
- Modify: `config.json`
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: 在配置层新增 Redis/FSM 读取接口**

```python
@property
def redis_url(self) -> str: ...
@property
def redis_enabled(self) -> bool: ...
@property
def redis_fsm_key_prefix(self) -> str: ...
@property
def redis_purge_legacy_fsm_prefixes(self) -> bool: ...
```

- [ ] **Step 2: 写入 `config.json` 默认 Redis 配置**

```json
"redis": {
  "enabled": true,
  "url": "",
  "host": "127.0.0.1",
  "port": 6379,
  "db": 0,
  "password": "",
  "fsm_key_prefix": "xxbot:fsm:v2:",
  "purge_legacy_fsm_prefixes": false
}
```

- [ ] **Step 3: bot 入口切换为 RedisStorage 并移除 MemoryStorage**

```python
storage = RedisStorage.from_url(redis_url, key_builder=DefaultKeyBuilder(with_destiny=False, prefix=config.redis_fsm_key_prefix))
try:
    await storage.redis.ping()  # 连接失败直接抛错，fail-fast
except Exception as exc:
    raise RuntimeError(f"RedisStorage unavailable: {exc}") from exc
dispatcher = Dispatcher(storage=storage, fsm_strategy=FSMStrategy.USER_IN_CHAT)
```

- [ ] **Step 4: 增加旧态清理逻辑（受控开关）**

Runbook:
1. 仅当 `redis_purge_legacy_fsm_prefixes` 为 `true` 执行 `SCAN xxbot:fsm:*`
2. 删除非 `xxbot:fsm:v2:*` 键
3. 记录 `legacy_fsm_purge_deleted=<count>`

- [ ] **Step 5: shutdown 显式关闭 storage/redis 与 HTTP 会话**

```python
finally:
    await dispatcher.storage.close()
    await close_http_session()
    await bot.session.close()
```

- [ ] **Step 6: 更新依赖**

Add: `redis>=5.0` 到 `requirements.txt` 与 `pyproject.toml`

- [ ] **Step 7: 运行静态检查**

Run: `rg -n "RedisStorage|RuntimeError\\(|storage\\.close\\(|close_http_session\\(|MemoryStorage|legacy_router|legacy_bridge" adapters/aiogram/bot.py`
Expected:
- 存在 `RedisStorage` / `RuntimeError` / `storage.close()` / `close_http_session()`
- 不存在 `MemoryStorage` / `legacy_router` / `legacy_bridge`

- [ ] **Step 8: Commit**

```bash
git add adapters/aiogram/bot.py core/config.py config.json requirements.txt pyproject.toml
git commit -m "feat(aiogram): enforce redis fsm storage and redis config"
```

## Task 3: 构建全域 UI 契约骨架（Main Controller）

**Files:**
- Modify: `adapters/aiogram/ui.py`
- Modify: `adapters/aiogram/states/__init__.py`（仅保留导出占位注释，不新增域实现）

- [ ] **Step 1: 在 `ui.py` 集中实现全域键盘与文本函数骨架**

```python
def main_menu_keyboard(...): ...
def hunt_battle_keyboard(...): ...
def breakthrough_keyboard(...): ...
# ...全域 UI 契约函数
```

- [ ] **Step 2: 在 `states/__init__.py` 写明统一导出约定（不跨代理抢写状态文件）**

示例：

```python
# Domain FSM modules are owned by their domain agents.
# Main controller only updates final exports during Task 10.
```

- [ ] **Step 3: 静态检查 UI 契约骨架**

Run: `rg -n "def .*_keyboard\\(|def format_" adapters/aiogram/ui.py`
Expected: UI 契约函数骨架已存在

- [ ] **Step 4: Commit**

```bash
git add adapters/aiogram/ui.py adapters/aiogram/states/__init__.py
git commit -m "refactor(aiogram): scaffold shared ui contracts"
```

## Task 4: 菜单与命令入口迁移（Agent B）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/menu_account.py`
- Create: `adapters/aiogram/states/common.py`

- [ ] **Step 1: 实现菜单主路由与账号相关回调**

Commands:
- `/xian_start`, `/start`
- `/xian_register`, `/register`
- `/xian_stat`, `/xian_status`, `/stat`, `/status`
- `/xian_version`, `/version`

Callbacks:
- `menu:home/register/stat/back`

- [ ] **Step 2: 使用统一 UI 契约接入“储物袋/灵装”一级入口（不修改 `ui.py`）**

```python
builder.button(text="🎒 储物袋", callback_data="bag:page:0")
builder.button(text="👕 灵装", callback_data="gear:page:0")
```

- [ ] **Step 3: 所有 query 先 `safe_answer` 再 `respond_query`**

Run: `rg -n "callback_query|query.answer|respond_query" adapters/aiogram/handlers/menu_account.py`
Expected: 回调路径使用统一导航函数

- [ ] **Step 4: 静态编译**

Run: `python -m compileall adapters/aiogram/handlers/menu_account.py`
Expected: 无 `SyntaxError`

- [ ] **Step 5: Commit**

```bash
git add adapters/aiogram/handlers/menu_account.py adapters/aiogram/states/common.py
git commit -m "feat(aiogram): migrate menu and account command entrypoints"
```

## Task 5: 修炼/狩猎迁移（Agent C）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/cultivation.py`
- Create: `adapters/aiogram/handlers/hunt.py`
- Create: `adapters/aiogram/states/combat.py`

- [ ] **Step 1: 修炼域实现**

Commands:
- `/xian_cul`, `/xian_cultivate`, `/cul`, `/cultivate`

Callbacks:
- `cul:start/status/end`

- [ ] **Step 2: 狩猎域实现（会话 + 战斗动作）**

Commands:
- `/xian_hunt`, `/hunt`

Callbacks:
- `hunt:list/start/act_normal/act_skill/settle/exit`

- [ ] **Step 3: 异常回退符合 FSM 设计**

Run: `rg -n "session|失效|clear\\(|set_state\\(" adapters/aiogram/handlers/cultivation.py adapters/aiogram/handlers/hunt.py`
Expected: 丢会话路径可回入口状态

- [ ] **Step 4: 静态编译**

Run: `python -m compileall adapters/aiogram/handlers/cultivation.py adapters/aiogram/handlers/hunt.py adapters/aiogram/states/combat.py`
Expected: 无 `SyntaxError`

- [ ] **Step 5: Commit**

```bash
git add adapters/aiogram/handlers/cultivation.py adapters/aiogram/handlers/hunt.py adapters/aiogram/states/combat.py
git commit -m "feat(aiogram): migrate cultivation and hunt domains"
```

## Task 6: 突破 + 储物袋/灵装/技能迁移（Agent D）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/breakthrough.py`
- Create: `adapters/aiogram/handlers/inventory_equipment.py`
- Create: `adapters/aiogram/handlers/skills.py`
- Create: `adapters/aiogram/states/inventory.py`

- [ ] **Step 1: 突破域实现**

Commands:
- `/xian_break`, `/xian_breakthrough`, `/break`, `/breakthrough`

Callbacks:
- `break:preview/help_toggle/confirm/cancel`

- [ ] **Step 2: 储物袋域实现**

Commands:
- `/xian_bag`, `/xian_inventory`, `/bag`, `/inventory`

Callbacks:
- `bag:page/use/detail`

- [ ] **Step 3: 灵装域实现**

Callbacks:
- `gear:page/equip/enhance/decompose/equipped_view/unequip/detail`

Rules:
- 强化/分解/装备后回 `gear_browsing`
- 文案统一为“灵装”，不出现“装备背包”

- [ ] **Step 4: 技能域实现**

Commands:
- `/xian_skills`, `/xian_skill`, `/skills`, `/skill`

Callbacks:
- `skill:list/learn/equip/unequip/detail`

- [ ] **Step 5: 静态检查（命名 + callback 协议）**

Run: `rg -n "装备背包|equipbag_|break:|bag:|gear:|skill:" adapters/aiogram/handlers/breakthrough.py adapters/aiogram/handlers/inventory_equipment.py adapters/aiogram/handlers/skills.py`
Expected:
- 无“装备背包/equipbag_”旧命名
- 新 callback 使用 `break:` / `bag:` / `gear:` / `skill:`

- [ ] **Step 6: Commit**

```bash
git add adapters/aiogram/handlers/breakthrough.py adapters/aiogram/handlers/inventory_equipment.py adapters/aiogram/handlers/skills.py adapters/aiogram/states/inventory.py
git commit -m "feat(aiogram): migrate breakthrough, storage bag, gear, and skills domains"
```

## Task 7: 商店/炼丹/锻造 + 剧情活动任务迁移（Agent E）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/shop_alchemy_forge.py`
- Create: `adapters/aiogram/handlers/story_events_quests.py`
- Create: `adapters/aiogram/states/economy.py`

- [ ] **Step 1: 商店和货币入口**

Commands:
- `/xian_shop`, `/shop`
- `/xian_currency`, `/currency`
- `/xian_convert`, `/convert`
- `/xian_alchemy`, `/alchemy`

Callbacks:
- `shop:currency/page/buy/back/noop`
- `alchemy:menu/craft/batch/back`
- `forge:menu/craft/enhance/back`

- [ ] **Step 2: 剧情/活动/任务域**

Commands:
- `/xian_quest`, `/xian_quests`, `/xian_task`, `/quest`, `/quests`, `/task`
- `/xian_events`, `/events`
- `/xian_bounty`, `/bounty`
- `/xian_worldboss`, `/xian_boss`, `/worldboss`, `/boss`
- `/xian_rank`, `/xian_leaderboard`, `/rank`, `/leaderboard`
- `/xian_guide`, `/xian_realms`, `/guide`, `/realms`
- `/xian_achievements`, `/xian_ach`, `/achievements`, `/ach`
- `/xian_codex`, `/codex`

Callbacks:
- `quest:list/detail/claim`
- `event:list/detail/claim`
- `story:menu/chapter/node/claim`
- `boss:menu/attack/rank`
- `bounty:menu/refresh/claim`
- `rank:menu/realm/combat/wealth`

- [ ] **Step 3: 静态编译**

Run: `python -m compileall adapters/aiogram/handlers/shop_alchemy_forge.py adapters/aiogram/handlers/story_events_quests.py adapters/aiogram/states/economy.py`
Expected: 无 `SyntaxError`

- [ ] **Step 4: Commit**

```bash
git add adapters/aiogram/handlers/shop_alchemy_forge.py adapters/aiogram/handlers/story_events_quests.py adapters/aiogram/states/economy.py
git commit -m "feat(aiogram): migrate economy, story, event, and quest domains"
```

## Task 8: 秘境域迁移（Agent F）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/secret_realms.py`
- Create: `adapters/aiogram/states/realms.py`

- [ ] **Step 1: 秘境命令入口**

Commands:
- `/xian_secret`, `/xian_mystic`, `/secret`, `/mystic`

- [ ] **Step 2: 秘境 callback 路由与状态机**

Callbacks:
- `secret:list/realm/path/choice/act_normal/act_skill/settle/exit`

States:
- `selecting_realm -> selecting_path -> in_event_choice|in_battle -> settlement`

- [ ] **Step 3: 会话失效回退策略**

Run: `rg -n "secret_session_id|失效|set_state\\(" adapters/aiogram/handlers/secret_realms.py`
Expected: 会话失效时回 `selecting_realm`

- [ ] **Step 4: Commit**

```bash
git add adapters/aiogram/handlers/secret_realms.py adapters/aiogram/states/realms.py
git commit -m "feat(aiogram): migrate secret realms domain"
```

## Task 9: 社交/PVP/宗门/管理迁移（Agent G）

前置依赖：Task 3 完成（`ui.py` 契约骨架已就绪）。

**Files:**
- Create: `adapters/aiogram/handlers/social_pvp_sect.py`
- Create: `adapters/aiogram/handlers/admin.py`
- Create: `adapters/aiogram/states/social_admin.py`

- [ ] **Step 1: 社交与 PVP 命令**

Commands:
- `/xian_chat`, `/xian_dao`, `/chat`, `/dao`
- `/xian_pvp`, `/pvp`
- `/xian_sect`, `/sect`

Callbacks:
- `social:menu/chat/dao/friend/reply`
- `pvp:menu/match/duel/history/claim_daily/refresh`
- `sect:menu/create/info/members/contribute/donate/train/leave`

- [ ] **Step 2: 管理命令与回调**

Commands:
- `/test`, `/xian_test`
- `/xian_give_low`, `/xian_give_mid`, `/xian_give_high`, `/xian_give_uhigh`, `/xian_give_xhigh`

Callbacks:
- `admin:menu/test/lookup/modify/preset/confirm/cancel`

- [ ] **Step 3: 权限校验与回退**

Run: `rg -n "super_admin|admin|permission|无权限" adapters/aiogram/handlers/admin.py`
Expected: 非超管路径统一拒绝且回 `admin_menu`

- [ ] **Step 4: Commit**

```bash
git add adapters/aiogram/handlers/social_pvp_sect.py adapters/aiogram/handlers/admin.py adapters/aiogram/states/social_admin.py
git commit -m "feat(aiogram): migrate social pvp sect and admin domains"
```

## Task 10: 路由聚合、桥接下线与旧文件清理（Main Controller）

**Files:**
- Modify: `adapters/aiogram/handlers/__init__.py`
- Delete: `adapters/aiogram/legacy_bridge.py`
- Delete: `adapters/aiogram/handlers/p1.py`
- Delete: `adapters/aiogram/states/p1.py`
- Modify: `adapters/aiogram/states/__init__.py`

- [ ] **Step 1: 在 `handlers/__init__.py` 聚合所有域 router**

```python
root_router = Router(name="aiogram_root")
root_router.include_router(menu_router)
root_router.include_router(cultivation_router)
# ...全量 include
```

- [ ] **Step 2: 仅检查（不修改）`bot.py`，确认只 include `root_router`**

Run: `rg -n "legacy_bridge|legacy_router|include_router\\(" adapters/aiogram/bot.py adapters/aiogram/handlers/__init__.py`
Expected:
- `bot.py` 不含 `legacy_bridge/legacy_router`
- `handlers/__init__.py` 含全域 router include

- [ ] **Step 3: 删除 legacy/p1 文件**

Run: `git rm adapters/aiogram/legacy_bridge.py adapters/aiogram/handlers/p1.py adapters/aiogram/states/p1.py`
Expected: 三个文件删除暂存

- [ ] **Step 4: Commit**

```bash
git add adapters/aiogram/handlers/__init__.py adapters/aiogram/states/__init__.py
git commit -m "refactor(aiogram): remove legacy bridge and p1 compatibility layer"
```

## Task 11: 静态集成门禁（不执行测试）

**Files:**
- Verify only (no file changes expected)

- [ ] **Step 1: 去桥接引用检查**

Run: `rg -n "legacy_bridge|from adapters.telegram import bot as legacy_bot" adapters/aiogram adapters/aiogram/__init__.py start.py`
Expected: 无输出

- [ ] **Step 2: Router 注册检查**

Run: `rg -n "root_router|include_router\\(" adapters/aiogram/handlers adapters/aiogram/bot.py`
Expected: 全部域 router 已注册且 `bot.py` 只 include `root_router`

- [ ] **Step 3: callback 协议一致性检查**

Run: `rg -n "callback_data=|parse_callback|CALLBACK_ACTIONS|MAX_CALLBACK_BYTES|DOMAIN_RE|ACTION_RE|ARG_RE" adapters/aiogram`
Expected: callback 构造与解析走显式白名单，且存在 64-byte 与 regex 语法门禁

- [ ] **Step 4: RedisStorage 接入与生命周期检查**

Run: `rg -n "RedisStorage|fsm_key_prefix|purge_legacy_fsm_prefixes|XXBOT_REDIS_URL|RuntimeError\\(|storage\\.close\\(|close_http_session\\(" adapters/aiogram core/config.py config.json`
Expected: RedisStorage、配置键、fail-fast、storage close 与 HTTP session close 均可见

- [ ] **Step 5: 命令入口覆盖检查**

Run: `rg -n "router\\.message\\(Command|xian_" adapters/aiogram/handlers`
Expected: 设计稿 9.1 命令集合均有对应入口

- [ ] **Step 6: Python 语法编译检查**

Run: `python -m compileall adapters/aiogram core/config.py`
Expected: 无 `SyntaxError`

- [ ] **Step 7: 启动入口接线检查**

Run: `rg -n "is_adapter_enabled\\(\"aiogram\"\\)|adapters[/\\\\]aiogram[/\\\\]bot\\.py" start.py`
Expected: 启动器可解析并拉起 aiogram adapter 入口

## Task 12: 变更日志落盘（Main Controller）

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 记录迁移条目**

```markdown
### aiogram + FSM 全量迁移
- 移除 legacy_bridge 与 p1 兼容层
- 接入 RedisStorage（xxbot:fsm:v2）
- 新增分域 handlers/states/services
- callback 协议改为显式 domain/action 白名单
- 菜单与背包命名统一为“储物袋/灵装”
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): record aiogram fsm full migration"
```

## Final Integration Commit

- [ ] **Step 1: 汇总提交（若前面是分批提交，这步可跳过）**

```bash
git status --short
```

Expected:
- 仅剩本次迁移相关改动

- [ ] **Step 2: 生成最终说明（不跑测试）**

包含：
- 已完成任务列表
- 13.1 静态检查结果摘要
- 未执行测试的明确声明（按用户指令）

## Execution Notes
- 所有子代理统一使用：`gpt-5.3-codex` + `xhigh`
- 不执行 `pytest` 或任何自动化测试命令
- 如遇文件冲突，按 Ownership Matrix 回退并重新分发，不在冲突文件上强行合并
