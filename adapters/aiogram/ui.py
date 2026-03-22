"""UI helpers for aiogram adapter."""

from __future__ import annotations

import time
from typing import Any, Iterable

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fmt_num(value: Any) -> str:
    return f"{_to_int(value):,}"


def _fmt_signed_pct_from_ratio(value: Any) -> str:
    try:
        ratio = float(value or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    pct = ratio * 100.0
    if abs(pct) < 1e-9:
        return "0%"
    sign = "+" if pct > 0 else "-"
    abs_pct = abs(pct)
    if abs(abs_pct - round(abs_pct)) < 1e-9:
        return f"{sign}{int(round(abs_pct))}%"
    return f"{sign}{abs_pct:.1f}%"


def _fmt_seconds(seconds: int) -> str:
    total = max(0, _to_int(seconds))
    if total >= 3600:
        h, rem = divmod(total, 3600)
        m = rem // 60
        if m > 0:
            return f"{h}小时{m}分钟"
        return f"{h}小时"
    if total >= 60:
        m, s = divmod(total, 60)
        if s > 0:
            return f"{m}分{s}秒"
        return f"{m}分钟"
    return f"{total}秒"


def _fmt_status_short(status: dict[str, Any]) -> str:
    hp = _to_int(status.get("hp"), 0)
    max_hp = _to_int(status.get("max_hp"), hp)
    mp = _to_int(status.get("mp"), 0)
    max_mp = _to_int(status.get("max_mp"), mp)
    return (
        f"❤️ HP: {hp}/{max_hp}\n"
        f"💙 MP: {mp}/{max_mp}\n"
        f"⚡ 精力: {status.get('stamina', 0)}/{status.get('max_stamina', 24)}"
    )


def _fmt_skills_lines(skills: Iterable[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for skill in skills or []:
        name = skill.get("name", skill.get("id", "技能"))
        mp_cost_text = skill.get("mp_cost_text")
        if not mp_cost_text:
            mp_cost_text = f"消耗{_to_int(skill.get('mp_cost'))}MP"
        rows.append(f"• {name} - {mp_cost_text}")
    return rows


def _fmt_rewards(rewards: dict[str, Any] | None) -> list[str]:
    payload = rewards or {}
    exp = _to_int(payload.get("exp"), 0)
    copper = _to_int(payload.get("copper"), 0)
    gold = _to_int(payload.get("gold"), 0)
    lines = ["🎁 奖励"]
    if exp > 0:
        lines.append(f"• 修为 +{_fmt_num(exp)}")
    if copper > 0:
        lines.append(f"• 下品灵石 +{_fmt_num(copper)}")
    if gold > 0:
        lines.append(f"• 中品灵石 +{_fmt_num(gold)}")
    if len(lines) == 1:
        lines.append("• 无")
    return lines


def _fmt_drops(drops: Iterable[dict[str, Any]] | None) -> list[str]:
    rows = list(drops or [])
    if not rows:
        return []
    lines = ["🎒 掉落"]
    for item in rows[:8]:
        name = item.get("item_name", item.get("item_id", "未知物品"))
        qty = _to_int(item.get("quantity"), 1)
        lines.append(f"• {name} x{qty}")
    if len(rows) > 8:
        lines.append(f"• ... 其余 {len(rows) - 8} 项")
    return lines


def register_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🆕 注册角色", callback_data="menu:register")
    return builder.as_markup()


def main_menu_keyboard(*, registered: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not registered:
        builder.button(text="🆕 注册角色", callback_data="menu:register")
        builder.button(text="🔄 刷新", callback_data="menu:home")
        builder.adjust(1, 1)
        return builder.as_markup()

    builder.button(text="📊 状态", callback_data="menu:stat")
    builder.button(text="🦴 狩猎", callback_data="menu:hunt")
    builder.button(text="⚡ 突破", callback_data="menu:break")
    builder.button(text="🗺️ 秘境", callback_data="menu:secret")
    builder.button(text="🔄 刷新菜单", callback_data="menu:home")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def hunt_monsters_keyboard(monsters: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(monsters or [])
    for monster in rows[:8]:
        monster_id = str(monster.get("id", "")).strip()
        if not monster_id:
            continue
        name = monster.get("name", monster_id)
        min_rank = _to_int(monster.get("min_rank"), 1)
        builder.button(text=f"{name}（{min_rank}+）", callback_data=f"hunt:start:{monster_id}")
    builder.button(text="🔄 刷新怪物", callback_data="hunt:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def hunt_battle_keyboard(skills: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗡️ 普通攻击", callback_data="hunt:act:normal")
    for skill in list(skills or [])[:3]:
        sid = str(skill.get("id", "")).strip()
        if not sid:
            continue
        name = str(skill.get("name", sid))
        builder.button(text=f"✨ {name}", callback_data=f"hunt:act:skill:{sid}")
    builder.button(text="🧹 结束战斗", callback_data="hunt:exit")
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


def hunt_settlement_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🦴 继续狩猎", callback_data="hunt:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1)
    return builder.as_markup()


def breakthrough_keyboard(selected_strategy: str | None, call_for_help: bool = True) -> InlineKeyboardMarkup:
    current = (selected_strategy or "normal").strip().lower()
    options = [
        ("normal", "普通冲关"),
        ("steady", "稳妥突破"),
        ("protect", "护脉突破"),
        ("desperate", "生死突破"),
    ]
    builder = InlineKeyboardBuilder()
    for key, label in options:
        text = f"✅ {label}" if key == current else label
        builder.button(text=text, callback_data=f"break:preview:{key}")
    help_text = "🤝 道友助阵：开" if call_for_help else "🤝 道友助阵：关"
    builder.button(text=help_text, callback_data="break:help:toggle")
    builder.button(text="⚡ 执行突破", callback_data="break:confirm")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 1, 1, 1)
    return builder.as_markup()


def secret_realms_keyboard(realms: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(realms or [])
    for realm in rows[:10]:
        rid = str(realm.get("id", "")).strip()
        if not rid:
            continue
        name = realm.get("name", rid)
        min_rank = _to_int(realm.get("min_rank"), 1)
        builder.button(text=f"{name}（{min_rank}+）", callback_data=f"secret:realm:{rid}")
    builder.button(text="🔄 刷新秘境", callback_data="secret:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def secret_paths_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛡️ 稳妥探索", callback_data="secret:path:safe")
    builder.button(text="⚖️ 普通探索", callback_data="secret:path:normal")
    builder.button(text="🔥 冒险探索", callback_data="secret:path:risky")
    builder.button(text="💎 寻宝路线", callback_data="secret:path:loot")
    builder.button(text="⬅️ 返回秘境列表", callback_data="secret:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def secret_battle_keyboard(skills: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗡️ 普通攻击", callback_data="secret:act:normal")
    for skill in list(skills or [])[:3]:
        sid = str(skill.get("id", "")).strip()
        if not sid:
            continue
        name = str(skill.get("name", sid))
        builder.button(text=f"✨ {name}", callback_data=f"secret:act:skill:{sid}")
    builder.button(text="🧹 结束探索", callback_data="secret:exit")
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


def secret_event_choices_keyboard(choices: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(choices or [])
    for choice in rows[:5]:
        cid = str(choice.get("id", "")).strip()
        if not cid:
            continue
        label = choice.get("label", cid)
        builder.button(text=str(label), callback_data=f"secret:choice:{cid}")
    builder.button(text="🧹 结束探索", callback_data="secret:exit")
    builder.adjust(1, 1, 1, 1, 1)
    return builder.as_markup()


def secret_settlement_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗺️ 继续秘境", callback_data="secret:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1)
    return builder.as_markup()


def format_status_card(status: dict[str, Any]) -> str:
    weak_until = _to_int(status.get("weak_until"), 0)
    weak_remaining = _to_int(status.get("weak_remaining_seconds"), 0)
    if weak_remaining <= 0 and weak_until > 0:
        weak_remaining = max(0, weak_until - int(time.time()))
    weak_line = "正常"
    if weak_remaining > 0 or bool(status.get("is_weak")):
        weak_line = f"虚弱中（剩余 {_fmt_seconds(weak_remaining)}）"

    lines = [
        f"👤 {status.get('in_game_username', '修士')}",
        f"🔮 境界: {status.get('realm_name', '凡人')}（Rank {status.get('rank', 1)}）",
        f"🌟 五行: {status.get('element', '无')}",
        f"✨ 修为: {_fmt_num(status.get('exp', 0))}",
        f"💰 下品灵石: {_fmt_num(status.get('copper', 0))}",
        f"💎 中品灵石: {_fmt_num(status.get('gold', 0))}",
        _fmt_status_short(status),
        f"🧘 修炼状态: {'修炼中' if bool(status.get('state')) else '空闲'}",
        f"☠️ 虚弱状态: {weak_line}",
    ]
    return "\n".join(lines)


def format_hunt_panel(
    monsters: Iterable[dict[str, Any]],
    *,
    cooldown_remaining: int = 0,
    can_hunt: bool = True,
) -> str:
    lines = ["🦴 狩猎选择"]
    if not can_hunt and cooldown_remaining > 0:
        lines.append(f"⏱️ 冷却中: {_fmt_seconds(cooldown_remaining)}")
    else:
        lines.append("✅ 当前可发起狩猎")
    lines.append("可挑战怪物：")
    for monster in list(monsters or [])[:8]:
        lines.append(
            f"• {monster.get('name', monster.get('id', '未知'))}（解锁境界 {monster.get('min_rank', 1)}）"
        )
    if len(lines) <= 3:
        lines.append("• 暂无可挑战怪物")
    return "\n".join(lines)


def format_hunt_battle_open(payload: dict[str, Any]) -> str:
    player = payload.get("player") or {}
    enemy = payload.get("enemy") or {}
    lines = [
        f"⚔️ 狩猎战斗已开始（回合 {payload.get('round', 0)}）",
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} HP, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} MP",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} HP",
    ]
    skills = _fmt_skills_lines(payload.get("active_skills") or [])
    if skills:
        lines.append("可用技能：")
        lines.extend(skills)
    return "\n".join(lines)


def format_battle_round(payload: dict[str, Any], *, title: str) -> str:
    player = payload.get("player") or {}
    enemy = payload.get("enemy") or {}
    lines = [
        f"{title}（回合 {payload.get('round', 0)}）",
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} HP, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} MP",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} HP",
    ]
    round_log = list(payload.get("round_log") or [])
    if round_log:
        lines.append("本回合日志：")
        for row in round_log[:8]:
            lines.append(f"• {row}")
        if len(round_log) > 8:
            lines.append(f"• ... 其余 {len(round_log) - 8} 条")
    return "\n".join(lines)


def format_hunt_settlement(payload: dict[str, Any]) -> str:
    lines = [
        f"{'✅ 胜利' if payload.get('victory') else '❌ 战败'} - {payload.get('message', '狩猎结束')}",
    ]
    lines.extend(_fmt_rewards(payload.get("rewards") or {}))
    lines.extend(_fmt_drops(payload.get("drops") or []))
    status = payload.get("post_status") or {}
    if status:
        lines.append("📊 当前状态")
        lines.append(_fmt_status_short(status))
    reasons = list(payload.get("failure_reasons") or [])
    if reasons:
        lines.append("📌 失败原因")
        for reason in reasons[:3]:
            lines.append(f"• {reason}")
    return "\n".join(lines)


def format_breakthrough_preview(preview: dict[str, Any]) -> str:
    strategy_name = preview.get("strategy_name", "普通冲关")
    is_tribulation = bool(preview.get("is_tribulation", False))
    location_name = str(preview.get("location_name") or preview.get("current_map") or "未知地带")
    spirit_density = float(preview.get("spirit_density", 1.0) or 1.0)
    location_bonus = _fmt_signed_pct_from_ratio(preview.get("location_bonus"))
    fortune_label = str(preview.get("fortune_label") or "平")
    fortune_bonus = _fmt_signed_pct_from_ratio(preview.get("fortune_bonus"))
    call_for_help = bool(preview.get("call_for_help", True))
    ally_bonus = _fmt_signed_pct_from_ratio(preview.get("ally_help_bonus"))
    tribulation_flat_penalty = float(preview.get("tribulation_flat_penalty", 0.0) or 0.0)
    tribulation_rate_multiplier = float(preview.get("tribulation_rate_multiplier", 1.0) or 1.0)
    tribulation_extra_cost = _to_int(preview.get("tribulation_extra_cost_copper"), 0)
    tribulation_extra_stamina = _to_int(preview.get("tribulation_extra_stamina"), 0)
    lines = [
        "⛈️ 渡劫突破预览" if is_tribulation else "⚡ 突破预览",
        f"策略: {strategy_name}",
        f"关卡类型: {'圆满渡劫（天雷劫）' if is_tribulation else '常规破境'}",
        f"当前境界: {preview.get('current_realm', '未知')} → {preview.get('next_realm', '未知')}",
        f"所在地: {location_name}（灵气×{spirit_density:.2f}，地脉{location_bonus}）",
        f"今日运势: {fortune_label}（{fortune_bonus}）",
        f"道友助阵: {'已召集' if call_for_help else '未召集'}（{ally_bonus if call_for_help else '0%'}）",
        f"成功率: {preview.get('success_rate_pct', 0)}%",
        f"消耗: {_fmt_num(preview.get('cost_copper', 0))} 下品灵石 + {preview.get('stamina_cost', 1)} 精力",
    ]
    if is_tribulation:
        lines.append(
            f"雷劫压制: {_fmt_signed_pct_from_ratio(-tribulation_flat_penalty)}，倍率 x{tribulation_rate_multiplier:.2f}"
        )
        lines.append(f"雷劫附加消耗: +{_fmt_num(tribulation_extra_cost)} 下品灵石，+{tribulation_extra_stamina} 精力")
    rate_parts = list(preview.get("rate_parts") or [])
    if rate_parts:
        lines.append("加成构成：")
        for part in rate_parts[:5]:
            lines.append(f"• {part}")
    strategy_notes = str(preview.get("strategy_notes", "")).strip()
    if strategy_notes:
        lines.append("策略说明：")
        lines.append(strategy_notes)
    return "\n".join(lines)


def format_breakthrough_result(payload: dict[str, Any]) -> str:
    if payload.get("success"):
        congrats = str(payload.get("congrats_message", "") or "").strip()
        lines = [
            f"✅ 突破成功：{payload.get('new_realm', '未知境界')}",
            f"*{congrats}*" if congrats else "",
            payload.get("message", ""),
        ]
        if payload.get("strategy_cost_text"):
            lines.append(f"📦 {payload.get('strategy_cost_text')}")
        if payload.get("stamina_cost") is not None:
            lines.append(f"⚡ 精力消耗: {payload.get('stamina_cost')}")
        if payload.get("post_breakthrough_restore_ratio") is not None:
            ratio = int(float(payload.get("post_breakthrough_restore_ratio", 0.0)) * 100)
            lines.append(f"❤️💙 突破后恢复系数: {ratio}%")
        return "\n".join([row for row in lines if row])

    lines = [
        "❌ 突破未成功",
        payload.get("message", "突破失败"),
    ]
    if payload.get("exp_lost") is not None:
        lines.append(f"📉 损失修为: {_fmt_num(payload.get('exp_lost', 0))}")
    if payload.get("weak_seconds") is not None:
        lines.append(f"☠️ 虚弱时间: {_fmt_seconds(_to_int(payload.get('weak_seconds'), 0))}")
    if payload.get("strategy_cost_text"):
        lines.append(f"📦 {payload.get('strategy_cost_text')}")
    return "\n".join([row for row in lines if row])


def format_secret_panel(realms: Iterable[dict[str, Any]], attempts_left: int) -> str:
    lines = [
        "🗺️ 秘境探索",
        f"今日剩余次数: {_to_int(attempts_left)}",
        "可进入秘境：",
    ]
    for realm in list(realms or [])[:10]:
        lines.append(
            f"• {realm.get('name', realm.get('id', '未知'))}（解锁境界 {realm.get('min_rank', 1)}）"
        )
    if len(lines) <= 3:
        lines.append("• 当前无可进入秘境")
    return "\n".join(lines)


def format_secret_path_prompt(realm: dict[str, Any]) -> str:
    return (
        "🧭 选择探索路线\n"
        f"秘境: {realm.get('name', realm.get('id', '未知'))}\n"
        "• 稳妥探索: 更安全，收益偏稳\n"
        "• 普通探索: 均衡\n"
        "• 冒险探索: 怪更强，收益更高\n"
        "• 寻宝路线: 事件更多，偏材料"
    )


def format_secret_event(payload: dict[str, Any]) -> str:
    encounter = payload.get("encounter") or {}
    if isinstance(encounter, dict):
        label = encounter.get("label", "未知事件")
        event_text = encounter.get("event_text", "")
    else:
        label = str(encounter)
        event_text = ""
    lines = [
        f"🎲 秘境事件: {label}",
    ]
    if event_text:
        lines.append(event_text)
    choices = list(payload.get("choices") or [])
    if choices:
        lines.append("可选处理方式：")
        for choice in choices[:5]:
            note = str(choice.get("note", "")).strip()
            label = choice.get("label", choice.get("id", "选项"))
            if note:
                lines.append(f"• {label} - {note}")
            else:
                lines.append(f"• {label}")
    return "\n".join(lines)


def format_secret_battle_open(payload: dict[str, Any]) -> str:
    player = payload.get("player") or {}
    enemy = payload.get("enemy") or {}
    lines = [
        f"⚔️ 秘境遭遇战（{(payload.get('encounter') or {}).get('label', '战斗')}）",
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} HP, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} MP",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} HP",
    ]
    skills = _fmt_skills_lines(payload.get("active_skills") or [])
    if skills:
        lines.append("可用技能：")
        lines.extend(skills)
    return "\n".join(lines)


def format_secret_settlement(payload: dict[str, Any]) -> str:
    lines = [
        f"{'✅ 探索成功' if payload.get('victory', True) else '❌ 探索失败'}",
    ]
    if payload.get("battle_message"):
        lines.append(str(payload.get("battle_message")))
    if payload.get("event"):
        lines.append(f"🌌 事件: {payload.get('event')}")
    rewards = payload.get("rewards") or {}
    if rewards:
        lines.extend(_fmt_rewards(rewards))
        lines.extend(_fmt_drops(rewards.get("drops") or []))
    status = payload.get("post_status") or {}
    if status:
        lines.append("📊 当前状态")
        lines.append(_fmt_status_short(status))
    if payload.get("attempts_left") is not None:
        lines.append(f"🗝️ 剩余秘境次数: {_to_int(payload.get('attempts_left'))}")
    reasons = list(payload.get("failure_reasons") or [])
    if reasons:
        lines.append("📌 失败原因")
        for reason in reasons[:3]:
            lines.append(f"• {reason}")
    return "\n".join(lines)
