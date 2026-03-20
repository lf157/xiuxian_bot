from core.services import breakthrough_pity as bp


def test_hard_pity_threshold_uses_config_table(monkeypatch):
    def _fake_get_nested(*keys, default=None):
        if keys == ("balance", "breakthrough", "hard_pity_thresholds"):
            return {"1-5": 7, "6-9": 11, "10": 13}
        if keys == ("balance", "breakthrough", "hard_pity_default"):
            return 99
        return default

    monkeypatch.setattr(bp.config, "get_nested", _fake_get_nested)

    assert bp.get_hard_pity_threshold(1) == 7
    assert bp.get_hard_pity_threshold(8) == 11
    assert bp.get_hard_pity_threshold(10) == 13
    assert bp.get_hard_pity_threshold(99) == 99


def test_hard_pity_threshold_falls_back_when_config_invalid(monkeypatch):
    def _fake_get_nested(*keys, default=None):
        if keys == ("balance", "breakthrough", "hard_pity_thresholds"):
            return {"bad": "oops", "2-x": 4, "7-6": 0}
        if keys == ("balance", "breakthrough", "hard_pity_default"):
            return 123
        return default

    monkeypatch.setattr(bp.config, "get_nested", _fake_get_nested)

    # Invalid config table should fall back to built-in defaults.
    assert bp.get_hard_pity_threshold(1) == 5
    assert bp.get_hard_pity_threshold(18) == 25
    assert bp.get_hard_pity_threshold(999) == 123
