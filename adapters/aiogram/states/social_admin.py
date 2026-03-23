"""Social/PVP/sect/admin FSM states."""

from aiogram.fsm.state import State, StatesGroup


class SocialPvpSectFSM(StatesGroup):
    social_menu = State()
    pvp_menu = State()
    sect_menu = State()


class AdminFSM(StatesGroup):
    admin_menu = State()
    target_lookup = State()
    modify_preview = State()
    confirm_apply = State()
