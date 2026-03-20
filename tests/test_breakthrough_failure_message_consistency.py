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

    assert status == 200
    assert not resp.get("success")
    assert resp.get("code") == "BREAKTHROUGH_FAILED"

    # config default: 10% + desperate 5% => 15%
    assert "损失15%修为" in str(resp.get("message", ""))
    # config default: 3600 + desperate 1800 => 5400s => 90分钟
    assert "虚弱状态90分钟" in str(resp.get("message", ""))
    assert "1小时" not in str(resp.get("message", ""))
    assert int(resp.get("weak_seconds", 0) or 0) == 5400
