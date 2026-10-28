import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from test_community_integration import account
from test_portfolio_applications import FORM, apply, mails, program, solved_captcha

from club_api.core.errors import ApiError
from club_api.modules.portfolio.captcha import (
    CAPTCHA_MAX_NUMBER,
    RETRY_MESSAGE,
    CaptchaBody,
    expiry,
    forget_expired,
    require_solution,
    sha256,
    signature,
)

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
    assert response.status_code == 400 and "Не удалось отправить заявку" in response.json()["error"]
    assert await app.state.store.read("orders", fields=("id",)) == [] and mails == []


async def test_solution_is_accepted_once(database_app, mails):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        captcha = await solved_captcha(client)
        first = await apply(client, item["slug"], {**FORM, "captcha": captcha})
        second = await apply(client, item["slug"], {**FORM, "captcha": captcha})
    assert first.status_code == 200
    assert second.status_code == 400 and second.json()["error"] == RETRY_MESSAGE


async def test_simultaneous_reuse_of_one_solution_creates_one_application(database_app, mails):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        captcha = await solved_captcha(client)
        results = await asyncio.gather(
            *(
                apply(client, item["slug"], {**FORM, "captcha": captcha}, {"idempotency-key": str(uuid4())})
                for _ in range(2)
            )
        )
    assert sorted(response.status_code for response in results) == [200, 400]
    assert len(await app.state.store.read("orders", fields=("id",))) == 1


async def test_challenge_is_never_cached(database_app):
    async with client_for(database_app) as client:
        response = await client.get("/portfolio/challenge")
    assert response.headers["cache-control"] == "no-store"


async def test_program_cannot_take_the_challenge_address(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    body = {"title": "Challenge", "format": "online", "duration": "4 недели", "price": 100000}
    async with client_for(app) as client:
        created = await client.post("/admin/portfolio", headers=editor, json=body)
    assert created.status_code == 200 and created.json()["slug"].startswith("challenge-")


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
    settings = database_app.state.settings
    salt = "a" * 32 + ".1800000000"
    captcha = CaptchaBody(salt=salt, number=1, signature=signature(settings, sha256(salt + "1")))
    require_solution(settings, captcha, expiry(captcha) - 1)
    with pytest.raises(ApiError, match=RETRY_MESSAGE):
        require_solution(settings, captcha, expiry(captcha) + 1)


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
