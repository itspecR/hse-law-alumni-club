import logging
from urllib.parse import quote
from zoneinfo import ZoneInfo

from club_api.core.models import parse_date
from club_api.modules.content.sitemap import address
from club_api.modules.notifications.mail import CLUB_NAME, club_subject

logger = logging.getLogger("club.events")


def event_path(event):
    return "/events/" + quote(address(event), safe="")


def next_step(event, url):
    if event["reg_url"]:
        return f"Регистрация: {event['reg_url']}\nПодробнее и файл для календаря: {url}"
    return f"Записаться и добавить событие в календарь можно на странице события: {url}"


async def announce(state, event):
    try:
        path = event_path(event)
        await state.push.to_all({"title": "Новое событие", "body": event["title"], "url": path})
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
                    f"Здравствуйте{', ' + row['fio'] if row['fio'] else ''}!\n\nВ клубе новое событие.\n\n{event['title']}\n{when}{' · ' + place if place else ''}\n\n{next_step(event, state.settings.PUBLIC_URL + path)}\nЗа участие начисляются баллы.\n\n{CLUB_NAME}",
                )
    except Exception:
        logger.error("Не удалось отправить анонс события")
