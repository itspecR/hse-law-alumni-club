from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from django.db import IntegrityError

from club_api.modules.campaigns.service import progress

TEASER = {
    "slug",
    "title",
    "subtitle",
    "lede",
    "date_text",
    "place_text",
    "goal_title",
    "goal_amount",
    "cover",
    "raised",
    "percent",
}
HIDDEN = {"id", "status", "extra_amount", "payment_purpose", "invite_subject", "invite_body", "created_at"}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def campaign(app, slug, **extra):
    data = {"slug": slug, "title": "Сбор " + slug, "status": "published", "goal_amount": 1000000}
    return await app.state.store.create("club_campaigns", {**data, **extra})


async def donation(app, amount, status="succeeded", campaign_id=None):
    await app.state.database.execute(
        "INSERT INTO club_donations(id,number,amount,email,status,campaign_id) VALUES(%s,%s,%s,%s,%s,%s)",
        (str(uuid4()), "DON-" + uuid4().hex[:12], amount, "donor@example.com", status, campaign_id),
    )


async def trainer(app, fio, status="published"):
    return await app.state.store.create("trainers", {"fio": fio, "status": status})


async def speak(app, campaign_row, trainer_row, sort):
    await app.state.store.create(
        "club_campaign_trainers", {"campaign_id": campaign_row["id"], "trainer_id": trainer_row["id"], "sort": sort}
    )


@pytest.mark.parametrize(
    ("goal", "extra", "donated", "expected"),
    [
        (1000000, 0, 0, {"raised": 0, "percent": 0}),
        (1000000, 0, 9999, {"raised": 9999, "percent": 0}),
        (1000000, 100000, 239999, {"raised": 339999, "percent": 33}),
        (1000000, 0, 1500000, {"raised": 1500000, "percent": 100}),
        (0, 500, 500, {"raised": 1000, "percent": 0}),
    ],
)
def test_progress_floors_percent_and_caps_at_goal(goal, extra, donated, expected):
    assert progress({"goal_amount": goal, "extra_amount": extra}, donated) == expected


async def test_current_is_empty_without_published_campaign(database_app):
    app = database_app
    await campaign(app, "draft", status="draft")
    await campaign(app, "archived", status="archived")
    async with client_for(app) as client:
        reply = await client.get("/campaigns/current")
    assert (reply.status_code, reply.json()) == (200, {"campaign": None})


async def test_current_shows_newest_published_campaign_as_teaser(database_app):
    app = database_app
    await campaign(app, "old", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await campaign(app, "new", created_at=datetime(2026, 2, 1, tzinfo=UTC), invite_body="Письмо", extra_amount=250000)
    await campaign(app, "newest-draft", created_at=datetime(2026, 3, 1, tzinfo=UTC), status="draft")
    async with client_for(app) as client:
        shown = (await client.get("/campaigns/current")).json()["campaign"]
    assert shown["slug"] == "new" and set(shown) == TEASER
    assert (shown["raised"], shown["percent"]) == (250000, 25)


async def test_raised_counts_paid_donations_of_this_campaign_and_manual_amount(database_app):
    app = database_app
    mine = await campaign(app, "mine", extra_amount=100000)
    other = await campaign(app, "other")
    await donation(app, 150000, campaign_id=mine["id"])
    await donation(app, 50000, campaign_id=mine["id"])
    await donation(app, 700000, campaign_id=other["id"])
    await donation(app, 800000)
    for status in ("new", "awaiting", "pending", "review", "canceled", "failed"):
        await donation(app, 900000, status=status, campaign_id=mine["id"])
    async with client_for(app) as client:
        card = (await client.get("/campaigns/mine")).json()
        second = (await client.get("/campaigns/other")).json()
    assert (card["raised"], card["percent"]) == (300000, 30)
    assert (second["raised"], second["percent"]) == (700000, 70)


async def test_page_shows_texts_and_published_speakers_in_saved_order(database_app):
    app = database_app
    texts = {
        "subtitle": "Имидж, речь, аудитория",
        "invite_threshold": 1500000,
        "amounts": [500000, 1500000],
        "modules": [{"title": "Блок", "points": ["Тема"], "time": "10:00-11:30"}],
        "faq": [{"question": "Вопрос", "answer": "Ответ"}],
        "note": "Участие бесплатное.",
        "payment_purpose": "на цель сбора",
        "invite_subject": "Приглашение",
        "invite_body": "Текст письма",
    }
    item = await campaign(app, "intensive", **texts)
    first, second, hidden = (
        await trainer(app, "Первый"),
        await trainer(app, "Второй"),
        await trainer(app, "Скрытый", "draft"),
    )
    await speak(app, item, second, 0)
    await speak(app, item, hidden, 1)
    await speak(app, item, first, 2)
    async with client_for(app) as client:
        card = (await client.get("/campaigns/intensive")).json()
    assert [row["fio"] for row in card["speakers"]] == ["Второй", "Первый"]
    assert set(card["speakers"][0]) == {"id", "fio", "photo", "bio"}
    assert not HIDDEN & set(card)
    for name in ("subtitle", "invite_threshold", "amounts", "modules", "faq", "note"):
        assert card[name] == texts[name]


async def test_page_hides_drafts_archived_and_unknown_campaigns(database_app):
    app = database_app
    await campaign(app, "draft", status="draft")
    await campaign(app, "archived", status="archived")
    async with client_for(app) as client:
        for slug in ("draft", "archived", "missing"):
            reply = await client.get("/campaigns/" + slug)
            assert (reply.status_code, reply.json()["error"]) == (404, "Сбор не найден.")


async def test_campaign_with_donations_cannot_be_deleted(database_app):
    app = database_app
    item = await campaign(app, "kept")
    await donation(app, 100000, campaign_id=item["id"])
    with pytest.raises(IntegrityError):
        await app.state.store.delete("club_campaigns", id=item["id"])
    assert await app.state.store.read("club_campaigns", fields=("slug",)) == [{"slug": "kept"}]


async def test_deleted_trainer_leaves_campaign_speakers(database_app):
    app = database_app
    item = await campaign(app, "speakers")
    person = await trainer(app, "Уходящий")
    await speak(app, item, person, 0)
    await app.state.store.delete("trainers", id=person["id"])
    assert await app.state.store.read("club_campaign_trainers", fields=("id",)) == []
    async with client_for(app) as client:
        assert (await client.get("/campaigns/speakers")).json()["speakers"] == []
