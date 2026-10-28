import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import club_api
from club_api.jobs.tasks import event_reminders
from club_api.modules.auth import routes
from club_api.modules.events.routes import UNNAMED, roster
from club_api.modules.notifications.mail import Notifications

STYLE = "[ёЁ–—]"
EMOJI = "[\U0001f300-\U0001faff]"


def notifications(sent):
    async def capture(to, subject, text, **options):
        sent.append((subject, text))
        return {"id": 1, "sent": True, "blocked": False}

    settings = SimpleNamespace(OFFICE_NOTIFY_CHANNEL="email", OFFICE_EMAIL="office@example.test")
    service = Notifications(settings, None, None)
    service.enqueue_mail = capture
    return service


def message_texts(node):
    if isinstance(node, ast.IfExp):
        return message_texts(node.body) + message_texts(node.orelse)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)]
    return []


def api_error_messages():
    for path in Path(club_api.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ApiError" and len(node.args) > 1:
                for text in message_texts(node.args[1]):
                    yield path.name, text


def test_api_errors_are_sentences_without_dashes():
    messages = list(api_error_messages())
    assert len(messages) > 100
    for name, text in messages:
        assert re.search(r"[.?!»]$", text) and not re.search(STYLE, text), (name, text)


async def test_office_notices_name_office_sections_without_emoji():
    sent = []
    service = notifications(sent)
    await service.office_text("Проверка")
    await service.order_notice("A-1", "Программа × 1", 1000000, 10)
    (office_subject, _), (order_subject, order_text) = sent
    assert office_subject == "Уведомление для учебного офиса" and order_subject == "Новая заявка A-1"
    assert order_text.endswith("Контакты и подробности в учебном офисе, раздел «Заявки».")
    assert re.findall(EMOJI, order_text) == [] and re.findall(STYLE, order_text) == []


def test_join_notices_point_to_members_section_without_emoji():
    assert routes.OFFICE_REVIEW == "Проверьте ее в учебном офисе, раздел «Выпускники»."
    assert re.findall(EMOJI, Path(routes.__file__).read_text()) == []


async def test_event_reminder_push_has_no_emoji_or_dash():
    event = {
        "id": "event-1",
        "title": "Встреча клуба",
        "starts_at": "2026-11-01T16:00:00Z",
        "format": "online",
        "location": None,
        "reminder_sent": False,
    }
    pushed = []

    async def read(table, **options):
        return [event] if table == "events" else [{"alumni_id": "alumni-1"}]

    async def to_many(ids, payload):
        pushed.append(payload)

    state = SimpleNamespace(store=SimpleNamespace(read=read, update=AsyncMock()), push=SimpleNamespace(to_many=to_many))
    await event_reminders(state)
    assert pushed == [
        {"title": "Завтра событие клуба", "body": "Встреча клуба, начало в 19:00, онлайн", "url": "/events"}
    ]


async def test_participants_without_name_are_labeled():
    async def read(table, **options):
        return (
            [{"id": "rsvp-1", "event_id": "event-1", "alumni_id": "gone", "attended": False}]
            if table == "event_rsvps"
            else []
        )

    rows = await roster(SimpleNamespace(read=read), "event-1")
    assert rows[0]["fio"] == UNNAMED == "ФИО не указано"
