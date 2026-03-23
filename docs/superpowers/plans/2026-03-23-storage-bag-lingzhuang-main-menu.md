# 储物袋与灵装主界面拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“装备背包”从背包二级入口拆分为主界面一级入口“灵装”，并将“背包”更名为“储物袋”且只保留可堆叠物品。

**Architecture:** 仅修改 Telegram 适配层 UI 组装与回调分发，保持核心 API 与数据结构不变。通过新增 `equipment` 回调别名和文案统一，实现低风险迁移；继续保留 `equipbag_*` 兼容历史消息按钮。

**Tech Stack:** Python 3, python-telegram-bot, pytest

---

## File Map
- Modify: `adapters/telegram/bot.py`
  - 主菜单按钮文案与 callback 调整
  - `_build_bag_panel` 去装备化并改名“储物袋”
  - `_build_equipment_bag_panel` 改名“灵装”与跨页按钮文案
  - `callback_handler` 新增 `equipment` 路由到灵装分页
- Create: `tests/test_telegram_inventory_split_ui.py`
  - 覆盖主菜单命名、储物袋展示、灵装文案与按钮、`equipment` 入口兼容

### Task 1: 主菜单入口改造（储物袋 + 灵装）

**Files:**
- Modify: `adapters/telegram/bot.py`
- Test: `tests/test_telegram_inventory_split_ui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_main_menu_has_storage_and_equipment_entry():
    kb = telegram_bot.get_main_menu_keyboard(include_miniapp=False)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "🎒 储物袋" in labels
    assert "👕 灵装" in labels
    assert "bag" in callbacks
    assert "equipment" in callbacks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py::test_main_menu_has_storage_and_equipment_entry`
Expected: FAIL（当前主菜单仍是“🎒 背包”，且没有 `equipment`）

- [ ] **Step 3: Write minimal implementation**

```python
# get_main_menu_keyboard
[
    InlineKeyboardButton("🎒 储物袋", callback_data="bag"),
    InlineKeyboardButton("👕 灵装", callback_data="equipment"),
],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py::test_main_menu_has_storage_and_equipment_entry`
Expected: PASS

### Task 2: 储物袋去装备化

**Files:**
- Modify: `adapters/telegram/bot.py`
- Test: `tests/test_telegram_inventory_split_ui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bag_panel_shows_storage_only_without_equipment_entry():
    text, keyboard = telegram_bot._build_bag_panel(sample_items(), page=0)
    callbacks = [btn.callback_data for row in keyboard for btn in row]
    assert "*我的储物袋*" in text
    assert "装备数量" not in text
    assert "装备背包" not in text
    assert "equipbag_0" not in callbacks
    assert "equipped_view" not in callbacks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py::test_bag_panel_shows_storage_only_without_equipment_entry`
Expected: FAIL（当前文本包含“装备数量”并带“装备背包”按钮）

- [ ] **Step 3: Write minimal implementation**

```python
text = f"🎒 *我的储物袋* ..."
# 删除装备数量与装备入口按钮，仅保留储物袋与返回
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py::test_bag_panel_shows_storage_only_without_equipment_entry`
Expected: PASS

### Task 3: 灵装命名统一 + equipment 入口分发

**Files:**
- Modify: `adapters/telegram/bot.py`
- Test: `tests/test_telegram_inventory_split_ui.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_equipment_panel_uses_lingzhuang_copy():
    text, keyboard = telegram_bot._build_equipment_bag_panel(sample_items(), page=0)
    callbacks = [btn.callback_data for row in keyboard for btn in row]
    assert "*灵装*" in text
    assert "储物袋" in "\n".join(btn.text for row in keyboard for btn in row)
    assert "bag" in callbacks


def test_callback_equipment_routes_to_equipment_panel(monkeypatch):
    # 回调 data="equipment" 时应与 equipbag 一样拉取物品并渲染灵装页
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py -k "equipment_panel or callback_equipment"`
Expected: FAIL（当前标题为“装备背包”，且无 `equipment` 回调分支）

- [ ] **Step 3: Write minimal implementation**

```python
# _build_equipment_bag_panel
text = f"👕 *灵装* ..."

# callback_handler
if data == "equipment":
    data = "equipbag_0"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py -k "equipment_panel or callback_equipment"`
Expected: PASS

### Task 4: 全量验证（本次改动相关）

**Files:**
- Test: `tests/test_telegram_inventory_split_ui.py`
- Test: `tests/test_telegram_feature_intro.py`
- Test: `tests/test_telegram_cultivate_buttons.py`

- [ ] **Step 1: Run targeted regression suite**

Run: `uv run python -m pytest -q tests/test_telegram_inventory_split_ui.py tests/test_telegram_feature_intro.py tests/test_telegram_cultivate_buttons.py`
Expected: 全部 PASS

- [ ] **Step 2: Manual sanity checklist**

Runbook:
1. `/xian_start` 打开主菜单，确认有 `🎒 储物袋` 和 `👕 灵装`
2. 点 `🎒 储物袋`，确认仅看到可堆叠物品信息
3. 点 `👕 灵装`，确认可执行装备/强化/分解与跳回储物袋

Expected: 交互符合设计，无“装备背包”旧命名残留
