"""Root router aggregation for aiogram adapter."""

from __future__ import annotations

from aiogram import Router

from adapters.aiogram.handlers.admin import router as admin_router
from adapters.aiogram.handlers.breakthrough import router as breakthrough_router
from adapters.aiogram.handlers.cultivation import router as cultivation_router
from adapters.aiogram.handlers.hunt import router as hunt_router
from adapters.aiogram.handlers.inventory_equipment import router as inventory_equipment_router
from adapters.aiogram.handlers.menu_account import router as menu_account_router
from adapters.aiogram.handlers.secret_realms import router as secret_realms_router
from adapters.aiogram.handlers.shop_alchemy_forge import router as shop_alchemy_forge_router
from adapters.aiogram.handlers.skills import router as skills_router
from adapters.aiogram.handlers.social_pvp_sect import router as social_pvp_sect_router
from adapters.aiogram.handlers.story_events_quests import router as story_events_quests_router
from adapters.aiogram.handlers.travel import router as travel_router

root_router = Router(name="aiogram_root")
root_router.include_router(menu_account_router)
root_router.include_router(cultivation_router)
root_router.include_router(hunt_router)
root_router.include_router(breakthrough_router)
root_router.include_router(inventory_equipment_router)
root_router.include_router(skills_router)
root_router.include_router(shop_alchemy_forge_router)
root_router.include_router(secret_realms_router)
root_router.include_router(social_pvp_sect_router)
root_router.include_router(story_events_quests_router)
root_router.include_router(travel_router)
root_router.include_router(admin_router)

__all__ = ["root_router"]
