"""Admin router."""

from __future__ import annotations

import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from adapters.aiogram import ui
from adapters.aiogram.services import (
    api_post,
    handle_expired_callback,
    new_request_id,
    parse_callback,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.social_admin import AdminFSM

router = Router(name="admin")

_ADMIN_GIVE_FIELD = {
    "low": "copper",
    "mid": "gold",
    "high": "spirit_high",
    "uhigh": "spirit_exquisite",
    "xhigh": "spirit_supreme",
}


def _super_admin_tg_ids() -> set[str]:
    raw = str(os.getenv("SUPER_ADMIN_TG_IDS", "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _is_super_admin(user_id: int | str | None) -> bool:
    return str(user_id or "").strip() in _super_admin_tg_ids()


async def _uid_from_message(message: Message) -> str | None:
    if message.from_user is None:
        return None
    return await resolve_uid(int(message.from_user.id))


async def _uid_from_query(query: CallbackQuery) -> str | None:
    if query.from_user is None:
        return None
    return await resolve_uid(int(query.from_user.id))


async def _respond_and_ack(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    toast: str | None = None,
    show_alert: bool = False,
) -> None:
    await respond_query(query, text, reply_markup=reply_markup)
    await safe_answer(query, text=toast, show_alert=show_alert)


async def _deny_if_not_admin_message(message: Message, state: FSMContext) -> bool:
    if not _is_super_admin(message.from_user.id if message.from_user else None):
        await state.set_state(AdminFSM.admin_menu)
        await message.answer("无权限使用该管理命令。", reply_markup=ui.admin_menu_keyboard())
        return True
    return False


async def _deny_if_not_admin_query(query: CallbackQuery, state: FSMContext) -> bool:
    if not _is_super_admin(query.from_user.id if query.from_user else None):
        await state.set_state(AdminFSM.admin_menu)
        await _respond_and_ack(
            query,
            "无权限使用该管理功能。",
            reply_markup=ui.admin_menu_keyboard(),
            toast="无权限",
            show_alert=True,
        )
        return True
    return False


@router.message(Command("test", "xian_test"))
async def cmd_admin_test(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_admin_message(message, state):
        return
    uid = await _uid_from_message(message)
    await state.set_state(AdminFSM.admin_menu)
    await state.update_data(uid=uid or "")
    await message.answer(ui.format_admin_panel({"mode": "test"}), reply_markup=ui.admin_menu_keyboard())


async def _handle_give(message: Message, tier: str, state: FSMContext) -> None:
    if await _deny_if_not_admin_message(message, state):
        return
    uid = await _uid_from_message(message)
    if not uid:
        await state.set_state(AdminFSM.admin_menu)
        await message.answer("未找到你的角色，无法执行。", reply_markup=ui.admin_menu_keyboard())
        return
    field = _ADMIN_GIVE_FIELD[tier]
    parts = (message.text or "").split()
    target_uid = uid
    amount = 1
    if len(parts) >= 3:
        target_token = str(parts[-2]).strip()
        try:
            amount = max(1, int(parts[-1]))
        except ValueError:
            amount = 1
        if target_token.isdigit():
            resolved = await resolve_uid(int(target_token))
            target_uid = resolved or target_token
        elif target_token:
            target_uid = target_token
    elif len(parts) >= 2:
        try:
            amount = max(1, int(parts[-1]))
        except ValueError:
            amount = 1
    payload = {"user_id": target_uid, "field": field, "action": "add", "value": amount}
    result = await api_post("/api/admin/modify", payload, actor_uid=uid, request_id=new_request_id())
    await state.set_state(AdminFSM.admin_menu)
    await state.update_data(uid=uid, admin_target_uid=target_uid)
    await message.answer(
        f"{'✅' if result.get('success') else '❌'} 发放 {tier} 灵石 x{amount} -> {target_uid}",
        reply_markup=ui.admin_menu_keyboard(),
    )


@router.message(Command("xian_give_low"))
async def cmd_give_low(message: Message, state: FSMContext) -> None:
    await _handle_give(message, "low", state)


@router.message(Command("xian_give_mid"))
async def cmd_give_mid(message: Message, state: FSMContext) -> None:
    await _handle_give(message, "mid", state)


@router.message(Command("xian_give_high"))
async def cmd_give_high(message: Message, state: FSMContext) -> None:
    await _handle_give(message, "high", state)


@router.message(Command("xian_give_uhigh"))
async def cmd_give_uhigh(message: Message, state: FSMContext) -> None:
    await _handle_give(message, "uhigh", state)


@router.message(Command("xian_give_xhigh"))
async def cmd_give_xhigh(message: Message, state: FSMContext) -> None:
    await _handle_give(message, "xhigh", state)


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin(query: CallbackQuery, state: FSMContext) -> None:
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "admin":
        await handle_expired_callback(query)
        return
    if await _deny_if_not_admin_query(query, state):
        return

    uid = await _uid_from_query(query)
    if not uid:
        await _respond_and_ack(query, "未找到角色。", reply_markup=ui.admin_menu_keyboard(), toast="失败")
        return

    if action == "menu":
        await state.set_state(AdminFSM.admin_menu)
        await _respond_and_ack(query, ui.format_admin_panel({"mode": "menu"}), reply_markup=ui.admin_menu_keyboard())
        return

    if action == "test":
        await state.set_state(AdminFSM.admin_menu)
        await _respond_and_ack(query, "🧪 Admin Test OK", reply_markup=ui.admin_menu_keyboard())
        return

    if action == "lookup":
        await state.set_state(AdminFSM.target_lookup)
        await _respond_and_ack(query, "请输入要查询的 UID/TGID。", reply_markup=ui.admin_menu_keyboard())
        return

    if action == "modify":
        await state.set_state(AdminFSM.modify_preview)
        await _respond_and_ack(query, "请选择预设或确认修改。", reply_markup=ui.admin_menu_keyboard())
        return

    if action == "preset":
        preset_id = args[0] if args else ""
        result = await api_post(
            "/api/admin/preset",
            {"user_id": uid, "preset_id": preset_id},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        await _respond_and_ack(
            query,
            ui.format_admin_panel(result),
            reply_markup=ui.admin_menu_keyboard(),
            toast="已应用" if result.get("success") else "应用失败",
        )
        return

    if action == "confirm":
        await state.set_state(AdminFSM.confirm_apply)
        await _respond_and_ack(query, "已确认执行。", reply_markup=ui.admin_menu_keyboard())
        return

    if action == "cancel":
        await state.set_state(AdminFSM.admin_menu)
        await _respond_and_ack(query, "已取消。", reply_markup=ui.admin_menu_keyboard())
        return

    await handle_expired_callback(query)
