import importlib.util

import pytest

_HAS_PSYCOPG2 = importlib.util.find_spec("psycopg2") is not None

if _HAS_PSYCOPG2:
    from core.services import settlement_extra
else:  # pragma: no cover - dependency-gated in CI/local envs without psycopg2
    settlement_extra = None

pytestmark = pytest.mark.skipif(not _HAS_PSYCOPG2, reason="requires psycopg2")


def test_protect_strategy_uses_copper_budget(monkeypatch):
    user = {
        "user_id": "u1",
        "rank": 1,
        "exp": 999999,
        "copper": 101,
        "stamina": 10,
        "element": "金",
        "breakthrough_pity": 0,
        "breakthrough_protect_until": 0,
        "breakthrough_boost_until": 0,
        "breakthrough_boost_pct": 0,
    }

    monkeypatch.setattr(settlement_extra, "get_user_by_id", lambda _uid: user)
    monkeypatch.setattr(settlement_extra, "is_realm_trial_complete", lambda _uid, _rank: True)
    monkeypatch.setattr(
        settlement_extra,
        "_breakthrough_cfg",
        lambda: {
            "fire_bonus": 0.03,
            "steady_bonus": 0.10,
            "post_breakthrough_restore_ratio": 0.30,
            "stamina_cost": 1,
            "protect_material_base": 2,
            "protect_material_per_10_rank": 0,
            "desperate_exp_penalty_add": 0.05,
            "desperate_exp_penalty_cap": 0.30,
            "desperate_weak_seconds_add": 1800,
            "desperate_success_gold_bonus": 1,
            "desperate_success_copper_min": 50,
            "desperate_success_copper_cost_divisor": 5,
        },
    )

    monkeypatch.setattr("core.game.realms.can_breakthrough", lambda _exp, _rank: True)
    monkeypatch.setattr("core.game.realms.calculate_breakthrough_cost", lambda _rank: 100)

    resp, status = settlement_extra.settle_breakthrough(user_id="u1", use_pill=False, strategy="protect")

    assert status == 400
    assert resp.get("code") == "INSUFFICIENT_COPPER"
    assert "102" in str(resp.get("message", ""))


def test_protect_preview_reports_total_copper_cost(monkeypatch):
    user = {
        "user_id": "u1",
        "rank": 1,
        "exp": 999999,
        "copper": 999999,
        "stamina": 10,
        "element": "金",
        "breakthrough_pity": 0,
        "breakthrough_protect_until": 0,
        "breakthrough_boost_until": 0,
        "breakthrough_boost_pct": 0,
    }

    monkeypatch.setattr(settlement_extra, "get_user_by_id", lambda _uid: user)
    monkeypatch.setattr(
        settlement_extra,
        "_breakthrough_cfg",
        lambda: {
            "fire_bonus": 0.03,
            "steady_bonus": 0.10,
            "post_breakthrough_restore_ratio": 0.30,
            "stamina_cost": 1,
            "protect_material_base": 2,
            "protect_material_per_10_rank": 0,
            "desperate_exp_penalty_add": 0.05,
            "desperate_exp_penalty_cap": 0.30,
            "desperate_weak_seconds_add": 1800,
            "desperate_success_gold_bonus": 1,
            "desperate_success_copper_min": 50,
            "desperate_success_copper_cost_divisor": 5,
        },
    )
    monkeypatch.setattr("core.game.realms.get_realm_by_id", lambda _rank: {"name": "炼气"})
    monkeypatch.setattr("core.game.realms.get_next_realm", lambda _rank: {"id": 2, "name": "筑基", "break_rate": 0.5})
    monkeypatch.setattr("core.game.realms.calculate_breakthrough_cost", lambda _rank: 100)

    resp, status = settlement_extra.get_breakthrough_preview(user_id="u1", strategy="protect")

    assert status == 200
    assert resp.get("success") is True
    preview = (resp.get("preview") or {})
    assert int(preview.get("base_cost_copper", 0)) == 100
    assert int(preview.get("extra_cost_copper", 0)) == 2
    assert int(preview.get("cost_copper", 0)) == 102


def test_consummation_preview_uses_tribulation_mode(monkeypatch):
    user = {
        "user_id": "u1",
        "rank": 5,
        "exp": 999999,
        "copper": 999999,
        "stamina": 10,
        "element": "金",
        "breakthrough_pity": 0,
        "breakthrough_protect_until": 0,
        "breakthrough_boost_until": 0,
        "breakthrough_boost_pct": 0,
    }

    monkeypatch.setattr(settlement_extra, "get_user_by_id", lambda _uid: user)
    monkeypatch.setattr(
        settlement_extra,
        "_breakthrough_cfg",
        lambda: {
            "fire_bonus": 0.03,
            "steady_bonus": 0.10,

            "spirit_density_bonus_scale": 0.20,
            "post_breakthrough_restore_ratio": 0.30,
            "stamina_cost": 1,
            "protect_material_base": 2,
            "protect_material_per_10_rank": 0,
            "desperate_exp_penalty_add": 0.05,
            "desperate_exp_penalty_cap": 0.30,
            "desperate_weak_seconds_add": 1800,
            "desperate_success_gold_bonus": 1,
            "desperate_success_copper_min": 50,
            "desperate_success_copper_cost_divisor": 5,
            "tribulation_flat_penalty": 0.10,
            "tribulation_rate_multiplier": 0.70,
            "tribulation_extra_cost_multiplier": 1.20,
            "tribulation_extra_stamina": 1,
            "tribulation_fail_exp_penalty_add": 0.05,
            "tribulation_fail_weak_seconds_add": 1200,
        },
    )
    monkeypatch.setattr(
        settlement_extra,
        "_resolve_breakthrough_environment",
        lambda **_kwargs: {
            "current_map": "canglan_city",
            "location_name": "苍岚城",
            "spirit_density": 1.0,
            "location_bonus": 0.0,
            "fortune_label": "平",
            "fortune_bonus": 0.0,
        },
    )
    monkeypatch.setattr(settlement_extra, "_pick_steady_breakthrough_pill", lambda **_kwargs: None)
    monkeypatch.setattr("core.game.realms.get_realm_by_id", lambda _rank: {"id": 5, "name": "练气圆满", "sub_level": 4})
    monkeypatch.setattr("core.game.realms.get_next_realm", lambda _rank: {"id": 6, "name": "筑基初期", "break_rate": 0.70})
    monkeypatch.setattr("core.game.realms.calculate_breakthrough_cost", lambda _rank: 100)

    resp, status = settlement_extra.get_breakthrough_preview(user_id="u1", strategy="steady")

    assert status == 200
    preview = (resp.get("preview") or {})
    assert preview.get("is_tribulation") is True
    assert int(preview.get("tribulation_extra_cost_copper", 0)) == 20
    assert int(preview.get("stamina_cost", 0)) == 2
    assert int(preview.get("success_rate_pct", 0)) == 42
    assert "渡劫突破" in str(preview.get("preview_text", ""))


def test_consummation_preview_high_tier_pill_ignores_tribulation_rate_penalty(monkeypatch):
    user = {
        "user_id": "u1",
        "rank": 5,
        "exp": 999999,
        "copper": 999999,
        "stamina": 10,
        "element": "金",
        "breakthrough_pity": 0,
        "breakthrough_protect_until": 0,
        "breakthrough_boost_until": 0,
        "breakthrough_boost_pct": 0,
    }

    monkeypatch.setattr(settlement_extra, "get_user_by_id", lambda _uid: user)
    monkeypatch.setattr(
        settlement_extra,
        "_breakthrough_cfg",
        lambda: {
            "fire_bonus": 0.03,
            "steady_bonus": 0.10,

            "spirit_density_bonus_scale": 0.20,
            "post_breakthrough_restore_ratio": 0.30,
            "stamina_cost": 1,
            "protect_material_base": 2,
            "protect_material_per_10_rank": 0,
            "desperate_exp_penalty_add": 0.05,
            "desperate_exp_penalty_cap": 0.30,
            "desperate_weak_seconds_add": 1800,
            "desperate_success_gold_bonus": 1,
            "desperate_success_copper_min": 50,
            "desperate_success_copper_cost_divisor": 5,
            "tribulation_flat_penalty": 0.10,
            "tribulation_rate_multiplier": 0.70,
            "tribulation_extra_cost_multiplier": 1.20,
            "tribulation_extra_stamina": 1,
            "tribulation_fail_exp_penalty_add": 0.05,
            "tribulation_fail_weak_seconds_add": 1200,
        },
    )
    monkeypatch.setattr(
        settlement_extra,
        "_resolve_breakthrough_environment",
        lambda **_kwargs: {
            "current_map": "canglan_city",
            "location_name": "苍岚城",
            "spirit_density": 1.0,
            "location_bonus": 0.0,
            "fortune_label": "平",
            "fortune_bonus": 0.0,
        },
    )
    monkeypatch.setattr(
        settlement_extra,
        "_pick_steady_breakthrough_pill",
        lambda **_kwargs: {
            "item_id": "advanced_breakthrough_pill",
            "item_name": "高级突破丹",
            "bonus": 0.20,
        },
    )
    monkeypatch.setattr("core.game.realms.get_realm_by_id", lambda _rank: {"id": 5, "name": "练气圆满", "sub_level": 4})
    monkeypatch.setattr("core.game.realms.get_next_realm", lambda _rank: {"id": 6, "name": "筑基初期", "break_rate": 0.70})
    monkeypatch.setattr("core.game.realms.calculate_breakthrough_cost", lambda _rank: 100)

    resp, status = settlement_extra.get_breakthrough_preview(user_id="u1", strategy="steady")

    assert status == 200
    preview = (resp.get("preview") or {})
    assert preview.get("is_tribulation") is True
    assert preview.get("tribulation_rate_ignored") is True
    assert int(preview.get("success_rate_pct", 0)) in (89, 90)
    rate_parts = [str(x) for x in (preview.get("rate_parts") or [])]
    assert any("破劫" in part for part in rate_parts)
    assert not any("雷劫强度倍率" in part for part in rate_parts)
