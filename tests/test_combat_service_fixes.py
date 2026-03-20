import time

import pytest

from core.database.connection import execute, fetch_one
from core.game import combat as combat_module
from core.game.secret_realms import scale_secret_realm_monster
from core.services import turn_battle_service as tbs
from core.services.settlement import settle_hunt, settle_secret_realm_explore
from tests.conftest import create_user


@pytest.fixture()
def clean_battle_sessions():
    tbs._BATTLE_SESSIONS.clear()
    tbs._SESSION_LOCKS.clear()
    yield
    tbs._BATTLE_SESSIONS.clear()
    tbs._SESSION_LOCKS.clear()


def test_settle_hunt_request_in_progress_returns_409(test_db):
    create_user("u1", "A")
    now = int(time.time())
    execute(
        "INSERT INTO request_dedup (request_id, user_id, action, created_at, response_json) VALUES (%s, %s, %s, %s, NULL)",
        ("RID-1", "u1", "hunt", now),
    )

    payload, status = settle_hunt(
        user_id="u1",
        monster_id="wild_boar",
        request_id="RID-1",
        hunt_cooldown_seconds=0,
        now=now,
    )
    assert status == 409
    assert payload.get("code") == "REQUEST_IN_PROGRESS"

    row = fetch_one("SELECT stamina, hunts_today FROM users WHERE user_id = %s", ("u1",))
    assert int(row["stamina"]) == 24
    assert int(row["hunts_today"]) == 0


def test_settle_secret_realm_request_in_progress_returns_409(test_db):
    create_user("u1", "A")
    now = int(time.time())
    execute(
        "INSERT INTO request_dedup (request_id, user_id, action, created_at, response_json) VALUES (%s, %s, %s, %s, NULL)",
        ("RID-2", "u1", "secret_realm", now),
    )

    payload, status = settle_secret_realm_explore(
        user_id="u1",
        realm_id="mist_forest",
        path="normal",
        request_id="RID-2",
        secret_cooldown_seconds=0,
        now=now,
    )
    assert status == 409
    assert payload.get("code") == "REQUEST_IN_PROGRESS"

    row = fetch_one("SELECT stamina, secret_realm_attempts FROM users WHERE user_id = %s", ("u1",))
    assert int(row["stamina"]) == 24
    assert int(row["secret_realm_attempts"]) == 0


def test_secret_realm_invalid_encounter_no_stamina_spent(monkeypatch, test_db):
    create_user("u1", "A")
    now = int(time.time())

    monkeypatch.setattr(
        "core.services.settlement.roll_secret_realm_encounter",
        lambda realm, path="normal": {
            "type": "monster",
            "label": "bad",
            "monster_id": "no_such_monster",
            "danger_scale": 1.0,
        },
    )

    payload, status = settle_secret_realm_explore(
        user_id="u1",
        realm_id="mist_forest",
        path="normal",
        request_id=None,
        secret_cooldown_seconds=0,
        now=now,
    )
    assert status == 404
    assert payload.get("code") == "NOT_FOUND"

    row = fetch_one(
        "SELECT stamina, secret_realm_attempts, last_secret_time FROM users WHERE user_id = %s",
        ("u1",),
    )
    assert int(row["stamina"]) == 24
    assert int(row["secret_realm_attempts"]) == 0
    assert int(row["last_secret_time"]) == 0


def test_hunt_monster_scales_hp_ratio(monkeypatch, test_db):
    observed = {}

    def fake_fight(self, max_rounds=50):
        observed["hp"] = int(self.attacker["hp"])
        observed["max_hp"] = int(self.attacker["max_hp"])
        return {
            "winner": "attacker",
            "rounds": 1,
            "log": [],
            "attacker_remaining_hp": int(self.attacker["hp"]),
            "attacker_remaining_mp": int(self.attacker.get("mp", 0) or 0),
            "defender_remaining_hp": 0,
        }

    monkeypatch.setattr(combat_module.Combat, "fight", fake_fight)

    user_data = {
        "user_id": "u1",
        "in_game_username": "A",
        "rank": 1,
        "hp": 50,
        "max_hp": 100,
    }
    result = combat_module.hunt_monster(user_data, "wild_boar", learned_skills=[], use_active=False)
    assert result.get("success") is True

    expected = int(round(observed["max_hp"] * (user_data["hp"] / user_data["max_hp"])))
    expected = max(1, min(observed["max_hp"], expected))
    assert observed["hp"] == expected


def test_start_hunt_session_blocks_parallel_session(clean_battle_sessions, test_db):
    create_user("u1", "A")
    now = int(time.time())

    payload1, status1 = tbs.start_hunt_session("u1", "wild_boar", now=now)
    assert status1 == 200

    payload2, status2 = tbs.start_hunt_session("u1", "wild_boar", now=now + 1)
    assert status2 == 200
    assert payload2.get("resumed") is True
    assert payload2.get("session_id") == payload1.get("session_id")


def test_action_hunt_session_locked_returns_409(clean_battle_sessions, test_db):
    create_user("u1", "A")
    now = int(time.time())

    payload, status = tbs.start_hunt_session("u1", "wild_boar", now=now)
    assert status == 200
    session_id = payload["session_id"]

    lock = tbs._get_session_lock(session_id)
    lock.acquire()
    try:
        payload2, status2 = tbs.action_hunt_session("u1", session_id, action="attack")
        assert status2 == 409
        assert payload2.get("success") is False
    finally:
        lock.release()


def test_action_hunt_session_loads_from_db(monkeypatch, clean_battle_sessions, test_db):
    create_user("u1", "A")
    now = int(time.time())

    payload, status = tbs.start_hunt_session("u1", "wild_boar", now=now)
    assert status == 200
    session_id = payload["session_id"]

    row = fetch_one("SELECT session_id FROM battle_sessions WHERE session_id = %s", (session_id,))
    assert row is not None

    tbs._BATTLE_SESSIONS.clear()

    def fake_run_round(session, action, skill_id=None):
        session["round"] = int(session.get("round", 0) or 0) + 1
        return {"finished": False, "round": session["round"], "round_log": ["ok"]}

    monkeypatch.setattr(tbs, "_run_round", fake_run_round)

    payload2, status2 = tbs.action_hunt_session("u1", session_id, action="attack")
    assert status2 == 200
    assert payload2.get("session_id") == session_id
    assert session_id in tbs._BATTLE_SESSIONS


def test_action_hunt_session_finalize_error_clears_session(monkeypatch, clean_battle_sessions, test_db):
    create_user("u1", "A")
    now = int(time.time())

    payload, status = tbs.start_hunt_session("u1", "wild_boar", now=now)
    assert status == 200
    session_id = payload["session_id"]

    def fake_run_round(session, action, skill_id=None):
        return {"finished": True, "victory": True, "round": 1, "round_log": ["ok"]}

    def fake_finalize_hunt(session, victory):
        raise RuntimeError("boom")

    monkeypatch.setattr(tbs, "_run_round", fake_run_round)
    monkeypatch.setattr(tbs, "_finalize_hunt", fake_finalize_hunt)

    payload2, status2 = tbs.action_hunt_session("u1", session_id, action="attack")
    assert status2 == 500
    assert payload2.get("code") == "HUNT_FINALIZE_ERROR"

    payload3, status3 = tbs.start_hunt_session("u1", "wild_boar", now=now + 9999)
    assert status3 == 200
    assert payload3.get("session_id") != session_id


def test_action_hunt_session_defeat_sets_weak(monkeypatch, clean_battle_sessions, test_db):
    create_user("u1", "A")
    now = int(time.time())

    payload, status = tbs.start_hunt_session("u1", "wild_boar", now=now)
    assert status == 200
    session_id = payload["session_id"]

    def fake_run_round(session, action, skill_id=None):
        session["history"] = ["败北"]
        return {"finished": True, "victory": False, "round": 1, "round_log": ["败北"]}

    monkeypatch.setattr(tbs, "_run_round", fake_run_round)

    payload2, status2 = tbs.action_hunt_session("u1", session_id, action="attack")
    assert status2 == 200
    assert payload2.get("victory") is False
    assert int(payload2.get("weak_seconds", 0) or 0) > 0

    row = fetch_one("SELECT hp, weak_until FROM users WHERE user_id = %s", ("u1",))
    assert int(row["hp"] or 0) == 1
    assert int(row["weak_until"] or 0) > now


def test_turn_secret_realm_invalid_encounter_no_stamina_spent(monkeypatch, clean_battle_sessions, test_db):
    create_user("u1", "A", rank=1)
    now = int(time.time())

    monkeypatch.setattr(
        "core.services.turn_battle_service.roll_secret_realm_encounter",
        lambda realm, path="normal": {
            "type": "monster",
            "label": "bad",
            "monster_id": "no_such_monster",
            "danger_scale": 1.0,
        },
    )

    payload, status = tbs.start_secret_realm_session(
        "u1",
        "mist_forest",
        "normal",
        secret_cooldown_seconds=0,
        now=now,
    )
    assert status == 404
    assert payload.get("message")

    row = fetch_one(
        "SELECT stamina, secret_realm_attempts, last_secret_time FROM users WHERE user_id = %s",
        ("u1",),
    )
    assert int(row["stamina"]) == 24
    assert int(row["secret_realm_attempts"]) == 0
    assert int(row["last_secret_time"]) == 0


def test_auto_skill_repeats_each_round(monkeypatch):
    attacker = {
        "name": "攻",
        "hp": 100,
        "max_hp": 100,
        "mp": 2,
        "max_mp": 2,
        "attack": 12,
        "defense": 2,
        "crit_rate": 0.0,
        "crit_dmg": 1.5,
        "damage_mul": 1.0,
        "damage_taken_mul": 1.0,
        "lifesteal": 0.0,
        "skill_damage": 0.0,
        "active_skill": {"name": "连斩", "mp_cost": 1, "effect": {"attack_multiplier": 1.5}},
    }
    defender = {
        "name": "防",
        "hp": 500,
        "max_hp": 500,
        "attack": 5,
        "defense": 5,
    }
    combat = combat_module.Combat(attacker, defender)
    result = combat.fight(max_rounds=3)
    skill_logs = [line for line in result.get("log", []) if "施展技能" in line]
    assert len(skill_logs) >= 2


def test_secret_realm_scaled_monster_used_in_auto_settlement(monkeypatch, test_db):
    create_user("u1", "甲", rank=5)
    now = int(time.time())
    encounter = {
        "type": "monster",
        "label": "测试遭遇",
        "monster_id": "wild_boar",
        "danger_scale": 1.8,
    }
    monkeypatch.setattr(
        "core.services.settlement.roll_secret_realm_encounter",
        lambda realm, path="normal": dict(encounter),
    )
    observed = {}

    def fake_fight(self, max_rounds=50):
        observed["defender"] = dict(self.defender)
        return {
            "winner": "attacker",
            "rounds": 1,
            "log": [],
            "attacker_remaining_hp": int(self.attacker["hp"]),
            "attacker_remaining_mp": int(self.attacker.get("mp", 0) or 0),
            "defender_remaining_hp": 0,
        }

    monkeypatch.setattr(combat_module.Combat, "fight", fake_fight)

    payload, status = settle_secret_realm_explore(
        user_id="u1",
        realm_id="mist_forest",
        path="normal",
        request_id=None,
        secret_cooldown_seconds=0,
        now=now,
    )
    assert status == 200
    scaled = scale_secret_realm_monster(combat_module.get_monster_by_id("wild_boar"), encounter)
    assert observed.get("defender", {}).get("hp") == scaled.get("hp")
    assert payload.get("combat_modifiers", {}).get("danger_scale") == 1.8
