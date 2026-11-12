import csv
import io
import json

import httpx
from test_campaigns import campaign
from test_community_integration import account
from test_donations import BODY, PAYMENT_ID, Provider, donate, donation, enable, letters, site, webhook
from test_donations_admin import client as admin_client
from test_donations_transfer import TRANSFER_BODY, enable_transfer

from club_api.domain import rubles
from club_api.modules.donations.service import CAMPAIGN_CONSENTS, DONATION_CONSENTS

PURPOSE = "на оснащение аудитории факультета права НИУ ВШЭ"
CAMPAIGN_BODY = {**BODY, "campaign": "intensive", "consent_gift": True}
DONATED = rubles(300000) + " " + PURPOSE


def pay_through(app, provider):
    enable(app)
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))


async def saved(app):
    return await app.state.database.rows("SELECT number,campaign_id FROM club_donations ORDER BY number")


async def test_campaign_donation_is_linked_paid_with_campaign_purpose_and_counted(database_app, monkeypatch):
    app, provider = database_app, Provider()
    item = await campaign(app, "intensive", payment_purpose=PURPOSE, invite_threshold=1500000)
    sent = letters(app, monkeypatch)
    pay_through(app, provider)
    async with site(app) as client:
        reply = await donate(client, CAMPAIGN_BODY)
        assert reply.status_code == 200, reply.text
        number = reply.json()["number"]
        before = (await client.get("/campaigns/intensive")).json()["raised"]
        provider.finish("succeeded")
        assert (await webhook(client)).status_code == 200
        after = (await client.get("/campaigns/intensive")).json()["raised"]
    row = await donation(app, number)
    assert (row["campaign_id"], row["status"], row["payment_id"]) == (item["id"], "succeeded", PAYMENT_ID)
    assert row["consents"] == CAMPAIGN_CONSENTS and row["consents"]["gift"] and row["invited_at"] is None
    assert provider.sent[0]["body"]["description"] == "Пожертвование " + PURPOSE
    assert (before, after) == (0, 300000)
    [donor] = [mail for mail in sent if mail["to"] == "donor@example.test"]
    assert DONATED in donor["text"] and "приглаш" not in donor["text"].lower()
    [event] = await app.state.database.rows("SELECT detail FROM audit_log WHERE event='donation.created'")
    detail = event["detail"]
    assert (json.loads(detail) if isinstance(detail, str) else detail)["campaign"] == "intensive"


async def test_campaign_donation_requires_gift_confirmation(database_app):
    app, provider = database_app, Provider()
    await campaign(app, "intensive")
    pay_through(app, provider)
    async with site(app) as client:
        for value in (False, "true", 1, None):
            reply = await donate(client, {**CAMPAIGN_BODY, "consent_gift": value})
            assert (reply.status_code, reply.json()["error"]) == (400, "Подтвердите, что пожертвование безвозмездно.")
        missing = await donate(client, {**BODY, "campaign": "intensive"})
    assert missing.status_code == 400
    assert await saved(app) == [] and provider.sent == []


async def test_donation_to_hidden_or_unknown_campaign_is_refused(database_app):
    app, provider = database_app, Provider()
    await campaign(app, "draft", status="draft")
    await campaign(app, "archived", status="archived")
    pay_through(app, provider)
    async with site(app) as client:
        for slug in ("draft", "archived", "missing"):
            reply = await donate(client, {**CAMPAIGN_BODY, "campaign": slug})
            assert (reply.status_code, reply.json()["error"]) == (404, "Сбор не найден или уже закрыт.")
        empty = await donate(client, {**CAMPAIGN_BODY, "campaign": ""})
    assert empty.status_code == 400
    assert await saved(app) == [] and provider.sent == []


async def test_campaign_without_purpose_uses_common_purpose_and_plain_donation_stays_plain(database_app):
    app, provider = database_app, Provider()
    await campaign(app, "intensive", payment_purpose="  ")
    pay_through(app, provider)
    async with site(app) as client:
        linked = (await donate(client, CAMPAIGN_BODY)).json()["number"]
        plain = (await donate(client, {**BODY, "consent_gift": True})).json()["number"]
    common = "Пожертвование на развитие факультета права НИУ ВШЭ"
    assert [sent["body"]["description"] for sent in provider.sent] == [common, common]
    row = await donation(app, plain)
    assert (row["campaign_id"], row["consents"]) == (None, DONATION_CONSENTS)
    assert (await donation(app, linked))["campaign_id"] is not None


async def test_campaign_transfer_carries_campaign_purpose(database_app, monkeypatch):
    app = database_app
    await campaign(app, "intensive", payment_purpose=PURPOSE)
    sent = letters(app, monkeypatch)
    enable_transfer(app, monkeypatch)
    async with site(app) as client:
        reply = await donate(client, {**TRANSFER_BODY, "campaign": "intensive", "consent_gift": True})
    assert reply.status_code == 200, reply.text
    number, transfer = reply.json()["number"], reply.json()["transfer"]
    assert transfer["purpose"] == f"Пожертвование {number} {PURPOSE}"
    [donor] = [mail for mail in sent if mail["to"] == "donor@example.test"]
    assert "Назначение платежа: " + transfer["purpose"] in donor["text"] and DONATED in donor["text"]
    _, headers = await account(app, "admin")
    async with admin_client(app) as http:
        received = await http.post(f"/admin/donations/{number}/received", headers=headers)
    assert received.status_code == 200, received.text
    thanks = [mail for mail in sent if mail["subject"].startswith("Спасибо за пожертвование")]
    assert len(thanks) == 1 and DONATED in thanks[0]["text"]


async def test_office_filters_sums_and_exports_donations_by_campaign(database_app):
    app, provider = database_app, Provider()
    item = await campaign(app, "intensive", title="Искусство влияния")
    await campaign(app, "other", title="Другой сбор")
    pay_through(app, provider)
    async with site(app) as client:
        linked = (await donate(client, CAMPAIGN_BODY)).json()["number"]
        provider.finish("succeeded")
        assert (await webhook(client)).status_code == 200
        plain = (await donate(client, BODY)).json()["number"]
    _, headers = await account(app, "admin")
    async with admin_client(app) as http:
        everything = (await http.get("/admin/donations", headers=headers)).json()
        chosen = (await http.get("/admin/donations?campaign=" + item["id"], headers=headers)).json()
        wrong = await http.get("/admin/donations?campaign=unknown", headers=headers)
        export = await http.get("/admin/donations/export.csv?campaign=" + item["id"], headers=headers)
        full = await http.get("/admin/donations/export.csv", headers=headers)
    assert everything["campaigns"] == {item["id"]: "Искусство влияния", **everything["campaigns"]}
    assert sorted(everything["campaigns"].values()) == ["Другой сбор", "Искусство влияния"]
    assert sorted(row["number"] for row in everything["items"]) == sorted([linked, plain])
    assert [(row["number"], row["campaign_id"], row["invited_at"]) for row in chosen["items"]] == [
        (linked, item["id"], None)
    ]
    assert (chosen["total"], chosen["summary"]) == (1, {"count": 1, "amount": 300000})
    assert wrong.status_code == 400
    header, *rows = list(csv.reader(io.StringIO(export.text.lstrip("﻿")), delimiter=";"))
    assert header[10:] == ["Сбор", "Приглашение отправлено"]
    assert [(row[0], row[10], row[11]) for row in rows] == [(linked, "Искусство влияния", "")]
    by_number = {row[0]: row for row in csv.reader(io.StringIO(full.text.lstrip("﻿")), delimiter=";")}
    assert by_number[plain][10] == ""
