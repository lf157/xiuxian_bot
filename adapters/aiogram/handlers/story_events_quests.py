"""Story/events/quests/boss/bounty/rank router."""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_get,
    api_post,
    handle_expired_callback,
    new_request_id,
    parse_callback,
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.economy import StoryEventsFSM

router = Router(name="story_events_quests")


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


def _normalize_event_payload(payload: dict | None) -> dict:
    data = dict(payload or {})
    rows = list(data.get("events") or [])
    events: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        event_id = str(item.get("event_id") or item.get("id") or "").strip()
        if event_id:
            item["event_id"] = event_id
            item["id"] = event_id
        if "can_claim" not in item:
            if "claimed_today" in item:
                item["can_claim"] = not bool(item.get("claimed_today"))
            else:
                item["can_claim"] = False
        events.append(item)
    data["events"] = events
    return data


async def _load_event_payload(uid: str) -> dict:
    data = await api_get(f"/api/events/status/{uid}", actor_uid=uid)
    if not data.get("success"):
        data = await api_get("/api/events", actor_uid=uid)
    return _normalize_event_payload(data)


def _story_menu_keyboard(story_data: dict | None) -> Any:
    builder = InlineKeyboardBuilder()
    story = story_data if isinstance(story_data, dict) else {}
    pending = list(story.get("pending_claims") or [])
    chapters = list(story.get("chapters") or [])

    if pending:
        builder.button(text=f"🎁 领取剧情奖励（{len(pending)}）", callback_data="story:claim")

    chapter_buttons = 0
    for row in pending[:6]:
        chapter_id = str(row.get("chapter_id") or row.get("id") or "").strip()
        if not chapter_id:
            continue
        title = str(row.get("title") or chapter_id)
        builder.button(text=f"📖 {title}", callback_data=f"story:chapter:{chapter_id}")
        chapter_buttons += 1

    if chapter_buttons == 0:
        for row in chapters[:6]:
            if not bool(row.get("unlocked")):
                continue
            chapter_id = str(row.get("chapter_id") or row.get("id") or "").strip()
            if not chapter_id:
                continue
            title = str(row.get("title") or chapter_id)
            builder.button(text=f"📖 {title}", callback_data=f"story:chapter:{chapter_id}")
            chapter_buttons += 1
            if chapter_buttons >= 6:
                break

    builder.button(text="🔄 刷新剧情", callback_data="story:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    if pending:
        builder.adjust(1, *([1] * chapter_buttons), 1, 1)
    else:
        builder.adjust(*([1] * chapter_buttons), 1, 1)
    return builder.as_markup()


def _format_story_status_panel(payload: dict | None) -> tuple[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    story = data.get("story") if isinstance(data.get("story"), dict) else {}
    lines = ["📖 剧情面板", ""]
    if not story:
        lines.append("当前暂无剧情数据。")
        return "\n".join(lines), _story_menu_keyboard({})

    unlocked = int(story.get("unlocked_count", 0) or 0)
    claimed = int(story.get("claimed_count", 0) or 0)
    total = int(story.get("total_chapters", 0) or 0)
    pending_count = int(story.get("pending_claim_count", 0) or 0)
    lines.append(f"章节进度：{claimed}/{total}（已解锁 {unlocked}）")
    lines.append(f"待领奖励：{pending_count}")

    pending = list(story.get("pending_claims") or [])
    if pending:
        lines.append("")
        lines.append("待领取章节：")
        for row in pending[:6]:
            chapter_id = str(row.get("chapter_id") or "")
            title = str(row.get("title") or chapter_id or "章节")
            lines.append(f"• {title}")

    counters = story.get("counters") if isinstance(story.get("counters"), dict) else {}
    if counters:
        lines.append("")
        lines.append("剧情计数：")
        lines.append(
            "• 签到:{signin} 修炼:{cultivate} 狩猎:{hunt} 秘境:{secret} 突破:{breakthrough}".format(
                signin=int(counters.get("signin_count", 0) or 0),
                cultivate=int(counters.get("cultivate_count", 0) or 0),
                hunt=int(counters.get("hunt_victory_count", 0) or 0),
                secret=int(counters.get("secret_realm_count", 0) or 0),
                breakthrough=int(counters.get("breakthrough_success_count", 0) or 0),
            )
        )

    return "\n".join(lines), _story_menu_keyboard(story)


def _story_read_keyboard(chapter_id: str, *, is_finished: bool) -> Any:
    builder = InlineKeyboardBuilder()
    if not is_finished:
        builder.button(text="📖 继续阅读", callback_data=f"story:chapter:{chapter_id}")
        builder.button(text="🧭 阅读节点", callback_data=f"story:node:{chapter_id}")
    builder.button(text="🔄 剧情主页", callback_data="story:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    if not is_finished:
        builder.adjust(2, 1, 1)
    else:
        builder.adjust(1, 1)
    return builder.as_markup()


def _enrich_quest_data(data: dict | None) -> dict:
    """将 quest_defs 中的 name/desc 合并到 quests 行，使 UI 层能显示中文名。"""
    payload = data if isinstance(data, dict) else {}
    defs = list(payload.get("quest_defs") or [])
    def_map = {str(d.get("id") or ""): d for d in defs if isinstance(d, dict)}
    quests = list(payload.get("quests") or [])
    for row in quests:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("quest_id") or row.get("id") or "")
        qdef = def_map.get(qid)
        if qdef:
            row.setdefault("name", qdef.get("name"))
            row.setdefault("desc", qdef.get("desc"))
            row.setdefault("description", qdef.get("desc"))
    payload["quests"] = quests
    return payload


def _find_quest(data: dict, quest_id: str) -> dict | None:
    qid = str(quest_id or "").strip()
    if not qid:
        return None
    for row in list(data.get("quests") or []):
        if not isinstance(row, dict):
            continue
        current = str(row.get("quest_id") or row.get("id") or "").strip()
        if current == qid:
            return row
    return None


def _find_event(data: dict, event_id: str) -> dict | None:
    eid = str(event_id or "").strip()
    if not eid:
        return None
    for row in list(data.get("events") or []):
        if not isinstance(row, dict):
            continue
        current = str(row.get("event_id") or row.get("id") or "").strip()
        if current == eid:
            return row
    return None


_BOUNTY_ACCEPT_FALLBACK_CODES = {"INVALID_STATUS", "FORBIDDEN", "NOT_ACCEPTED", "NOT_CLAIMER"}
_BOUNTY_ACCEPT_FALLBACK_HINTS = ("未处于进行中", "非接取者", "仅接取者", "未接取")


def _normalize_bounty_payload(payload: dict | None, uid: str) -> dict:
    data = dict(payload or {})
    rows = list(data.get("bounties") or data.get("entries") or [])
    bounties: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        bounty_id = str(item.get("id") or item.get("bounty_id") or "").strip()
        if bounty_id:
            item["id"] = bounty_id
            item["bounty_id"] = bounty_id
        status = str(item.get("status") or "").strip().lower()
        if status:
            item["status"] = status
        bounties.append(item)
    data["bounties"] = bounties
    data["_actor_uid"] = uid
    return data


async def _load_bounty_payload(uid: str) -> dict:
    data = await api_get("/api/bounties", params={"status": "all", "limit": 30}, actor_uid=uid)
    return _normalize_bounty_payload(data, uid)


def _should_fallback_to_bounty_accept(result: dict | None) -> bool:
    payload = result if isinstance(result, dict) else {}
    if payload.get("success"):
        return False
    code = str(payload.get("code") or "").strip().upper()
    message = str(payload.get("message") or "").strip()
    if code in _BOUNTY_ACCEPT_FALLBACK_CODES:
        return True
    return any(token in message for token in _BOUNTY_ACCEPT_FALLBACK_HINTS)


@router.message(Command("xian_quest"))
async def cmd_quest(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = _enrich_quest_data(await api_get(f"/api/quests/{uid}", actor_uid=uid))
    await state.set_state(StoryEventsFSM.quest_menu)
    await state.update_data(uid=uid)
    await reply_or_answer(message, ui.format_quest_panel(data), reply_markup=ui.quest_menu_keyboard(data))


@router.message(Command("xian_events"))
async def cmd_events(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await _load_event_payload(uid)
    await state.set_state(StoryEventsFSM.event_menu)
    await reply_or_answer(message, ui.format_event_panel(data), reply_markup=ui.event_menu_keyboard(data))


@router.message(Command("xian_bounty"))
async def cmd_bounty(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await _load_bounty_payload(uid)
    await state.set_state(StoryEventsFSM.bounty_menu)
    await reply_or_answer(message,
        ui.format_bounty_panel(data, actor_uid=uid),
        reply_markup=ui.bounty_menu_keyboard(data, actor_uid=uid),
    )


@router.message(Command("xian_boss"))
async def cmd_worldboss(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get("/api/worldboss/status", actor_uid=uid)
    await state.set_state(StoryEventsFSM.boss_menu)
    await reply_or_answer(message, ui.format_boss_panel(data), reply_markup=ui.boss_menu_keyboard())


@router.message(Command("xian_rank"))
async def cmd_rank(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get("/api/pvp/ranking", params={"limit": 10}, actor_uid=uid)
    await state.set_state(StoryEventsFSM.rank_menu)
    await reply_or_answer(message, ui.format_rank_panel(data), reply_markup=ui.rank_menu_keyboard())


@router.message(Command("xian_guide"))
async def cmd_guide(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/story/{uid}", actor_uid=uid)
    await state.set_state(StoryEventsFSM.story_menu)
    text, keyboard = _format_story_status_panel(data)
    await reply_or_answer(message, text, reply_markup=keyboard)


@router.message(Command("xian_achievements"))
async def cmd_achievements(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/story/{uid}", actor_uid=uid)
    await state.set_state(StoryEventsFSM.story_menu)
    text, keyboard = _format_story_status_panel(data)
    await reply_or_answer(message, text, reply_markup=keyboard)


@router.message(Command("xian_codex"))
async def cmd_codex(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/codex/{uid}", params={"kind": "items"}, actor_uid=uid)
    lines = ["📚 图鉴（物品）", ""]
    items = list(data.get("items") or [])
    if not items:
        lines.append("暂无图鉴数据。")
    else:
        for row in items[:10]:
            name = str(row.get("name") or row.get("item_id") or "未知物品")
            lines.append(f"• {name}")
    await state.set_state(StoryEventsFSM.story_menu)
    await reply_or_answer(message, "\n".join(lines), reply_markup=ui.main_menu_keyboard(registered=True))


@router.callback_query(
    F.data.startswith("quest:")
    | F.data.startswith("event:")
    | F.data.startswith("story:")
    | F.data.startswith("boss:")
    | F.data.startswith("bounty:")
    | F.data.startswith("rank:")
)
async def cb_story_events_quests(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    if domain == "quest":
        if action == "list":
            data = _enrich_quest_data(await api_get(f"/api/quests/{uid}", actor_uid=uid))
            await state.set_state(StoryEventsFSM.quest_menu)
            await respond_query(query, ui.format_quest_panel(data), reply_markup=ui.quest_menu_keyboard(data))
            await safe_answer(query)
            return
        if action == "claim":
            quest_id = args[0] if args else ""
            if not str(quest_id).strip():
                refreshed = _enrich_quest_data(await api_get(f"/api/quests/{uid}", actor_uid=uid))
                await respond_query(query, ui.format_quest_panel(refreshed), reply_markup=ui.quest_menu_keyboard(refreshed))
                await safe_answer(query, text="任务ID无效")
                return
            result = await api_post("/api/quests/claim", {"user_id": uid, "quest_id": quest_id, "request_id": new_request_id()}, actor_uid=uid)
            refreshed = _enrich_quest_data(await api_get(f"/api/quests/{uid}", actor_uid=uid))
            panel_text = ui.format_quest_panel(refreshed)
            if result.get("success"):
                rewards = result.get("rewards") if isinstance(result.get("rewards"), dict) else {}
                parts = []
                if int(rewards.get("copper", 0) or 0):
                    parts.append(f"🟦下品灵石+{int(rewards['copper'])}")
                if int(rewards.get("exp", 0) or 0):
                    parts.append(f"📖修为+{int(rewards['exp'])}")
                if int(rewards.get("gold", 0) or 0):
                    parts.append(f"🟩中品灵石+{int(rewards['gold'])}")
                reward_line = "  ".join(parts) if parts else "领取成功"
                panel_text = f"🎁 {reward_line}\n\n{panel_text}"
            else:
                panel_text = f"⚠️ {result.get('message') or '领取失败'}\n\n{panel_text}"
            await respond_query(query, panel_text, reply_markup=ui.quest_menu_keyboard(refreshed))
            await safe_answer(query, text="领取成功" if result.get("success") else str(result.get("message") or "领取失败"))
            return
        if action == "detail":
            quest_id = args[0] if args else ""
            data = _enrich_quest_data(await api_get(f"/api/quests/{uid}", actor_uid=uid))
            quest = _find_quest(data, quest_id)
            if quest is None:
                text = f"📜 未找到任务：{quest_id}" if quest_id else "📜 任务ID无效"
            else:
                name = str(quest.get("name") or quest.get("quest_id") or quest_id or "任务")
                progress = int(quest.get("progress") or 0)
                goal = max(1, int(quest.get("goal") or 1))
                claimed = bool(quest.get("claimed"))
                status = "已领取" if claimed else ("可领取" if progress >= goal else "进行中")
                detail = str(quest.get("desc") or quest.get("description") or "").strip()
                lines = ["📜 任务详情", f"名称: {name}", f"进度: {progress}/{goal}", f"状态: {status}"]
                if detail:
                    lines.append(f"说明: {detail}")
                text = "\n".join(lines)
            await respond_query(query, text, reply_markup=ui.quest_menu_keyboard(data))
            await safe_answer(query)
            return

    if domain == "event":
        if action == "list":
            data = await _load_event_payload(uid)
            await state.set_state(StoryEventsFSM.event_menu)
            await respond_query(query, ui.format_event_panel(data), reply_markup=ui.event_menu_keyboard(data))
            await safe_answer(query)
            return
        if action == "claim":
            event_id = args[0] if args else ""
            if not str(event_id).strip():
                refreshed = await _load_event_payload(uid)
                await respond_query(query, ui.format_event_panel(refreshed), reply_markup=ui.event_menu_keyboard(refreshed))
                await safe_answer(query, text="活动ID无效")
                return
            result = await api_post("/api/events/claim", {"user_id": uid, "event_id": event_id}, actor_uid=uid)
            refreshed = await _load_event_payload(uid)
            panel_text = ui.format_event_panel(refreshed)
            if result.get("success"):
                rewards = result.get("rewards") if isinstance(result.get("rewards"), dict) else {}
                parts = []
                if int(rewards.get("copper", 0) or 0):
                    parts.append(f"🟦下品灵石+{int(rewards['copper'])}")
                if int(rewards.get("exp", 0) or 0):
                    parts.append(f"📖修为+{int(rewards['exp'])}")
                if int(rewards.get("gold", 0) or 0):
                    parts.append(f"🟩中品灵石+{int(rewards['gold'])}")
                items = rewards.get("items") or []
                for it in items:
                    item_name = it.get("item_name") or it.get("item_id") or "物品"
                    qty = int(it.get("quantity", 1) or 1)
                    parts.append(f"📦{item_name}x{qty}")
                pts = int(result.get("points_granted", 0) or 0)
                if pts > 0:
                    parts.append(f"🏅积分+{pts}")
                reward_line = "  ".join(parts) if parts else "领取成功"
                panel_text = f"🎁 {reward_line}\n\n{panel_text}"
            else:
                panel_text = f"⚠️ {result.get('message') or '领取失败'}\n\n{panel_text}"
            await respond_query(query, panel_text, reply_markup=ui.event_menu_keyboard(refreshed))
            await safe_answer(query, text="领取成功" if result.get("success") else str(result.get("message") or "领取失败"))
            return
        if action == "detail":
            event_id = args[0] if args else ""
            data = await _load_event_payload(uid)
            event = _find_event(data, event_id)
            if event is None:
                text = f"🎉 未找到活动：{event_id}" if event_id else "🎉 活动ID无效"
            else:
                name = str(event.get("name") or event.get("event_id") or event_id or "活动")
                detail = str(event.get("desc") or event.get("description") or "").strip()
                claimed_today = bool(event.get("claimed_today"))
                can_claim = bool(event.get("can_claim"))
                status = "今日已领取" if claimed_today else ("可领取" if can_claim else "进行中")
                lines = ["🎉 活动详情", f"名称: {name}", f"状态: {status}"]
                if event.get("remaining_days") is not None:
                    lines.append(f"剩余天数: {event.get('remaining_days')}")
                if event.get("points_balance") is not None:
                    lines.append(f"积分: {event.get('points_balance')}")
                if detail:
                    lines.append(f"说明: {detail}")
                text = "\n".join(lines)
            await respond_query(query, text, reply_markup=ui.event_menu_keyboard(data))
            await safe_answer(query)
            return

    if domain == "story":
        await state.set_state(StoryEventsFSM.story_menu)
        if action == "menu":
            data = await api_get(f"/api/story/{uid}", actor_uid=uid)
            text, keyboard = _format_story_status_panel(data)
            await respond_query(query, text, reply_markup=keyboard)
            await safe_answer(query)
            return
        if action in {"chapter", "node"}:
            chapter_id = str(args[0] if args else "").strip()
            if not chapter_id:
                data = await api_get(f"/api/story/{uid}", actor_uid=uid)
                text, keyboard = _format_story_status_panel(data)
                await respond_query(query, text, reply_markup=keyboard)
                await safe_answer(query, text="请选择章节")
                return
            result = await api_post(
                "/api/story/read",
                {"user_id": uid, "chapter_id": chapter_id, "count": 5},
                actor_uid=uid,
            )
            if not result.get("success"):
                data = await api_get(f"/api/story/{uid}", actor_uid=uid)
                text, keyboard = _format_story_status_panel(data)
                msg = str(result.get("message") or "章节读取失败")
                await respond_query(query, f"❌ {msg}\n\n{text}", reply_markup=keyboard)
                await safe_answer(query, text=msg)
                return
            title = str(result.get("title") or chapter_id)
            lines = [f"📖 {title}", ""]
            chunks = list(result.get("lines") or [])
            if not chunks:
                lines.append("暂无新内容。")
            else:
                for row in chunks[:10]:
                    lines.append(str(row))
            current = int(result.get("current_line", 0) or 0)
            total = int(result.get("total_lines", 0) or 0)
            is_finished = bool(result.get("is_finished"))
            lines.append("")
            lines.append(f"进度：{current}/{total}")
            if is_finished:
                lines.append("✅ 本章阅读完成，可回剧情主页领取章节奖励。")
            await respond_query(
                query,
                "\n".join(lines),
                reply_markup=_story_read_keyboard(chapter_id, is_finished=is_finished),
            )
            await safe_answer(query)
            return
        if action == "claim":
            result = await api_post("/api/story/claim", {"user_id": uid}, actor_uid=uid)
            data = await api_get(f"/api/story/{uid}", actor_uid=uid)
            text, keyboard = _format_story_status_panel(data)
            if result.get("success"):
                chapter = result.get("chapter") if isinstance(result.get("chapter"), dict) else {}
                rewards = result.get("rewards") if isinstance(result.get("rewards"), dict) else {}
                reward_line = f"修为+{int(rewards.get('exp', 0) or 0)} 灵石+{int(rewards.get('copper', 0) or 0)}"
                chapter_title = str(chapter.get("title") or chapter.get("chapter_id") or "章节")
                text = f"🎁 已领取：{chapter_title}\n{reward_line}\n\n{text}"
            else:
                msg = str(result.get("message") or "暂无可领取剧情奖励")
                text = f"⚠️ {msg}\n\n{text}"
            await respond_query(query, text, reply_markup=keyboard)
            await safe_answer(query, text="领取成功" if result.get("success") else str(result.get("message") or "领取失败"))
            return
        data = await api_get(f"/api/story/{uid}", actor_uid=uid)
        text, keyboard = _format_story_status_panel(data)
        await respond_query(query, text, reply_markup=keyboard)
        await safe_answer(query)
        return

    if domain == "boss":
        if action == "menu":
            data = await api_get("/api/worldboss/status", actor_uid=uid)
            await state.set_state(StoryEventsFSM.boss_menu)
            await respond_query(query, ui.format_boss_panel(data), reply_markup=ui.boss_menu_keyboard())
            await safe_answer(query)
            return
        if action == "attack":
            result = await api_post("/api/worldboss/attack", {"user_id": uid}, actor_uid=uid)
            refreshed = await api_get("/api/worldboss/status", actor_uid=uid)
            panel_text = ui.format_boss_panel(refreshed)
            if result.get("success"):
                details: list[str] = []
                if result.get("damage") is not None:
                    details.append(f"本次伤害: {result.get('damage')}")
                rewards = result.get("rewards") or {}
                reward_copper = int(rewards.get("copper", 0) or 0)
                reward_exp = int(rewards.get("exp", 0) or 0)
                reward_gold = int(rewards.get("gold", 0) or 0)
                if reward_exp > 0:
                    details.append(f"修为 +{reward_exp:,}")
                if reward_copper > 0:
                    details.append(f"🟦 下品灵石 +{reward_copper:,}")
                if reward_gold > 0:
                    details.append(f"🟩 中品灵石 +{reward_gold:,}")
                reward_items = list(rewards.get("items") or [])
                for item in reward_items[:5]:
                    item_name = str(item.get("item_name") or item.get("item_id") or "物品")
                    qty = int(item.get("quantity", 1) or 1)
                    details.append(f"🎒 {item_name} x{qty}")
                if result.get("attacks_left") is not None:
                    details.append(f"剩余次数: {result.get('attacks_left')}")
                if result.get("defeated"):
                    details.append("🎉 世界BOSS已被击败！")
                if details:
                    panel_text = f"{panel_text}\n\n" + "\n".join(f"• {line}" for line in details)
            elif result.get("message"):
                panel_text = f"{panel_text}\n\n⚠️ {result.get('message')}"
            await respond_query(query, panel_text, reply_markup=ui.boss_menu_keyboard())
            message = "攻击完成" if result.get("success") else str(result.get("message") or "攻击失败")
            await safe_answer(query, text=message)
            return
        if action == "rank":
            data = await api_get("/api/worldboss/status", actor_uid=uid)
            text = f"{ui.format_boss_panel(data)}\n\n📌 暂无世界BOSS排行接口，已显示当前BOSS状态。"
            await respond_query(query, text, reply_markup=ui.boss_menu_keyboard())
            await safe_answer(query, text="暂无BOSS排行接口")
            return

    if domain == "bounty":
        await state.set_state(StoryEventsFSM.bounty_menu)
        toast: str | None = None
        if action == "claim":
            bounty_id_raw = str(args[0] if args else "").strip()
            if not bounty_id_raw:
                toast = "悬赏ID无效"
            else:
                try:
                    bounty_id = int(bounty_id_raw)
                except (TypeError, ValueError):
                    bounty_id = 0
                if bounty_id <= 0:
                    toast = "悬赏ID无效"
                else:
                    submit_result = await api_post(
                        "/api/bounty/submit",
                        {"user_id": uid, "bounty_id": bounty_id},
                        actor_uid=uid,
                    )
                    if submit_result.get("success"):
                        toast = str(submit_result.get("message") or "提交成功")
                    elif _should_fallback_to_bounty_accept(submit_result):
                        accept_result = await api_post(
                            "/api/bounty/accept",
                            {"user_id": uid, "bounty_id": bounty_id},
                            actor_uid=uid,
                        )
                        if accept_result.get("success"):
                            toast = str(accept_result.get("message") or "接取成功")
                        else:
                            toast = str(accept_result.get("message") or "接取失败")
                    else:
                        toast = str(submit_result.get("message") or "悬赏操作失败")
        elif action == "refresh":
            toast = "已刷新悬赏列表"
        elif action != "menu":
            toast = "该悬赏按钮已失效，已返回悬赏面板"
        data = await _load_bounty_payload(uid)
        await respond_query(
            query,
            ui.format_bounty_panel(data, actor_uid=uid),
            reply_markup=ui.bounty_menu_keyboard(data, actor_uid=uid),
        )
        if toast:
            await safe_answer(query, text=toast)
        else:
            await safe_answer(query)
        return

    if domain == "rank":
        data = await api_get("/api/pvp/ranking", params={"limit": 10}, actor_uid=uid)
        await state.set_state(StoryEventsFSM.rank_menu)
        await respond_query(query, ui.format_rank_panel(data), reply_markup=ui.rank_menu_keyboard())
        if action == "menu":
            await safe_answer(query)
        elif action in {"realm", "combat", "wealth"}:
            await safe_answer(query, text="后端当前仅支持综合榜，已显示默认排行")
        else:
            await safe_answer(query, text="该排行按钮已失效，已返回排行面板")
        return

    await handle_expired_callback(query)
