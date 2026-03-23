"""Combat-domain FSM states."""

from aiogram.fsm.state import State, StatesGroup


class CultivationFSM(StatesGroup):
    idle = State()
    cultivating = State()
    reward_preview = State()


class HuntFSM(StatesGroup):
    selecting_monster = State()
    in_battle = State()
    settlement = State()
