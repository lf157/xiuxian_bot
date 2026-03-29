"""
用户状态工具
"""

import logging
import time
from core.database.connection import fetch_one, refresh_user_stamina, refresh_user_vitals, DEFAULT_STAMINA_MAX
from core.game.realms import get_realm_by_id, get_next_realm, format_realm_progress, format_realm_display, get_game_time
from core.game.currency import wallet_from_user
from core.services.sect_service import apply_sect_stat_buffs, get_user_sect_buffs

logger = logging.getLogger(__name__)


def _format_stamina_value(value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return value
    if abs(val - int(val)) < 1e-6:
        return int(val)
    return round(val, 1)


def _format_remaining_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        if minutes > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{hours}小时"
    if minutes > 0:
        if secs > 0:
            return f"{minutes}分钟{secs}秒"
        return f"{minutes}分钟"
    return f"{secs}秒"


def get_user_status(user_id):
    """获取用户完整状态"""
    try:
        refresh_user_stamina(user_id)
        refresh_user_vitals(user_id)
        user = fetch_one("SELECT * FROM users WHERE user_id = %s", (user_id,))
        
        if not user:
            return None
        user = apply_sect_stat_buffs(user)
        sect_buffs = user.get("sect_buffs") or get_user_sect_buffs(user_id)
        wallet = wallet_from_user(user)

        rank = user.get('rank', 1)
        realm = get_realm_by_id(rank)
        next_realm = get_next_realm(rank)
        current_map = str(user.get("current_map") or "canglan_city")
        current_map_name = current_map
        try:
            from core.game.maps import get_map
            map_info = get_map(current_map)
            if map_info:
                current_map_name = str(map_info.get("name") or current_map)
        except Exception:
            pass
        weak_until = int(user.get("weak_until", 0) or 0)
        weak_remaining_seconds = max(0, weak_until - int(time.time()))
        is_weak = bool(user.get("is_weak", False) or weak_remaining_seconds > 0)
        
        element = user.get("element") or "无"
        
        current_exp = user.get('exp', 0)
        next_exp = next_realm['exp_required'] if next_realm else None
        
        return {
            'user_id': user_id,
            'in_game_username': user.get('in_game_username', '未知'),
            'rank': rank,
            'realm_name': realm['name'] if realm else "未知",
            'exp': current_exp,
            'next_exp': next_exp,
            'element': element,
            'current_map': current_map,
            'current_map_name': current_map_name,
            'copper': user.get('copper', 0),
            'gold': user.get('gold', 0),
            'spirit_stone_low': wallet.get('spirit_low', 0),
            'spirit_stone_mid': wallet.get('spirit_mid', 0),
            'spirit_stone_high': wallet.get('spirit_high', 0),
            'spirit_stone_exquisite': wallet.get('spirit_exquisite', 0),
            'spirit_stone_supreme': wallet.get('spirit_supreme', 0),
            'immortal_stone_flawed': wallet.get('immortal_flawed', 0),
            'immortal_stone_low': wallet.get('immortal_low', 0),
            'immortal_stone_mid': wallet.get('immortal_mid', 0),
            'immortal_stone_high': wallet.get('immortal_high', 0),
            'immortal_stone_supreme': wallet.get('immortal_supreme', 0),
            'stamina': user.get('stamina', DEFAULT_STAMINA_MAX),
            'max_stamina': DEFAULT_STAMINA_MAX,
            'hp': user.get('hp', 100),
            'mp': user.get('mp', 50),
            'max_hp': user.get('max_hp', 100),
            'max_mp': user.get('max_mp', 50),
            'attack': user.get('attack', 10),
            'defense': user.get('defense', 5),
            'crit_rate': user.get('crit_rate', 0.05),
            'dy_times': user.get('dy_times', 0),
            'breakthrough_pity': user.get('breakthrough_pity', 0),
            'breakthrough_boost_until': int(user.get('breakthrough_boost_until', 0) or 0),
            'breakthrough_boost_pct': float(user.get('breakthrough_boost_pct', 0) or 0.0),
            'breakthrough_protect_until': int(user.get('breakthrough_protect_until', 0) or 0),
            'state': user.get('state', False),
            'weak_until': weak_until,
            'is_weak': is_weak,
            'weak_remaining_seconds': weak_remaining_seconds,
            'weak_debuff_pct': int(user.get('weak_debuff_pct', 30) or 30),
            'weak_effects': user.get('weak_effects') or (
                ["不能开始修炼", "气血/灵力/攻击/防御/暴击率 -30%"] if is_weak else []
            ),
            'lang': user.get('lang', 'CHS'),
            'equipped_weapon': user.get('equipped_weapon'),
            'equipped_armor': user.get('equipped_armor'),
            'equipped_accessory1': user.get('equipped_accessory1'),
            'equipped_accessory2': user.get('equipped_accessory2'),
            'sect_buffs': sect_buffs,
        }
        
    except Exception:
        logger.exception("Error getting user status for %s", user_id)
        return None


def _make_bar(current: int, maximum: int, length: int = 10) -> str:
    """生成 ██████░░░░ 形式的进度条。"""
    if maximum <= 0:
        ratio = 1.0
    else:
        ratio = max(0.0, min(1.0, current / maximum))
    filled = int(round(ratio * length))
    return '█' * filled + '░' * (length - filled)


def format_status_text(status_info, lang="CHS", platform=None, equipped_items=None):
    """格式化状态显示"""
    if not status_info:
        return "❌ 用户信息未找到"

    username = status_info.get('in_game_username', '未知')
    realm_display = format_realm_display(status_info.get('rank', 1))
    element = status_info.get('element', '无')

    # ── 状态 & 异常 ──
    state_label = "修炼中" if status_info.get("state") else "空闲中"
    weak_active = bool(status_info.get("is_weak"))
    if weak_active:
        weak_left = max(0, int(status_info.get("weak_remaining_seconds", 0) or 0))
        condition_label = f"虚弱（{_format_remaining_duration(weak_left)}）"
    else:
        condition_label = "无异常"

    # ── 数值 ──
    current_exp = int(status_info.get('exp', 0) or 0)
    next_exp = status_info.get('next_exp')
    hp = int(status_info.get('hp', 100) or 100)
    max_hp = int(status_info.get('max_hp', 100) or 100)
    mp = int(status_info.get('mp', 50) or 50)
    max_mp = int(status_info.get('max_mp', 50) or 50)
    stamina_raw = status_info.get('stamina', DEFAULT_STAMINA_MAX)
    max_stamina = int(status_info.get('max_stamina', DEFAULT_STAMINA_MAX) or DEFAULT_STAMINA_MAX)
    try:
        stamina_int = int(float(stamina_raw))
    except (TypeError, ValueError):
        stamina_int = max_stamina

    attack = int(status_info.get('attack', 10) or 10)
    defense = int(status_info.get('defense', 5) or 5)
    copper = int(status_info.get('copper', 0) or 0)
    gold = int(status_info.get('gold', 0) or 0)
    map_name = status_info.get('current_map_name') or status_info.get('current_map', '苍澜城')

    # ── 进度条 ──
    if next_exp:
        next_exp_int = int(next_exp)
        exp_bar = _make_bar(current_exp, next_exp_int)
        exp_line = f"📖修为  {exp_bar} {current_exp:,}/{next_exp_int:,}"
    else:
        exp_line = f"📖修为  {'█' * 10} {current_exp:,}（已满级）"

    hp_bar = _make_bar(hp, max_hp)
    mp_bar = _make_bar(mp, max_mp)
    stamina_bar = _make_bar(stamina_int, max_stamina)

    lines = [
        f"*{username}*　·　{realm_display}　·　{element}灵根",
        "",
        f"——🧘状态 · {state_label} · {condition_label}——",
        exp_line,
        f"❤️气血  {hp_bar} {hp:,}/{max_hp:,}",
        f"💙灵力  {mp_bar} {mp:,}/{max_mp:,}",
        f"⚡️精力  {stamina_bar} {stamina_int}/{max_stamina}",
        "",
        f"⚔️攻击 {attack:,}　　🛡️防御 {defense:,}",
        f"🟦下品灵石 {copper:,}　　🟩中品灵石 {gold:,}",
    ]

    # ── 虚弱详情 ──
    if weak_active:
        weak_effects = status_info.get("weak_effects") or [
            "不能开始修炼",
            f"气血/灵力/攻击/防御/暴击率 -{int(status_info.get('weak_debuff_pct', 30) or 30)}%",
        ]
        lines.append("")
        lines.append("⚠️虚弱影响：")
        for effect in weak_effects:
            lines.append(f"　• {effect}")

    # ── 装备 ──
    if equipped_items:
        equip_parts = [f"　{item}" for item in equipped_items.values() if item]
        if equip_parts:
            lines.append("")
            lines.append("——👕装备——")
            lines.extend(equip_parts)

    # ── 宗门 ──
    sect_buffs = status_info.get("sect_buffs") or {}
    if sect_buffs.get("in_sect"):
        lines.append("")
        lines.append(f"——🏛️宗门 · {sect_buffs.get('sect_name')}——")
        lines.append(
            f"修炼+{int(float(sect_buffs.get('cultivation_pct', 0) or 0))}%　"
            f"属性+{int(float(sect_buffs.get('stat_pct', 0) or 0))}%　"
            f"战斗收益+{int(float(sect_buffs.get('battle_reward_pct', 0) or 0))}%"
        )

    # ── 所在地 ──
    lines.append("")
    lines.append("——🗺所在地——")
    lines.append(f"📌{map_name}")

    return "\n".join(lines)


def check_user_exists(user_id):
    """检查用户是否存在"""
    try:
        user = fetch_one("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
        return user is not None
    except Exception:
        logger.exception("Error checking user existence for %s", user_id)
        return False
