"""Shop/alchemy/forge router."""

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
    parse_callback,
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.economy import ShopFSM

router = Router(name="shop_alchemy_forge")


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


def _safe_int(raw: str | None, default: int = 1, *, minimum: int = 1) -> int:
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _alchemy_select_keyboard(recipes: list[dict[str, Any]], mode: str) -> Any:
    builder = InlineKeyboardBuilder()
    for recipe in recipes[:10]:
        recipe_id = str(recipe.get("id", "")).strip()
        if not recipe_id:
            continue
        name = str(recipe.get("name", recipe_id))
        icon = "⚗️" if mode == "craft" else "📦"
        builder.button(text=f"{icon} {name}", callback_data=f"alchemy:{mode}:{recipe_id}")
    builder.button(text="⬅️ 炼丹菜单", callback_data="alchemy:menu")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    builder.adjust(*([1] * min(len(recipes), 10)), 1, 1)
    return builder.as_markup()


def _forge_menu_keyboard(status: dict[str, Any], catalog: list[dict[str, Any]]) -> Any:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔨 普通锻造", callback_data="forge:craft:normal")
    high_enabled = bool(((status.get("modes") or {}).get("high") or {}).get("enabled"))
    if high_enabled:
        builder.button(text="💎 高投入锻造", callback_data="forge:craft:high")
    for row in catalog[:6]:
        item_id = str(row.get("item_id", "")).strip()
        if not item_id:
            continue
        name = str(row.get("name", item_id))
        builder.button(text=f"🎯 定向 {name}", callback_data=f"forge:enhance:{item_id}")
    builder.button(text="⬅️ 返回商店", callback_data="shop:back")
    builder.button(text="⬅️ 主菜单", callback_data="menu:home")
    base_rows = 2 if high_enabled else 1
    builder.adjust(base_rows, *([1] * min(len(catalog), 6)), 1, 1)
    return builder.as_markup()


async def _fetch_alchemy_recipes(uid: str) -> tuple[list[dict[str, Any]], str | None]:
    data = await api_get("/api/alchemy/recipes", params={"user_id": uid}, actor_uid=uid)
    if not data.get("success"):
        return [], _error_text(data, "丹方获取失败")
    return list(data.get("recipes") or []), None


async def _show_alchemy_menu_query(query: CallbackQuery, state: FSMContext, uid: str, notice: str | None = None) -> None:
    await state.set_state(ShopFSM.alchemy_panel)
    text = "⚗️ 炼丹菜单\n请选择操作。"
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(query, text, reply_markup=ui.alchemy_menu_keyboard())
    await safe_answer(query)


async def _show_forge_menu_query(query: CallbackQuery, state: FSMContext, uid: str, notice: str | None = None) -> None:
    status = await api_get(f"/api/forge/{uid}", actor_uid=uid)
    catalog_data = await api_get(f"/api/forge/catalog/{uid}", actor_uid=uid)
    catalog = list(catalog_data.get("items") or []) if catalog_data.get("success") else []
    text_lines = ["🔨 锻造菜单"]
    if notice:
        text_lines = [notice, ""] + text_lines
    if status.get("success"):
        cost = status.get("cost_copper")
        material_name = status.get("material_item_name", "材料")
        material_need = status.get("material_need")
        text_lines.append(f"普通锻造消耗：{cost} 下品灵石 + {material_need} {material_name}")
        if bool(((status.get("modes") or {}).get("high") or {}).get("enabled")):
            text_lines.append("高投入锻造已开启。")
    else:
        text_lines.append("⚠️ 锻造状态获取失败，将显示基础菜单。")
    if catalog:
        text_lines.append("可选定向锻造：")
        for row in catalog[:6]:
            text_lines.append(f"• {row.get('name', row.get('item_id', '未知'))}")
    await state.set_state(ShopFSM.forge_panel)
    await respond_query(
        query,
        "\n".join(text_lines),
        reply_markup=_forge_menu_keyboard(status if isinstance(status, dict) else {}, catalog),
    )
    await safe_answer(query)


async def _show_shop_currency_message(message: Message, state: FSMContext, uid: str) -> None:
    await state.update_data(uid=uid, shop_currency="copper", shop_page=1)
    await state.set_state(ShopFSM.selecting_currency)
    await reply_or_answer(message, "🏪 请选择商店货币", reply_markup=ui.shop_currency_keyboard())


async def _show_shop_currency_query(query: CallbackQuery, state: FSMContext, uid: str, notice: str | None = None) -> None:
    await state.update_data(uid=uid, shop_currency="copper", shop_page=1)
    await state.set_state(ShopFSM.selecting_currency)
    text = "🏪 请选择商店货币"
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(query, text, reply_markup=ui.shop_currency_keyboard())
    await safe_answer(query)


async def _show_shop_items(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    currency: str,
    page: int,
    notice: str | None = None,
) -> None:
    data = await api_get("/api/shop", params={"currency": currency, "page": page, "user_id": uid}, actor_uid=uid)
    if not data.get("success"):
        err_notice = f"❌ {_error_text(data, '商店数据获取失败')}"
        if notice:
            err_notice = f"{notice}\n\n{err_notice}"
        await _show_shop_currency_query(query, state, uid, notice=err_notice)
        return
    items = list(data.get("items") or [])
    page_no = int(data.get("page") or page or 1)
    total_pages = int(data.get("total_pages") or 1)
    currency_role = str(data.get("currency_balance") or "")
    await state.update_data(shop_currency=currency, shop_page=page_no)
    await state.set_state(ShopFSM.shop_browsing)
    await respond_query(
        query,
        (
            f"{notice}\n\n{ui.format_shop_panel(items, currency, page_no, total_pages, currency_role)}"
            if notice
            else ui.format_shop_panel(items, currency, page_no, total_pages, currency_role)
        ),
        reply_markup=ui.shop_items_keyboard(items, page_no, total_pages, currency),
    )
    await safe_answer(query)


@router.message(Command("xian_shop"))
async def cmd_shop(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_shop_currency_message(message, state, uid)


@router.message(Command("xian_convert"))
async def cmd_convert(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await reply_or_answer(message, "🔁 资源转化面板开发中。", reply_markup=ui.main_menu_keyboard(registered=True))


@router.message(Command("xian_alchemy"))
async def cmd_alchemy(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message)
    if not uid:
        await reply_or_answer(message, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await state.update_data(uid=uid)
    await state.set_state(ShopFSM.alchemy_panel)
    await reply_or_answer(message, "⚗️ 炼丹菜单\n请选择操作。", reply_markup=ui.alchemy_menu_keyboard())


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "shop":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    if action == "currency":
        if args:
            currency = str(args[0] or "").strip().lower()
            if currency not in {"copper", "gold", "spirit_high"}:
                await _show_shop_items(query, state, uid, "copper", page=1, notice="货币参数异常，已切换到下品灵石商店。")
                return
            await _show_shop_items(query, state, uid, currency, page=1)
        else:
            await _show_shop_currency_query(query, state, uid)
        return

    if action == "page":
        data = await state.get_data()
        currency = str(args[0] if len(args) >= 1 else data.get("shop_currency") or "copper").strip().lower()
        if currency not in {"copper", "gold", "spirit_high"}:
            currency = str(data.get("shop_currency") or "copper").strip().lower()
            if currency not in {"copper", "gold", "spirit_high"}:
                currency = "copper"
            page = _safe_int(args[1] if len(args) >= 2 else None, default=1, minimum=1)
            await _show_shop_items(query, state, uid, currency, page, notice="分页参数异常，已恢复到当前货币列表。")
            return
        page = _safe_int(args[1] if len(args) >= 2 else None, default=1, minimum=1)
        await _show_shop_items(query, state, uid, currency, page)
        return

    if action == "buy":
        item_id = args[0] if len(args) >= 1 else ""
        data = await state.get_data()
        if not item_id:
            currency = str(data.get("shop_currency") or "copper").strip().lower()
            page = int(data.get("shop_page", 1) or 1)
            await _show_shop_items(query, state, uid, currency, page, notice="商品参数缺失，请重新选择要购买的条目。")
            return
        currency = str(args[1] if len(args) >= 2 else data.get("shop_currency") or "copper").strip().lower()
        if currency not in {"copper", "gold", "spirit_high"}:
            currency = str(data.get("shop_currency") or "copper").strip().lower()
            if currency not in {"copper", "gold", "spirit_high"}:
                currency = "copper"
        result = await api_post("/api/shop/buy", {"user_id": uid, "item_id": item_id, "currency": currency}, actor_uid=uid)
        page = int(data.get("shop_page", 1) or 1)
        if result.get("success"):
            notice = f"✅ {result.get('message', '购买成功')}"
        else:
            notice = f"❌ {_error_text(result, '购买失败')}"
        await _show_shop_items(query, state, uid, currency, page, notice=notice)
        return

    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return

    if action == "noop":
        await safe_answer(query)
        return

    await _show_shop_currency_query(query, state, uid, notice="该商店按钮已失效，已返回货币选择。")


@router.callback_query(F.data.startswith("alchemy:"))
async def cb_alchemy(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "alchemy":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    if action == "menu":
        await _show_alchemy_menu_query(query, state, uid)
        return
    if action == "craft":
        if not args:
            recipes, err = await _fetch_alchemy_recipes(uid)
            if err:
                await _show_alchemy_menu_query(query, state, uid, notice=f"❌ {err}")
                return
            await state.set_state(ShopFSM.alchemy_panel)
            await respond_query(
                query,
                "⚗️ 选择要炼制的丹方",
                reply_markup=_alchemy_select_keyboard(recipes, "craft"),
            )
            await safe_answer(query)
            return
        recipe_id = str(args[0]).strip()
        if not recipe_id:
            recipes, err = await _fetch_alchemy_recipes(uid)
            if err:
                await _show_alchemy_menu_query(query, state, uid, notice=f"❌ {err}")
                return
            await state.set_state(ShopFSM.alchemy_panel)
            await respond_query(
                query,
                "丹方参数异常，请重新选择要炼制的丹方。",
                reply_markup=_alchemy_select_keyboard(recipes, "craft"),
            )
            await safe_answer(query, text="请重新选择丹方")
            return
        result = await api_post("/api/alchemy/brew", {"user_id": uid, "recipe_id": recipe_id}, actor_uid=uid)
        text = f"{'✅' if result.get('success') else '❌'} 炼制结果\n{result.get('message', '')}"
        await respond_query(query, text, reply_markup=ui.alchemy_menu_keyboard())
        await safe_answer(query, text="炼制成功" if result.get("success") else "炼制失败")
        return
    if action == "batch":
        if not args:
            recipes, err = await _fetch_alchemy_recipes(uid)
            if err:
                await _show_alchemy_menu_query(query, state, uid, notice=f"❌ {err}")
                return
            await state.set_state(ShopFSM.alchemy_panel)
            await respond_query(
                query,
                "📦 选择要批量炼制的丹方（默认 5 次）",
                reply_markup=_alchemy_select_keyboard(recipes, "batch"),
            )
            await safe_answer(query)
            return
        recipe_id = str(args[0]).strip()
        if not recipe_id:
            recipes, err = await _fetch_alchemy_recipes(uid)
            if err:
                await _show_alchemy_menu_query(query, state, uid, notice=f"❌ {err}")
                return
            await state.set_state(ShopFSM.alchemy_panel)
            await respond_query(
                query,
                "丹方参数异常，请重新选择要批量炼制的丹方。",
                reply_markup=_alchemy_select_keyboard(recipes, "batch"),
            )
            await safe_answer(query, text="请重新选择丹方")
            return
        total = 5
        success_count = 0
        last_message = ""
        for _ in range(total):
            result = await api_post("/api/alchemy/brew", {"user_id": uid, "recipe_id": recipe_id}, actor_uid=uid)
            last_message = str(result.get("message") or "")
            if result.get("success"):
                success_count += 1
            else:
                break
        text = (
            "📦 批量炼制结果\n"
            f"丹方: {recipe_id}\n"
            f"成功次数: {success_count}/{total}\n"
            f"结果: {last_message or '完成'}"
        )
        await respond_query(query, text, reply_markup=ui.alchemy_menu_keyboard())
        await safe_answer(query, text=f"完成 {success_count}/{total}")
        return
    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return
    await _show_alchemy_menu_query(query, state, uid, notice="该炼丹按钮已失效，已返回炼丹菜单。")


@router.callback_query(F.data.startswith("forge:"))
async def cb_forge(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "forge":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query)
    if not uid:
        await respond_query(query, "未找到你的角色，请先注册。", reply_markup=ui.register_keyboard())
        await safe_answer(query, text="请先注册")
        return

    await state.set_state(ShopFSM.forge_panel)
    if action == "menu":
        await _show_forge_menu_query(query, state, uid)
        return
    if action == "craft":
        mode = str(args[0]).strip().lower() if args else "normal"
        if mode not in {"normal", "high"}:
            await _show_forge_menu_query(query, state, uid, notice="锻造模式参数异常，请从菜单重新选择。")
            return
        result = await api_post("/api/forge", {"user_id": uid, "mode": mode}, actor_uid=uid)
        text = f"{'✅' if result.get('success') else '❌'} 锻造结果（{mode}）\n{result.get('message', '')}"
        await respond_query(query, text, reply_markup=ui.forge_menu_keyboard())
        await safe_answer(query, text="锻造成功" if result.get("success") else "锻造失败")
        return
    if action == "enhance":
        if not args:
            await _show_forge_menu_query(query, state, uid, notice="缺少目标装备，请重新选择定向锻造目标。")
            return
        item_id = str(args[0]).strip()
        if not item_id:
            await _show_forge_menu_query(query, state, uid, notice="目标装备参数异常，请重新选择。")
            return
        result = await api_post("/api/forge/targeted", {"user_id": uid, "item_id": item_id}, actor_uid=uid)
        text = f"{'✅' if result.get('success') else '❌'} 定向锻造结果\n{result.get('message', '')}"
        await respond_query(query, text, reply_markup=ui.forge_menu_keyboard())
        await safe_answer(query, text="定向完成" if result.get("success") else "定向失败")
        return
    if action == "back":
        await _show_shop_currency_query(query, state, uid)
        return
    await _show_forge_menu_query(query, state, uid, notice="该锻造按钮已失效，已返回锻造菜单。")
