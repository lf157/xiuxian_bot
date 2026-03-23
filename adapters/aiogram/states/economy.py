"""Economy/story FSM states."""

from aiogram.fsm.state import State, StatesGroup


class EconomyFSM(StatesGroup):
    selecting_currency = State()
    shop_browsing = State()
    alchemy_panel = State()
    forge_panel = State()
    quest_menu = State()
    event_menu = State()
    story_menu = State()
    rank_menu = State()
    boss_menu = State()
    bounty_menu = State()


class ShopFSM(EconomyFSM):
    """Backward-compat alias for handlers that still use ShopFSM name."""


class StoryEventsFSM(EconomyFSM):
    """Backward-compat alias for handlers that still use StoryEventsFSM name."""
