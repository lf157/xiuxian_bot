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
    reject_non_owner,
    reply_or_answer,
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
        item_type = str(item.get("item_type") or "").strip().lower()
        is_equipment = bool(
            item.get("is_equipment")
            or item.get("category") == "equipment"
            or item.get("slot")
            or item_type in {"weapon", "armor", "accessory", "equipment"}
        )
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


async def _load_items(uid: str) -> tuple[list[dict[str, Any]], str | None]:
    result = await api_get(f"/api/items/{uid}", actor_uid=uid)
    if not result.get("success"):
        return [], _error_text(result, "储物袋数据获取失败")
    return list(result.get("items") or []), None


async def _show_bag_message(
    message: Message,
    state: FSMContext,
    uid: str,
    page: int = 1,
    notice: str | None = None,
) -> None:
    items, err = await _load_items(uid)
    if err:
        await state.set_state(InventoryFSM.bag_browsing)
        await state.update_data(uid=uid, bag_page=1)
        text = f"❌ {err}"
        if notice:
            text = f"{notice}\n\n{text}"
        await reply_or_answer(message, text, reply_markup=ui.main_menu_keyboard(registered=True))
        return
    bag_items, _, _ = _split_items(items)
    page_rows, cur, total = _paginate(bag_items, page)
    await state.set_state(InventoryFSM.bag_browsing)
    await state.update_data(uid=uid, bag_page=cur)
    text = ui.format_bag_panel(page_rows, cur, total)
    if notice:
        text = f"{notice}\n\n{text}"
    await reply_or_answer(message, text, reply_markup=ui.bag_items_keyboard(page_rows, cur, total))


async def _show_bag_query(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    page: int = 1,
    notice: str | None = None,
) -> None:
    items, err = await _load_items(uid)
    if err:
        await state.set_state(InventoryFSM.bag_browsing)
        await state.update_data(uid=uid, bag_page=1)
        text = f"❌ {err}"
        if notice:
            text = f"{notice}\n\n{text}"
        await respond_query(query, text, reply_markup=ui.main_menu_keyboard(registered=True))
        return
    bag_items, _, _ = _split_items(items)
    page_rows, cur, total = _paginate(bag_items, page)
    await state.set_state(InventoryFSM.bag_browsing)
    await state.update_data(uid=uid, bag_page=cur)
    text = ui.format_bag_panel(page_rows, cur, total)
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(query, text, reply_markup=ui.bag_items_keyboard(page_rows, cur, total))


async def _show_gear_query(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    page: int = 1,
    notice: str | None = None,
) -> None:
    items, err = await _load_items(uid)
    if err:
        await state.set_state(InventoryFSM.gear_browsing)
        await state.update_data(uid=uid, gear_page=1)
        text = f"❌ {err}"
        if notice:
            text = f"{notice}\n\n{text}"
        await respond_query(query, text, reply_markup=ui.main_menu_keyboard(registered=True))
        return
    _, gear_items, _ = _split_items(items)
    page_rows, cur, total = _paginate(gear_items, page)
    await state.set_state(InventoryFSM.gear_browsing)
    await state.update_data(uid=uid, gear_page=cur)
    text = ui.format_gear_panel(page_rows, cur, total)
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(query, text, reply_markup=ui.gear_items_keyboard(page_rows, cur, total))


async def _show_equipped_query(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    notice: str | None = None,
) -> None:
    items, err = await _load_items(uid)
    if err:
        await state.set_state(InventoryFSM.equipped_view)
        text = f"❌ {err}"
        if notice:
            text = f"{notice}\n\n{text}"
        await respond_query(query, text, reply_markup=ui.main_menu_keyboard(registered=True))
        return
    _, _, equipped_from_items = _split_items(items)
    status_data = await api_get(f"/api/stat/{uid}", actor_uid=uid)
    status = status_data.get("status") if isinstance(status_data, dict) else {}
    item_by_id: dict[int, dict[str, Any]] = {}
    for row in items:
        try:
            item_db_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        item_by_id[item_db_id] = row

    slot_defs = [
        ("equipped_weapon", "武器"),
        ("equipped_armor", "护甲"),
        ("equipped_accessory1", "饰品一"),
        ("equipped_accessory2", "饰品二"),
    ]
    equipped_rows: list[dict[str, Any]] = []
    if isinstance(status, dict):
        for slot_key, slot_name in slot_defs:
            raw_id = status.get(slot_key)
            try:
                item_db_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            item = item_by_id.get(item_db_id) or {}
            item_name = str(item.get("name") or item.get("item_name") or f"#{item_db_id}")
            item_level = int(item.get("enhance_level", 0) or 0)
            equipped_rows.append(
                {
                    "slot": slot_key,
                    "slot_name": slot_name,
                    "item_db_id": item_db_id,
                    "name": item_name,
                    "enhance_level": item_level,
                }
            )
    if not equipped_rows and equipped_from_items:
        for idx, item in enumerate(equipped_from_items[:4], start=1):
            equipped_rows.append(
                {
                    "slot": "",
                    "slot_name": str(item.get("slot") or f"部位{idx}"),
                    "item_db_id": item.get("id"),
                    "name": str(item.get("name") or item.get("item_name") or item.get("item_id") or "未知灵装"),
                    "enhance_level": int(item.get("enhance_level", 0) or 0),
                }
            )

    await state.set_state(InventoryFSM.equipped_view)
    lines = ["📌 已佩戴灵装", ""]
    if notice:
        lines = [notice, ""] + lines
    if not equipped_rows:
        lines.append("当前没有已佩戴灵装。")
    for row in equipped_rows[:12]:
        level = int(row.get("enhance_level", 0) or 0)
        level_text = f" +{level}" if level > 0 else ""
        lines.append(f"• {row.get('slot_name')}：{row.get('name')}{level_text}")
    await respond_query(query, "\n".join(lines), reply_markup=ui.gear_equipped_keyboard(equipped_rows))


async def _try_number(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _parse_page_arg(args: list[str]) -> int:
    if not args:
        return 1
    value = await _try_number(args[0])
    if value is None:
        return 1
    return max(1, value)


@router.message(Command("xian_bag"))
async def cmd_bag(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_bag_message(message, state, uid, page=1)


@router.callback_query(F.data.startswith("bag:") | F.data.startswith("gear:"))
async def cb_inventory(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
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
            page = await _parse_page_arg(args)
            await _show_bag_query(query, state, uid, page=page)
            return
        if action == "detail":
            if not args:
                await _show_bag_query(query, state, uid, page=1, notice="缺少物品参数，已返回储物袋列表。")
                return
            item_id = args[0]
            await respond_query(
                query,
                f"🎒 物品详情: {item_id}",
                reply_markup=ui.bag_detail_keyboard(item_id),
            )
            return
        if action == "use":
            if not args:
                await _show_bag_query(query, state, uid, page=1, notice="缺少物品参数，已返回储物袋列表。")
                return
            item_id = args[0]
            result = await api_post(
                "/api/item/use",
                payload={"user_id": uid, "item_id": item_id},
                actor_uid=uid,
                request_id=new_request_id(),
            )
            if not result.get("success"):
                await _show_bag_query(query, state, uid, page=1, notice=f"❌ {_error_text(result, '道具使用失败')}")
                return
            await _show_bag_query(query, state, uid, page=1)
            return
        await _show_bag_query(query, state, uid, page=1, notice="该储物袋按钮已失效，已返回储物袋列表。")
        return

    if action == "page":
        page = await _parse_page_arg(args)
        await _show_gear_query(query, state, uid, page=page)
        return
    if action == "equipped_view":
        await _show_equipped_query(query, state, uid)
        return
    if action == "detail":
        if not args:
            await _show_gear_query(query, state, uid, page=1, notice="缺少灵装参数，已返回灵装列表。")
            return
        item_id = await _try_number(args[0])
        if item_id is None:
            await _show_gear_query(query, state, uid, page=1, notice="灵装参数异常，已返回灵装列表。")
            return
        await respond_query(
            query,
            f"👕 灵装详情: #{item_id}",
            reply_markup=ui.gear_detail_keyboard(item_id),
        )
        return
    if action in {"equip", "enhance", "decompose"}:
        if not args:
            await _show_gear_query(query, state, uid, page=1, notice="缺少灵装参数，已返回灵装列表。")
            return
        item_id = await _try_number(args[0])
        if item_id is None:
            await _show_gear_query(query, state, uid, page=1, notice="灵装参数异常，已返回灵装列表。")
            return
        endpoint = "/api/equip" if action == "equip" else ("/api/enhance" if action == "enhance" else "/api/decompose")
        payload = {"user_id": uid, "item_id": item_id}
        result = await api_post(endpoint, payload=payload, actor_uid=uid, request_id=new_request_id())
        if not result.get("success"):
            await _show_gear_query(query, state, uid, page=1, notice=f"❌ {_error_text(result, '灵装操作失败')}")
            return
        await _show_gear_query(query, state, uid, page=1)
        return
    if action == "unequip":
        if not args:
            await _show_equipped_query(query, state, uid, notice="缺少卸下槽位参数，已返回已佩戴列表。")
            return
        slot = args[0]
        result = await api_post(
            "/api/unequip",
            payload={"user_id": uid, "slot": slot},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await _show_equipped_query(query, state, uid, notice=f"❌ {_error_text(result, '卸下失败')}")
            return
        await _show_equipped_query(query, state, uid)
        return
    await _show_gear_query(query, state, uid, page=1, notice="该灵装按钮已失效，已返回灵装列表。")
