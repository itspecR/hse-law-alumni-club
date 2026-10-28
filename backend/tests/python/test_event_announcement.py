import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from club_api.modules.events.notifications import announce
from club_api.modules.notifications.mail import CLUB_NAME

EVENT = {
    "title": "Встреча клуба",
    "starts_at": "2026-11-01T12:00:00Z",
    "format": "online",
    "location": None,
    "reg_url": None,
}


async def test_announcement_letter_follows_style():
    sent = []

    async def capture(to, subject, text):
        sent.append((to, subject, text))

    state = SimpleNamespace(
        push=SimpleNamespace(to_all=AsyncMock()),
        database=SimpleNamespace(rows=AsyncMock(return_value=[{"fio": "Анна", "email": "anna@example.test"}])),
        notifications=SimpleNamespace(send_email=capture),
        settings=SimpleNamespace(PUBLIC_URL="https://club.example.test"),
    )
    await announce(state, EVENT)
    [(to, subject, text)] = sent
    assert to == "anna@example.test" and subject == "Новое событие «Встреча клуба» - " + CLUB_NAME
    assert text.startswith("Здравствуйте, Анна!\n\nВ клубе новое событие.\n\nВстреча клуба\n01.11.2026 15:00 · онлайн")
    assert "Записаться и добавить событие в календарь можно на сайте: https://club.example.test/events" in text
    assert text.endswith("За участие начисляются баллы.\n\n" + CLUB_NAME)
    assert re.findall("[ёЁ–—]", subject + text) == []
