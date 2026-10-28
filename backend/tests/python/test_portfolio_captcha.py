import re
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from test_portfolio_applications import FORM, apply, mails, program, solved_captcha

from club_api.core.errors import ApiError
from club_api.modules.portfolio.captcha import CAPTCHA_MAX_NUMBER, CaptchaBody, expiry, forget_expired, require_solution

__all__ = ("mails",)


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def test_challenge_is_signed_and_short_lived(database_app):
    async with client_for(database_app) as client:
        task = (await client.get("/portfolio/challenge")).json()
    assert (task["algorithm"], task["maxnumber"]) == ("SHA-256", CAPTCHA_MAX_NUMBER)
    assert re.fullmatch(r"[0-9a-f]{32}\.[0-9]{10}", task["salt"]) and re.fullmatch(r"[0-9a-f]{64}", task["signature"])
    assert 0 < int(task["salt"].split(".")[1]) - time.time() <= 600


@pytest.mark.parametrize(
    "spoil",
    [
        lambda captcha: None,
        lambda captcha: {**captcha, "number": (captcha["number"] + 1) % (CAPTCHA_MAX_NUMBER + 1)},
        lambda captcha: {**captcha, "signature": "0" * 64},
        lambda captcha: {**captcha, "salt": captcha["salt"][:-1] + str((int(captcha["salt"][-1]) + 1) % 10)},
    ],
)
async def test_application_without_valid_solution_is_rejected(database_app, mails, spoil):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        captcha = spoil(await solved_captcha(client))
        response = await apply(client, item["slug"], {**FORM, "captcha": captcha})
    assert response.status_code == 400 and "Проверка не пройдена" in response.json()["error"]
    assert await app.state.store.read("orders", fields=("id",)) == [] and mails == []


async def test_solution_is_accepted_once(database_app, mails):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        captcha = await solved_captcha(client)
        first = await apply(client, item["slug"], {**FORM, "captcha": captcha})
        second = await apply(client, item["slug"], {**FORM, "captcha": captcha})
    assert first.status_code == 200
    assert second.status_code == 400 and "уже использована" in second.json()["error"]


async def test_retry_with_new_solution_returns_same_application(database_app, mails):
    app = database_app
    item = await program(app)
    key = {"idempotency-key": str(uuid4())}
    async with client_for(app) as client:
        first = (await apply(client, item["slug"], FORM, key)).json()
        again = (await apply(client, item["slug"], FORM, key)).json()
    assert first["number"] == again["number"]
    assert len(await app.state.store.read("orders", fields=("id",))) == 1


def test_expired_solution_is_rejected(database_app):
    captcha = CaptchaBody(salt="a" * 32 + ".1800000000", number=1, signature="0" * 64)
    with pytest.raises(ApiError, match="устарела"):
        require_solution(database_app.state.settings, captcha, expiry(captcha) + 1)


async def test_expired_uses_are_forgotten(database_app):
    state = database_app.state
    now = datetime.now(UTC)
    for salt_hash, expires in (("a" * 64, now - timedelta(minutes=1)), ("b" * 64, now + timedelta(minutes=9))):
        await state.database.execute(
            "INSERT INTO club_captcha_uses(salt_hash,expires_at) VALUES(%s,%s)", (salt_hash, expires)
        )
    await forget_expired(state)
    rows = await state.database.rows("SELECT salt_hash FROM club_captcha_uses")
    assert [row["salt_hash"] for row in rows] == ["b" * 64]
