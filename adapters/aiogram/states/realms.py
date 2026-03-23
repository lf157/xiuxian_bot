"""Secret realms FSM states."""

from aiogram.fsm.state import State, StatesGroup


class SecretRealmsFSM(StatesGroup):
    selecting_realm = State()
    selecting_path = State()
    in_event_choice = State()
    in_battle = State()
    settlement = State()

