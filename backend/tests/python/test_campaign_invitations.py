import json
from uuid import uuid4

import httpx
from test_campaigns import campaign
from test_community_integration import account
from test_donations import letters

from club_api.modules.notifications.mail import CLUB_NAME

LETTER = {
    "invite_subject": "Приглашение на интенсив",
    "invite_body": "  Клуб приглашает вас на интенсив.\nДата придет отдельно.  ",
}
THRESHOLD = 1500000


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def donor(app, number, amount, campaign_id, status="succeeded", name="Вымышленный Даритель", email=None):
    await app.state.database.execute(
        "INSERT INTO club_donations(id,number,amount,email,name,status,campaign_id,paid_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())",
        (str(uuid4()), number, amount, email or number.lower() + "@example.test", name, status, campaign_id),
    )


async def invited_at(app, number):
    [row] = await app.state.database.rows("SELECT invited_at FROM club_donations WHERE number=%s", (number,))
    return row["invited_at"]


async def test_editor_sees_only_donors_eligible_for_invitation(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    item = await campaign(app, "intensive", invite_threshold=THRESHOLD)
    other = await campaign(app, "other", invite_threshold=0)
    await donor(app, "DON-A", THRESHOLD, item["id"])
    await donor(app, "DON-B", 5000000, item["id"], name=None)
    await donor(app, "DON-LOW", THRESHOLD - 1, item["id"])
    await donor(app, "DON-WAIT", 5000000, item["id"], status="awaiting")
    await donor(app, "DON-OTHER", 5000000, other["id"])
    await donor(app, "DON-PLAIN", 5000000, None)
    async with client_for(app) as client:
        reply = await client.get(f"/admin/campaigns/{item['id']}/donors", headers=editor)
        ready = await client.patch("/admin/campaigns/" + item["id"], headers=editor, json=LETTER)
        after = (await client.get(f"/admin/campaigns/{item['id']}/donors", headers=editor)).json()
        anonymous = await client.get(f"/admin/campaigns/{item['id']}/donors")
        unknown = await client.get(f"/admin/campaigns/{uuid4()}/donors", headers=editor)
    listed = reply.json()
    assert reply.status_code == 200 and ready.status_code == 200
    assert (listed["threshold"], listed["letter_ready"], after["letter_ready"]) == (THRESHOLD, False, True)
    assert sorted(row["number"] for row in listed["items"]) == ["DON-A", "DON-B"]
    assert set(listed["items"][0]) == {"number", "name", "email", "amount", "paid_at", "invited_at"}
    assert (anonymous.status_code, unknown.status_code) == (401, 404)


async def test_invitation_is_sent_once_with_campaign_letter_and_logged(database_app, monkeypatch):
    app = database_app
    admin, editor = await account(app, "editor")
    item = await campaign(app, "intensive", invite_threshold=THRESHOLD, **LETTER)
    await donor(app, "DON-A", THRESHOLD, item["id"], email="donor@example.test")
    sent = letters(app, monkeypatch)
    path = f"/admin/campaigns/{item['id']}/donors/DON-A/invite"
    async with client_for(app) as client:
        first = await client.post(path, headers=editor)
        second = await client.post(path, headers=editor)
        [listed] = (await client.get(f"/admin/campaigns/{item['id']}/donors", headers=editor)).json()["items"]
    assert first.status_code == 200, first.text
    assert (second.status_code, second.json()["error"]) == (409, "Приглашение этому дарителю уже отправлено.")
    [letter] = sent
    assert (letter["to"], letter["subject"]) == ("donor@example.test", "Приглашение на интенсив - " + CLUB_NAME)
    assert letter["text"] == (
        "Здравствуйте, Вымышленный Даритель!\n\nКлуб приглашает вас на интенсив.\nДата придет отдельно.\n\n" + CLUB_NAME
    )
    assert listed["invited_at"] and await invited_at(app, "DON-A")
    [event] = await app.state.database.rows("SELECT actor,subject,detail FROM audit_log WHERE event='donation.invited'")
    detail = event["detail"]
    assert event["subject"] == "donation:DON-A" and event["actor"].startswith("admin:")
    assert (json.loads(detail) if isinstance(detail, str) else detail) == {"campaign": "intensive"}
    assert admin


async def test_invitation_is_refused_for_ineligible_donations(database_app, monkeypatch):
    app = database_app
    _, editor = await account(app, "editor")
    item = await campaign(app, "intensive", invite_threshold=THRESHOLD, **LETTER)
    other = await campaign(app, "other", invite_threshold=0, **LETTER)
    silent = await campaign(app, "silent", invite_threshold=0, invite_subject="Тема", invite_body="  ")
    await donor(app, "DON-LOW", THRESHOLD - 1, item["id"])
    await donor(app, "DON-WAIT", 5000000, item["id"], status="awaiting")
    await donor(app, "DON-OTHER", 5000000, other["id"])
    await donor(app, "DON-ERASED", 5000000, item["id"], email="-")
    await donor(app, "DON-SILENT", 5000000, silent["id"])
    sent = letters(app, monkeypatch)
    cases = (
        (item, "DON-LOW", 409, "Приглашение отправляют дарителю оплаченного пожертвования от порога сбора."),
        (item, "DON-WAIT", 409, "Приглашение отправляют дарителю оплаченного пожертвования от порога сбора."),
        (item, "DON-OTHER", 404, "Пожертвование не найдено."),
        (item, "DON-MISSING", 404, "Пожертвование не найдено."),
        (item, "DON-ERASED", 409, "Почта дарителя удалена по сроку хранения, отправить письмо некуда."),
        (
            silent,
            "DON-SILENT",
            409,
            "Заполните тему и текст письма-приглашения в блоке «Платеж и приглашение» и сохраните сбор.",
        ),
    )
    async with client_for(app) as client:
        for target, number, status, message in cases:
            reply = await client.post(f"/admin/campaigns/{target['id']}/donors/{number}/invite", headers=editor)
            assert (reply.status_code, reply.json()["error"]) == (status, message), number
        anonymous = await client.post(f"/admin/campaigns/{item['id']}/donors/DON-LOW/invite")
        unknown = await client.post(f"/admin/campaigns/{uuid4()}/donors/DON-LOW/invite", headers=editor)
    assert (anonymous.status_code, unknown.status_code) == (401, 404)
    assert sent == []
    rows = await app.state.database.rows("SELECT number FROM club_donations WHERE invited_at IS NOT NULL")
    assert rows == [] and await app.state.database.rows("SELECT id FROM audit_log WHERE event='donation.invited'") == []
