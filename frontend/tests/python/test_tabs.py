import pytest

from club_web import features
from club_web.icons import ICONS
from club_web.tabs import mobile_tabs


def labels(path):
    return [(label, current) for _, label, _, current in mobile_tabs(path)]


def test_tabs_offer_trainings_while_dpo_is_off(monkeypatch):
    monkeypatch.setattr(features, "DPO_ENABLED", False)
    assert labels("/portfolio/contracts") == [("Клуб", False), ("Тренинги", True), ("Мерч", False), ("Кабинет", False)]


def test_tabs_offer_news_and_dpo_while_dpo_is_on(monkeypatch):
    monkeypatch.setattr(features, "DPO_ENABLED", True)
    assert [label for label, _ in labels("/")] == ["Клуб", "Новости", "ДПО", "Мерч", "Кабинет"]


@pytest.mark.parametrize("enabled", [False, True])
def test_every_tab_has_a_known_icon(monkeypatch, enabled):
    monkeypatch.setattr(features, "DPO_ENABLED", enabled)
    assert all(icon in ICONS for _, _, icon, _ in mobile_tabs("/"))


@pytest.mark.parametrize(
    "path,current",
    [
        ("/", "Клуб"),
        ("/tg", "Клуб"),
        ("/lk/profile", "Кабинет"),
        ("/merch/cup", "Мерч"),
        ("/merchandise", None),
        ("/events", None),
    ],
)
def test_tab_marks_current_section(monkeypatch, path, current):
    monkeypatch.setattr(features, "DPO_ENABLED", False)
    assert [label for label, on in labels(path) if on] == ([current] if current else [])
