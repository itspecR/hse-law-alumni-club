from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from club_api.modules.telegram.bot import Telegram


@pytest.mark.parametrize("active", [True, False])
async def test_private_bot_profile_uses_current_account_access(active):
    user_id = str(uuid4())
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "fio": "Вымышленный участник",
        "verification_status": "verified",
        "points_cached": 123,
        "personal_discount": 15,
    }
    state = SimpleNamespace(
        store=SimpleNamespace(read=AsyncMock(return_value=[row])),
        auth=SimpleNamespace(user=AsyncMock(return_value={"id": user_id} if active else None)),
        settings=SimpleNamespace(PUBLIC_URL="https://synthetic.invalid"),
    )
    result = await Telegram(state).reply("/points", "", "12345")
    state.auth.user.assert_awaited_once_with(id=user_id, alumni=True, active=True)
    assert ("Вымышленный участник" in result) is active
    assert ("15%" in result) is active
    assert ("До уровня «Друг клуба» осталось баллов: 77" in result) is active
    assert ("«Подключить Telegram»" in result and "/lk/profile" in result) is not active


@pytest.mark.parametrize("active", [True, False])
async def test_bot_calendar_keeps_rsvp_private_when_account_is_blocked(active):
    user_id, alumni_id, event_id = (str(uuid4()) for _ in range(3))
    event = {
        "id": event_id,
        "title": "Публичное событие",
        "starts_at": "2026-12-01T15:00:00Z",
        "location": "Москва",
        "format": "offline",
        "reg_url": None,
    }
    alumni = {"id": alumni_id, "user_id": user_id}
    state = SimpleNamespace(
        store=SimpleNamespace(
            read=AsyncMock(
                side_effect=[
                    [event],
                    [alumni],
                    [{"event_id": event_id, "alumni_id": alumni_id}],
                ]
            )
        ),
        auth=SimpleNamespace(user=AsyncMock(return_value={"id": user_id} if active else None)),
        settings=SimpleNamespace(PUBLIC_URL="https://synthetic.invalid"),
    )
    result = await Telegram(state).reply("/calendar", "", "12345")
    assert "Публичное событие" in result
    assert ("вы записаны" in result) is active
