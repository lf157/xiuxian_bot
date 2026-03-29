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
    reject_non_owner,
    reply_or_answer,
    resolve_uid,
    respond_query,
    safe_answer,
)
from adapters.aiogram.states.realms import SecretRealmsFSM

router = Router(name="secret_realms")


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        await reply_or_answer(message,
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    realms = data.get("realms") or []
    attempts_left = int(data.get("attempts_left", 0) or 0)
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    panel_text = ui.format_secret_panel(realms, attempts_left=attempts_left)
    if attempts_left <= 0:
        panel_text = f"⚠️ 今日秘境次数已用完，请明日再来！\n\n{panel_text}"
    await reply_or_answer(
        message,
        panel_text,
        reply_markup=ui.secret_realms_keyboard(realms),
    )



async def _show_secret_panel_query(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    notice: str | None = None,
) -> None:
    data = await api_get(f"/api/secret-realms/{uid}", actor_uid=uid)
    if not data.get("success"):
        await respond_query(
            query,
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    realms = data.get("realms") or []
    attempts_left = int(data.get("attempts_left", 0) or 0)
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    panel_text = ui.format_secret_panel(realms, attempts_left=attempts_left)
    if attempts_left <= 0:
        panel_text = f"⚠️ 今日秘境次数已用完，请明日再来！\n\n{panel_text}"
    if notice:
        panel_text = f"{notice}\n\n{panel_text}"
    await respond_query(
        query,
        panel_text,
        reply_markup=ui.secret_realms_keyboard(realms),
    )


async def _show_path_panel_query(
    query: CallbackQuery,
    state: FSMContext,
    realm_id: str,
    *,
    notice: str | None = None,
) -> None:
    await state.set_state(SecretRealmsFSM.selecting_path)
    await state.update_data(secret_realm_id=realm_id)
    text = f"已选择秘境：{realm_id}\n请选择路线。"
    if notice:
        text = f"{notice}\n\n{text}"
    await respond_query(query, text, reply_markup=ui.secret_paths_keyboard())


async def _show_event_panel_query(
    query: CallbackQuery,
    state: FSMContext,
    choices: list[dict[str, Any]],
    *,
    notice: str | None = None,
) -> None:
    await state.set_state(SecretRealmsFSM.in_event_choice)
    await state.update_data(secret_last_choices=choices, secret_last_skills=[])
    text = "请选择一个事件选项继续。"
    if notice:
        text = notice
    await respond_query(query, text, reply_markup=ui.secret_event_choices_keyboard(choices))


async def _show_battle_panel_query(
    query: CallbackQuery,
    state: FSMContext,
    skills: list[dict[str, Any]],
    *,
    notice: str | None = None,
) -> None:
    await state.set_state(SecretRealmsFSM.in_battle)
    await state.update_data(secret_last_choices=[], secret_last_skills=skills)
    text = "请选择普通攻击或技能继续战斗。"
    if notice:
        text = notice
    await respond_query(query, text, reply_markup=ui.secret_battle_keyboard(skills))


async def _reset_session_to_realm(
    query: CallbackQuery,
    state: FSMContext,
    uid: str,
    *,
    notice: str | None = None,
) -> None:
    await state.update_data(secret_session_id="", secret_last_choices=[], secret_last_skills=[])
    await state.set_state(SecretRealmsFSM.selecting_realm)
    await _show_secret_panel_query(query, state, uid, notice=notice)


@router.message(Command("xian_secret"))
async def cmd_secret(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await reply_or_answer(message, "未找到角色，请先注册。", reply_markup=ui.register_keyboard())
        return
    await _show_secret_panel_message(message, state, uid)


@router.callback_query(F.data.startswith("secret:"))
async def cb_secret(query: CallbackQuery, state: FSMContext) -> None:
    if await reject_non_owner(query):
        return
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
            await _show_secret_panel_query(query, state, uid, notice="缺少秘境参数，已返回秘境列表，请重新选择。")
            return
        # 选择秘境前再次检查次数
        check = await api_get(f"/api/secret-realms/{uid}", actor_uid=uid)
        if check.get("success") and _to_int(check.get("attempts_left")) <= 0:
            await _show_secret_panel_query(query, state, uid, notice="⚠️ 今日秘境次数已用完，请明日再来！")
            return
        realm_id = args[0]
        await _show_path_panel_query(query, state, realm_id)
        return
    if action == "path":
        data = await state.get_data()
        realm_id = str(data.get("secret_realm_id") or "")
        if not args:
            if realm_id:
                await _show_path_panel_query(query, state, realm_id, notice="缺少路线参数，请重新选择路线。")
            else:
                await _show_secret_panel_query(query, state, uid, notice="尚未选择秘境，已返回秘境列表。")
            return
        # Check remaining attempts before starting
        check = await api_get(f"/api/secret-realms/{uid}", actor_uid=uid)
        if check.get("success") and _to_int(check.get("attempts_left")) <= 0:
            await _show_secret_panel_query(query, state, uid, notice="⚠️ 今日秘境次数已用完，请明日再来！")
            return
        if not realm_id:
            await _show_secret_panel_query(query, state, uid, notice="尚未选择秘境，已返回秘境列表。")
            return
        result = await api_post(
            "/api/secret-realms/turn/start",
            payload={"user_id": uid, "realm_id": realm_id, "path": args[0], "interactive": True},
            actor_uid=uid,
            request_id=new_request_id(),
        )
        if not result.get("success"):
            await respond_query(query, f"❌ {_error_text(result, '秘境启动失败')}", reply_markup=ui.secret_paths_keyboard())
            return
        session_id = str(result.get("session_id") or "").strip()
        await state.update_data(secret_session_id=session_id)
        if result.get("needs_choice") or result.get("phase") == "event":
            choices = list(result.get("choices") or [])
            await state.set_state(SecretRealmsFSM.in_event_choice)
            await state.update_data(secret_last_choices=choices, secret_last_skills=[])
            await respond_query(
                query,
                ui.format_secret_event(result),
                reply_markup=ui.secret_event_choices_keyboard(choices),
            )
            return
        active_skills = list(result.get("active_skills") or [])
        await state.set_state(SecretRealmsFSM.in_battle)
        await state.update_data(secret_last_choices=[], secret_last_skills=active_skills)
        await respond_query(
            query,
            ui.format_secret_battle_open(result),
            reply_markup=ui.secret_battle_keyboard(active_skills),
        )
        return
    if action in {"choice", "act_normal", "act_skill"}:
        data = await state.get_data()
        session_id = str(data.get("secret_session_id") or "").strip()
        if not session_id:
            await _reset_session_to_realm(query, state, uid, notice="秘境会话已失效，已返回秘境列表。")
            return
        payload: dict[str, Any] = {
            "user_id": uid,
            "session_id": session_id,
            "request_id": new_request_id(),
        }
        if action == "choice":
            if not args:
                choices = list(data.get("secret_last_choices") or [])
                if choices:
                    await _show_event_panel_query(query, state, choices, notice="缺少事件选项参数，请重新选择。")
                    return
                await _reset_session_to_realm(query, state, uid, notice="事件选项已失效，已返回秘境列表。")
                return
            payload["action"] = "choice"
            payload["choice"] = args[0]
        elif action == "act_normal":
            payload["action"] = "normal"
        else:
            if not args:
                skills = list(data.get("secret_last_skills") or [])
                await _show_battle_panel_query(query, state, skills, notice="缺少技能参数，请重新选择技能或使用普通攻击。")
                return
            payload["action"] = "skill"
            payload["skill_id"] = args[0]
        result = await api_post("/api/secret-realms/turn/action", payload=payload, actor_uid=uid, request_id=new_request_id())
        if not result.get("success"):
            err = _error_text(result, "秘境行动失败")
            if _is_session_lost(err):
                await _reset_session_to_realm(query, state, uid, notice="秘境会话已失效，已返回秘境列表。")
                return
            if action == "choice":
                choices = list(data.get("secret_last_choices") or [])
                await _show_event_panel_query(query, state, choices, notice=f"❌ {err}\n请重新选择事件选项。")
            else:
                skills = list(data.get("secret_last_skills") or [])
                await _show_battle_panel_query(query, state, skills, notice=f"❌ {err}\n请继续选择行动。")
            return
        if result.get("needs_battle"):
            active_skills = list(result.get("active_skills") or [])
            await state.set_state(SecretRealmsFSM.in_battle)
            await state.update_data(secret_last_choices=[], secret_last_skills=active_skills)
            await respond_query(
                query,
                ui.format_secret_battle_open(result),
                reply_markup=ui.secret_battle_keyboard(active_skills),
            )
            return
        if result.get("needs_choice"):
            choices = list(result.get("choices") or [])
            await state.set_state(SecretRealmsFSM.in_event_choice)
            await state.update_data(secret_last_choices=choices, secret_last_skills=[])
            await respond_query(
                query,
                ui.format_secret_event(result),
                reply_markup=ui.secret_event_choices_keyboard(choices),
            )
            return
        if result.get("finished"):
            await state.set_state(SecretRealmsFSM.settlement)
            await state.update_data(secret_session_id="", secret_last_choices=[], secret_last_skills=[])
            await respond_query(query, ui.format_secret_settlement(result), reply_markup=ui.secret_settlement_keyboard())
            return
        phase = str(result.get("phase") or "")
        if phase == "event":
            choices = list(result.get("choices") or [])
            await state.set_state(SecretRealmsFSM.in_event_choice)
            await state.update_data(secret_last_choices=choices, secret_last_skills=[])
            await respond_query(query, ui.format_secret_event(result), reply_markup=ui.secret_event_choices_keyboard(choices))
            return
        active_skills = list(result.get("active_skills") or [])
        await state.set_state(SecretRealmsFSM.in_battle)
        await state.update_data(secret_last_choices=[], secret_last_skills=active_skills)
        await respond_query(query, ui.format_battle_round(result, title="🗺️ 秘境战斗"), reply_markup=ui.secret_battle_keyboard(active_skills))
        return
    if action == "exit":
        await _reset_session_to_realm(query, state, uid, notice="已结束探索，返回秘境列表。")
        return
    await _show_secret_panel_query(query, state, uid, notice="该秘境按钮已失效，已返回秘境列表。")
