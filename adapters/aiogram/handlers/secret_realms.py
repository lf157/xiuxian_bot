"""Secret realms handlers."""

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
from adapters.aiogram.states.realms import SecretRealmsFSM

router = Router(name="secret_realms")


def _error_text(payload: dict[str, Any] | None, default: str = "操作失败") -> str:
    if not isinstance(payload, dict):
        return default
    message = str(payload.get("message") or payload.get("error") or default).strip()
    code = str(payload.get("code") or "").strip()
    if code and code not in message:
        return f"{message}（{code}）"
    return message


def _is_session_lost(message: str) -> bool:
    text = (message or "").lower()
    return "会话已失效" in text or "重新进入秘境" in text or ("session" in text and "invalid" in text)


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


async def _show_secret_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    data = await api_get(f"/api/secret-realms/{uid}", actor_uid=uid)
    if not data.get("success"):
        await message.answer(
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    realms = data.get("realms") or []
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    await message.answer(
        ui.format_secret_panel(realms, attempts_left=int(data.get("attempts_left", 0) or 0)),
        reply_markup=ui.secret_realms_keyboard(realms),
    )


async def _show_secret_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    data = await api_get(f"/api/secret-realms/{uid}", actor_uid=uid)
    if not data.get("success"):
        await respond_query(
            query,
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    realms = data.get("realms") or []
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    await respond_query(
        query,
        ui.format_secret_panel(realms, attempts_left=int(data.get("attempts_left", 0) or 0)),
        reply_markup=ui.secret_realms_keyboard(realms),
    )


async def _reset_session_to_realm(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    await state.update_data(secret_session_id="")
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await _show_secret_panel_query(query, state, uid)


@router.message(Command("xian_secret", "xian_mystic", "secret", "mystic"))
async def cmd_secret(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer("未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_secret_panel_message(message, state, uid)


@router.callback_query(F.data.startswith("secret:"))
async def cb_secret(query: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(query)
    parsed = parse_callback(str(query.data or ""))
    if parsed is None:
        await handle_expired_callback(query)
        return
    domain, action, args = parsed
    if domain != "secret":
        await handle_expired_callback(query)
        return
    uid = await _uid_from_query(query, state)
    if not uid:
        await respond_query(query, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return

    if action in {"list", "settle"}:
        await _show_secret_panel_query(query, state, uid)
        return
    if action == "realm":
        if not args:
            await handle_expired_callback(query)
            return
        realm_id = args[0]
        await state.set_state(SecretRealmsFSM.selecting_path)
        await state.update_data(secret_realm_id=realm_id)
        await respond_query(query, f"已选择秘境：{realm_id}\n请选择路线。", reply_markup=ui.secret_paths_keyboard())
        return
    if action == "path":
        if not args:
            await handle_expired_callback(query)
            return
        data = await state.get_data()
        realm_id = str(data.get("secret_realm_id") or "")
        if not realm_id:
            await _show_secret_panel_query(query, state, uid)
            return
        result = await api_post(
            "/api/secret-realms/turn/start",
            payload={"user_id": uid, "realm_id": realm_id, "path": args[0]},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '秘境启动失败')}", reply_markup=ui.secret_paths_keyboard())
            return
        session_id = str(result.get("session_id") or "").strip()
        await state.update_data(secret_session_id=session_id)
        if result.get("phase") == "event":
            await state.set_state(SecretRealmsFSM.in_event_choice)
            await respond_query(
                query,
                ui.format_secret_event(result),
                reply_markup=ui.secret_event_choices_keyboard(result.get("choices") or []),
            )
            return
        await state.set_state(SecretRealmsFSM.in_battle)
        await respond_query(
            query,
            ui.format_secret_battle_open(result),
            reply_markup=ui.secret_battle_keyboard(result.get("active_skills") or []),
        )
        return
    if action in {"choice", "act_normal", "act_skill"}:
        data = await state.get_data()
        session_id = str(data.get("secret_session_id") or "").strip()
        if not session_id:
            await _reset_session_to_realm(query, state, uid)
            return
        payload: dict[str, Any] = {
            "user_id": uid,
            "session_id": session_id,
            "request_id": new_request_id(),
        }
        if action == "choice":
            if not args:
                await handle_expired_callback(query)
                return
            payload["action"] = "choice"
            payload["choice_id"] = args[0]
        elif action == "act_normal":
            payload["action"] = "normal"
        else:
            if not args:
                await handle_expired_callback(query)
                return
            payload["action"] = "skill"
            payload["skill_id"] = args[0]
        result = await api_post("/api/secret-realms/turn/action", payload=payload, actor_uid=uid, request_id=new_request_id())
        if not result.get("success"):
            err = _error_text(result, "秘境行动失败")
            if _is_session_lost(err):
                await _reset_session_to_realm(query, state, uid)
                return
            await respond_query(query, f"❌ {err}", reply_markup=ui.main_menu_keyboard(registered=True))
            return
        if result.get("finished"):
            await state.set_state(SecretRealmsFSM.settlement)
            await state.update_data(secret_session_id="")
            await respond_query(query, ui.format_secret_settlement(result), reply_markup=ui.secret_settlement_keyboard())
            return
        phase = str(result.get("phase") or "")
        if phase == "event":
            await state.set_state(SecretRealmsFSM.in_event_choice)
            await respond_query(query, ui.format_secret_event(result), reply_markup=ui.secret_event_choices_keyboard(result.get("choices") or []))
            return
        await state.set_state(SecretRealmsFSM.in_battle)
        await respond_query(query, ui.format_battle_round(result, title="🗺️ 秘境战斗"), reply_markup=ui.secret_battle_keyboard(result.get("active_skills") or []))
        return
    if action == "exit":
        await state.update_data(secret_session_id="")
        await _reset_session_to_realm(query, state, uid)
        return
    await handle_expired_callback(query)
