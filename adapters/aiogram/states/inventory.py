"""Inventory/skills FSM states."""

from aiogram.fsm.state import State, StatesGroup


class InventoryFSM(StatesGroup):
    bag_browsing = State()
    gear_browsing = State()
    equipped_view = State()


class SkillsFSM(StatesGroup):
    listing = State()
    learning = State()
    equipping = State()


class InventoryBreakthroughFSM(StatesGroup):
    breakthrough_selecting = State()
    breakthrough_confirm = State()
    unequipping = State()


class BreakthroughFSM(StatesGroup):
    selecting_strategy = State()
    confirm = State()
    result = State()
