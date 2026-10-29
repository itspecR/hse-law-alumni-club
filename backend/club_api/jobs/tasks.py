from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from club_api.core.models import parse_date
from club_api.db.queries import Query
from club_api.modules.catalog.sync import sync_catalog
from club_api.modules.news.importer import sync_news
from club_api.modules.notifications.mail import MAIL_RETENTION_DAYS
from club_api.modules.portfolio.captcha import forget_expired

EXPIRED_NAME = "срок хранения истек"


async def retention(state):
    await state.database.execute("DELETE FROM club_support_tickets WHERE expires_at<=now()")
    await state.database.execute(
        Query(
            "UPDATE orders SET contact_fio=%s,contact_phone='-',contact_email='-',address=NULL,comment=NULL WHERE created_at<now()-%s*interval '1 day' AND (contact_email IS NULL OR contact_email<>'-')",
            "UPDATE orders SET contact_fio=%s,contact_phone='-',contact_email='-',address=NULL,comment=NULL WHERE created_at<DATE_SUB(now(),INTERVAL %s DAY) AND (contact_email IS NULL OR contact_email<>'-')",
        ),
        (EXPIRED_NAME, state.settings.ORDER_RETENTION_DAYS),
    )
    await state.database.execute(
        Query(
            "DELETE FROM audit_log WHERE created_at<now()-%s*interval '1 day'",
            "DELETE FROM audit_log WHERE created_at<DATE_SUB(now(),INTERVAL %s DAY)",
        ),
        (state.settings.AUDIT_RETENTION_DAYS,),
    )
    await state.database.execute(
        Query(
            "DELETE FROM club_mail_outbox WHERE status IN ('sent','failed') AND created_at<now()-%s*interval '1 day'",
            "DELETE FROM club_mail_outbox WHERE status IN ('sent','failed') AND created_at<DATE_SUB(now(),INTERVAL %s DAY)",
        ),
        (MAIL_RETENTION_DAYS,),
    )
    await forget_expired(state)


async def event_reminders(state):
    now = datetime.now(UTC)
    rows = await state.store.read(
        "events",
        filters={"status": {"_eq": "published"}, "starts_at": {"_gt": now, "_lte": now + timedelta(days=1)}},
        fields=("id", "title", "starts_at", "location", "format", "reminder_sent"),
        limit=-1,
    )
    for event in rows:
        if event["reminder_sent"]:
            continue
        rsvps = await state.store.read(
            "event_rsvps", filters={"event_id": {"_eq": event["id"]}}, fields=("alumni_id",), limit=-1
        )
        when = parse_date(event["starts_at"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%H:%M")
        place = "онлайн" if event["format"] == "online" else event["location"] or ""
        await state.push.to_many(
            [row["alumni_id"] for row in rsvps],
            {
                "title": "Завтра событие клуба",
                "body": f"{event['title']}, начало в {when}{', ' + place if place else ''}",
                "url": "/events",
            },
        )
        await state.store.update("events", {"reminder_sent": True}, id=event["id"])


async def podcast_reminders(state):
    now = datetime.now(UTC)
    rows = await state.store.read(
        "alumni",
        filters={"podcast_sub_until": {"_gt": now, "_lte": now + timedelta(days=10)}},
        fields=("id", "fio", "podcast_sub_until", "podcast_reminder_sent", "contacts_json"),
        limit=-1,
    )
    due = [row for row in rows if not row["podcast_reminder_sent"]]
    if not due:
        return
    await state.push.to_many(
        [row["id"] for row in due],
        {
            "title": "Подписка на подкасты заканчивается",
            "body": "Осталось меньше 10 дней. Продлите, чтобы не потерять доступ к выпускам.",
            "url": "/podcasts",
        },
    )
    for row in due:
        to = (row["contacts_json"] or {}).get("email")
        if to and state.notifications.mail_enabled:
            until = parse_date(row["podcast_sub_until"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
            await state.notifications.send_email(
                to,
                "Подписка на подкасты клуба заканчивается",
                f"{row['fio'] or 'Здравствуйте'}!\n\nВаша подписка на подкасты клуба выпускников действует до {until}.\nПосле этой даты выпуски снова закроются.\n\nПродлить можно в разделе «Подкасты» на сайте клуба.",
            )
        await state.store.update("alumni", {"podcast_reminder_sent": True}, id=row["id"])


def scheduled(state):
    return [
        ("points-decay", lambda now: now.day == 1 and now.hour == 3 and now.minute == 0, state.gamification.decay),
        (
            "dpo-sync",
            lambda now: state.settings.DPO_SYNC_ENABLED == "true" and now.hour == 5 and now.minute == 0,
            lambda: sync_catalog(state),
        ),
        ("event-reminders", lambda now: now.hour == 10 and now.minute == 0, lambda: event_reminders(state)),
        ("podcast-reminders", lambda now: now.hour == 11 and now.minute == 0, lambda: podcast_reminders(state)),
        ("retention", lambda now: now.hour == 4 and now.minute == 0, lambda: retention(state)),
        ("reserve-expiry", lambda now: now.minute % 15 == 0, state.checkout.expire_reservations),
        ("mail-outbox", lambda now: now.minute % 5 == 0, state.notifications.drain_mail),
        (
            "news-sync",
            lambda now: state.settings.NEWS_SYNC_ENABLED == "true" and now.minute == 17,
            lambda: sync_news(state),
        ),
    ]
