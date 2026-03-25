import time
import importlib.util

import pytest

from core.game import realms as realms_module


HAS_PG_DRIVER = importlib.util.find_spec("psycopg2") is not None


def _create_user(user_id: str, *, rank: int = 1, exp: int = 10000, copper: int = 20000) -> None:
    from core.database.connection import execute

    execute(
        "INSERT INTO users (user_id, in_game_username, rank, element, created_at, exp, copper, gold) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, f"U_{user_id}", rank, "火", int(time.time()), exp, copper, 0),
    )
    execute("UPDATE users SET stamina = %s WHERE user_id = %s", (10, user_id))


def test_attempt_breakthrough_failure_message_has_no_hardcoded_penalty(monkeypatch):
    user = {"rank": 1, "exp": 10000, "element": "火", "breakthrough_pity": 0}
    monkeypatch.setattr(realms_module.random, "random", lambda: 1.0)

    ok, msg = realms_module.attempt_breakthrough(user)

    assert not ok
    assert "10%" not in msg
    assert "1小时" not in msg


def test_attempt_breakthrough_bonus_clamp_is_applied_at_end(monkeypatch):
    user = {"rank": 1, "exp": 10000, "element": "火"}
    # 对应服务端可能出现的组合：展示概率=100%，传入额外加成为负值用于抵消重复项。
    monkeypatch.setattr(realms_module.random, "random", lambda: 0.99)

    ok, _ = realms_module.attempt_breakthrough(user, use_pill=True, extra_bonus=-0.08)

    assert ok is True


def test_attempt_breakthrough_can_use_forced_final_rate(monkeypatch):
    user = {"rank": 1, "exp": 10000, "element": "火"}

    monkeypatch.setattr(realms_module.random, "random", lambda: 0.9999)
    ok, _ = realms_module.attempt_breakthrough(user, forced_success_rate=1.0)
    assert ok is True

    monkeypatch.setattr(realms_module.random, "random", lambda: 0.0)
    ok, _ = realms_module.attempt_breakthrough(user, forced_success_rate=0.0)
    assert ok is False


def test_pick_steady_breakthrough_pill_prefers_higher_tier(monkeypatch):
    from core.services import settlement_extra

    inventory = {
        "super_breakthrough_pill": 1,
        "advanced_breakthrough_pill": 5,
        "breakthrough_pill": 99,
    }

    def _fake_fetch_one(_sql, params):
        item_id = str(params[1] or "")
        qty = int(inventory.get(item_id, 0) or 0)
        if qty <= 0:
            return None
        return {"id": 1, "quantity": qty}

    monkeypatch.setattr(settlement_extra, "fetch_one", _fake_fetch_one)

    selected = settlement_extra._pick_steady_breakthrough_pill(
        user_id="u_test",
        bt_cfg={"steady_bonus": 0.10},
    )
    assert selected is not None
    assert selected.get("item_id") == "super_breakthrough_pill"
    assert float(selected.get("bonus", 0.0) or 0.0) == pytest.approx(0.50)


@pytest.mark.skipif(not HAS_PG_DRIVER, reason="psycopg2 not installed")
def test_settle_breakthrough_failure_message_uses_actual_penalty(monkeypatch, test_db):
    from core.services import settlement_extra

    _create_user("bt_fail_msg")

    monkeypatch.setattr(settlement_extra, "is_realm_trial_complete", lambda _uid, _rank: True)
    monkeypatch.setattr(realms_module.random, "random", lambda: 1.0)

    resp, status = settlement_extra.settle_breakthrough(
        user_id="bt_fail_msg",
        use_pill=False,
        strategy="desperate",
    )

    assert status == 400
    assert not resp.get("success")
    assert resp.get("code") == "BREAKTHROUGH_FAILED"

    # config default: 10% + desperate 5% => 15%
    assert "损失15%修为" in str(resp.get("message", ""))
    # config default: 3600 + desperate 1800 => 5400s => 90分钟
    assert "虚弱状态90分钟" in str(resp.get("message", ""))
    assert "1小时" not in str(resp.get("message", ""))
    assert int(resp.get("weak_seconds", 0) or 0) == 5400


@pytest.mark.skipif(not HAS_PG_DRIVER, reason="psycopg2 not installed")
def test_settle_breakthrough_uses_preview_rate_for_final_roll(monkeypatch, test_db):
    from core.services import settlement_extra

    _create_user("bt_force_roll")

    monkeypatch.setattr(settlement_extra, "is_realm_trial_complete", lambda _uid, _rank: True)
    captured = {}

    def _fake_attempt(user_data, use_pill=False, extra_bonus=0.0, forced_success_rate=None):
        captured["forced_success_rate"] = forced_success_rate
        captured["extra_bonus"] = extra_bonus
        return False, "突破失败。"

    monkeypatch.setattr(realms_module, "attempt_breakthrough", _fake_attempt)

    resp, status = settlement_extra.settle_breakthrough(
        user_id="bt_force_roll",
        use_pill=False,
        strategy="steady",
    )

    assert status == 400
    assert resp.get("code") == "BREAKTHROUGH_FAILED"
    assert captured.get("forced_success_rate") == pytest.approx(float(resp.get("success_rate", 0.0) or 0.0))


@pytest.mark.skipif(not HAS_PG_DRIVER, reason="psycopg2 not installed")
def test_settle_breakthrough_tribulation_failure_has_extra_penalty(monkeypatch, test_db):
    from core.services import settlement_extra

    _create_user("bt_fail_tribulation", rank=5)

    monkeypatch.setattr(settlement_extra, "is_realm_trial_complete", lambda _uid, _rank: True)
    monkeypatch.setattr(realms_module.random, "random", lambda: 1.0)

    resp, status = settlement_extra.settle_breakthrough(
        user_id="bt_fail_tribulation",
        use_pill=False,
        strategy="steady",
    )

    assert status == 400
    assert not resp.get("success")
    assert resp.get("code") == "BREAKTHROUGH_FAILED"
    assert resp.get("is_tribulation") is True
    assert "渡劫失败" in str(resp.get("message", ""))
    # default tribulation multiplier applies: 100 -> 120
    assert int(resp.get("cost", 0) or 0) == 120
    # default stamina 1 + tribulation extra 1
    assert int(resp.get("stamina_cost", 0) or 0) == 2
    # default fail 10% + tribulation fail add 5% = 15%
    assert "损失15%修为" in str(resp.get("message", ""))
    # default weak 3600 + tribulation weak add 1200 = 4800s => 80分钟
    assert int(resp.get("weak_seconds", 0) or 0) == 4800
