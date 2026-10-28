import pytest

from club_api.domain import (
    ACHIEVEMENT_FLAGS,
    ACHIEVEMENTS,
    achievement_progress,
    decay_delta,
    effective_discount,
    level_info,
    member_discount,
    order_totals,
)


@pytest.mark.parametrize(
    ("points", "key", "discount"),
    [
        (-1, "graduate", 5),
        (0, "graduate", 5),
        (199, "graduate", 5),
        (200, "friend", 10),
        (499, "friend", 10),
        (500, "expert", 15),
        (999, "expert", 15),
        (1000, "ambassador", 20),
    ],
)
def test_level_boundaries(points, key, discount):
    assert level_info(points)["level"] == key
    assert member_discount(points) == discount
    assert effective_discount(False, points, 10) == 0
    assert member_discount(points, 999) == min(25, discount + 10)


@pytest.mark.parametrize(("points", "delta"), [(0, 0), (-1, 0), (10, -2), (30, -5), (100, -15)])
def test_decay_uses_existing_rounding(points, delta):
    assert decay_delta(points) == delta


def test_discount_applies_to_training_only_and_rounds_half_up():
    items = [{"type": "dpo", "price": 10, "qty": 1}, {"type": "merch", "price": 5000, "qty": 2}]
    assert order_totals(items, 5) == {"subtotal": 10010, "discount": 5, "discountAmount": 1, "total": 10009}
    assert level_info(-10)["next_level"] == "Друг клуба"


@pytest.mark.parametrize("discount", [5, 10, 15, 20, 25])
def test_discount_preserves_integer_precision_at_safe_money_limit(discount):
    amount = 9007199254740991
    totals = order_totals([{"type": "dpo", "price": amount, "qty": 1}], discount)
    expected = (amount * discount + 50) // 100
    assert type(totals["discountAmount"]) is int and totals["discountAmount"] == expected
    assert type(totals["total"]) is int and totals["total"] == amount - expected


def test_hidden_achievement_is_not_shown_or_seeded_as_column():
    keys = {item["key"] for item in achievement_progress({})}
    assert "club_voice" not in keys and "first_step" in keys
    assert all("hidden" not in item for item in ACHIEVEMENTS)
    assert "hidden" in ACHIEVEMENT_FLAGS
