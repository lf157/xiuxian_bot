"""API smoke test: simulate real-user requests via Flask test client."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _assert_ok(resp, label: str):
    data = resp.get_json(silent=True)
    if resp.status_code != 200:
        raise AssertionError(f"{label} HTTP {resp.status_code}: {data}")
    if isinstance(data, dict) and data.get("success") is False:
        raise AssertionError(f"{label} failed: {data}")
    return data


def _assert_status(resp, label: str, allowed: tuple[int, ...] = (200,)):
    data = resp.get_json(silent=True)
    status = int(resp.status_code)
    if status >= 500:
        raise AssertionError(f"{label} HTTP {status}: {data}")
    if status not in allowed:
        raise AssertionError(f"{label} HTTP {status} not in {allowed}: {data}")
    if status == 200 and isinstance(data, dict) and data.get("success") is False:
        raise AssertionError(f"{label} HTTP 200 but success=false: {data}")
    return data


def _checkpoint(name: str) -> None:
    print(f"CHECKPOINT:{name}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.environ["XXBOT_INTERNAL_API_TOKEN"] = "test_internal_token"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from core.database.connection import connect_db, create_tables, execute, fetch_one, close_db, add_item
    from core.database.migrations import run_migrations
    from core.server import create_app
    from core.config import config
    from core.game.items import generate_material, generate_pill, get_item_by_id, generate_equipment, Quality

    connect_db()
    create_tables()
    run_migrations()

    app = create_app()
    client = app.test_client()

    token = config.internal_api_token
    base_headers = {
        "X-Internal-Token": token,
        "Content-Type": "application/json",
    }

    def api_post(path: str, payload: dict, actor: str | None = None):
        headers = dict(base_headers)
        if actor:
            headers["X-Actor-User-Id"] = actor
        return client.post(path, data=json.dumps(payload), headers=headers)

    def api_get(path: str, actor: str | None = None, **params):
        headers = dict(base_headers)
        if actor:
            headers["X-Actor-User-Id"] = actor
        return client.get(path, query_string=params or None, headers=headers)

    # Register users
    a = _assert_ok(
        api_post(
            "/api/register",
            {"platform": "telegram", "platform_id": "tg_a", "username": "测试甲"},
        ),
        "register A",
    )
    b = _assert_ok(
        api_post(
            "/api/register",
            {"platform": "telegram", "platform_id": "tg_b", "username": "测试乙"},
        ),
        "register B",
    )
    c = _assert_ok(
        api_post(
            "/api/register",
            {"platform": "telegram", "platform_id": "tg_c", "username": "测试丙"},
        ),
        "register C",
    )
    uid_a = a["user_id"]
    uid_b = b["user_id"]
    uid_c = c["user_id"]
    _checkpoint("register_ok")

    now = int(time.time())
    # Seed resources for tests
    execute(
        "UPDATE users SET copper = %s, gold = %s, stamina = %s, stamina_updated_at = %s, rank = %s WHERE user_id = %s",
        (20000, 50, 24, now, 10, uid_a),
    )
    execute(
        "UPDATE users SET copper = %s, gold = %s, stamina = %s, stamina_updated_at = %s, rank = %s WHERE user_id = %s",
        (20000, 50, 24, now, 10, uid_b),
    )
    execute(
        "UPDATE users SET copper = %s, gold = %s, stamina = %s, stamina_updated_at = %s, rank = %s WHERE user_id = %s",
        (20000, 50, 24, now, 10, uid_c),
    )
    sword = get_item_by_id("spirit_sword")
    armor = get_item_by_id("spirit_armor")
    if not sword or not armor:
        raise AssertionError("missing base equipment definitions")
    eq_a_weapon = add_item(uid_a, generate_equipment(sword, Quality.COMMON, level=1))
    eq_a_armor = add_item(uid_a, generate_equipment(armor, Quality.COMMON, level=1))
    add_item(uid_a, generate_pill("small_exp_pill", 2))
    if not eq_a_weapon or not eq_a_armor:
        raise AssertionError("failed to seed base equipment for API e2e")

    # Shop list + buy
    shop = _assert_ok(api_get("/api/shop", actor=uid_a, user_id=uid_a, currency="copper"), "shop list")
    items = shop.get("items", [])
    if not items:
        raise AssertionError("shop list empty")
    if not items[0].get("name"):
        raise AssertionError("shop item missing name")
    buy_item_id = items[0].get("item_id")
    _assert_ok(
        api_post("/api/shop/buy", {"user_id": uid_a, "item_id": buy_item_id, "quantity": 1}, actor=uid_a),
        "shop buy",
    )
    _checkpoint("shop_buy_ok")

    # Sect create + join
    sect = _assert_ok(
        api_post(
            "/api/sect/create",
            {"user_id": uid_a, "name": "测试宗门", "description": "自动化测试"},
            actor=uid_a,
        ),
        "sect create",
    )
    sect_id = sect.get("sect_id")
    if not sect_id:
        raise AssertionError("sect_id missing")
    _assert_ok(
        api_post("/api/sect/join", {"user_id": uid_b, "sect_id": sect_id}, actor=uid_b),
        "sect join",
    )

    # Learn a skill for secret realm test (qixue_slash)
    # Ensure user has enough copper for skill learn
    execute("UPDATE users SET copper = 20000, gold = 50 WHERE user_id = %s", (uid_a,))
    _assert_ok(
        api_post("/api/skills/learn", {"user_id": uid_a, "skill_id": "qixue_slash"}, actor=uid_a),
        "skill learn",
    )

    # Secret realm list + turn start + action (skill)
    realms = _assert_ok(api_get(f"/api/secret-realms/{uid_a}", actor=uid_a), "secret realms")
    realm_list = realms.get("realms", [])
    if not realm_list:
        raise AssertionError("secret realm list empty")
    realm_id = realm_list[0].get("id")
    if not realm_id:
        raise AssertionError("realm id missing")

    session_id = None
    active_skill_id = None
    for _ in range(5):
        # reset cooldown/attempts and stamina to ensure test stability
        execute(
            "UPDATE users SET last_secret_time = 0, secret_realm_attempts = 0, stamina = 24, stamina_updated_at = %s WHERE user_id = %s",
            (int(time.time()), uid_a),
        )
        start = _assert_ok(
            api_post(
                "/api/secret-realms/turn/start",
                {"user_id": uid_a, "realm_id": realm_id, "path": "normal"},
                actor=uid_a,
            ),
            "secret realm start",
        )
        if start.get("needs_battle"):
            session_id = start.get("session_id")
            skills = start.get("active_skills") or []
            if skills:
                active_skill_id = skills[0].get("id")
            break
    if not session_id:
        raise AssertionError("secret realm did not enter battle after retries")
    if not active_skill_id:
        raise AssertionError("no active skill available in secret realm session")

    _assert_ok(
        api_post(
            "/api/secret-realms/turn/action",
            {"user_id": uid_a, "session_id": session_id, "action": "skill", "skill_id": active_skill_id},
            actor=uid_a,
        ),
        "secret realm action",
    )
    _checkpoint("secret_realm_turn_action_ok")

    # Alchemy: list recipes, add materials, brew first recipe
    recipes = _assert_ok(api_get("/api/alchemy/recipes", actor=uid_a, user_id=uid_a), "alchemy recipes")
    recipe_list = recipes.get("recipes", [])
    if not recipe_list:
        raise AssertionError("alchemy recipes empty")
    recipe = recipe_list[0]
    for mat in recipe.get("materials", []):
        item = generate_material(mat["item_id"], int(mat.get("quantity", 1) or 1))
        if item:
            from core.database.connection import add_item
            add_item(uid_a, item)
    execute("UPDATE users SET copper = 20000, stamina = 24, stamina_updated_at = %s WHERE user_id = %s", (int(time.time()), uid_a))
    _assert_ok(
        api_post("/api/alchemy/brew", {"user_id": uid_a, "recipe_id": recipe["id"]}, actor=uid_a),
        "alchemy brew",
    )
    _checkpoint("alchemy_brew_ok")

    # Gacha: list banners, pull once
    banners = _assert_ok(api_get("/api/gacha/banners"), "gacha banners")
    banner_list = banners.get("banners", [])
    if not banner_list:
        raise AssertionError("gacha banners empty")
    banner_id = banner_list[0].get("banner_id")
    execute("UPDATE users SET gold = 100, copper = 20000, stamina = 24, stamina_updated_at = %s WHERE user_id = %s", (int(time.time()), uid_a))
    _assert_ok(
        api_post("/api/gacha/pull", {"user_id": uid_a, "banner_id": banner_id, "count": 1}, actor=uid_a),
        "gacha pull",
    )
    _checkpoint("gacha_pull_ok")

    # Resource conversion: list options and convert 1 batch (steady)
    options = _assert_ok(api_get(f"/api/convert/options/{uid_a}", actor=uid_a), "convert options")
    targets = options.get("targets", [])
    if not targets:
        raise AssertionError("convert targets empty")
    target_id = targets[0].get("item_id")
    execute("UPDATE users SET copper = 20000, stamina = 24, stamina_updated_at = %s WHERE user_id = %s", (int(time.time()), uid_a))
    _assert_ok(
        api_post(
            "/api/convert",
            {"user_id": uid_a, "target_item_id": target_id, "quantity": 1, "route": "steady"},
            actor=uid_a,
        ),
        "resource convert",
    )
    _checkpoint("resource_convert_ok")

    # PVP: get opponents and challenge
    opponents = _assert_ok(api_get(f"/api/pvp/opponents/{uid_a}", actor=uid_a), "pvp opponents")
    opp_list = opponents.get("opponents", [])
    if not opp_list:
        raise AssertionError("pvp opponents empty")
    opp_id = opp_list[0].get("user_id")
    if not opp_id:
        raise AssertionError("pvp opponent id missing")
    execute("UPDATE users SET stamina = 24, stamina_updated_at = %s WHERE user_id = %s", (int(time.time()), uid_a))
    _assert_ok(
        api_post("/api/pvp/challenge", {"user_id": uid_a, "opponent_id": opp_id}, actor=uid_a),
        "pvp challenge",
    )
    _checkpoint("pvp_challenge_ok")

    # Social chat flow (A->B) via API
    chat_req = _assert_ok(
        api_post("/api/social/chat/request", {"user_id": uid_a, "target_user_id": uid_b}, actor=uid_a),
        "chat request",
    )
    req_id = chat_req.get("request_id")
    if not req_id:
        raise AssertionError("chat request_id missing")
    _assert_ok(
        api_post("/api/social/chat/accept", {"user_id": uid_b, "request_id": req_id}, actor=uid_b),
        "chat accept",
    )

    # Surface coverage for remaining API routes (allow business 4xx, fail on 5xx)
    _assert_status(client.get("/health"), "health", allowed=(200,))
    _assert_status(client.get("/api/health"), "api health", allowed=(200,))

    _assert_status(api_get("/api/user/lookup", platform="telegram", platform_id="tg_a"), "lookup A", allowed=(200,))
    _assert_status(api_get(f"/api/stat/{uid_a}", actor=uid_a), "stat A", allowed=(200,))
    _assert_status(api_get(f"/api/codex/{uid_a}", actor=uid_a, kind="items"), "codex items", allowed=(200,))
    _assert_status(api_get(f"/api/codex/{uid_a}", actor=uid_a, kind="monsters"), "codex monsters", allowed=(200,))
    _assert_status(api_get("/api/leaderboard", mode="power"), "leaderboard misc power", allowed=(200,))
    _assert_status(
        api_get("/api/leaderboard", mode="stage", user_id=uid_a, stage_only="true"),
        "leaderboard misc stage",
        allowed=(200,),
    )

    _assert_status(api_post("/api/cultivate/start", {"user_id": uid_a}, actor=uid_a), "cultivate start", allowed=(200, 400))
    _assert_status(api_get(f"/api/cultivate/status/{uid_a}", actor=uid_a), "cultivate status", allowed=(200,))
    _assert_status(api_post("/api/cultivate/end", {"user_id": uid_a}, actor=uid_a), "cultivate end", allowed=(200, 400))
    _assert_status(api_get(f"/api/realm-trial/{uid_a}", actor=uid_a), "realm trial", allowed=(200,))
    _assert_status(api_get(f"/api/signin/{uid_a}", actor=uid_a), "signin status", allowed=(200,))
    _assert_status(api_post("/api/signin", {"user_id": uid_a}, actor=uid_a), "signin post", allowed=(200, 400))
    _assert_status(
        api_post("/api/breakthrough", {"user_id": uid_a, "strategy": "steady"}, actor=uid_a),
        "breakthrough",
        allowed=(200, 400),
    )

    monsters_resp = _assert_status(api_get("/api/monsters"), "monsters list", allowed=(200,))
    _assert_status(api_get("/api/realms"), "realms list", allowed=(200,))
    _assert_status(api_get(f"/api/hunt/status/{uid_a}", actor=uid_a), "hunt status", allowed=(200,))
    monster_list = (monsters_resp or {}).get("monsters") or []
    monster_id = monster_list[0].get("id") if monster_list else None
    if monster_id:
        execute(
            "UPDATE users SET last_hunt_time = 0, stamina = 24, stamina_updated_at = %s WHERE user_id = %s",
            (int(time.time()), uid_a),
        )
        _assert_status(
            api_post("/api/hunt", {"user_id": uid_a, "monster_id": monster_id}, actor=uid_a),
            "hunt auto",
            allowed=(200, 400),
        )
        execute(
            "UPDATE users SET last_hunt_time = 0, stamina = 24, stamina_updated_at = %s WHERE user_id = %s",
            (int(time.time()), uid_a),
        )
        hstart_resp = api_post("/api/hunt/turn/start", {"user_id": uid_a, "monster_id": monster_id}, actor=uid_a)
        hstart_data = _assert_status(hstart_resp, "hunt turn start", allowed=(200, 400))
        if hstart_resp.status_code == 200 and isinstance(hstart_data, dict) and hstart_data.get("session_id"):
            _assert_status(
                api_post(
                    "/api/hunt/turn/action",
                    {"user_id": uid_a, "session_id": hstart_data["session_id"], "action": "normal"},
                    actor=uid_a,
                ),
                "hunt turn action",
                allowed=(200, 400),
            )
        _assert_status(
            api_post("/api/secret-realms/explore", {"user_id": uid_a, "realm_id": realm_id}, actor=uid_a),
            "secret realms explore",
            allowed=(200, 400),
        )

    items_resp = _assert_status(api_get(f"/api/items/{uid_a}", actor=uid_a), "items list", allowed=(200,))
    _assert_status(api_post("/api/equip", {"user_id": uid_a, "item_id": int(eq_a_weapon)}, actor=uid_a), "equip", allowed=(200, 400))
    _assert_status(api_post("/api/unequip", {"user_id": uid_a, "slot": "equipped_weapon"}, actor=uid_a), "unequip", allowed=(200, 400))
    execute("UPDATE users SET last_enhance_time = 0 WHERE user_id = %s", (uid_a,))
    _assert_status(
        api_post("/api/enhance", {"user_id": uid_a, "item_id": int(eq_a_weapon), "strategy": "steady"}, actor=uid_a),
        "enhance",
        allowed=(200, 400),
    )
    _assert_status(api_get(f"/api/forge/{uid_a}", actor=uid_a), "forge status", allowed=(200,))
    _assert_status(api_post("/api/forge", {"user_id": uid_a}, actor=uid_a), "forge post", allowed=(200, 400))
    catalog_data = _assert_status(api_get(f"/api/forge/catalog/{uid_a}", actor=uid_a), "forge catalog", allowed=(200,))
    target_item = None
    catalog_items = (catalog_data or {}).get("items") or []
    if catalog_items:
        target_item = catalog_items[0].get("item_id")
    if target_item:
        _assert_status(
            api_post("/api/forge/targeted", {"user_id": uid_a, "item_id": target_item}, actor=uid_a),
            "forge targeted",
            allowed=(200, 400),
        )
    _assert_status(api_post("/api/decompose", {"user_id": uid_a, "item_id": int(eq_a_armor)}, actor=uid_a), "decompose", allowed=(200, 400))
    _assert_status(api_post("/api/item/use", {"user_id": uid_a, "item_id": "small_exp_pill"}, actor=uid_a), "item use", allowed=(200, 400))

    _assert_status(api_get(f"/api/skills/{uid_a}", actor=uid_a), "skills list", allowed=(200,))
    _assert_status(api_post("/api/skills/equip", {"user_id": uid_a, "skill_id": "qixue_slash"}, actor=uid_a), "skills equip", allowed=(200, 400))
    _assert_status(api_post("/api/skills/unequip", {"user_id": uid_a, "skill_id": "qixue_slash"}, actor=uid_a), "skills unequip", allowed=(200, 400))
    _assert_status(
        api_post("/api/skills/upgrade", {"user_id": uid_a, "skill_id": "qixue_slash"}, actor=uid_a),
        "skills upgrade",
        allowed=(200, 400),
    )

    _assert_status(api_get(f"/api/gacha/pity/{uid_a}", actor=uid_a, banner_id=banner_id), "gacha pity", allowed=(200, 400))
    _assert_status(api_get(f"/api/gacha/status/{uid_a}", actor=uid_a), "gacha status", allowed=(200,))

    quests_data = _assert_status(api_get(f"/api/quests/{uid_a}", actor=uid_a), "quests list", allowed=(200,))
    quests = (quests_data or {}).get("quests") or []
    if quests:
        _assert_status(
            api_post("/api/quests/claim", {"user_id": uid_a, "quest_id": quests[0].get("quest_id")}, actor=uid_a),
            "quests claim",
            allowed=(200, 400),
        )
    _assert_status(api_post("/api/quests/claim_all", {"user_id": uid_a}, actor=uid_a), "quests claim all", allowed=(200, 400))

    ach_data = _assert_status(api_get(f"/api/achievements/{uid_a}", actor=uid_a), "achievements list", allowed=(200,))
    achievements = (ach_data or {}).get("achievements") or []
    if achievements:
        _assert_status(
            api_post("/api/achievements/claim", {"user_id": uid_a, "achievement_id": achievements[0].get("id")}, actor=uid_a),
            "achievements claim",
            allowed=(200, 400),
        )

    events_data = _assert_status(api_get("/api/events"), "events list", allowed=(200,))
    _assert_status(api_get(f"/api/events/status/{uid_a}", actor=uid_a), "events status", allowed=(200,))
    events = (events_data or {}).get("events") or []
    if events:
        event_id = events[0].get("id")
        if event_id:
            _assert_status(api_post("/api/events/claim", {"user_id": uid_a, "event_id": event_id}, actor=uid_a), "events claim", allowed=(200, 400))
            exchange_shop = events[0].get("exchange_shop") or []
            if exchange_shop and exchange_shop[0].get("id"):
                _assert_status(
                    api_post(
                        "/api/events/exchange",
                        {"user_id": uid_a, "event_id": event_id, "exchange_id": exchange_shop[0]["id"], "quantity": 1},
                        actor=uid_a,
                    ),
                    "events exchange",
                    allowed=(200, 400),
                )
    _assert_status(api_get("/api/worldboss/status"), "worldboss status", allowed=(200,))
    _assert_status(api_post("/api/worldboss/attack", {"user_id": uid_a}, actor=uid_a), "worldboss attack", allowed=(200, 400))

    _assert_status(api_get(f"/api/pvp/records/{uid_a}", actor=uid_a), "pvp records", allowed=(200,))
    _assert_status(api_get("/api/pvp/ranking"), "pvp ranking", allowed=(200,))

    _assert_status(api_get("/api/sect/list"), "sect list", allowed=(200,))
    _assert_status(api_get(f"/api/sect/{sect_id}"), "sect detail", allowed=(200, 404))
    _assert_status(api_get(f"/api/sect/member/{uid_a}", actor=uid_a), "sect member", allowed=(200, 404))
    _assert_status(api_get(f"/api/sect/buffs/{uid_a}", actor=uid_a), "sect buffs", allowed=(200,))
    _assert_status(api_post("/api/sect/donate", {"user_id": uid_a, "copper": 100, "gold": 0}, actor=uid_a), "sect donate", allowed=(200, 400, 403))
    sect_quests_data = _assert_status(api_get(f"/api/sect/quests/{sect_id}", actor=uid_a, user_id=uid_a), "sect quests", allowed=(200, 404))
    sect_quests = (sect_quests_data or {}).get("quests") or []
    if sect_quests:
        _assert_status(
            api_post("/api/sect/quests/claim", {"user_id": uid_a, "quest_id": sect_quests[0].get("id")}, actor=uid_a),
            "sect quests claim",
            allowed=(200, 400, 403),
        )
    _assert_status(
        api_post("/api/sect/promote", {"user_id": uid_a, "target_user_id": uid_b, "role": "elder"}, actor=uid_a),
        "sect promote",
        allowed=(200, 400, 403),
    )
    _assert_status(
        api_post("/api/sect/transfer", {"user_id": uid_a, "target_user_id": uid_b}, actor=uid_a),
        "sect transfer",
        allowed=(200, 400, 403),
    )
    _assert_status(
        api_post("/api/sect/kick", {"user_id": uid_a, "target_user_id": uid_c}, actor=uid_a),
        "sect kick",
        allowed=(200, 400, 403, 404),
    )
    sect2_resp = api_post(
        "/api/sect/create",
        {"user_id": uid_c, "name": "测试分宗", "description": "自动化测试二号"},
        actor=uid_c,
    )
    sect2_data = _assert_status(sect2_resp, "sect create 2", allowed=(200, 400))
    target_sect = sect2_data.get("sect_id") if sect2_resp.status_code == 200 else None
    if target_sect:
        _assert_status(
            api_post("/api/sect/war/challenge", {"user_id": uid_a, "target_sect_id": target_sect}, actor=uid_a),
            "sect war",
            allowed=(200, 400, 403),
        )
    branch_req_resp = api_post(
        "/api/sect/branch/request",
        {"user_id": uid_b, "name": "测试别院", "description": "分支测试"},
        actor=uid_b,
    )
    branch_req_data = _assert_status(branch_req_resp, "sect branch request", allowed=(200, 400, 403))
    branch_req_id = branch_req_data.get("request_id") if branch_req_resp.status_code == 200 else None
    if branch_req_id:
        _assert_status(
            api_post("/api/sect/branch/review", {"user_id": uid_a, "request_id": int(branch_req_id), "approve": True}, actor=uid_a),
            "sect branch review",
            allowed=(200, 400, 403),
        )
        sect_detail = _assert_status(api_get(f"/api/sect/{sect_id}"), "sect detail after branch", allowed=(200, 404))
        branches = (sect_detail or {}).get("sect", {}).get("branches") or []
        if branches and branches[0].get("branch_id"):
            _assert_status(
                api_post(
                    "/api/sect/branch/join",
                    {"user_id": uid_b, "branch_id": branches[0]["branch_id"]},
                    actor=uid_b,
                ),
                "sect branch join",
                allowed=(200, 400, 403),
            )
    _assert_status(api_post("/api/sect/leave", {"user_id": uid_b}, actor=uid_b), "sect leave", allowed=(200, 400, 403))

    chat_req2 = _assert_status(
        api_post("/api/social/chat/request", {"user_id": uid_c, "target_user_id": uid_a}, actor=uid_c),
        "chat request 2",
        allowed=(200, 400),
    )
    req2_id = chat_req2.get("request_id") if isinstance(chat_req2, dict) else None
    if req2_id:
        _assert_status(
            api_post("/api/social/chat/reject", {"user_id": uid_a, "request_id": req2_id}, actor=uid_a),
            "chat reject",
            allowed=(200, 400, 403, 404),
        )

    print(
        "OK: API smoke suite passed "
        "(core routes + surface coverage across user/cultivation/combat/equipment/skills/shop/quests/"
        "pvp/sect/alchemy/gacha/events/social/misc)."
    )
    _checkpoint("smoke_complete")

    close_db()


if __name__ == "__main__":
    main()
