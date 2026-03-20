from concurrent.futures import ThreadPoolExecutor
import time

from core.database.connection import execute, fetch_one
from core.services import pvp_service, sect_service, social_service
from core.services.settlement_extra import settle_shop_buy, settle_use_item
from core.utils.timeutil import today_local
from tests.conftest import create_user


def _seed_basic_sect(sect_id: str, leader_id: str, *, max_members: int = 10):
    execute(
        """INSERT INTO sects
           (sect_id, name, description, leader_id, level, exp, fund_copper, fund_gold, max_members,
            war_wins, war_losses, last_war_time, created_at)
           VALUES (?, ?, '', ?, 1, 0, 0, 0, ?, 0, 0, 0, ?)""",
        (sect_id, f"宗门{sect_id}", leader_id, max_members, int(time.time())),
    )
    execute(
        "INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at) VALUES (%s, %s, 'leader', 0, %s)",
        (sect_id, leader_id, int(time.time())),
    )


def test_join_sect_concurrent_respects_member_limit(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    create_user("u3", "丙")
    _seed_basic_sect("S1", "u1", max_members=2)

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda uid: sect_service.join_sect(uid, "S1"), ("u2", "u3")))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    assert success_count == 1
    row = fetch_one("SELECT COUNT(1) AS c FROM sect_members WHERE sect_id = %s", ("S1",))
    assert int(row["c"]) == 2


def test_join_branch_concurrent_respects_member_limit(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    create_user("u3", "丙")
    execute(
        """INSERT INTO sect_branches
           (branch_id, parent_sect_id, name, display_name, leader_user_id, max_members, description, created_at)
           VALUES ('B1', 'S1', '青木别院', '青木别院', 'u1', 2, '', ?)""",
        (int(time.time()),),
    )
    execute(
        "INSERT INTO sect_branch_members (branch_id, user_id, role, contribution, joined_at) VALUES ('B1', 'u1', 'leader', 0, %s)",
        (int(time.time()),),
    )

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda uid: sect_service.join_branch(uid, "B1"), ("u2", "u3")))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    assert success_count == 1
    row = fetch_one("SELECT COUNT(1) AS c FROM sect_branch_members WHERE branch_id = %s", ("B1",))
    assert int(row["c"]) == 2


def test_create_branch_request_concurrent_single_pending(test_db):
    create_user("leader", "宗主")
    create_user("u1", "甲")
    execute("UPDATE users SET copper = 10000, gold = 10 WHERE user_id = %s", ("u1",))
    _seed_basic_sect("S1", "leader", max_members=10)
    execute(
        "INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at) VALUES ('S1', 'u1', 'member', 0, %s)",
        (int(time.time()),),
    )

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: sect_service.create_branch_request("u1", "青木"), range(2)))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    pending_count = sum(1 for payload, status in results if status == 400 and payload.get("code") == "PENDING")
    assert success_count == 1
    assert pending_count == 1

    row = fetch_one(
        "SELECT COUNT(1) AS c FROM sect_branch_requests WHERE parent_sect_id = 'S1' AND applicant_user_id = 'u1' AND status = 'pending'",
    )
    assert int(row["c"]) == 1


def test_review_branch_request_concurrent_approve_single_effect(test_db):
    create_user("leader", "宗主")
    create_user("u1", "甲")
    execute("UPDATE users SET copper = 10000, gold = 10 WHERE user_id = %s", ("u1",))
    _seed_basic_sect("S1", "leader", max_members=10)
    execute(
        "INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at) VALUES ('S1', 'u1', 'member', 0, %s)",
        (int(time.time()),),
    )
    req_resp, req_status = sect_service.create_branch_request("u1", "青木")
    assert req_status == 200, req_resp
    req_row = fetch_one("SELECT id FROM sect_branch_requests WHERE applicant_user_id = 'u1' AND status = 'pending'")
    req_id = int(req_row["id"])

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: sect_service.review_branch_request("leader", req_id, True), range(2)))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    assert success_count == 1
    failures = [payload for payload, status in results if not (status == 200 and payload.get("success"))]
    assert len(failures) == 1
    assert failures[0].get("code") in {"DONE", "CONFLICT", "DUPLICATE"}

    req_row = fetch_one("SELECT status FROM sect_branch_requests WHERE id = %s", (req_id,))
    assert req_row["status"] == "approved"
    branch_row = fetch_one("SELECT COUNT(1) AS c FROM sect_branches WHERE parent_sect_id = 'S1' AND leader_user_id = 'u1'")
    assert int(branch_row["c"]) == 1
    user_row = fetch_one("SELECT copper, gold FROM users WHERE user_id = 'u1'")
    assert int(user_row["copper"]) == 7000
    assert int(user_row["gold"]) == 7


def test_donate_concurrent_single_daily_quest_row(test_db):
    create_user("leader", "宗主")
    create_user("u1", "甲")
    _seed_basic_sect("S1", "leader", max_members=10)
    execute(
        "INSERT INTO sect_members (sect_id, user_id, role, contribution, joined_at) VALUES ('S1', 'u1', 'member', 0, %s)",
        (int(time.time()),),
    )
    execute("UPDATE users SET copper = 5000, gold = 20 WHERE user_id = %s", ("u1",))

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: sect_service.donate("u1", copper=100, gold=1), range(2)))
    assert all(status == 200 for _, status in results)

    today = today_local()
    row = fetch_one(
        "SELECT COUNT(1) AS c FROM sect_quests WHERE sect_id = 'S1' AND quest_type = 'donate' AND assigned_date = %s",
        (today,),
    )
    assert int(row["c"]) == 1
    quest = fetch_one(
        "SELECT progress FROM sect_quests WHERE sect_id = 'S1' AND quest_type = 'donate' AND assigned_date = %s",
        (today,),
    )
    assert int(quest["progress"]) == 400


def test_challenge_war_sets_both_cooldowns(test_db):
    create_user("a_leader", "甲")
    create_user("b_leader", "乙")
    _seed_basic_sect("SA", "a_leader", max_members=10)
    _seed_basic_sect("SB", "b_leader", max_members=10)

    payload, status = sect_service.challenge_war("a_leader", "SB")
    assert status == 200, payload

    payload2, status2 = sect_service.challenge_war("b_leader", "SA")
    assert status2 == 400
    assert payload2.get("code") == "COOLDOWN"


def test_request_chat_concurrent_single_pending(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: social_service.request_chat(user_id="u1", target_user_id="u2"), range(2)))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    pending_count = sum(
        1 for payload, status in results if status == 400 and payload.get("code") == "CHAT_TARGET_DAILY_LIMIT"
    )
    assert success_count == 1
    assert pending_count == 1

    row = fetch_one(
        """SELECT COUNT(1) AS c
           FROM social_chat_requests
           WHERE status = 'pending'
             AND ((from_user_id = 'u1' AND to_user_id = 'u2') OR (from_user_id = 'u2' AND to_user_id = 'u1'))"""
    )
    assert int(row["c"]) == 1


def test_request_chat_same_target_once_per_day(test_db, monkeypatch):
    create_user("u1", "甲")
    create_user("u2", "乙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))

    day_start = social_service.midnight_timestamp()
    payload1, status1 = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status1 == 200, payload1

    payload2, status2 = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status2 == 400
    assert payload2.get("code") == "CHAT_TARGET_DAILY_LIMIT"

    monkeypatch.setattr(social_service, "midnight_timestamp", lambda: day_start + 86400)
    payload3, status3 = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status3 == 200, payload3


def test_accept_chat_request_adds_stamina_without_overwrite(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    create_user("u3", "丙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u3", "u3"))
    execute("UPDATE users SET stamina = 5, chat_energy_today = 0, chat_energy_reset = %s WHERE user_id = %s", (int(time.time()), "u1"))

    req1, status1 = social_service.request_chat(user_id="u1", target_user_id="u2")
    req2, status2 = social_service.request_chat(user_id="u1", target_user_id="u3")
    assert status1 == 200 and status2 == 200

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(
            ex.map(
                lambda params: social_service.accept_chat_request(user_id=params[0], request_id=params[1]),
                (("u2", int(req1["request_id"])), ("u3", int(req2["request_id"]))),
            )
        )
    assert all(status == 200 for _, status in results)

    row = fetch_one("SELECT stamina, exp FROM users WHERE user_id = %s", ("u1",))
    assert float(row["stamina"]) >= 7.0
    assert int(row["exp"]) >= 1020


def test_accept_chat_request_capped_no_rewards(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))

    req, status = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status == 200
    now = int(time.time())
    execute(
        "UPDATE users SET stamina = 5, exp = 1000, chat_energy_today = %s, chat_energy_reset = %s WHERE user_id IN (%s, %s)",
        (social_service.CHAT_DAILY_LIMIT, now, "u1", "u2"),
    )

    payload, status = social_service.accept_chat_request(user_id="u2", request_id=int(req["request_id"]))
    assert status == 200
    assert payload.get("from_chat_capped") is True
    assert payload.get("to_chat_capped") is True
    row = fetch_one("SELECT stamina, exp FROM users WHERE user_id = %s", ("u1",))
    assert float(row["stamina"]) == 5.0
    assert int(row["exp"]) == 1000


def test_accept_chat_request_expired(test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))
    req, status = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status == 200
    old_ts = int(time.time()) - social_service.CHAT_REQUEST_TTL_SECONDS - 10
    execute(
        "UPDATE social_chat_requests SET created_at = %s WHERE id = %s",
        (old_ts, int(req["request_id"])),
    )
    payload, status = social_service.accept_chat_request(user_id="u2", request_id=int(req["request_id"]))
    assert status == 400
    assert payload.get("code") == "EXPIRED"


def test_chat_exp_scales_with_rank(test_db):
    create_user("u_low", "低", rank=3)
    create_user("u_high", "高", rank=18)
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-low", "u_low"))
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-high", "u_high"))

    req, status = social_service.request_chat(user_id="u_low", target_user_id="u_high")
    assert status == 200

    payload, status = social_service.accept_chat_request(user_id="u_high", request_id=int(req["request_id"]))
    assert status == 200

    low_exp_gain = int(payload.get("from_exp_gain", 0) or 0)
    high_exp_gain = int(payload.get("to_exp_gain", 0) or 0)
    assert low_exp_gain > 0
    assert high_exp_gain > low_exp_gain

    low_row = fetch_one("SELECT exp FROM users WHERE user_id = %s", ("u_low",))
    high_row = fetch_one("SELECT exp FROM users WHERE user_id = %s", ("u_high",))
    assert int(low_row["exp"]) == 1000 + low_exp_gain
    assert int(high_row["exp"]) == 1000 + high_exp_gain


def test_request_chat_daily_limit(monkeypatch, test_db):
    create_user("u1", "甲")
    create_user("u2", "乙")
    create_user("u3", "丙")
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u2", "u2"))
    execute("UPDATE users SET telegram_id = %s WHERE user_id = %s", ("tg-u3", "u3"))

    monkeypatch.setattr(social_service, "CHAT_REQUEST_DAILY_LIMIT", 1)

    payload1, status1 = social_service.request_chat(user_id="u1", target_user_id="u2")
    assert status1 == 200
    payload2, status2 = social_service.request_chat(user_id="u1", target_user_id="u3")
    assert status2 == 400
    assert payload2.get("code") == "CHAT_REQUEST_LIMIT"


def test_pvp_request_in_progress_returns_409(test_db):
    create_user("u1", "甲", rank=5)
    create_user("u2", "乙", rank=5)
    now = int(time.time())
    execute(
        "INSERT INTO request_dedup (request_id, user_id, action, created_at, response_json) VALUES (%s, %s, %s, %s, NULL)",
        ("RID-PVP", "u1", "pvp_challenge", now),
    )
    payload, status = pvp_service.do_pvp_challenge("u1", "u2", request_id="RID-PVP")
    assert status == 409
    assert payload.get("code") == "REQUEST_IN_PROGRESS"


def test_shop_buy_request_in_progress_returns_409(test_db, monkeypatch):
    create_user("u1", "甲")
    now = int(time.time())
    execute(
        "INSERT INTO request_dedup (request_id, user_id, action, created_at, response_json) VALUES (%s, %s, %s, %s, NULL)",
        ("RID-SHOP", "u1", "shop_buy", now),
    )
    monkeypatch.setattr(
        "core.services.settlement_extra.can_buy_item",
        lambda item_id, user_copper, user_gold, user_rank=1, preferred_currency=None, quantity=1: (True, "copper", ""),
    )
    monkeypatch.setattr(
        "core.services.settlement_extra.get_shop_offer",
        lambda item_id, currency=None: {"item_id": item_id, "name": "回血丹", "price": 10, "currency": "copper"},
    )
    payload, status = settle_shop_buy(user_id="u1", item_id="hp_pill", quantity=1, request_id="RID-SHOP")
    assert status == 409
    assert payload.get("code") == "REQUEST_IN_PROGRESS"


def test_settle_use_item_concurrent_single_consume(test_db):
    create_user("u1", "甲")
    execute(
        """INSERT INTO items (user_id, item_id, item_name, item_type, quality, quantity, level,
           attack_bonus, defense_bonus, hp_bonus, mp_bonus,
           first_round_reduction_pct, crit_heal_pct, element_damage_pct, low_hp_shield_pct)
           VALUES (?, 'attack_buff_pill', '攻击丹', 'pill', 'common', 1, 1, 0, 0, 0, 0, 0, 0, 0, 0)""",
        ("u1",),
    )

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: settle_use_item(user_id="u1", item_id="attack_buff_pill"), range(2)))

    success_count = sum(1 for payload, status in results if status == 200 and payload.get("success"))
    not_found_count = sum(1 for payload, status in results if status == 400 and payload.get("code") == "NOT_FOUND")
    assert success_count == 1
    assert not_found_count == 1

    row = fetch_one("SELECT COUNT(1) AS c FROM items WHERE user_id = 'u1' AND item_id = 'attack_buff_pill'")
    assert int(row["c"]) == 0
