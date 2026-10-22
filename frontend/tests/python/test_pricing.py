from datetime import date

import pytest

from club_web.pages import home_programs, resource
from club_web.pricing import discount_amount, member_prices

LEVELS = resource("domain-data.json")["levels"]


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [(4000000, 5, 200000), (123457, 5, 6173), (123450, 10, 12345), (99, 25, 25), (0, 20, 0)],
)
def test_discount_rounds_half_up_like_api(price, percent, expected):
    assert discount_amount(price, percent) == expected


def test_member_prices_follow_levels_in_order():
    prices = member_prices(5000000, list(reversed(LEVELS)))
    assert [row["level"] for row in prices] == ["graduate", "friend", "expert", "ambassador"]
    assert [row["discount"] for row in prices] == [5, 10, 15, 20]
    assert [row["price"] for row in prices] == [4750000, 4500000, 4250000, 4000000]


def test_home_programs_prefer_upcoming_starts_with_prices():
    base = {"direction": "Право", "format": "online", "document": "Диплом"}
    rows = [
        {
            **base,
            "slug": "past",
            "title": "Прошедший",
            "price": 1000000,
            "enrollment": "actual",
            "dates": {"start": "1 октября 2026"},
        },
        {
            **base,
            "slug": "soon",
            "title": "Скоро",
            "price": 4000000,
            "enrollment": "actual",
            "dates": {"start": "24 октября 2026"},
        },
        {
            **base,
            "slug": "free",
            "title": "Без цены",
            "price": None,
            "enrollment": "actual",
            "dates": {"start": "25 октября 2026"},
        },
        {**base, "slug": "closed", "title": "Закрыт", "price": 2000000, "enrollment": "nonactual", "dates": {}},
    ]
    data = home_programs(rows, LEVELS, date(2026, 10, 22))
    assert data["home_starts"] is True
    assert [item["slug"] for item in data["home_programs"]] == ["soon"]
    assert data["home_programs"][0]["member_prices"][0]["price"] == 3800000
    assert (data["programs_total"], data["programs_open"]) == (4, 3)
    assert [level["key"] for level in data["levels"]] == ["graduate", "friend", "expert", "ambassador"]


def test_home_programs_fall_back_to_open_programs_without_dates():
    rows = [
        {"slug": str(index), "title": f"Курс {index}", "price": 100000 * (index + 1), "enrollment": "actual"}
        for index in range(8)
    ] + [{"slug": "closed", "title": "А закрытый", "price": 100000, "enrollment": "nonactual"}]
    data = home_programs(rows, LEVELS, date(2026, 10, 22))
    assert data["home_starts"] is False
    assert len(data["home_programs"]) == 6
    assert "closed" not in [item["slug"] for item in data["home_programs"]]
