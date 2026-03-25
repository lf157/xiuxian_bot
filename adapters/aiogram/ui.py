"""UI helpers for aiogram adapter."""

from __future__ import annotations

import re
import time
from typing import Any, Iterable

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

_ELEMENT_NAMES = {
    "fire": "火灵根",
    "water": "水灵根",
    "wood": "木灵根",
    "metal": "金灵根",
    "earth": "土灵根",
    "火": "火灵根",
    "水": "水灵根",
    "木": "木灵根",
    "金": "金灵根",
    "土": "土灵根",
}

_ELEMENT_EMOJI = {
    "火": "🔥", "fire": "🔥",
    "水": "🌊", "water": "🌊",
    "木": "🌿", "wood": "🌿",
    "金": "🗡️", "metal": "🗡️",
    "土": "🪨", "earth": "🪨",
}


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
        f"❤️ 气血: {hp}/{max_hp}\n"
        f"💙 灵力: {mp}/{max_mp}\n"
        f"⚡ 精力: {status.get('stamina', 0)}/{status.get('max_stamina', 24)}"
    )


def _fmt_skills_lines(skills: Iterable[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for skill in skills or []:
        name = skill.get("name", skill.get("id", "技能"))
        mp_cost_text = skill.get("mp_cost_text")
        if not mp_cost_text:
            mp_cost_text = f"消耗{_to_int(skill.get('mp_cost'))}灵力"
        damage_pct = _to_int(skill.get("damage_pct"), 0)
        if damage_pct > 0 and damage_pct != 100:
            rows.append(f"• {name} — {damage_pct}%伤害 / {mp_cost_text}")
        else:
            rows.append(f"• {name} — {mp_cost_text}")
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
        lines.append(f"• 🟦 下品灵石 +{_fmt_num(copper)}")
    if gold > 0:
        lines.append(f"• 🟩 中品灵石 +{_fmt_num(gold)}")
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
    builder.button(text="⬅️ 返回", callback_data="menu:back")
    builder.adjust(1, 1)
    return builder.as_markup()


def element_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    elements = [
        ("🔥 火灵根", "fire"),
        ("🌊 水灵根", "water"),
        ("🌿 木灵根", "wood"),
        ("🗡 金灵根", "metal"),
        ("🪨 土灵根", "earth"),
    ]
    for text, val in elements:
        builder.button(text=text, callback_data=f"register:element:{val}")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def cultivation_keyboard(*, is_cultivating: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_cultivating:
        builder.button(text="⏹️ 结束修炼", callback_data="cul:end")
    else:
        builder.button(text="▶️ 开始修炼", callback_data="cul:start")
    builder.button(text="🔄 刷新状态", callback_data="cul:status")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def main_menu_keyboard(*, registered: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not registered:
        builder.button(text="🆕 注册角色", callback_data="menu:register")
        builder.button(text="🔄 刷新", callback_data="menu:home")
        builder.adjust(1, 1)
        return builder.as_markup()

    builder.button(text="📊 状态", callback_data="menu:stat")
    builder.button(text="🧘 修炼", callback_data="cul:status")
    builder.button(text="🦴 狩猎", callback_data="hunt:list")
    builder.button(text="⚡ 突破", callback_data="break:preview:steady")
    builder.button(text="🎒 储物袋", callback_data="bag:page:0")
    builder.button(text="👕 灵装", callback_data="gear:page:0")
    builder.button(text="📘 技能", callback_data="skill:list")
    builder.button(text="🗺️ 地图", callback_data="travel:map")
    builder.button(text="🌀 秘境", callback_data="secret:list")
    builder.button(text="🏪 万宝阁", callback_data="shop:currency:copper")
    builder.button(text="👥 社交", callback_data="social:menu")
    builder.button(text="🏯 宗门", callback_data="sect:menu")
    builder.button(text="⚔️ PVP", callback_data="pvp:menu")
    builder.button(text="📜 任务", callback_data="quest:list")
    builder.button(text="🎉 活动", callback_data="event:list")
    builder.button(text="🧾 悬赏", callback_data="bounty:menu")
    builder.button(text="🐲 世界BOSS", callback_data="boss:menu")
    builder.button(text="🏆 排行", callback_data="rank:menu")
    builder.button(text="📖 剧情", callback_data="story:menu")
    builder.button(text="⚗️ 炼丹", callback_data="alchemy:menu")
    builder.button(text="🔨 锻造", callback_data="forge:menu")
    builder.button(text="📖 指南", callback_data="story:chapter:guide")
    builder.button(text="🔄 刷新菜单", callback_data="menu:home")
    builder.adjust(2, 2, 3, 3, 2, 3, 3, 3, 2, 1)
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
    builder.button(text="🗡️ 普通攻击", callback_data="hunt:act_normal")
    for skill in list(skills or [])[:3]:
        sid = str(skill.get("id", "")).strip()
        if not sid:
            continue
        name = str(skill.get("name", sid))
        mp_cost_text = skill.get("mp_cost_text") or f"{_to_int(skill.get('mp_cost'))}灵力"
        damage_pct = _to_int(skill.get("damage_pct"), 0)
        if damage_pct > 0 and damage_pct != 100:
            label = f"✨ {name}（{damage_pct}% {mp_cost_text}）"
        else:
            label = f"✨ {name}（{mp_cost_text}）"
        builder.button(text=label, callback_data=f"hunt:act_skill:{sid}")
    builder.button(text="🧹 结束战斗", callback_data="hunt:exit")
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


def hunt_settlement_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🦴 继续狩猎", callback_data="hunt:settle")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1)
    return builder.as_markup()


def breakthrough_keyboard(
    selected_strategy: str | None,
    *,
    resource_ok: bool = True,
    is_tribulation: bool = False,
) -> InlineKeyboardMarkup:
    current = (selected_strategy or "steady").strip().lower()
    builder = InlineKeyboardBuilder()
    if is_tribulation:
        # 渡劫模式：不显示策略切换按钮
        confirm_text = "⛈️ 执行渡劫" if resource_ok else "⚠️ 资源不足"
        builder.button(text=confirm_text, callback_data="break:confirm")
        builder.button(text="⬅️ 主菜单", callback_data="menu:home")
        builder.adjust(1, 1)
    else:
        options = [
            ("steady", "🛡️ 稳妥突破"),
            ("protect", "🌿 护脉突破"),
            ("desperate", "🔥 生死突破"),
        ]
        for key, label in options:
            text = f"👉 {label}" if key == current else label
            builder.button(text=text, callback_data=f"break:preview:{key}")
        confirm_text = "⚡ 执行突破" if resource_ok else "⚠️ 资源不足"
        builder.button(text=confirm_text, callback_data="break:confirm")
        builder.button(text="⬅️ 主菜单", callback_data="menu:home")
        builder.adjust(3, 1, 1)
    return builder.as_markup()


def breakthrough_trial_keyboard(trial_requirements: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    """突破失败（试炼未完成）时的导航键盘。"""
    builder = InlineKeyboardBuilder()
    reqs = trial_requirements or {}
    hunt = reqs.get("hunt") or {}
    secret = reqs.get("secret") or {}
    if int(hunt.get("remaining", 0) or 0) > 0:
        builder.button(text="🦴 前往狩猎", callback_data="hunt:list")
    if int(secret.get("remaining", 0) or 0) > 0:
        builder.button(text="🗺️ 前往秘境", callback_data="secret:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1)
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
    builder.button(text="🗡️ 普通攻击", callback_data="secret:act_normal")
    for skill in list(skills or [])[:3]:
        sid = str(skill.get("id", "")).strip()
        if not sid:
            continue
        name = str(skill.get("name", sid))
        builder.button(text=f"✨ {name}", callback_data=f"secret:act_skill:{sid}")
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
    builder.button(text="🗺️ 继续秘境", callback_data="secret:settle")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1)
    return builder.as_markup()


def _next_step_guide(rank: int, status: dict[str, Any]) -> str:
    """根据境界和状态生成下一步引导文案"""
    exp = _to_int(status.get("exp"), 0)
    next_exp = _to_int(status.get("next_exp"), 0)
    is_cultivating = bool(status.get("state"))
    is_weak = bool(status.get("is_weak"))

    if is_weak:
        return "⚠️ 你正处于虚弱状态，等待恢复后再行动。"
    if is_cultivating:
        return "🧘 正在修炼中，修炼结束后记得结算修为。"

    exp_enough = next_exp > 0 and exp >= next_exp

    if rank <= 1:
        return "🌱 刚踏入修行之路！先去「修炼」积累修为，达到100点即可「突破」至练气期。"
    if rank <= 5:
        if exp_enough:
            return "⚡ 修为已满！前往「突破」查看所需资源，准备好灵石和突破丹再行突破。"
        return "🗡️ 练气阶段：「狩猎」赚灵石和修为，「修炼」提升根基。攒够修为就去「突破」！"
    if rank <= 9:
        if exp_enough:
            return "⚡ 修为已满！前往「突破」确认灵石、突破丹等资源是否充足，再选择策略突破。"
        return "🗺️ 筑基阶段：探索「秘境」获取稀有材料，到「万宝阁」购置突破丹。尝试加入宗门获得修炼加成。"
    if rank <= 13:
        if exp_enough:
            return "⚡ 修为已满！前往「突破」检查资源与策略，冲击更高境界。"
        return "🔥 金丹阶段：「锻造」装备提升战力，挑战高级怪物。准备凝丹露突破元婴！"
    if rank <= 17:
        if exp_enough:
            return "⚡ 修为已满！前往「突破」检查资源与策略，冲击更高境界。"
        return "🌌 元婴阶段：前往星陨海探索，挑战更强的秘境Boss。收集元婴结晶冲击化神！"
    if rank <= 21:
        if exp_enough:
            return "⚡ 修为已满！前往「突破」检查资源与策略，向渡劫之路迈进！"
        return "⛈️ 化神阶段：逆墟荒原等待你的探索，法则之力蕴含无上机缘。向渡劫之路迈进！"
    if exp_enough:
        return "⚡ 修为已满！前往「突破」检查资源，向更高境界发起冲击！"
    return "🏔️ 你已是当世顶尖强者。继续探索未知领域，追寻长生大道！"


def _bar(current: int, maximum: int, length: int = 10) -> str:
    """生成进度条 ██████████"""
    if maximum <= 0:
        filled = length
    else:
        pct = min(100, int(current / maximum * 100))
        filled = pct * length // 100
    return "█" * filled + "░" * (length - filled)


def format_status_card(
    status: dict[str, Any],
    *,
    quests: list[dict[str, Any]] | None = None,
) -> str:
    rank = _to_int(status.get("rank"), 1)
    exp = _to_int(status.get("exp"), 0)
    next_exp = _to_int(status.get("next_exp"), 0)
    hp = _to_int(status.get("hp"), 0)
    max_hp = _to_int(status.get("max_hp"), max(hp, 1))
    mp = _to_int(status.get("mp"), 0)
    max_mp = _to_int(status.get("max_mp"), max(mp, 1))
    stamina = status.get("stamina", 0)
    max_stamina = _to_int(status.get("max_stamina"), 24)
    try:
        stamina_int = int(float(stamina))
    except (TypeError, ValueError):
        stamina_int = 0

    # 元素
    element_raw = str(status.get("element") or "无")
    element_name = _ELEMENT_NAMES.get(element_raw, element_raw)
    element_emoji = _ELEMENT_EMOJI.get(element_raw, "")

    # 虚弱
    weak_until = _to_int(status.get("weak_until"), 0)
    weak_remaining = _to_int(status.get("weak_remaining_seconds"), 0)
    if weak_remaining <= 0 and weak_until > 0:
        weak_remaining = max(0, weak_until - int(time.time()))
    is_weak = weak_remaining > 0 or bool(status.get("is_weak"))

    # 异常状态
    abnormal = "无"
    if is_weak:
        abnormal = f"☠️ 虚弱中（剩余 {_fmt_seconds(weak_remaining)}）"

    # 修炼状态
    is_cultivating = bool(status.get("state"))
    cultivate_text = "修炼中" if is_cultivating else "无"

    # 修为进度条
    if next_exp > 0:
        exp_bar = f"📖修为  {_bar(exp, next_exp)} {_fmt_num(exp)}/{_fmt_num(next_exp)}"
    else:
        exp_bar = f"📖修为  {'█' * 10} {_fmt_num(exp)}（已满级）"

    # ── 头部 ──
    realm_name = str(status.get("realm_name") or "凡人")
    username = str(status.get("in_game_username") or "修士")
    header = f"👤 *{username}* · {element_emoji}{element_name} · {realm_name}"

    # ── 🧘状态 ──
    state_label = "修炼中" if is_cultivating else "空闲中"
    abnormal_label = abnormal if is_weak else "无异常"
    state_lines = [
        f"——🧘状态 · {state_label} · {abnormal_label}——",
        exp_bar,
        f"❤️气血  {_bar(hp, max_hp)} {_fmt_num(hp)}/{_fmt_num(max_hp)}",
        f"💙灵力  {_bar(mp, max_mp)} {_fmt_num(mp)}/{_fmt_num(max_mp)}",
        f"⚡️精力  {_bar(stamina_int, max_stamina)} {stamina_int}/{max_stamina}",
    ]

    # ── 🗺所在地 ──
    map_name = str(status.get("current_map_name") or status.get("current_map") or "苍澜城")
    location_lines = [
        "——🗺所在地——",
        f"📌{map_name}",
    ]

    # ── 💰钱袋 ──
    copper = _to_int(status.get("copper"), 0)
    gold = _to_int(status.get("gold"), 0)
    wallet_lines = [
        "——💰钱袋——",
        f"🟦 下品灵石 {_fmt_num(copper)}",
        f"🟩 中品灵石 {_fmt_num(gold)}",
    ]

    # ── 📋今日任务 ──
    quest_lines: list[str] = []
    if quests:
        quest_lines.append("")
        quest_lines.append("——📋 今日任务——")
        done_count = 0
        for q in quests:
            progress = _to_int(q.get("progress"), 0)
            goal = _to_int(q.get("goal"), 1)
            claimed = bool(q.get("claimed"))
            name = q.get("name", "任务")
            if claimed:
                quest_lines.append(f"✅ {name}")
                done_count += 1
            elif progress >= goal:
                quest_lines.append(f"🎁 {name}（可领取）")
            else:
                quest_lines.append(f"⏳ {name} ({progress}/{goal})")
        if done_count == len(quests):
            quest_lines.append("🎉 今日全部完成！")

    # ── 底部引导 ──
    tip = _next_step_guide(rank, status)

    parts = [header, ""]
    parts.extend(state_lines)
    parts.append("")
    parts.extend(location_lines)
    parts.append("")
    parts.extend(wallet_lines)
    if quest_lines:
        parts.extend(quest_lines)
    parts.append("")
    parts.append(f"_{tip}_")

    return "\n".join(parts)


def format_hunt_panel(
    monsters: Iterable[dict[str, Any]],
    *,
    cooldown_remaining: int = 0,
    can_hunt: bool = True,
    current_map_name: str = "",
) -> str:
    lines = ["🦴 狩猎选择"]
    if current_map_name:
        lines.append(f"📍 当前地点: {current_map_name}")
    if not can_hunt and cooldown_remaining > 0:
        lines.append(f"⏱️ 冷却中: {_fmt_seconds(cooldown_remaining)}")
    else:
        lines.append("✅ 当前可发起狩猎")
    lines.append("可挑战怪物：")
    for monster in list(monsters or [])[:8]:
        name = monster.get("name", monster.get("id", "未知"))
        element = monster.get("element", "")
        realm_name = monster.get("realm_name", "")
        difficulty = monster.get("difficulty", "")
        diff_icon = {"碾压": "🟢", "轻松": "🟢", "适中": "🟡", "挑战": "🟠", "极难": "🔴", "必死": "💀"}.get(difficulty, "⚪")
        lines.append(
            f"• {name}（{element}）{realm_name} {diff_icon}{difficulty}"
        )
    if len(lines) <= 4:
        lines.append("• 当前地点无可挑战怪物，试试换个地图")
    return "\n".join(lines)


def format_hunt_battle_open(payload: dict[str, Any]) -> str:
    player = payload.get("player") or {}
    enemy = payload.get("enemy") or {}
    lines = [
        f"⚔️ 狩猎战斗已开始（回合 {payload.get('round', 0)}）",
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} 气血, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} 灵力",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} 气血",
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
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} 气血, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} 灵力",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} 气血",
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
    strategy = str(preview.get("strategy", "steady")).strip().lower()
    strategy_name = preview.get("strategy_name", "稳妥突破")
    is_tribulation = bool(preview.get("is_tribulation", False))
    current_realm = preview.get("current_realm", "未知")
    next_realm = preview.get("next_realm", "未知")

    # ── 环境 ──
    location_name = str(preview.get("location_name") or preview.get("current_map") or "未知地带")
    spirit_density = float(preview.get("spirit_density", 1.0) or 1.0)
    location_bonus = _fmt_signed_pct_from_ratio(preview.get("location_bonus"))
    fortune_label = str(preview.get("fortune_label") or "平")
    fortune_bonus = _fmt_signed_pct_from_ratio(preview.get("fortune_bonus"))

    # ── 消耗 ──
    cost_copper = _to_int(preview.get("cost_copper"), 0)
    stamina_cost = _to_int(preview.get("stamina_cost"), 1)
    protect_material_need = _to_int(preview.get("protect_material_need") or preview.get("extra_cost_copper"), 0)

    # ── 用户资源 ──
    user_copper = _to_int(preview.get("user_copper"), 0)
    user_stamina = _to_int(preview.get("user_stamina"), 0)
    user_spirit_stone = _to_int(preview.get("user_spirit_stone"), 0)
    user_exp = _to_int(preview.get("user_exp"), 0)
    next_exp = _to_int(preview.get("next_exp"), 0)

    # ── 突破丹信息 ──
    steady_pill_name = str(preview.get("steady_pill_name") or "").strip()
    steady_pill_bonus = float(preview.get("steady_pill_bonus", 0.0) or 0.0)
    related_items = list(preview.get("related_items") or [])
    # 按类型统计突破丹
    _PILL_IDS = {"breakthrough_pill", "advanced_breakthrough_pill", "super_breakthrough_pill"}
    pill_lines: list[str] = []
    pill_total = 0
    for ri in related_items:
        iid = str(ri.get("item_id") or "")
        if iid in _PILL_IDS:
            qty = int(ri.get("quantity", 0) or 0)
            pill_total += qty
            if qty > 0:
                pill_lines.append(f"{ri.get('item_name', iid)} x{qty}")

    # ── Buff 状态 ──
    protect_buff_active = bool(preview.get("protect_buff_active", False))
    protect_buff_remaining = _to_int(preview.get("protect_buff_remaining"), 0)
    boost_buff_active = bool(preview.get("boost_buff_active", False))
    boost_buff_remaining = _to_int(preview.get("boost_buff_remaining"), 0)
    boost_buff_pct = float(preview.get("boost_buff_pct", 0.0) or 0.0)

    # ── 成功率 ──
    success_rate_pct = _to_int(preview.get("success_rate_pct"), 0)
    rate_parts = list(preview.get("rate_parts") or [])

    # ── 资源检查 ──
    resource_ok = bool(preview.get("resource_ok", True))
    resource_hints = list(preview.get("resource_hints") or [])

    # ── 头部 ──
    header = "⛈️ 突破 · 渡劫突破" if is_tribulation else "⚡ 突破 · 常规破境"
    lines = [
        header,
        "",
        f"【{current_realm}】 至 【{next_realm}】",
    ]

    # ── 环境区 ──
    lines.append("")
    lines.append("——🗺环境——")
    lines.append(f"所在地：{location_name}")
    lines.append(f"灵气浓度：×{spirit_density:.2f}（地脉{location_bonus}）")
    lines.append(f"今日运势：{fortune_label} {fortune_bonus}")

    # ── 资源区 ──
    lines.append("")
    lines.append("——💰资源——")
    if next_exp > 0:
        lines.append(f"📖 修为：{_fmt_num(user_exp)}/{_fmt_num(next_exp)}")
    else:
        lines.append(f"📖 修为：{_fmt_num(user_exp)}（已满级）")
    lines.append(f"🟦 下品灵石：{_fmt_num(user_copper)}")
    lines.append(f"⚡ 精力：{user_stamina}")
    lines.append(f"🪨 聚元石：{user_spirit_stone}")
    if pill_lines:
        lines.append(f"💊 突破丹：{' / '.join(pill_lines)}")
    else:
        lines.append("💊 突破丹：无")

    # ── 增益状态 ──
    buff_parts: list[str] = []
    if protect_buff_active and protect_buff_remaining > 0:
        buff_parts.append(f"🛡️ 保护丹 剩余{_fmt_seconds(protect_buff_remaining)}")
    if boost_buff_active and boost_buff_remaining > 0:
        buff_parts.append(f"✨ 聚灵增益 +{int(boost_buff_pct)}% 剩余{_fmt_seconds(boost_buff_remaining)}")
    if buff_parts:
        lines.append("")
        lines.append("——🔮增益——")
        for bp in buff_parts:
            lines.append(bp)

    # ── 方案区 ──
    lines.append("")
    lines.append("——📋方案——")
    _STRATEGY_LABELS = {
        "steady": "🛡️稳妥突破",
        "protect": "🌿护脉突破",
        "desperate": "🔥生死突破",
    }
    if is_tribulation:
        lines.append("当前方案：⛈️渡劫突破")
    else:
        lines.append(f"当前方案：{_STRATEGY_LABELS.get(strategy, strategy_name)}")
    lines.append(f"成功率：{success_rate_pct}%")

    # 消耗行
    cost_parts: list[str] = [f"🟦下品灵石×{_fmt_num(cost_copper)}"]
    if strategy == "protect" and protect_material_need > 0:
        cost_parts.append(f"🪨聚元石×{protect_material_need}")
    if strategy == "steady" and steady_pill_name:
        bonus_text = f"（+{int(steady_pill_bonus * 100)}%）" if steady_pill_bonus > 0 else ""
        cost_parts.append(f"💊{steady_pill_name}×1{bonus_text}")
    elif strategy == "steady" and not steady_pill_name:
        if boost_buff_active:
            cost_parts.append("💊无需突破丹（聚灵增益替代）")
        else:
            cost_parts.append("💊突破丹×1（缺少）")
    cost_parts.append(f"⚡精力×{stamina_cost}")
    lines.append(f"消耗：{' + '.join(cost_parts)}")

    # 策略特殊效果
    if is_tribulation:
        lines.append("特殊效果：渡劫模式，无法选择其他策略")
    elif strategy == "steady":
        lines.append("特殊效果：失败损失减半")
    elif strategy == "protect":
        lines.append("特殊效果：失败不进虚弱")
    elif strategy == "desperate":
        lines.append("特殊效果：成功额外奖励，失败惩罚加重")

    # 渡劫信息
    if is_tribulation:
        tribulation_flat_penalty = float(preview.get("tribulation_flat_penalty", 0.0) or 0.0)
        tribulation_rate_multiplier = float(preview.get("tribulation_rate_multiplier", 1.0) or 1.0)
        tribulation_extra_cost = _to_int(preview.get("tribulation_extra_cost_copper"), 0)
        tribulation_extra_stamina = _to_int(preview.get("tribulation_extra_stamina"), 0)
        if bool(preview.get("tribulation_rate_ignored")):
            lines.append(f"雷劫压制：已被{steady_pill_name or '高阶突破丹'}豁免")
        else:
            lines.append(f"雷劫压制：{_fmt_signed_pct_from_ratio(-tribulation_flat_penalty)}，×{tribulation_rate_multiplier:.2f}")
        lines.append(f"雷劫附加：+{_fmt_num(tribulation_extra_cost)}灵石 +{tribulation_extra_stamina}精力")

    # ── 成功率明细 ──
    lines.append("")
    lines.append("——✨成功率——")
    for part in rate_parts[:8]:
        lines.append(f"• {part}")
    lines.append(f"总计：{success_rate_pct}%")

    # ── 资源检查提示 ──
    lines.append("")
    if resource_ok:
        lines.append("✅ 资源充足，可以突破！")
    else:
        lines.append("⚠️ 资源不足，无法突破：")
        for hint in resource_hints[:4]:
            lines.append(f"• {hint}")

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
        if payload.get("post_breakthrough_hp") is not None:
            lines.append("❤️💙 精力充沛，气血与灵力恢复至巅峰")
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
        f"你: {_fmt_num(player.get('hp'))}/{_fmt_num(player.get('max_hp'))} 气血, {_fmt_num(player.get('mp'))}/{_fmt_num(player.get('max_mp'))} 灵力",
        f"敌: {enemy.get('name', '怪物')} {_fmt_num(enemy.get('hp'))}/{_fmt_num(enemy.get('max_hp'))} 气血",
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


# ---------------------------------------------------------------------------
# Shop (万宝阁)
# ---------------------------------------------------------------------------

_CURRENCY_LABELS: dict[str, str] = {
    "copper": "下品灵石",
    "gold": "中品灵石",
    "spirit_high": "上品灵石",
}

_CATEGORY_LABELS: dict[str, str] = {
    "pill": "丹药",
    "array": "法阵",
    "material": "材料",
    "book": "功法",
    "equipment": "装备",
    "other": "其他",
}


def shop_currency_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🟦 下品灵石", callback_data="shop:currency:copper")
    builder.button(text="🟩 中品灵石", callback_data="shop:currency:gold")
    builder.button(text="🟨 上品灵石", callback_data="shop:currency:spirit_high")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def shop_items_keyboard(
    items: Iterable[dict[str, Any]],
    page: int,
    total_pages: int,
    currency: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(items or [])
    for item in rows[:8]:
        item_id = str(item.get("item_id", "")).strip()
        if not item_id:
            continue
        name = item.get("name", item_id)
        price = _to_int(item.get("price"), 0)
        tag = item.get("tag")
        label = f"{name} - {_fmt_num(price)}"
        if tag:
            label = f"[{tag}] {label}"
        builder.button(text=label, callback_data=f"shop:buy:{item_id}:{currency}")

    nav_row: list[tuple[str, str]] = []
    if page > 1:
        nav_row.append(("⬅️ 上一页", f"shop:page:{currency}:{page - 1}"))
    nav_row.append((f"{page}/{total_pages}", "shop:noop"))
    if page < total_pages:
        nav_row.append(("➡️ 下一页", f"shop:page:{currency}:{page + 1}"))
    for text, cb in nav_row:
        builder.button(text=text, callback_data=cb)

    builder.button(text="🔄 刷新", callback_data=f"shop:page:{currency}:{page}")
    builder.button(text="⬅️ 选择货币", callback_data="shop:back")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")

    # Layout: up to 8 item buttons (1 per row), then nav row, then action buttons
    item_count = min(len(rows), 8)
    adjust_args: list[int] = [1] * item_count
    adjust_args.append(len(nav_row))  # nav row
    adjust_args.extend([1, 1, 1])  # refresh, back currency, main menu
    builder.adjust(*adjust_args)
    return builder.as_markup()


def format_shop_panel(
    items: Iterable[dict[str, Any]],
    currency: str,
    page: int,
    total_pages: int,
    currency_role: str = "",
) -> str:
    _CURRENCY_EMOJI = {"copper": "🟦", "gold": "🟩", "spirit_high": "🟨"}
    currency_label = _CURRENCY_LABELS.get(currency, currency)
    currency_emoji = _CURRENCY_EMOJI.get(currency, "🟦")
    lines = [
        f"🏪 万宝阁 — {currency_label}商店",
    ]
    if currency_role:
        lines.append(f"{currency_emoji} 当前{currency_label}: {_fmt_num(currency_role)}")
    lines.append(f"📖 第 {page}/{total_pages} 页")
    lines.append("")

    rows = list(items or [])
    if not rows:
        lines.append("暂无可购买商品")
    for item in rows[:8]:
        name = item.get("name", item.get("item_id", "未知物品"))
        price = _to_int(item.get("price"), 0)
        category = _CATEGORY_LABELS.get(str(item.get("category", "")), "")
        tag = item.get("tag")

        stock = item.get("stock")
        remaining_limit = item.get("remaining_limit")
        stock_text = ""
        if remaining_limit is not None:
            stock_text = f"限购剩余{remaining_limit}"
        elif stock is not None and _to_int(stock) >= 0:
            stock_text = f"库存{_to_int(stock)}"
        else:
            stock_text = "不限量"

        line = f"• {name}  💲{_fmt_num(price)}"
        if category:
            line += f"  [{category}]"
        if tag:
            line += f"  🏷️{tag}"
        line += f"  ({stock_text})"
        lines.append(line)

    lines.append("")
    lines.append("点击商品按钮即可购买")
    return "\n".join(lines)


def bag_items_keyboard(items: Iterable[dict[str, Any]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(items or [])
    for item in rows[:8]:
        item_id = str(item.get("item_id", "")).strip()
        if not item_id:
            continue
        name = str(item.get("name", item_id))
        builder.button(text=name, callback_data=f"bag:detail:{item_id}")
    if page > 1:
        builder.button(text="⬅️ 上一页", callback_data=f"bag:page:{page - 1}")
    builder.button(text=f"{page}/{max(1, total_pages)}", callback_data="menu:home")
    if page < total_pages:
        builder.button(text="➡️ 下一页", callback_data=f"bag:page:{page + 1}")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(8, len(rows))), 3, 1)
    return builder.as_markup()


def gear_items_keyboard(items: Iterable[dict[str, Any]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(items or [])
    for item in rows[:8]:
        item_db_id = str(item.get("id", "")).strip()
        if not item_db_id:
            continue
        name = str(item.get("name", item.get("item_id", item_db_id)))
        builder.button(text=name, callback_data=f"gear:detail:{item_db_id}")
    if page > 1:
        builder.button(text="⬅️ 上一页", callback_data=f"gear:page:{page - 1}")
    builder.button(text=f"{page}/{max(1, total_pages)}", callback_data="gear:equipped_view")
    if page < total_pages:
        builder.button(text="➡️ 下一页", callback_data=f"gear:page:{page + 1}")
    builder.button(text="📌 已佩戴", callback_data="gear:equipped_view")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(8, len(rows))), 3, 1, 1)
    return builder.as_markup()


def skill_list_keyboard(skills: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(skills or [])
    for skill in rows[:10]:
        sid = str(skill.get("id", "")).strip()
        if not sid:
            continue
        name = str(skill.get("name", sid))
        builder.button(text=name, callback_data=f"skill:detail:{sid}")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(10, len(rows))), 1)
    return builder.as_markup()


def bag_detail_keyboard(item_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 使用", callback_data=f"bag:use:{item_id}")
    builder.button(text="🎒 返回储物袋", callback_data="bag:page:1")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def gear_detail_keyboard(item_db_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧷 佩戴", callback_data=f"gear:equip:{item_db_id}")
    builder.button(text="🛠️ 强化", callback_data=f"gear:enhance:{item_db_id}")
    builder.button(text="♻️ 分解", callback_data=f"gear:decompose:{item_db_id}")
    builder.button(text="👕 返回灵装", callback_data="gear:page:1")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def gear_equipped_keyboard(equipped_items: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(equipped_items or [])
    action_count = 0
    for row in rows[:8]:
        slot = str(row.get("slot", "")).strip()
        if not slot:
            continue
        slot_name = str(row.get("slot_name") or slot)
        item_name = str(row.get("name", "灵装"))
        builder.button(text=f"🧹 卸下 {slot_name}·{item_name}", callback_data=f"gear:unequip:{slot}")
        action_count += 1
    builder.button(text="👕 返回灵装", callback_data="gear:page:1")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * action_count), 1, 1)
    return builder.as_markup()


def skill_detail_keyboard(skill_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 学习", callback_data=f"skill:learn:{skill_id}")
    builder.button(text="🧩 装备", callback_data=f"skill:equip:{skill_id}")
    builder.button(text="🧹 卸下", callback_data=f"skill:unequip:{skill_id}")
    builder.button(text="📘 返回技能列表", callback_data="skill:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def alchemy_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚗️ 炼制", callback_data="alchemy:craft")
    builder.button(text="⚗️ 批量炼制", callback_data="alchemy:batch")
    builder.button(text="⬅️ 返回商店", callback_data="shop:back")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def forge_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔨 锻造", callback_data="forge:craft")
    builder.button(text="🎯 定向锻造", callback_data="forge:menu")
    builder.button(text="⬅️ 返回商店", callback_data="shop:back")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def social_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 聊天", callback_data="social:chat")
    builder.button(text="🧭 论道", callback_data="social:dao")
    builder.button(text="👤 好友", callback_data="social:friend")
    builder.button(text="↩️ 回复", callback_data="social:reply")
    builder.button(text="⚔️ PVP", callback_data="pvp:menu")
    builder.button(text="🏯 宗门", callback_data="sect:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏛️ 管理主页", callback_data="admin:menu")
    builder.button(text="🧪 Test", callback_data="admin:test")
    builder.button(text="🔍 查询", callback_data="admin:lookup")
    builder.button(text="✍️ 修改", callback_data="admin:modify")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def admin_modify_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧩 预设修改", callback_data="admin:preset")
    builder.button(text="✅ 确认执行", callback_data="admin:confirm")
    builder.button(text="🧹 取消修改", callback_data="admin:cancel")
    builder.button(text="⬅️ 返回管理面板", callback_data="admin:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def quest_menu_keyboard(payload: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    quests = list((payload or {}).get("quests") or [])
    for row in quests[:8]:
        quest_id = str(row.get("quest_id") or row.get("id") or "").strip()
        if not quest_id:
            continue
        name = str(row.get("name") or quest_id)
        progress = _to_int(row.get("progress"), 0)
        goal = max(1, _to_int(row.get("goal"), 1))
        claimed = bool(row.get("claimed"))
        if claimed:
            builder.button(text=f"✅ {name}", callback_data=f"quest:detail:{quest_id}")
        elif progress >= goal:
            builder.button(text=f"🎁 领取 {name}", callback_data=f"quest:claim:{quest_id}")
        else:
            builder.button(text=f"⏳ {name}", callback_data=f"quest:detail:{quest_id}")
    builder.button(text="🔄 刷新任务", callback_data="quest:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(len(quests), 8)), 1, 1)
    return builder.as_markup()


def event_menu_keyboard(payload: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    events = list((payload or {}).get("events") or [])
    for row in events[:8]:
        event_id = str(row.get("id") or row.get("event_id") or "").strip()
        if not event_id:
            continue
        name = str(row.get("name") or event_id)
        can_claim = bool(row.get("can_claim"))
        claimed_today = bool(row.get("claimed_today") or row.get("claimed"))
        if claimed_today:
            builder.button(text=f"✅ {name}", callback_data=f"event:detail:{event_id}")
        else:
            builder.button(text=f"🎁 领取 {name}", callback_data=f"event:claim:{event_id}")
    builder.button(text="🔄 刷新活动", callback_data="event:list")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(len(events), 8)), 1, 1)
    return builder.as_markup()


def boss_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚔️ 攻击世界BOSS", callback_data="boss:attack")
    builder.button(text="🏆 伤害排行", callback_data="boss:rank")
    builder.button(text="🔄 刷新状态", callback_data="boss:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def rank_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔮 境界排行", callback_data="rank:realm")
    builder.button(text="⚔️ 战力排行", callback_data="rank:combat")
    builder.button(text="💰 财富排行", callback_data="rank:wealth")
    builder.button(text="🔄 刷新", callback_data="rank:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


_BOUNTY_CALLBACK_ARG_RE = re.compile(r"^[0-9]+$")
_BOUNTY_STATUS_LABELS: dict[str, str] = {
    "open": "待接取",
    "claimed": "进行中",
    "completed": "已完成",
    "cancelled": "已取消",
}


def _iter_bounty_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    rows = list(data.get("bounties") or data.get("entries") or [])
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        bounty_id = str(item.get("id") or item.get("bounty_id") or "").strip()
        if bounty_id:
            item["id"] = bounty_id
        status = str(item.get("status") or "").strip().lower()
        if status:
            item["status"] = status
        result.append(item)
    return result


def _bounty_callback_id(row: dict[str, Any]) -> str:
    bounty_id = str(row.get("id") or row.get("bounty_id") or "").strip()
    if not bounty_id:
        return ""
    if _BOUNTY_CALLBACK_ARG_RE.fullmatch(bounty_id) is None:
        return ""
    try:
        if int(bounty_id) <= 0:
            return ""
    except (TypeError, ValueError):
        return ""
    callback_data = f"bounty:claim:{bounty_id}"
    if len(callback_data.encode("utf-8")) > 64:
        return ""
    return bounty_id


def _bounty_actor_label(row: dict[str, Any], *, uid_key: str, name_key: str, actor_uid: str | None) -> str:
    user_id = str(row.get(uid_key) or "").strip()
    if actor_uid and user_id and actor_uid == user_id:
        return "你"
    username = str(row.get(name_key) or "").strip()
    if username:
        return username
    if user_id:
        return user_id
    return "-"


def _bounty_status_label(raw_status: Any) -> str:
    status = str(raw_status or "").strip().lower()
    if not status:
        return "未知"
    return _BOUNTY_STATUS_LABELS.get(status, status)


def _bounty_requirement(row: dict[str, Any]) -> str:
    wanted_name = str(row.get("wanted_item_name") or row.get("wanted_item_id") or "").strip()
    wanted_qty = _to_int(row.get("wanted_quantity"), 0)
    if wanted_name and wanted_qty > 0:
        return f"{wanted_name} x{wanted_qty}"
    if wanted_name:
        return wanted_name
    title = str(row.get("title") or row.get("name") or "").strip()
    return title or "未提供"


def _bounty_reward(row: dict[str, Any]) -> str:
    reward_low = _to_int(row.get("reward_spirit_low"), 0)
    reward_copper = _to_int(row.get("reward_copper"), 0)
    reward_gold = _to_int(row.get("reward_gold"), 0)
    parts: list[str] = []
    if reward_low > 0:
        parts.append(f"{_fmt_num(reward_low)} 下品灵石")
    if reward_gold > 0:
        parts.append(f"{_fmt_num(reward_gold)} 中品灵石")
    if reward_copper > 0 and reward_copper != reward_low:
        parts.append(f"{_fmt_num(reward_copper)} 灵石")
    if not parts and row.get("reward") is not None:
        reward_text = str(row.get("reward")).strip()
        if reward_text:
            parts.append(reward_text)
    return " + ".join(parts) if parts else "未提供"


def bounty_menu_keyboard(payload: dict[str, Any] | None = None, *, actor_uid: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = _iter_bounty_rows(payload)
    action_count = 0
    for row in rows[:8]:
        bounty_id = _bounty_callback_id(row)
        if not bounty_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        claimer_uid = str(row.get("claimer_user_id") or "").strip()
        can_submit = bool(row.get("can_submit"))
        can_accept = bool(row.get("can_accept") or row.get("can_claim"))
        if status == "claimed" and actor_uid and claimer_uid and claimer_uid == actor_uid:
            can_submit = True
        if status == "open" and not can_submit:
            can_accept = True
        if can_submit:
            builder.button(text=f"📦 提交 #{bounty_id}", callback_data=f"bounty:claim:{bounty_id}")
            action_count += 1
            continue
        if can_accept:
            builder.button(text=f"🤝 接取 #{bounty_id}", callback_data=f"bounty:claim:{bounty_id}")
            action_count += 1
    builder.button(text="🔄 刷新悬赏", callback_data="bounty:refresh")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    layout = ([1] * action_count) + [1, 1]
    builder.adjust(*layout)
    return builder.as_markup()


def format_bag_panel(items: Iterable[dict[str, Any]], page: int, total_pages: int) -> str:
    rows = list(items or [])
    lines = [f"🎒 储物袋  第 {page}/{max(1, total_pages)} 页", ""]
    if not rows:
        lines.append("空空如也。")
    for item in rows[:8]:
        name = str(item.get("name", item.get("item_id", "未知物品")))
        qty = _to_int(item.get("quantity"), 1)
        lines.append(f"• {name} x{qty}")
    return "\n".join(lines)


def format_gear_panel(items: Iterable[dict[str, Any]], page: int, total_pages: int) -> str:
    rows = list(items or [])
    lines = [f"👕 灵装  第 {page}/{max(1, total_pages)} 页", ""]
    if not rows:
        lines.append("暂无可穿戴灵装。")
    for item in rows[:8]:
        name = str(item.get("name", item.get("item_id", "未知灵装")))
        level = _to_int(item.get("enhance_level"), 0)
        lines.append(f"• {name}  +{level}")
    return "\n".join(lines)


def format_skill_panel(skills: Iterable[dict[str, Any]]) -> str:
    lines = ["📘 技能面板", ""]
    rows = list(skills or [])
    if not rows:
        lines.append("尚未习得技能。")
    for skill in rows[:10]:
        name = str(skill.get("name", skill.get("id", "技能")))
        lines.append(f"• {name}")
    return "\n".join(lines)


def format_alchemy_panel(payload: dict[str, Any] | None = None) -> str:
    return "⚗️ 炼丹面板"


def format_forge_panel(payload: dict[str, Any] | None = None) -> str:
    return "🔨 锻造面板"


def format_social_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    action = str(data.get("action", "")).strip()
    if action == "chat":
        return "💬 论道\n请选择目标修士发起论道。"
    if action == "dao":
        return "🧭 论道交流\n请选择目标修士。"
    return "💬 社交面板\n可发起论道、查看 PVP、管理宗门。"


def format_pvp_panel(payload: dict[str, Any] | None = None) -> str:
    return "⚔️ PVP 面板"


def format_sect_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    member = data.get("member") or data.get("sect_member") or {}
    sect = (member.get("sect") if isinstance(member, dict) else None) or data.get("sect") or {}
    lines = ["🏯 宗门面板", ""]
    if sect:
        lines.append(f"宗门: {sect.get('name', sect.get('sect_name', '未知宗门'))}")
        if sect.get("level") is not None:
            lines.append(f"等级: {sect.get('level')}")
        if sect.get("members_count") is not None:
            lines.append(f"成员: {sect.get('members_count')}")
    else:
        lines.append("当前未加入宗门。")
    if member and member.get("role"):
        lines.append(f"身份: {member.get('role')}")
    if data.get("message"):
        lines.append(str(data.get("message")))
    return "\n".join(lines)


def format_admin_panel(payload: dict[str, Any] | None = None) -> str:
    return "🛠️ 管理面板"


def format_quest_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    quests = list(data.get("quests") or [])
    lines = ["📜 任务面板", ""]
    if not quests:
        lines.append("当前暂无任务。")
    for row in quests[:8]:
        quest_id = row.get("quest_id", row.get("id", ""))
        name = row.get("name", quest_id or "任务")
        progress = _to_int(row.get("progress"), 0)
        goal = max(1, _to_int(row.get("goal"), 1))
        claimed = bool(row.get("claimed"))
        if claimed:
            lines.append(f"✅ {name}")
        elif progress >= goal:
            lines.append(f"🎁 {name}（可领取）")
        else:
            lines.append(f"⏳ {name} ({progress}/{goal})")
    return "\n".join(lines)


def format_event_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    events = list(data.get("events") or [])
    lines = ["🎉 活动面板", ""]
    if not events:
        lines.append("当前暂无活动。")
    for row in events[:8]:
        name = row.get("name", row.get("id", "活动"))
        claimed_today = bool(row.get("claimed_today") or row.get("claimed"))
        if claimed_today:
            lines.append(f"✅ {name}")
        else:
            lines.append(f"🎁 {name}")
    return "\n".join(lines)


def format_story_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    action = str(data.get("action", "menu"))
    if action == "chapter":
        chapter = data.get("args", [""])[0] if isinstance(data.get("args"), list) else ""
        return f"📖 剧情章节：{chapter or '未指定'}"
    if action == "node":
        node = data.get("args", [""])[0] if isinstance(data.get("args"), list) else ""
        return f"📖 剧情节点：{node or '未指定'}"
    if action == "claim":
        return "📖 剧情奖励已处理。"
    return "📖 剧情面板（开发中）"


def format_boss_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    lines = ["🐲 世界 BOSS", ""]
    boss = data.get("boss") or {}
    if boss:
        lines.append(f"名称: {boss.get('name', '未知')}")
        lines.append(f"血量: {_fmt_num(boss.get('hp', 0))}/{_fmt_num(boss.get('max_hp', 0))}")
    if data.get("message"):
        lines.append(str(data.get("message")))
    if len(lines) == 2:
        lines.append("暂无世界BOSS数据。")
    return "\n".join(lines)


def format_bounty_panel(payload: dict[str, Any] | None = None, *, actor_uid: str | None = None) -> str:
    data = payload if isinstance(payload, dict) else {}
    rows = _iter_bounty_rows(data)
    current_uid = str(actor_uid or data.get("_actor_uid") or "").strip() or None
    lines = ["🧾 悬赏面板", ""]
    if not rows:
        lines.append("当前暂无悬赏。")
    for row in rows[:8]:
        bounty_id = str(row.get("id") or row.get("bounty_id") or "-")
        lines.append(f"#{bounty_id}｜{_bounty_status_label(row.get('status'))}")
        lines.append(f"需求: {_bounty_requirement(row)}")
        lines.append(f"奖励: {_bounty_reward(row)}")
        lines.append(
            "发布: {poster}  接取: {claimer}".format(
                poster=_bounty_actor_label(row, uid_key="poster_user_id", name_key="poster_name", actor_uid=current_uid),
                claimer=_bounty_actor_label(row, uid_key="claimer_user_id", name_key="claimer_name", actor_uid=current_uid),
            )
        )
        description = str(row.get("description") or row.get("desc") or "").strip()
        if description:
            lines.append(f"备注: {description}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_rank_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    rows = list(data.get("entries") or data.get("data", {}).get("entries") or [])
    lines = ["🏆 排行面板", ""]
    if not rows:
        lines.append("暂无排行数据。")
    for idx, row in enumerate(rows[:10], start=1):
        name = row.get("username") or row.get("name") or row.get("user_id") or "匿名修士"
        score = row.get("score", row.get("value", row.get("rating", "-")))
        lines.append(f"{idx}. {name} - {score}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Travel / Map (地图)
# ---------------------------------------------------------------------------


def travel_map_keyboard(
    map_nodes: Iterable[dict[str, Any]],
    *,
    current_map_id: str = "",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = list(map_nodes or [])
    btn_count = 0
    for node in rows:
        map_id = str(node.get("id", "")).strip()
        if not map_id or map_id == current_map_id:
            continue
        if not node.get("is_adjacent"):
            continue
        name = str(node.get("name", map_id))
        cost = _to_int(node.get("travel_cost"), 0)
        density = float(node.get("spirit_density", 1.0) or 1.0)
        if node.get("can_travel"):
            label = f"🚶 {name}（⚡{cost} 灵气×{density:.1f}）"
            builder.button(text=label, callback_data=f"travel:go:{map_id}")
        else:
            reason = str(node.get("unlock_reason") or node.get("travel_block_reason") or "不可前往")
            short_reason = reason[:12]
            label = f"🔒 {name}（{short_reason}）"
            builder.button(text=label, callback_data=f"travel:info:{map_id}")
        btn_count += 1
        if btn_count >= 8:
            break
    builder.button(text="🔄 刷新地图", callback_data="travel:map")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * btn_count), 1, 1)
    return builder.as_markup()


def format_travel_map_panel(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    player = data.get("player") or {}
    world = data.get("world") or {}
    maps = list(data.get("maps") or [])

    current_map_id = str(player.get("current_map") or "")
    current_map_name = str(player.get("current_map_name") or current_map_id or "未知")
    stamina = player.get("stamina", 0)
    try:
        stamina_int = int(float(stamina))
    except (TypeError, ValueError):
        stamina_int = 0

    # 找到当前地图节点
    current_node: dict[str, Any] = {}
    for m in maps:
        if str(m.get("id", "")) == current_map_id:
            current_node = m
            break

    spirit_density = float(current_node.get("spirit_density", 1.0) or 1.0)

    lines = [
        "🗺️ 大地图",
        "",
        f"📍 当前位置：{current_map_name}",
        f"灵气浓度：×{spirit_density:.2f}",
        f"⚡ 精力：{stamina_int}",
    ]

    if current_node.get("desc"):
        lines.append(f"_{current_node['desc']}_")

    # 可前往的相邻地点
    adjacent = [m for m in maps if m.get("is_adjacent") and str(m.get("id", "")) != current_map_id]
    if adjacent:
        lines.append("")
        lines.append("——🚶可前往——")
        for node in adjacent[:8]:
            name = str(node.get("name", node.get("id", "未知")))
            cost = _to_int(node.get("travel_cost"), 0)
            density = float(node.get("spirit_density", 1.0) or 1.0)
            if node.get("can_travel"):
                lines.append(f"• {name}  灵气×{density:.2f}  ⚡{cost}")
            else:
                reason = str(node.get("unlock_reason") or node.get("travel_block_reason") or "")
                lines.append(f"• 🔒 {name}（{reason}）" if reason else f"• 🔒 {name}")
    else:
        lines.append("")
        lines.append("当前无可前往的相邻地点。")

    return "\n".join(lines)


def format_travel_result(payload: dict[str, Any] | None = None) -> str:
    data = payload or {}
    if not data.get("success"):
        return f"❌ {data.get('message', '移动失败')}"

    to_name = str(data.get("to_name") or data.get("to_map") or "未知")
    stamina_cost = _to_int(data.get("stamina_cost"), 0)
    lines = [
        f"✅ 已抵达 *{to_name}*",
        f"⚡ 消耗精力 {stamina_cost}",
    ]
    if data.get("to_desc"):
        lines.append(f"_{data['to_desc']}_")
    if data.get("first_visit"):
        lines.append("🌟 首次到达！")
        if data.get("first_visit_text"):
            lines.append(str(data["first_visit_text"]))
    return "\n".join(lines)
