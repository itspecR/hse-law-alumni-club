import asyncio
from uuid import uuid4

import httpx
import pytest
from test_community_integration import account

from club_api.core.errors import ApiError
from club_api.domain import order_totals

PRICE = 4500000
FORM = {
    "contact_fio": "Вымышленный Заявитель",
    "contact_phone": "+70000000000",
    "contact_email": "applicant@example.test",
    "applicant": "self",
    "consent_pdn": True,
    "consent_terms": True,
}
GROUP = {**FORM, "applicant": "organization", "organization": "ООО «Пример»", "participants": 3}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def program(app, **extra):
    data = {"slug": str(uuid4()), "title": "Переговоры", "price": PRICE, "catalog": "portfolio", "status": "published"}
    return await app.state.store.create("programs", {**data, **extra})


async def apply(client, slug, body, headers=None):
    return await client.post(f"/portfolio/{slug}/applications", json=body, headers=headers or {})


async def order(app, number):
    return (await app.state.store.read("orders", filters={"number": {"_eq": number}}, limit=1))[0]


@pytest.fixture
def mails(database_app, monkeypatch):
    sent = []

    async def capture(to, subject, text):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(database_app.state.notifications, "send_email", capture)
    return sent


def test_member_discount_covers_club_programs():
    items = [{"type": "portfolio", "price": 100000, "qty": 2}, {"type": "merch", "price": 50000, "qty": 1}]
    assert order_totals(items, 10)["total"] == 230000


async def test_guest_application_is_priced_on_server_and_confirmed_by_mail(database_app, mails):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        response = await apply(client, item["slug"], {**FORM, "price": 1})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["total_estimate"] == PRICE and receipt["member_discount"] == 0 and receipt["participants"] == 1
    row = await order(app, receipt["number"])
    assert (row["type"], row["program_id"], row["payment_status"], row["organization"]) == (
        "portfolio",
        item["id"],
        None,
        None,
    )
    assert row["consent_pdn"] is True and set(row["consents"]) == {"pdn", "terms"}
    assert row["items_json"][0]["qty"] == 1 and row["items_json"][0]["price"] == PRICE
    assert mails[0]["to"] == "applicant@example.test" and receipt["number"] in mails[0]["subject"]
    assert "Переговоры" in mails[0]["text"]


async def test_verified_member_gets_discount_only_for_self(database_app, mails):
    app = database_app
    item = await program(app)
    _, member = await account(app)
    async with client_for(app) as client:
        own = (await apply(client, item["slug"], FORM, member)).json()
        group = (await apply(client, item["slug"], GROUP, member)).json()
    assert own["member_discount"] > 0 and own["total_estimate"] < PRICE
    assert (group["member_discount"], group["subtotal"], group["total_estimate"]) == (0, 3 * PRICE, 3 * PRICE)
    saved = await order(app, group["number"])
    assert (saved["participants"], saved["organization"]) == (3, "ООО «Пример»")


@pytest.mark.parametrize(
    "body,message",
    [
        ({**GROUP, "organization": " "}, "Укажите организацию"),
        ({**GROUP, "participants": 1}, "от 2 до 20"),
        ({**FORM, "participants": 2}, "на одного участника"),
        ({**GROUP, "participants": 21}, "Некорректные данные"),
        ({**FORM, "consent_terms": False}, "Некорректные данные"),
        ({key: value for key, value in FORM.items() if key != "consent_pdn"}, "Некорректные данные"),
        ({**FORM, "website": "bot"}, "Некорректные данные"),
    ],
)
async def test_invalid_applications_are_rejected(database_app, mails, body, message):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        response = await apply(client, item["slug"], body)
    assert response.status_code == 400 and message in response.json()["error"]
    assert await app.state.store.read("orders", fields=("id",)) == [] and mails == []


async def test_only_open_published_club_programs_accept_applications(database_app, mails):
    app = database_app
    closed = await program(app, enrollment="nonactual")
    draft = await program(app, status="draft")
    dpo = await program(app, catalog="dpo")
    async with client_for(app) as client:
        assert (await apply(client, closed["slug"], FORM)).status_code == 400
        for row in (draft, dpo):
            assert (await apply(client, row["slug"], FORM)).status_code == 404
        assert (await apply(client, "missing", FORM)).status_code == 404


async def test_repeated_submission_keeps_one_application(database_app, mails):
    app = database_app
    item = await program(app)
    key = {"idempotency-key": str(uuid4())}
    async with client_for(app) as client:
        first, second = await asyncio.gather(
            apply(client, item["slug"], FORM, key), apply(client, item["slug"], FORM, key)
        )
        changed = await apply(client, item["slug"], {**FORM, "contact_fio": "Другой Заявитель"}, key)
    assert first.json()["number"] == second.json()["number"]
    assert changed.status_code == 409
    assert len(await app.state.store.read("orders", fields=("id",))) == 1


async def test_seats_are_taken_only_by_confirmed_applications(database_app, mails):
    app = database_app
    item = await program(app, capacity=3)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        pair = {**GROUP, "participants": 2}
        numbers = [(await apply(client, item["slug"], body)).json()["number"] for body in (pair, FORM, FORM)]
        ids = [(await order(app, number))["id"] for number in numbers]
        assert (await client.get("/portfolio/" + item["slug"])).json()["seats_left"] == 3

        async def status(id, value):
            return await client.patch("/admin/orders/" + id, headers=admin, json={"status": value})

        assert (await status(ids[0], "confirmed")).status_code == 200
        assert (await status(ids[1], "confirmed")).status_code == 200
        assert (await client.get("/portfolio/" + item["slug"])).json()["seats_left"] == 0
        refused = await status(ids[2], "confirmed")
        assert refused.status_code == 409
        assert "Мест не осталось: подтверждено 3 из 3" in refused.json()["error"]
        assert (await status(ids[0], "done")).status_code == 200
        assert (await status(ids[1], "canceled")).status_code == 200
        assert (await status(ids[2], "confirmed")).status_code == 200
        assert (await client.get("/portfolio/" + item["slug"])).json()["seats_left"] == 0


async def test_last_seat_is_confirmed_once_under_concurrency(database_app, mails):
    app = database_app
    item = await program(app, capacity=1)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        numbers = [(await apply(client, item["slug"], FORM)).json()["number"] for _ in range(2)]
        ids = [(await order(app, number))["id"] for number in numbers]
        results = await asyncio.gather(
            *(client.patch("/admin/orders/" + id, headers=admin, json={"status": "confirmed"}) for id in ids)
        )
    assert sorted(response.status_code for response in results) == [200, 409]


async def test_unlimited_program_reports_no_seat_limit(database_app, mails):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        assert (await client.get("/portfolio/" + item["slug"])).json()["seats_left"] is None


async def test_club_application_cannot_be_paid_online(database_app, mails):
    app = database_app
    item = await program(app)
    alumni_id, member = await account(app)
    async with client_for(app) as client:
        number = (await apply(client, item["slug"], FORM, member)).json()["number"]
    with pytest.raises(ApiError) as error:
        await app.state.payments.prepare(number, alumni_id)
    assert error.value.status == 400


async def test_office_sees_and_exports_club_application(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        number = (await apply(client, item["slug"], GROUP)).json()["number"]
        listed = (await client.get("/admin/orders", headers=admin)).json()["items"][0]
        export = await client.get("/admin/orders/export.csv", headers=admin)
    assert (listed["number"], listed["organization"], listed["participants"]) == (number, "ООО «Пример»", 3)
    row = next(line for line in export.text.splitlines() if number in line)
    assert '"Портфель программ"' in row and '"ООО «Пример»";"3"' in row and "Самовывоз" not in row


async def test_card_and_sitemap_announce_club_programs(database_app):
    app = database_app
    item = await program(app)
    async with client_for(app) as client:
        card = (await client.get("/portfolio/" + item["slug"])).json()
        sitemap = (await client.get("/sitemap.xml")).text
    assert card["group"] == {"min": 2, "max": 20}
    assert "/portfolio</loc>" in sitemap and "/portfolio/" + item["slug"] + "</loc>" in sitemap


async def test_member_sees_club_application_details(database_app, mails):
    app = database_app
    item = await program(app)
    _, member = await account(app)
    async with client_for(app) as client:
        await apply(client, item["slug"], GROUP, member)
        rows = (await client.get("/me/orders", headers=member)).json()
    assert (rows[0]["type"], rows[0]["participants"], rows[0]["organization"]) == ("portfolio", 3, "ООО «Пример»")


async def test_retry_after_failed_notice_returns_full_receipt(database_app, mails, monkeypatch):
    app = database_app
    item = await program(app)

    async def broken(*args):
        raise RuntimeError("office channel is down")

    monkeypatch.setattr(app.state.notifications, "order_notice", broken)
    key = {"idempotency-key": str(uuid4())}
    async with client_for(app) as client:
        failed = await apply(client, item["slug"], GROUP, key)
        retried = await apply(client, item["slug"], GROUP, key)
    assert failed.status_code == 500 and retried.status_code == 200
    receipt = retried.json()
    assert (receipt["participants"], receipt["discount_source"], receipt["total_estimate"]) == (3, None, 3 * PRICE)
    assert receipt["notified"]["ok"] is False
    assert len(await app.state.store.read("orders", fields=("id",))) == 1
