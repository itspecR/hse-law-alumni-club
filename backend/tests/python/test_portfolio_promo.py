import asyncio
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from test_community_integration import account
from test_portfolio_applications import FORM, GROUP, PRICE, apply, mails, order, program

from club_api.modules.portfolio.applications import promo_message

DAY = timedelta(days=1)
PROMO = {"code": "spring-15", "percent": 15}
DATES = ("starts_on", "ends_on")
__all__ = ("mails",)


def today():
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def shifted(days):
    return str(today() + days * DAY)


def promo_body(extra):
    dates = {key: shifted(extra[key]) for key in DATES if isinstance(extra.get(key), int)}
    return {**PROMO, "starts_on": shifted(-1), "ends_on": shifted(1), **extra, **dates}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def create_promo(client, headers, **extra):
    response = await client.post("/admin/promo-codes", headers=headers, json=promo_body(extra))
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def promo_row(client, headers, id):
    rows = (await client.get("/admin/promo-codes", headers=headers)).json()
    return next(row for row in rows if row["id"] == id)


def with_code(body, code="SPRING-15", email=None):
    return {**body, "promo_code": code, **({"contact_email": email} if email else {})}


async def test_office_creates_normalized_promo_code(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        id = await create_promo(client, editor, note="Весенняя акция")
        row = await promo_row(client, editor, id)
        duplicate = await client.post("/admin/promo-codes", headers=editor, json=promo_body({"code": "SPRING-15"}))
    assert (row["code"], row["percent"], row["used"], row["program_ids"]) == ("SPRING-15", 15, 0, [])
    assert (row["per_person_limit"], row["total_limit"], row["all_programs"], row["status"]) == (
        1,
        None,
        True,
        "active",
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    "extra,status",
    [
        ({"percent": 51}, 400),
        ({"percent": 0}, 400),
        ({"code": "ab"}, 400),
        ({"code": "пром-10"}, 400),
        ({"ends_on": -2}, 400),
        ({"starts_on": "2026-02-30"}, 400),
        ({"all_programs": False}, 400),
    ],
)
async def test_office_rejects_invalid_promo(database_app, extra, status):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        response = await client.post("/admin/promo-codes", headers=editor, json=promo_body(extra))
    assert response.status_code == status
    assert await app.state.store.read("promo_codes", fields=("id",)) == []


async def test_promo_programs_are_portfolio_only_and_dates_checked_on_patch(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    club, dpo = await program(app), await program(app, catalog="dpo")
    async with client_for(app) as client:
        rejected = await client.post(
            "/admin/promo-codes", headers=editor, json=promo_body({"all_programs": False, "program_ids": [dpo["id"]]})
        )
        id = await create_promo(client, editor, all_programs=False, program_ids=[club["id"]])
        assert (await promo_row(client, editor, id))["program_ids"] == [club["id"]]
        late = await client.patch("/admin/promo-codes/" + id, headers=editor, json={"ends_on": shifted(-2)})
    assert rejected.status_code == 400 and late.status_code == 400


async def test_promo_beats_smaller_member_discount_and_is_recorded(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        await create_promo(client, admin, per_person_limit=None)
        receipt = (await apply(client, item["slug"], with_code(FORM, " spring-15 "), member)).json()
        listed = (await client.get("/admin/orders", headers=admin)).json()["items"][0]
        export = (await client.get("/admin/orders/export.csv", headers=admin)).text
    assert (receipt["member_discount"], receipt["discount_source"], receipt["promo_code"]) == (15, "promo", "SPRING-15")
    assert receipt["total_estimate"] == PRICE * 85 // 100 and "Промокод применен" in receipt["message"]
    assert (listed["promo_code"], listed["discount_source"]) == ("SPRING-15", "promo")
    assert '"SPRING-15"' in export


async def test_bigger_member_discount_keeps_promo_unused(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        id = await create_promo(client, admin, percent=1, total_limit=1)
        receipt = (await apply(client, item["slug"], with_code(FORM), member)).json()
        assert (await promo_row(client, admin, id))["used"] == 0
    assert receipt["discount_source"] == "member" and receipt["promo_code"] is None
    assert receipt["member_discount"] > 1 and "промокод не нужен" in receipt["message"]
    assert (await order(app, receipt["number"]))["promo_id"] is None


async def test_organization_gets_promo(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin)
        receipt = (await apply(client, item["slug"], with_code(GROUP))).json()
    assert (receipt["member_discount"], receipt["total_estimate"]) == (15, 3 * PRICE * 85 // 100)


@pytest.mark.parametrize(
    "extra,code,message",
    [
        ({}, "OTHER-10", "Промокод не найден"),
        ({"status": "disabled"}, "SPRING-15", "Промокод не найден"),
        ({"starts_on": 1, "ends_on": 2}, "SPRING-15", "еще не действует"),
        ({"starts_on": -2, "ends_on": -1}, "SPRING-15", "истек"),
    ],
)
async def test_unusable_promo_blocks_application(database_app, mails, extra, code, message):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin, **extra)
        response = await apply(client, item["slug"], with_code(FORM, code))
        quote = await client.post(f"/portfolio/{item['slug']}/quote", json={"applicant": "self", "promo_code": code})
    assert response.status_code == 400 and message in response.json()["error"]
    assert quote.status_code == 400 and message in quote.json()["error"]
    assert await app.state.store.read("orders", fields=("id",)) == [] and mails == []


async def test_promo_limited_to_other_program(database_app, mails):
    app = database_app
    item, other = await program(app), await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin, all_programs=False, program_ids=[other["id"]])
        response = await apply(client, item["slug"], with_code(FORM))
        accepted = await apply(client, other["slug"], with_code(FORM))
    assert response.status_code == 400 and "не действует на эту программу" in response.json()["error"]
    assert accepted.status_code == 200


async def test_person_limit_ignores_email_case(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin)
        assert (await apply(client, item["slug"], with_code(FORM, email="Person@Example.test"))).status_code == 200
        again = await apply(client, item["slug"], with_code(FORM, email="person@example.test"))
        other = await apply(client, item["slug"], with_code(FORM, email="other@example.test"))
    assert again.status_code == 400 and "уже использовали" in again.json()["error"]
    assert other.status_code == 200


async def test_canceled_application_returns_promo_use(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        id = await create_promo(client, admin, total_limit=1)
        first = (await apply(client, item["slug"], with_code(FORM, email="first@example.test"))).json()
        refused = await apply(client, item["slug"], with_code(FORM, email="second@example.test"))
        assert refused.status_code == 400 and "использован максимальное число раз" in refused.json()["error"]
        order_id = (await order(app, first["number"]))["id"]
        await client.patch("/admin/orders/" + order_id, headers=admin, json={"status": "canceled"})
        assert (await promo_row(client, admin, id))["used"] == 0
        accepted = await apply(client, item["slug"], with_code(FORM, email="second@example.test"))
    assert accepted.status_code == 200


async def test_last_promo_use_is_granted_once_under_concurrency(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin, total_limit=1)
        responses = await asyncio.gather(
            *(
                apply(
                    client,
                    item["slug"],
                    with_code(FORM, email=f"{index}@example.test"),
                    {"idempotency-key": str(uuid4())},
                )
                for index in range(2)
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 400]


async def test_quote_matches_server_pricing(database_app):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        await create_promo(client, admin, percent=1)
        plain = (
            await client.post(f"/portfolio/{item['slug']}/quote", json={"applicant": "organization", "participants": 3})
        ).json()
        promo = (
            await client.post(
                f"/portfolio/{item['slug']}/quote",
                json={"applicant": "organization", "participants": 3, "promo_code": "spring-15"},
            )
        ).json()
        member_wins = (
            await client.post(
                f"/portfolio/{item['slug']}/quote",
                headers=member,
                json={"applicant": "self", "promo_code": "SPRING-15"},
            )
        ).json()
    assert (plain["subtotal"], plain["total"], plain["source"], plain["message"]) == (3 * PRICE, 3 * PRICE, None, None)
    assert (promo["discount"], promo["source"], promo["total"]) == (1, "promo", 3 * PRICE * 99 // 100)
    assert member_wins["source"] == "member" and "промокод не нужен" in member_wins["message"]


async def test_deleted_promo_keeps_code_in_application(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        id = await create_promo(client, admin)
        number = (await apply(client, item["slug"], with_code(FORM))).json()["number"]
        assert (await client.delete("/admin/promo-codes/" + id, headers=admin)).status_code == 200
    saved = await order(app, number)
    assert (saved["promo_id"], saved["promo_code"], saved["discount_source"]) == (None, "SPRING-15", "promo")


async def test_patch_of_deleted_promo_is_not_found(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        id = await create_promo(client, admin)
        await client.delete("/admin/promo-codes/" + id, headers=admin)
        response = await client.patch("/admin/promo-codes/" + id, headers=admin, json={"percent": 10})
    assert response.status_code == 404 and response.json()["error"] == "Промокод не найден."


async def test_promo_dates_are_stored_in_iso_form(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    day = today()
    week = day.isocalendar()
    async with client_for(app) as client:
        id = await create_promo(
            client, admin, starts_on=day.strftime("%Y%m%d"), ends_on=f"{week.year}-W{week.week:02d}-7"
        )
        row = await promo_row(client, admin, id)
    sunday = day + timedelta(days=7 - week.weekday)
    assert (row["starts_on"], row["ends_on"]) == (day.isoformat(), sunday.isoformat())


async def test_quote_does_not_reveal_who_used_the_code(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        await create_promo(client, admin)
        assert (await apply(client, item["slug"], with_code(FORM))).status_code == 200
        quote = await client.post(
            f"/portfolio/{item['slug']}/quote",
            json={"applicant": "self", "promo_code": "SPRING-15", "contact_email": FORM["contact_email"]},
        )
        again = await apply(client, item["slug"], with_code(FORM))
    assert quote.status_code == 200 and quote.json()["source"] == "promo"
    assert again.status_code == 400 and "уже использовали" in again.json()["error"]


@pytest.mark.parametrize(
    "extra,code,reason",
    [
        ({}, "OTHER-10", "Промокод не найден."),
        (
            {"starts_on": -2, "ends_on": -1},
            "SPRING-15",
            "Срок действия промокода истек.",
        ),
    ],
)
async def test_member_keeps_discount_when_code_is_unusable(database_app, mails, extra, code, reason):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        await create_promo(client, admin, **extra)
        response = await apply(client, item["slug"], with_code(FORM, code), member)
        quote = await client.post(
            f"/portfolio/{item['slug']}/quote", headers=member, json={"applicant": "self", "promo_code": code}
        )
        group = await apply(client, item["slug"], with_code(GROUP, code), member)
    receipt = response.json()
    assert response.status_code == 200 and (receipt["discount_source"], receipt["promo_code"]) == ("member", None)
    assert receipt["message"] == f"{reason} Действует ваша скидка выпускника {receipt['member_discount']}%."
    assert quote.status_code == 200 and quote.json()["message"] == receipt["message"]
    assert group.status_code == 400 and group.json()["error"] == reason


def test_equal_member_discount_message_does_not_claim_it_is_bigger():
    assert promo_message("SPRING-15", "member", 15) == "Ваша скидка выпускника не меньше, промокод не нужен."
