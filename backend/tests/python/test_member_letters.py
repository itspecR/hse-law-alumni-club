import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from club_api.modules.auth import routes
from club_api.modules.members import admin
from club_api.modules.notifications.mail import CLUB_NAME

PUBLIC_URL = "https://club.example.test"


def mailbox():
    sent = []

    async def capture(to, subject, text, **options):
        sent.append({"to": to, "subject": subject, "text": text})
        return {"id": 1, "sent": True}

    return sent, capture


@pytest.mark.parametrize(
    "status,subject,lines",
    [
        (
            "verified",
            "Статус выпускника подтвержден",
            ("Мы подтвердили Ваш статус выпускника, кабинет открыт.", PUBLIC_URL + "/lk"),
        ),
        ("rejected", "Заявка на вступление в клуб", ("Мы не смогли подтвердить данные Вашей заявки.",)),
    ],
)
async def test_verification_letter_follows_style(monkeypatch, status, subject, lines):
    sent, capture = mailbox()
    monkeypatch.setattr(admin, "alumni_email", AsyncMock(return_value="alumni@example.test"))
    state = SimpleNamespace(
        notifications=SimpleNamespace(send_email=capture), settings=SimpleNamespace(PUBLIC_URL=PUBLIC_URL)
    )
    await admin.notify_verification(state, "alumni-1", status)
    [letter] = sent
    assert letter["subject"] == subject + " - " + CLUB_NAME
    assert letter["text"].startswith("Здравствуйте!") and letter["text"].endswith("\n\n" + CLUB_NAME)
    assert all(line in letter["text"] for line in lines)
    assert re.findall("[ёЁ–—🎓]", letter["subject"] + letter["text"]) == []


async def test_confirmation_letter_follows_style():
    sent, capture = mailbox()
    auth = SimpleNamespace(send_code=AsyncMock(return_value="012345"), settings=SimpleNamespace(PUBLIC_URL=PUBLIC_URL))
    request = SimpleNamespace(services=SimpleNamespace(auth=auth, notifications=SimpleNamespace(enqueue_mail=capture)))
    await routes.queue_confirmation(request, "user-1", "new@example.test")
    [letter] = sent
    assert letter["subject"] == "Код подтверждения почты - " + CLUB_NAME
    assert f"Вы подали заявку на вступление в {CLUB_NAME}. Код подтверждения почты: 012345" in letter["text"]
    assert f"Код действует 15 минут. Введите его на странице {PUBLIC_URL}/confirm" in letter["text"]
    assert letter["text"].endswith("Если заявку подавали не Вы, проигнорируйте письмо, аккаунт останется неактивным.")
    assert re.findall("[ёЁ–—]", letter["subject"] + letter["text"]) == []


async def test_confirmation_is_not_queued_while_code_waits():
    sent, capture = mailbox()
    auth = SimpleNamespace(send_code=AsyncMock(return_value=None), settings=SimpleNamespace(PUBLIC_URL=PUBLIC_URL))
    request = SimpleNamespace(services=SimpleNamespace(auth=auth, notifications=SimpleNamespace(enqueue_mail=capture)))
    assert await routes.queue_confirmation(request, "user-1", "new@example.test") == {"sent": False, "id": None}
    assert sent == []


def test_reset_letter_names_code_and_page():
    letter = routes.reset_letter("654321", PUBLIC_URL)
    assert "Вы запросили восстановление пароля. Код: 654321" in letter
    assert f"Код действует 15 минут и срабатывает один раз. Введите его на странице {PUBLIC_URL}/reset" in letter
    assert re.findall("[ёЁ–—]", letter) == []
