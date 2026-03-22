"""P1 aiogram router: start/stat, hunt, breakthrough, secret realms."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from adapters.aiogram import ui
from adapters.aiogram.services.api_client import api_get, api_post, new_request_id, resolve_uid
from adapters.aiogram.states.p1 import BreakthroughFSM, HuntFSM, SecretRealmFSM

logger = logging.getLogger("adapters.aiogram.p1")
router = Router(name="aiogram_p1")

_USERNAME_ALLOWED = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff]")


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
    return (
        "战斗已失效" in text
        or "会话已失效" in text
        or "重新开始狩猎" in text
        or "重新进入秘境" in text
        or "session" in text and "invalid" in text
    )


def _pick_username(user: User, explicit_name: str | None = None) -> str:
    raw = (explicit_name or "").strip()
    if not raw:
        raw = (user.full_name or "").strip()
    if not raw:
        raw = (user.username or "").strip()
    raw = _USERNAME_ALLOWED.sub("", raw)
    if len(raw) < 2:
        raw = f"修士{str(user.id)[-6:]}"
    if len(raw) > 16:
        raw = raw[:16]
    return raw


def _markdown_safe_tg_mention(user: User | None) -> str:
    username = ""
    if user is not None:
        username = str(getattr(user, "username", "") or "").strip()
    clean = re.sub(r"[^A-Za-z0-9_]", "", username)
    if not clean:
        return ""
    return "@" + clean.replace("_", "\\_")


def _extract_callback_tail(query: CallbackQuery, prefix: str) -> str:
    data = str(query.data or "")
    if not data.startswith(prefix):
        return ""
    return data[len(prefix):]


def _breakthrough_trial_hint(payload: dict[str, Any]) -> str:
    req = payload.get("trial_requirements") or {}
    hunt = req.get("hunt") or {}
    secret = req.get("secret") or {}
    lines: list[str] = []
    if hunt.get("target"):
        lines.append(
            f"• 狩猎 {hunt.get('progress', 0)}/{hunt.get('target', 0)}（还差 {hunt.get('remaining', 0)}）"
        )
    if secret.get("target"):
        lines.append(
            f"• 秘境 {secret.get('progress', 0)}/{secret.get('target', 0)}（还差 {secret.get('remaining', 0)}）"
        )
    return "\n".join(lines)


async def _safe_answer_callback(
    query: CallbackQuery,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


async def _respond_query(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    alert_text: str | None = None,
    show_alert: bool = False,
    parse_mode: ParseMode | str | None = None,
) -> None:
    if query.message:
        try:
            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            await query.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    await _safe_answer_callback(query, text=alert_text, show_alert=show_alert)


async def _uid_from_message(message: Message, state: FSMContext) -> str | None:
    if not message.from_user:
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
    if not query.from_user:
        return None
    data = await state.get_data()
    uid = str(data.get("uid") or "").strip()
    if uid:
        return uid
    uid = await resolve_uid(int(query.from_user.id))
    if uid:
        await state.update_data(uid=uid)
    return uid


async def _require_uid_from_message(message: Message, state: FSMContext) -> str | None:
    uid = await _uid_from_message(message, state)
    if uid:
        return uid
    await message.answer(
        "未找到你的角色，请先注册后再使用功能。",
        reply_markup=ui.register_keyboard(),
    )
    return None


async def _require_uid_from_query(query: CallbackQuery, state: FSMContext) -> str | None:
    uid = await _uid_from_query(query, state)
    if uid:
        return uid
    await _respond_query(
        query,
        "未找到你的角色，请先注册后再使用功能。",
        reply_markup=ui.register_keyboard(),
        alert_text="请先注册角色",
    )
    return None


async def _show_main_menu_message(message: Message, state: FSMContext) -> None:
    uid = await _uid_from_message(message, state)
    if not uid:
        await message.answer(
            "欢迎来到修仙世界。你还没有角色，先点下方按钮注册。",
            reply_markup=ui.main_menu_keyboard(registered=False),
        )
        return
    stat = await api_get(f"/api/stat/{uid}")
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {})
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await message.answer(text, reply_markup=ui.main_menu_keyboard(registered=True))


async def _show_main_menu_query(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _uid_from_query(query, state)
    if not uid:
        await _respond_query(
            query,
            "欢迎来到修仙世界。你还没有角色，先点击注册。",
            reply_markup=ui.main_menu_keyboard(registered=False),
        )
        return
    stat = await api_get(f"/api/stat/{uid}")
    if stat.get("success"):
        text = ui.format_status_card(stat.get("status") or {})
    else:
        text = f"欢迎回来，修士。\n{_error_text(stat, '状态获取失败')}"
    await _respond_query(
        query,
        text,
        reply_markup=ui.main_menu_keyboard(registered=True),
    )


async def _show_status_message(message: Message, state: FSMContext) -> None:
    uid = await _require_uid_from_message(message, state)
    if not uid:
        return
    stat = await api_get(f"/api/stat/{uid}")
    if not stat.get("success"):
        await message.answer(
            f"❌ {_error_text(stat, '状态获取失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    await message.answer(
        ui.format_status_card(stat.get("status") or {}),
        reply_markup=ui.main_menu_keyboard(registered=True),
    )


async def _show_status_query(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    stat = await api_get(f"/api/stat/{uid}")
    if not stat.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(stat, '状态获取失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="状态获取失败",
        )
        return
    await _respond_query(
        query,
        ui.format_status_card(stat.get("status") or {}),
        reply_markup=ui.main_menu_keyboard(registered=True),
    )


async def _show_hunt_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    monsters_data = await api_get("/api/monsters", params={"user_id": uid})
    status_data = await api_get(f"/api/hunt/status/{uid}")
    if not monsters_data.get("success"):
        await message.answer(
            f"❌ {_error_text(monsters_data, '获取怪物列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    monsters = monsters_data.get("monsters") or []
    can_hunt = bool((status_data or {}).get("can_hunt", True))
    cooldown_remaining = int((status_data or {}).get("cooldown_remaining", 0) or 0)
    await state.set_state(HuntFSM.selecting_monster)
    await state.update_data(uid=uid)
    await message.answer(
        ui.format_hunt_panel(monsters, cooldown_remaining=cooldown_remaining, can_hunt=can_hunt),
        reply_markup=ui.hunt_monsters_keyboard(monsters),
    )


async def _show_hunt_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    monsters_data = await api_get("/api/monsters", params={"user_id": uid})
    status_data = await api_get(f"/api/hunt/status/{uid}")
    if not monsters_data.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(monsters_data, '获取怪物列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="获取怪物失败",
        )
        return
    monsters = monsters_data.get("monsters") or []
    can_hunt = bool((status_data or {}).get("can_hunt", True))
    cooldown_remaining = int((status_data or {}).get("cooldown_remaining", 0) or 0)
    await state.set_state(HuntFSM.selecting_monster)
    await state.update_data(uid=uid)
    await _respond_query(
        query,
        ui.format_hunt_panel(monsters, cooldown_remaining=cooldown_remaining, can_hunt=can_hunt),
        reply_markup=ui.hunt_monsters_keyboard(monsters),
    )


async def _show_secret_panel_message(message: Message, state: FSMContext, uid: str) -> None:
    data = await api_get(f"/api/secret-realms/{uid}")
    if not data.get("success"):
        await message.answer(
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return
    realms = data.get("realms") or []
    await state.set_state(SecretRealmFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    await message.answer(
        ui.format_secret_panel(realms, attempts_left=int(data.get("attempts_left", 0) or 0)),
        reply_markup=ui.secret_realms_keyboard(realms),
    )


async def _show_secret_panel_query(query: CallbackQuery, state: FSMContext, uid: str) -> None:
    data = await api_get(f"/api/secret-realms/{uid}")
    if not data.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="获取秘境失败",
        )
        return
    realms = data.get("realms") or []
    await state.set_state(SecretRealmFSM.selecting_realm)
    await state.update_data(uid=uid, secret_realms=realms)
    await _respond_query(
        query,
        ui.format_secret_panel(realms, attempts_left=int(data.get("attempts_left", 0) or 0)),
        reply_markup=ui.secret_realms_keyboard(realms),
    )


@router.message(Command("start", "menu", "xian_start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_main_menu_message(message, state)


@router.callback_query(F.data == "menu:home")
async def cb_menu_home(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_main_menu_query(query, state)


@router.message(Command("register", "xian_register"))
async def cmd_register(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    existing_uid = await resolve_uid(int(message.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await message.answer(
            "你已注册过角色。",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return

    explicit_name = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            explicit_name = parts[1].strip()
    username = _pick_username(message.from_user, explicit_name=explicit_name)
    payload = {
        "platform": "telegram",
        "platform_id": str(message.from_user.id),
        "username": username,
    }
    result = await api_post("/api/register", payload=payload)
    if not result.get("success") and str(result.get("code", "")).upper() == "USERNAME_TAKEN":
        fallback_name = _pick_username(message.from_user, explicit_name=f"{username}{str(message.from_user.id)[-2:]}")
        payload["username"] = fallback_name[:16]
        result = await api_post("/api/register", payload=payload)

    if result.get("success"):
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await message.answer(
            f"✅ 注册成功，欢迎 {result.get('username', username)}。",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        await _show_status_message(message, state)
        return

    if str(result.get("code", "")).upper() == "ALREADY_EXISTS":
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await message.answer(
            "你已注册过角色。",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return

    await message.answer(
        f"❌ 注册失败：{_error_text(result, '注册失败')}",
        reply_markup=ui.main_menu_keyboard(registered=False),
    )


@router.callback_query(F.data == "menu:register")
async def cb_register(query: CallbackQuery, state: FSMContext) -> None:
    if not query.from_user:
        await _safe_answer_callback(query)
        return

    existing_uid = await resolve_uid(int(query.from_user.id))
    if existing_uid:
        await state.update_data(uid=existing_uid)
        await _respond_query(
            query,
            "你已注册过角色。",
            reply_markup=ui.main_menu_keyboard(registered=True),
        )
        return

    username = _pick_username(query.from_user)
    payload = {
        "platform": "telegram",
        "platform_id": str(query.from_user.id),
        "username": username,
    }
    result = await api_post("/api/register", payload=payload)
    if not result.get("success") and str(result.get("code", "")).upper() == "USERNAME_TAKEN":
        payload["username"] = _pick_username(query.from_user, explicit_name=f"{username}{str(query.from_user.id)[-2:]}")[:16]
        result = await api_post("/api/register", payload=payload)

    if result.get("success") or str(result.get("code", "")).upper() == "ALREADY_EXISTS":
        uid = str(result.get("user_id") or "").strip()
        if uid:
            await state.update_data(uid=uid)
        await _show_status_query(query, state)
        return

    await _respond_query(
        query,
        f"❌ 注册失败：{_error_text(result, '注册失败')}",
        reply_markup=ui.main_menu_keyboard(registered=False),
        alert_text="注册失败",
    )


@router.message(Command("stat", "status", "xian_stat"))
async def cmd_stat(message: Message, state: FSMContext) -> None:
    await _show_status_message(message, state)


@router.callback_query(F.data == "menu:stat")
async def cb_stat(query: CallbackQuery, state: FSMContext) -> None:
    await _show_status_query(query, state)


@router.message(Command("hunt", "xian_hunt"))
async def cmd_hunt(message: Message, state: FSMContext) -> None:
    uid = await _require_uid_from_message(message, state)
    if not uid:
        return
    await _show_hunt_panel_message(message, state, uid)


@router.callback_query(F.data == "menu:hunt")
async def cb_menu_hunt(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    await _show_hunt_panel_query(query, state, uid)


@router.callback_query(F.data == "hunt:list")
async def cb_hunt_list(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    await _show_hunt_panel_query(query, state, uid)


@router.callback_query(F.data.startswith("hunt:start:"))
async def cb_hunt_start(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    monster_id = _extract_callback_tail(query, "hunt:start:")
    if not monster_id:
        await _safe_answer_callback(query, text="怪物参数错误", show_alert=True)
        return

    payload = {"user_id": uid, "monster_id": monster_id}
    result = await api_post("/api/hunt/turn/start", payload=payload)
    if not result.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(result, '发起狩猎失败')}",
            reply_markup=ui.hunt_settlement_keyboard(),
            alert_text="狩猎失败",
        )
        return

    session_id = str(result.get("session_id") or "").strip()
    if not session_id:
        await _respond_query(
            query,
            "❌ 未获取到战斗会话，请重试。",
            reply_markup=ui.hunt_settlement_keyboard(),
            alert_text="会话创建失败",
        )
        return

    await state.set_state(HuntFSM.in_battle)
    await state.update_data(uid=uid, hunt_session_id=session_id)
    await _respond_query(
        query,
        ui.format_hunt_battle_open(result),
        reply_markup=ui.hunt_battle_keyboard(result.get("active_skills") or []),
    )


async def _do_hunt_action(
    query: CallbackQuery,
    state: FSMContext,
    *,
    action: str,
    skill_id: str | None = None,
) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    session_id = str(data.get("hunt_session_id") or "").strip()
    if not session_id:
        await _show_hunt_panel_query(query, state, uid)
        return

    payload: dict[str, Any] = {
        "user_id": uid,
        "session_id": session_id,
        "action": action,
        "request_id": new_request_id(),
    }
    if skill_id:
        payload["skill_id"] = skill_id
    result = await api_post("/api/hunt/turn/action", payload=payload)

    if not result.get("success"):
        err = _error_text(result, "战斗处理失败")
        if _is_session_lost(err):
            await state.clear()
            await _show_hunt_panel_query(query, state, uid)
            return
        await _respond_query(
            query,
            f"❌ {err}",
            reply_markup=ui.hunt_battle_keyboard([]),
            alert_text="本回合失败",
        )
        return

    if result.get("finished") is False:
        await _respond_query(
            query,
            ui.format_battle_round(result, title="🦴 狩猎战斗"),
            reply_markup=ui.hunt_battle_keyboard(result.get("active_skills") or []),
        )
        return

    await state.clear()
    await _respond_query(
        query,
        ui.format_hunt_settlement(result),
        reply_markup=ui.hunt_settlement_keyboard(),
    )


@router.callback_query(F.data == "hunt:act:normal")
async def cb_hunt_action_normal(query: CallbackQuery, state: FSMContext) -> None:
    await _do_hunt_action(query, state, action="normal")


@router.callback_query(F.data.startswith("hunt:act:skill:"))
async def cb_hunt_action_skill(query: CallbackQuery, state: FSMContext) -> None:
    skill_id = _extract_callback_tail(query, "hunt:act:skill:")
    if not skill_id:
        await _safe_answer_callback(query, text="技能参数错误", show_alert=True)
        return
    await _do_hunt_action(query, state, action="skill", skill_id=skill_id)


@router.callback_query(F.data == "hunt:exit")
async def cb_hunt_exit(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_main_menu_query(query, state)

@router.message(Command("breakthrough", "bt", "xian_breakthrough"))
async def cmd_breakthrough(message: Message, state: FSMContext) -> None:
    uid = await _require_uid_from_message(message, state)
    if not uid:
        return
    strategy = "normal"
    call_for_help = True
    preview_resp = await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
            "call_for_help": "true" if call_for_help else "false",
        },
    )
    if not preview_resp.get("success"):
        text = f"❌ {_error_text(preview_resp, '突破预览失败')}"
        if str(preview_resp.get("code", "")).upper() == "REALM_TRIAL":
            hint = _breakthrough_trial_hint(preview_resp)
            if hint:
                text += f"\n\n需完成试炼：\n{hint}"
        await message.answer(text, reply_markup=ui.main_menu_keyboard(registered=True))
        return

    preview = preview_resp.get("preview") or {}
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await message.answer(
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
    )


@router.callback_query(F.data == "menu:break")
async def cb_menu_break(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    strategy = "normal"
    call_for_help = True
    preview_resp = await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
            "call_for_help": "true" if call_for_help else "false",
        },
    )
    if not preview_resp.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(preview_resp, '突破预览失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="预览失败",
        )
        return

    preview = preview_resp.get("preview") or {}
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await _respond_query(
        query,
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
    )


@router.callback_query(F.data.startswith("break:preview:"))
async def cb_break_preview(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    call_for_help = bool(data.get("breakthrough_call_for_help", True))
    strategy = _extract_callback_tail(query, "break:preview:").strip().lower() or "normal"
    preview_resp = await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
            "call_for_help": "true" if call_for_help else "false",
        },
    )
    if not preview_resp.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(preview_resp, '突破预览失败')}",
            reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
            alert_text="预览失败",
        )
        return

    preview = preview_resp.get("preview") or {}
    strategy = str(preview.get("strategy") or strategy)
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await _respond_query(
        query,
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
    )


@router.callback_query(F.data == "break:help:toggle")
async def cb_break_help_toggle(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    strategy = str(data.get("breakthrough_strategy") or "normal").strip().lower() or "normal"
    call_for_help = not bool(data.get("breakthrough_call_for_help", True))
    preview_resp = await api_get(
        f"/api/breakthrough/preview/{uid}",
        params={
            "strategy": strategy,
            "use_pill": "false",
            "call_for_help": "true" if call_for_help else "false",
        },
    )
    if not preview_resp.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(preview_resp, '突破预览失败')}",
            reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
            alert_text="预览失败",
        )
        return
    preview = preview_resp.get("preview") or {}
    strategy = str(preview.get("strategy") or strategy)
    await state.set_state(BreakthroughFSM.selecting_strategy)
    await state.update_data(
        uid=uid,
        breakthrough_strategy=strategy,
        breakthrough_call_for_help=call_for_help,
    )
    await _respond_query(
        query,
        ui.format_breakthrough_preview(preview),
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
        alert_text="已切换道友助阵",
    )


@router.callback_query(F.data == "break:confirm")
async def cb_break_confirm(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    strategy = str(data.get("breakthrough_strategy") or "normal").strip().lower()
    call_for_help = bool(data.get("breakthrough_call_for_help", True))

    result = await api_post(
        "/api/breakthrough",
        payload={
            "user_id": uid,
            "strategy": strategy,
            "use_pill": False,
            "call_for_help": call_for_help,
        },
    )
    if result.get("success"):
        mention = _markdown_safe_tg_mention(query.from_user)
        if mention:
            realm_name = str(result.get("new_realm", "") or "").strip() or "未知境界"
            result["congrats_message"] = (
                f"灵光一闪！恭喜 {mention} 道友，修为精进，成功突破至【{realm_name}】！"
            )
        await _respond_query(
            query,
            ui.format_breakthrough_result(result),
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="突破成功",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    code = str(result.get("code", "")).upper()
    if code == "BREAKTHROUGH_FAILED":
        await _respond_query(
            query,
            ui.format_breakthrough_result(result),
            reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
        )
        return

    text = f"❌ {_error_text(result, '突破失败')}"
    if code == "REALM_TRIAL":
        hint = _breakthrough_trial_hint(result)
        if hint:
            text += f"\n\n需完成试炼：\n{hint}"
    await _respond_query(
        query,
        text,
        reply_markup=ui.breakthrough_keyboard(strategy, call_for_help),
        alert_text="突破失败",
    )


@router.message(Command("secret", "realm", "xian_secret"))
async def cmd_secret(message: Message, state: FSMContext) -> None:
    uid = await _require_uid_from_message(message, state)
    if not uid:
        return
    await _show_secret_panel_message(message, state, uid)


@router.callback_query(F.data == "menu:secret")
async def cb_menu_secret(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    await _show_secret_panel_query(query, state, uid)


@router.callback_query(F.data == "secret:list")
async def cb_secret_list(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    await _show_secret_panel_query(query, state, uid)


@router.callback_query(F.data.startswith("secret:realm:"))
async def cb_secret_choose_realm(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    realm_id = _extract_callback_tail(query, "secret:realm:").strip()
    if not realm_id:
        await _safe_answer_callback(query, text="秘境参数错误", show_alert=True)
        return

    data = await api_get(f"/api/secret-realms/{uid}")
    if not data.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(data, '获取秘境列表失败')}",
            reply_markup=ui.main_menu_keyboard(registered=True),
            alert_text="获取秘境失败",
        )
        return
    realms = data.get("realms") or []
    selected = next((r for r in realms if str(r.get("id")) == realm_id), None)
    if not selected:
        await _respond_query(
            query,
            "❌ 该秘境暂不可进入，请刷新列表。",
            reply_markup=ui.secret_realms_keyboard(realms),
            alert_text="秘境不可用",
        )
        return

    await state.set_state(SecretRealmFSM.selecting_path)
    await state.update_data(
        uid=uid,
        secret_realms=realms,
        secret_realm_id=realm_id,
        secret_realm_name=selected.get("name", realm_id),
    )
    await _respond_query(
        query,
        ui.format_secret_path_prompt(selected),
        reply_markup=ui.secret_paths_keyboard(),
    )


@router.callback_query(F.data.startswith("secret:path:"))
async def cb_secret_choose_path(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    path = _extract_callback_tail(query, "secret:path:").strip().lower()
    if path not in ("safe", "normal", "risky", "loot"):
        await _safe_answer_callback(query, text="路线参数错误", show_alert=True)
        return
    data = await state.get_data()
    realm_id = str(data.get("secret_realm_id") or "").strip()
    if not realm_id:
        await _show_secret_panel_query(query, state, uid)
        return

    payload = {
        "user_id": uid,
        "realm_id": realm_id,
        "path": path,
        "interactive": True,
    }
    result = await api_post("/api/secret-realms/turn/start", payload=payload)
    if not result.get("success"):
        await _respond_query(
            query,
            f"❌ {_error_text(result, '进入秘境失败')}",
            reply_markup=ui.secret_paths_keyboard(),
            alert_text="进入失败",
        )
        return

    if result.get("needs_choice"):
        session_id = str(result.get("session_id") or "").strip()
        await state.set_state(SecretRealmFSM.in_event_choice)
        await state.update_data(
            uid=uid,
            secret_realm_id=realm_id,
            secret_path=path,
            secret_session_id=session_id,
        )
        await _respond_query(
            query,
            ui.format_secret_event(result),
            reply_markup=ui.secret_event_choices_keyboard(result.get("choices") or []),
        )
        return

    if result.get("needs_battle"):
        session_id = str(result.get("session_id") or "").strip()
        await state.set_state(SecretRealmFSM.in_battle)
        await state.update_data(
            uid=uid,
            secret_realm_id=realm_id,
            secret_path=path,
            secret_session_id=session_id,
        )
        await _respond_query(
            query,
            ui.format_secret_battle_open(result),
            reply_markup=ui.secret_battle_keyboard(result.get("active_skills") or []),
        )
        return

    await state.set_state(SecretRealmFSM.selecting_realm)
    await state.update_data(uid=uid)
    await _respond_query(
        query,
        ui.format_secret_settlement(result),
        reply_markup=ui.secret_settlement_keyboard(),
    )


@router.callback_query(F.data.startswith("secret:choice:"))
async def cb_secret_choice(query: CallbackQuery, state: FSMContext) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    session_id = str(data.get("secret_session_id") or "").strip()
    if not session_id:
        await _show_secret_panel_query(query, state, uid)
        return
    choice = _extract_callback_tail(query, "secret:choice:").strip().lower()
    if not choice:
        await _safe_answer_callback(query, text="选项参数错误", show_alert=True)
        return

    result = await api_post(
        "/api/secret-realms/turn/action",
        payload={
            "user_id": uid,
            "session_id": session_id,
            "action": "choice",
            "choice": choice,
            "request_id": new_request_id(),
        },
    )
    if not result.get("success"):
        err = _error_text(result, "事件处理失败")
        if _is_session_lost(err):
            await state.clear()
            await _show_secret_panel_query(query, state, uid)
            return
        await _respond_query(
            query,
            f"❌ {err}",
            reply_markup=ui.secret_settlement_keyboard(),
            alert_text="事件处理失败",
        )
        return

    if result.get("needs_battle"):
        session_id = str(result.get("session_id") or session_id).strip()
        await state.set_state(SecretRealmFSM.in_battle)
        await state.update_data(uid=uid, secret_session_id=session_id)
        await _respond_query(
            query,
            ui.format_secret_battle_open(result),
            reply_markup=ui.secret_battle_keyboard(result.get("active_skills") or []),
        )
        return

    await state.set_state(SecretRealmFSM.selecting_realm)
    await state.update_data(uid=uid)
    await _respond_query(
        query,
        ui.format_secret_settlement(result),
        reply_markup=ui.secret_settlement_keyboard(),
    )

async def _do_secret_action(
    query: CallbackQuery,
    state: FSMContext,
    *,
    action: str,
    skill_id: str | None = None,
) -> None:
    uid = await _require_uid_from_query(query, state)
    if not uid:
        return
    data = await state.get_data()
    session_id = str(data.get("secret_session_id") or "").strip()
    if not session_id:
        await _show_secret_panel_query(query, state, uid)
        return

    payload: dict[str, Any] = {
        "user_id": uid,
        "session_id": session_id,
        "action": action,
        "request_id": new_request_id(),
    }
    if skill_id:
        payload["skill_id"] = skill_id
    result = await api_post("/api/secret-realms/turn/action", payload=payload)

    if not result.get("success"):
        err = _error_text(result, "战斗处理失败")
        if _is_session_lost(err):
            await state.clear()
            await _show_secret_panel_query(query, state, uid)
            return
        await _respond_query(
            query,
            f"❌ {err}",
            reply_markup=ui.secret_battle_keyboard([]),
            alert_text="本回合失败",
        )
        return

    if result.get("finished") is False:
        await _respond_query(
            query,
            ui.format_battle_round(result, title="🗺️ 秘境战斗"),
            reply_markup=ui.secret_battle_keyboard(result.get("active_skills") or []),
        )
        return

    await state.set_state(SecretRealmFSM.selecting_realm)
    await state.update_data(uid=uid)
    await _respond_query(
        query,
        ui.format_secret_settlement(result),
        reply_markup=ui.secret_settlement_keyboard(),
    )


@router.callback_query(F.data == "secret:act:normal")
async def cb_secret_action_normal(query: CallbackQuery, state: FSMContext) -> None:
    await _do_secret_action(query, state, action="normal")


@router.callback_query(F.data.startswith("secret:act:skill:"))
async def cb_secret_action_skill(query: CallbackQuery, state: FSMContext) -> None:
    skill_id = _extract_callback_tail(query, "secret:act:skill:")
    if not skill_id:
        await _safe_answer_callback(query, text="技能参数错误", show_alert=True)
        return
    await _do_secret_action(query, state, action="skill", skill_id=skill_id)


@router.callback_query(F.data == "secret:exit")
async def cb_secret_exit(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_main_menu_query(query, state)


@router.callback_query()
async def cb_fallback(query: CallbackQuery) -> None:
    await _safe_answer_callback(query, text="该按钮尚未接入 aiogram FSM 流程", show_alert=False)
