"""Storage bag and gear handlers."""

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
    new_request_id,
    parse_callback,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.inventory import InventoryFSM

router = Router(name="inventory_equipment")

_PAGE_SIZE = 8


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


async def _uid_from_message(message: Message, state: FSMContext) -> str | None:
    if message.from_user is None:
        return None
    data = await state.get_data()
    uid = str(data.get("uid") or "").strip()
    if uid:
        return uid
    uid = await resolve_uid(int(message.from_user.id))
    if uid:
        await state.update_data(uid=uid)
    return uid


async def _uid_from_query(query: CallbackQuery, state: FSMContext) -> str | None:
    if query.from_user is None:
        return None
    data = await state.get_data()
    uid = str(data.get("uid") or "").strip()
    if uid:
        return uid
    uid = await resolve_uid(int(query.from_user.id))
    if uid:
        await state.update_data(uid=uid)
    return uid


def _split_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bag_items: list[dict[str, Any]] = []
    gear_items: list[dict[str, Any]] = []
    equipped_items: list[dict[str, Any]] = []
    for item in items:
        is_equipment = bool(item.get("is_equipment") or item.get("category") == "equipment" or item.get("slot"))
        if bool(item.get("equipped")):
            equipped_items.append(item)
        if is_equipment:
            gear_items.append(item)
        else:
            bag_items.append(item)
    return bag_items, gear_items, equipped_items


def _paginate(rows: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int, int]:
    total_pages = max(1, (len(rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    current = max(1, min(page, total_pages))
    start = (current - 1) * _PAGE_SIZE
    return rows[start:start + _PAGE_SIZE], current, total_pages


async def _load_items(uid: str) -> list[dict[str, Any]]:
    result = await api_get(f"/api/items/{uid}", actor_uid=uid)
    if not result.get("success"):
        return []
    return list(result.get("items") or [])


async def _show_bag_message(message: Message, state: FSMContext, uid: str, page: int = 1) -> None:
    items = await _load_items(uid)
    bag_items, _, _ = _split_items(items)
    page_rows, cur, total = _paginate(bag_items, page)
    await state.set_state(InventoryFSM.bag_browsing)
    await state.update_data(uid=uid, bag_page=cur)
    await message.answer(ui.format_bag_panel(page_rows, cur, total), reply_markup=ui.bag_items_keyboard(page_rows, cur, total))


async def _show_bag_query(query: CallbackQuery, state: FSMContext, uid: str, page: int = 1) -> None:
    items = await _load_items(uid)
    bag_items, _, _ = _split_items(items)
    page_rows, cur, total = _paginate(bag_items, page)
    await state.set_state(InventoryFSM.bag_browsing)
    await state.update_data(uid=uid, bag_page=cur)
    await respond_query(query, ui.format_bag_panel(page_rows, cur, total), reply_markup=ui.bag_items_keyboard(page_rows, cur, total))


async def _show_gear_query(query: CallbackQuery, state: FSMContext, uid: str, page: int = 1) -> None:
    items = await _load_items(uid)
    _, gear_items, _ = _split_items(items)
    page_rows, cur, total = _paginate(gear_items, page)
    await state.set_state(InventoryFSM.gear_browsing)
    await state.update_data(uid=uid, gear_page=cur)
    await respond_query(query, ui.format_gear_panel(page_rows, cur, total), reply_markup=ui.gear_items_keyboard(page_rows, cur, total))


async def _show_equipped_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    items = await _load_items(uid)
    _, _, equipped = _split_items(items)
    await state.set_state(InventoryFSM.equipped_view)
    lines = ["📌 已佩戴灵装", ""]
    if not equipped:
        lines.append("当前没有已佩戴灵装。")
    for item in equipped[:12]:
        name = str(item.get("name", item.get("item_id", "未知灵装")))
        slot = str(item.get("slot") or "未知部位")
        lines.append(f"• {name} ({slot})")
    await respond_query(query, "\n".join(lines), reply_markup=ui.main_menu_keyboard(registered=True))


async def _try_number(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.message(Command("xian_bag", "xian_inventory", "bag", "inventory"))
async def cmd_bag(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_bag_message(message, state, uid, page=1)


@router.callback_query(F.data.startswith("bag:") | F.data.startswith("gear:"))
async def cb_inventory(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain not in {"bag", "gear"}:
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if domain == "bag":
        if action == "page":
            page = max(1, await _try_number(args[0]) if args else 1) if args else 1
            await _show_bag_query(query, state, uid, page=page)
            return
        if action == "detail":
            if not args:
                await handle_expired_callback(query)
                return
            await respond_query(query, f"🎒 物品详情: {args[0]}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        if action == "use":
            if not args:
                await handle_expired_callback(query)
                return
            item_id = args[0]
            result = await api_post(
                "/api/item/use",
                payload={"user_id": uid, "item_id": item_id},
                actor_uid=uid,
                request_id=new_request_id(),
            )
            if not result.get("success"):
                await respond_query(query, f"❌ {_error_text(result, '道具使用失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
                return
            await _show_bag_query(query, state, uid, page=1)
            return
        await handle_expired_callback(query)
        return

    if action == "page":
        page = max(1, await _try_number(args[0]) if args else 1) if args else 1
        await _show_gear_query(query, state, uid, page=page)
        return
    if action == "equipped_view":
        await _show_equipped_query(query, state, uid)
        return
    if action == "detail":
        if not args:
            await handle_expired_callback(query)
            return
        await respond_query(query, f"👕 灵装详情: {args[0]}", reply_markup=ui.main_menu_keyboard(registered=True))
        return
    if action in {"equip", "enhance", "decompose"}:
        if not args:
            await handle_expired_callback(query)
            return
        item_id = await _try_number(args[0])
        if item_id is None:
            await handle_expired_callback(query)
            return
        endpoint = "/api/equip" if action == "equip" else ("/api/enhance" if action == "enhance" else "/api/decompose")
        payload = {"user_id": uid, "item_id": item_id}
        result = await api_post(endpoint, payload=payload, actor_uid=uid, request_id=new_request_id())
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '灵装操作失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        await _show_gear_query(query, state, uid, page=1)
        return
    if action == "unequip":
        if not args:
            await handle_expired_callback(query)
            return
        slot = args[0]
        result = await api_post(
            "/api/unequip",
            payload={"user_id": uid, "slot": slot},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '卸下失败')}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        await _show_equipped_query(query, state, uid)
        return
    await handle_expired_callback(query)
