import pytest

from club_web.formatting import discount_label, plain_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("🔄 Готовится / изменяется", "Готовится / изменяется"),
        ("⚖️ Обзор законодательства", "Обзор законодательства"),
        ("«Цитата» дня", "«Цитата» дня"),
        ("№ 237-ФЗ", "№ 237-ФЗ"),
        ("Федеральный закон", "Федеральный закон"),
        (None, ""),
    ],
)
def test_plain_title_drops_only_leading_symbols(raw, expected):
    assert plain_title(raw) == expected


@pytest.mark.parametrize(
    ("order", "label", "automatic"),
    [
        (
            {"member_discount": 15, "discount_source": "promo", "promo_code": "SPRING-15"},
            "промокод SPRING-15: 15%",
            None,
        ),
        ({"member_discount": 10, "discount_source": "member"}, "скидка выпускника: 10%", None),
        ({"member_discount": 0, "discount_source": None}, "без скидки", None),
        (
            {"member_discount": 15, "discount_source": "promo", "promo_code": "SPRING-15", "office_discount": 0},
            "индивидуальная скидка: 0%",
            "промокод SPRING-15: 15%",
        ),
    ],
)
def test_discount_label_names_its_source(order, label, automatic):
    assert discount_label(order) == label
    assert discount_label(order, True) == (automatic or label)
