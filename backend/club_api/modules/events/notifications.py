import logging
from zoneinfo import ZoneInfo

from club_api.core.models import parse_date
from club_api.modules.notifications.mail import CLUB_NAME, club_subject

logger = logging.getLogger("club.events")


async def announce(state, event):
    try:
        await state.push.to_all({"title": "Новое событие клуба 📅", "body": event["title"], "url": "/events"})
        rows = await state.database.rows(
            "SELECT a.fio,u.email FROM alumni a JOIN directus_users u ON u.id=a.user_id WHERE a.verification_status='verified' AND a.telegram_id IS NULL AND NOT EXISTS(SELECT 1 FROM push_subs p WHERE p.alumni_id=a.id)"
        )
        when = parse_date(event["starts_at"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
        place = "онлайн" if event["format"] == "online" else event["location"] or ""
        for row in rows:
            if row["email"]:
                await state.notifications.send_email(
                    row["email"],
                    club_subject(f"Новое событие «{event['title']}»"),
                    f"Здравствуйте{', ' + row['fio'] if row['fio'] else ''}!\n\nВ клубе новое событие.\n\n{event['title']}\n{when}{' · ' + place if place else ''}\n{('Регистрация: ' + event['reg_url']) if event['reg_url'] else ''}\n\nЗаписаться и добавить событие в календарь можно на сайте: {state.settings.PUBLIC_URL}/events\nЗа участие начисляются баллы.\n\n{CLUB_NAME}",
                )
    except Exception:
        logger.error("Не удалось отправить анонс события")
