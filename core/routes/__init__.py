"""Blueprint 注册中心。"""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from core.routes.health import health_bp
    from core.routes.user import user_bp
    from core.routes.cultivation import cultivation_bp
    from core.routes.combat import combat_bp
    from core.routes.equipment import equipment_bp
    from core.routes.skills import skills_bp
    from core.routes.shop import shop_bp
    from core.routes.quests import quests_bp
    from core.routes.misc import misc_bp
    from core.routes.pvp import pvp_bp
    from core.routes.sect import sect_bp
    from core.routes.alchemy import alchemy_bp
    from core.routes.gacha import gacha_bp
    from core.routes.achievements import achievements_bp
    from core.routes.events import events_bp
    from core.routes.resource_conversion import convert_bp
    from core.routes.social import social_bp
    from core.routes.story import story_bp

    for bp in (health_bp, user_bp, cultivation_bp, combat_bp,
               equipment_bp, skills_bp, shop_bp, quests_bp, misc_bp,
               pvp_bp, sect_bp, alchemy_bp, gacha_bp, achievements_bp, events_bp, convert_bp, social_bp, story_bp):
        app.register_blueprint(bp)
