"""Story/events/quests/boss/bounty/rank router."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_get,
    api_post,
    handle_expired_callback,
    new_request_id,
    parse_callback,
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


@router.message(Command("xian_quest", "xian_quests", "xian_task", "quest", "quests", "task"))
async def cmd_quest(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/quests/{uid}", actor_uid=uid)
    await state.set_state(StoryEventsFSM.quest_menu)
    await state.update_data(uid=uid)
    await message.answer(ui.format_quest_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_events", "events"))
async def cmd_events(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/events/status/{uid}", actor_uid=uid)
    if not data.get("success"):
        data = await api_get("/api/events", actor_uid=uid)
    await state.set_state(StoryEventsFSM.event_menu)
    await message.answer(ui.format_event_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_bounty", "bounty"))
async def cmd_bounty(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.set_state(StoryEventsFSM.bounty_menu)
    await message.answer(ui.format_bounty_panel({"user_id": uid}), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_worldboss", "xian_boss", "worldboss", "boss"))
async def cmd_worldboss(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get("/api/worldboss/status", actor_uid=uid)
    await state.set_state(StoryEventsFSM.boss_menu)
    await message.answer(ui.format_boss_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_rank", "xian_leaderboard", "rank", "leaderboard"))
async def cmd_rank(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get("/api/pvp/ranking", params={"limit": 10}, actor_uid=uid)
    await state.set_state(StoryEventsFSM.rank_menu)
    await message.answer(ui.format_rank_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_guide", "xian_realms", "guide", "realms"))
async def cmd_guide(message: Message, state: FSMContext) -> None:
    await state.set_state(StoryEventsFSM.story_menu)
    await message.answer(ui.format_story_panel({"topic": "guide"}), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_achievements", "xian_ach", "achievements", "ach"))
async def cmd_achievements(message: Message, state: FSMContext) -> None:
    await state.set_state(StoryEventsFSM.story_menu)
    await message.answer(ui.format_story_panel({"topic": "achievements"}), reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_codex", "codex"))
async def cmd_codex(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    data = await api_get(f"/api/codex/{uid}", params={"kind": "items"}, actor_uid=uid)
    await state.set_state(StoryEventsFSM.story_menu)
    await message.answer(ui.format_story_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))


@router.callback_query(
    F.data.startswith("quest:")
    | F.data.startswith("event:")
    | F.data.startswith("story:")
    | F.data.startswith("boss:")
    | F.data.startswith("bounty:")
    | F.data.startswith("rank:")
)
async def cb_story_events_quests(query: CallbackQuery, state: FSMContext) -> None:
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
            data = await api_get(f"/api/quests/{uid}", actor_uid=uid)
            await state.set_state(StoryEventsFSM.quest_menu)
            await respond_query(query, ui.format_quest_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return
        if action == "claim":
            quest_id = args[0] if args else ""
            result = await api_post("/api/quests/claim", {"user_id": uid, "quest_id": quest_id, "request_id": new_request_id()}, actor_uid=uid)
            await respond_query(query, ui.format_quest_panel(result), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query, text="领取成功" if result.get("success") else "领取失败")
            return
        if action == "detail":
            await respond_query(query, f"📜 任务详情：{args[0] if args else ''}", reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return

    if domain == "event":
        if action == "list":
            data = await api_get("/api/events", actor_uid=uid)
            await state.set_state(StoryEventsFSM.event_menu)
            await respond_query(query, ui.format_event_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return
        if action == "claim":
            event_id = args[0] if args else ""
            result = await api_post("/api/events/claim", {"user_id": uid, "event_id": event_id}, actor_uid=uid)
            await respond_query(query, ui.format_event_panel(result), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query, text="领取成功" if result.get("success") else "领取失败")
            return
        if action == "detail":
            await respond_query(query, f"🎉 活动详情：{args[0] if args else ''}", reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return

    if domain == "story":
        await state.set_state(StoryEventsFSM.story_menu)
        await respond_query(query, ui.format_story_panel({"action": action, "args": args}), reply_markup=ui.main_menu_keyboard(registered=True))
        await safe_answer(query)
        return

    if domain == "boss":
        if action == "menu":
            data = await api_get("/api/worldboss/status", actor_uid=uid)
            await state.set_state(StoryEventsFSM.boss_menu)
            await respond_query(query, ui.format_boss_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return
        if action == "attack":
            result = await api_post("/api/worldboss/attack", {"user_id": uid}, actor_uid=uid)
            await respond_query(query, ui.format_boss_panel(result), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query, text="攻击完成" if result.get("success") else "攻击失败")
            return
        if action == "rank":
            data = await api_get("/api/worldboss/status", actor_uid=uid)
            await respond_query(query, ui.format_rank_panel(data), reply_markup=ui.main_menu_keyboard(registered=True))
            await safe_answer(query)
            return

    if domain == "bounty":
        await state.set_state(StoryEventsFSM.bounty_menu)
        await respond_query(query, ui.format_bounty_panel({"action": action, "arg": args[0] if args else ""}), reply_markup=ui.main_menu_keyboard(registered=True))
        await safe_answer(query)
        return

    if domain == "rank":
        data = await api_get("/api/pvp/ranking", params={"limit": 10}, actor_uid=uid)
        await state.set_state(StoryEventsFSM.rank_menu)
        await respond_query(query, ui.format_rank_panel({"type": action, "data": data}), reply_markup=ui.main_menu_keyboard(registered=True))
        await safe_answer(query)
        return

    await handle_expired_callback(query)
