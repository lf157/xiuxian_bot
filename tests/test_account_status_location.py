from core.utils.account_status import format_status_text


def test_format_status_text_contains_location():
    status = {
        "in_game_username": "风霜",
        "rank": 1,
        "element": "火",
        "current_map": "canglan_city",
        "current_map_name": "苍澜城",
        "copper": 0,
        "gold": 0,
        "stamina": 24,
        "max_stamina": 24,
        "hp": 100,
        "max_hp": 100,
        "mp": 50,
        "max_mp": 50,
        "attack": 10,
        "defense": 5,
        "exp": 0,
        "next_exp": 100,
        "dy_times": 0,
        "state": 0,
        "is_weak": False,
    }

    text = format_status_text(status, "CHS")

    assert "📍 所在地: 苍澜城" in text
