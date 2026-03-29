"""Travel / map handlers."""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_get,
    api_post,
    handle_expired_callback,
    parse_callback,
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.common import TravelFSM

router = Router(name="travel")


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


async def _load_map(uid: str) -> dict[str, Any]:
    return await api_get(f"/api/travel/map/{uid}", actor_uid=uid)


async def _show_map_query(query: CallbackQuery, state: FSMContext, uid: str, *, notice: str | None = None) -> None:
    data = await _load_map(uid)
    if not data.get("success"):
        msg = str(data.get("message") or "地图加载失败")
        await respond_query(query, f"❌ {msg}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await state.set_state(TravelFSM.viewing_map)
    player = data.get("player") or {}
    current_map_id = str(player.get("current_map") or "")
    maps = list(data.get("maps") or [])
    text = ui.format_travel_map_panel(data)
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(
        query,
        text,
        reply_markup=ui.travel_map_keyboard(maps, current_map_id=current_map_id),
    )


async def _show_map_message(message: Message, state: FSMContext, uid: str) -> None:
    data = await _load_map(uid)
    if not data.get("success"):
        msg = str(data.get("message") or "地图加载失败")
        await reply_or_answer(message, f"❌ {msg}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    await state.set_state(TravelFSM.viewing_map)
    player = data.get("player") or {}
    current_map_id = str(player.get("current_map") or "")
    maps = list(data.get("maps") or [])
    await reply_or_answer(
        message,
        ui.format_travel_map_panel(data),
        reply_markup=ui.travel_map_keyboard(maps, current_map_id=current_map_id),
    )


@router.message(Command("xian_map"))
async def cmd_map(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_map_message(message, state, uid)


@router.callback_query(F.data.startswith("travel:"))
async def cb_travel(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "travel":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if action == "map":
        await _show_map_query(query, state, uid)
        return

    if action == "go":
        to_map = str(args[0] if args else "").strip()
        if not to_map:
            await _show_map_query(query, state, uid, notice="目标地图无效，已刷新地图。")
            return
        result = await api_post("/api/travel", {"user_id": uid, "to_map": to_map}, actor_uid=uid)
        if not result.get("success"):
            msg = str(result.get("message") or "移动失败")
            await _show_map_query(query, state, uid, notice=f"❌ {msg}")
            return
        # 移动成功，显示结果后刷新地图
        result_text = ui.format_travel_result(result)
        await _show_map_query(query, state, uid, notice=result_text)
        return

    if action == "info":
        to_map = str(args[0] if args else "").strip()
        if not to_map:
            await _show_map_query(query, state, uid)
            return
        # 查询移动信息
        data = await _load_map(uid)
        player = data.get("player") or {}
        current_map_id = str(player.get("current_map") or "")
        maps = list(data.get("maps") or [])
        target_node: dict[str, Any] = {}
        for m in maps:
            if str(m.get("id", "")) == to_map:
                target_node = m
                break
        if target_node:
            name = str(target_node.get("name", to_map))
            reason = str(target_node.get("unlock_reason") or target_node.get("travel_block_reason") or "条件未满足")
            notice = f"🔒 {name}：{reason}"
        else:
            notice = "未找到该地点信息。"
        await _show_map_query(query, state, uid, notice=notice)
        return

    # fallback
    await _show_map_query(query, state, uid, notice="该地图按钮已失效，已刷新地图。")
