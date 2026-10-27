import pytest

from club_web.formatting import plain_title


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
