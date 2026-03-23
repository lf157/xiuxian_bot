"""Shop/alchemy/forge router."""

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
    parse_callback,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.economy import ShopFSM

router = Router(name="shop_alchemy_forge")


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


async def _show_shop_currency_message(message: Message, state: FSMContext, uid: str) -> None:
    await state.update_data(uid=uid, shop_currency="copper", shop_page=1)
    await state.set_state(ShopFSM.selecting_currency)
    await message.answer("🏪 请选择商店货币", reply_markup=ui.shop_currency_keyboard())


async def _show_shop_currency_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    await state.update_data(uid=uid, shop_currency="copper", shop_page=1)
    await state.set_state(ShopFSM.selecting_currency)
    await respond_query(query, "🏪 请选择商店货币", reply_markup=ui.shop_currency_keyboard())
    await safe_answer(query)


async def _show_shop_items(query: CallbackQuery, state: FSMContext, uid: str, currency: str, page: int) -> None:
    data = await api_get("/api/shop", params={"currency": currency, "page": page, "user_id": uid}, actor_uid=uid)
    items = list(data.get("items") or [])
    page_no = int(data.get("page") or page or 1)
    total_pages = int(data.get("total_pages") or 1)
    currency_role = str(data.get("currency_balance") or "")
    await state.update_data(shop_currency=currency, shop_page=page_no)
    await state.set_state(ShopFSM.shop_browsing)
    await respond_query(
        query,
        ui.format_shop_panel(items, currency, page_no, total_pages, currency_role),
        reply_markup=ui.shop_items_keyboard(items, page_no, total_pages, currency),
    )
    await safe_answer(query)


@router.message(Command("xian_shop", "shop", "xian_currency", "currency"))
async def cmd_shop(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_shop_currency_message(message, state, uid)


@router.message(Command("xian_convert", "convert"))
async def cmd_convert(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await message.answer("🔁 资源转化面板开发中。", reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_alchemy", "alchemy"))
async def cmd_alchemy(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await message.answer("未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await state.set_state(ShopFSM.alchemy_panel)
    await message.answer(ui.format_alchemy_panel(), reply_markup=ui.alchemy_menu_keyboard())


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    _, action, args = parsed
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    if action == "currency":
        await _show_shop_currency_query(query, state, uid)
        return

    if action == "page":
        currency = args[0] if len(args) >= 1 else "copper"
        page = int(args[1]) if len(args) >= 2 else 1
        await _show_shop_items(query, state, uid, currency, page)
        return

    if action == "buy":
        item_id = args[0] if len(args) >= 1 else ""
        currency = args[1] if len(args) >= 2 else "copper"
        result = await api_post("/api/shop/buy", {"user_id": uid, "item_id": item_id, "currency": currency}, actor_uid=uid)
        data = await state.get_data()
        page = int(data.get("shop_page", 1) or 1)
        await _show_shop_items(query, state, uid, currency, page)
        await safe_answer(query, text="购买成功" if result.get("success") else "购买失败")
        return

    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return

    if action == "noop":
        await safe_answer(query)
        return

    await handle_expired_callback(query)


@router.callback_query(F.data.startswith("alchemy:"))
async def cb_alchemy(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    _, action, _ = parsed
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    if action == "menu":
        await state.set_state(ShopFSM.alchemy_panel)
        await respond_query(query, ui.format_alchemy_panel(), reply_markup=ui.alchemy_menu_keyboard())
        await safe_answer(query)
        return
    if action == "craft":
        result = await api_post("/api/alchemy/craft", {"user_id": uid}, actor_uid=uid)
        await respond_query(query, ui.format_alchemy_panel(result), reply_markup=ui.alchemy_menu_keyboard())
        await safe_answer(query, text="炼制成功" if result.get("success") else "炼制失败")
        return
    if action == "batch":
        result = await api_post("/api/alchemy/craft", {"user_id": uid, "batch": 5}, actor_uid=uid)
        await respond_query(query, ui.format_alchemy_panel(result), reply_markup=ui.alchemy_menu_keyboard())
        await safe_answer(query, text="批量完成" if result.get("success") else "批量失败")
        return
    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return
    await handle_expired_callback(query)


@router.callback_query(F.data.startswith("forge:"))
async def cb_forge(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    _, action, _ = parsed
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    await state.set_state(ShopFSM.forge_panel)
    if action == "menu":
        await respond_query(query, ui.format_forge_panel(), reply_markup=ui.forge_menu_keyboard())
        await safe_answer(query)
        return
    if action == "craft":
        result = await api_post("/api/forge/craft", {"user_id": uid}, actor_uid=uid)
        await respond_query(query, ui.format_forge_panel(result), reply_markup=ui.forge_menu_keyboard())
        await safe_answer(query, text="锻造成功" if result.get("success") else "锻造失败")
        return
    if action == "enhance":
        result = await api_post("/api/forge/enhance", {"user_id": uid}, actor_uid=uid)
        await respond_query(query, ui.format_forge_panel(result), reply_markup=ui.forge_menu_keyboard())
        await safe_answer(query, text="强化成功" if result.get("success") else "强化失败")
        return
    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return
    await handle_expired_callback(query)
