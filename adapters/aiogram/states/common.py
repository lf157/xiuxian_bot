"""Common/menu FSM states."""

from aiogram.fsm.state import State, StatesGroup


class MenuAccountFSM(StatesGroup):
    idle = State()
    menu_home = State()
    registering = State()
    viewing_stat = State()
    version_view = State()
