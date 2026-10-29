import asyncio
import html
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from club_api.core.models import parse_date
from club_api.domain import level_info
from club_api.modules.gamification.social import record_reaction
from club_api.modules.telegram.faq import answer_faq
from club_api.modules.telegram.links import consume_link

logger = logging.getLogger("club.telegram")
COMMANDS = [
    {"command": "start", "description": "Приветствие и ссылки клуба"},
    {"command": "points", "description": "Мои баллы и уровень"},
    {"command": "calendar", "description": "Ближайшие события"},
    {"command": "cabinet", "description": "Открыть личный кабинет"},
    {"command": "dpo", "description": "Программы обучения"},
    {"command": "support", "description": "Обратиться в поддержку"},
    {"command": "help", "description": "Список команд"},
]


def parse_command(text):
    pieces = text.strip().split()
    if not pieces or not pieces[0].startswith("/"):
        return "", ""
    return pieces[0].split("@")[0].lower(), " ".join(pieces[1:])


class Telegram:
    def __init__(self, state):
        self.state = state
        self.polling = None

    async def api(self, method, body=None, *, timeout=10):
        response = await self.state.client.post(
            f"https://api.telegram.org/bot{self.state.settings.secret('TELEGRAM_BOT_TOKEN')}/{method}",
            json=body or {},
            timeout=timeout,
        )
        data = response.json()
        if not response.is_success or not data.get("ok"):
            raise RuntimeError("Telegram временно недоступен")
        return data.get("result")

    async def alumni(self, telegram_id):
        rows = await self.state.store.read(
            "alumni",
            filters={"telegram_id": {"_eq": telegram_id}},
            fields=("id", "user_id", "fio", "verification_status", "points_cached", "personal_discount"),
            limit=1,
        )
        alumni = rows[0] if rows else None
        if (
            alumni
            and alumni["user_id"]
            and not await self.state.auth.user(id=alumni["user_id"], alumni=True, active=True)
        ):
            return None
        return alumni

    def link(self, path, label):
        return f'<a href="{html.escape(self.state.settings.PUBLIC_URL.rstrip("/") + path)}">{html.escape(label)}</a>'

    async def reply(self, command, argument, telegram_id):
        if command == "/start" and argument.startswith("l"):
            owner = await consume_link(self.state.database, argument, telegram_id)
            if owner:
                return f"✅ Telegram подключен к аккаунту <b>{html.escape(owner['fio'])}</b>.\n\nТеперь /points покажет ваши баллы, а /calendar отметит события, куда вы записаны."
            return "Ссылка для подключения устарела или уже использована, либо Telegram уже подключен. Получите новую ссылку в профиле на сайте."
        if command in ("/start", "/help"):
            lines = [
                "👋 <b>Клуб выпускников факультета права НИУ ВШЭ</b>"
                if command == "/start"
                else "ℹ️ <b>Команды бота</b>",
                "",
                "/points - баллы и уровень",
                "/calendar - ближайшие события",
                "/cabinet - личный кабинет",
                "/dpo - программы обучения",
                "/support - обратиться в поддержку",
                "/help - подсказка",
                "",
                "Можно написать вопрос о клубе или ДПО, бот ответит по материалам сайта.",
            ]
            if command == "/start":
                lines.extend(
                    [
                        "",
                        "🎓 "
                        + self.link(
                            "/join" + ("?ref=" + quote(argument, safe="") if argument else ""), "Вступить в клуб"
                        ),
                    ]
                )
                if not await self.alumni(telegram_id):
                    lines.append(
                        "Чтобы /points показывал ваши данные, нажмите «Подключить Telegram» в профиле на сайте клуба."
                    )
            lines.extend(["", "🌐 " + self.link("/", "Сайт клуба")])
            return "\n".join(lines)
        if command == "/points":
            alumni = await self.alumni(telegram_id)
            if not alumni:
                return (
                    "🔒 <b>Telegram не подключен</b>\n\nОткройте "
                    + self.link("/lk/profile", "профиль")
                    + " и нажмите «Подключить Telegram». После этого /points покажет баллы, уровень и скидку."
                )
            name = html.escape(alumni["fio"] or "Выпускник")
            if alumni["verification_status"] != "verified":
                return (
                    "⏳ <b>Профиль на проверке</b>\n\n"
                    + name
                    + ", мы еще проверяем ваш статус выпускника.\nПосле подтверждения здесь появятся баллы и уровень.\n\n🌐 "
                    + self.link("/lk", "Личный кабинет")
                )
            info = level_info(alumni["points_cached"] or 0, alumni["personal_discount"] or 0)
            lines = [
                f"📊 <b>{name}</b>",
                "",
                f"Уровень: <b>{html.escape(info['level_title'])}</b>",
                f"Баллы: <b>{info['points']}</b>",
                f"Скидка выпускника: <b>{info['discount']}%</b>",
            ]
            if info["next_level"]:
                lines.append(f"До уровня «{html.escape(info['next_level'])}» осталось баллов: {info['to_next']}")
            return "\n".join([*lines, "", "🌐 " + self.link("/lk", "Открыть кабинет")])
        if command == "/calendar":
            events = await self.state.store.read(
                "events",
                filters={"status": {"_eq": "published"}, "starts_at": {"_gte": datetime.now(UTC)}},
                fields=("id", "title", "starts_at", "location", "format", "reg_url"),
                sort=("starts_at",),
                limit=5,
            )
            alumni = await self.alumni(telegram_id)
            rsvps = (
                await self.state.store.read(
                    "event_rsvps",
                    filters={"event_id": {"_in": [event["id"] for event in events]}},
                    fields=("event_id", "alumni_id"),
                    limit=-1,
                )
                if events
                else []
            )
            lines = (
                ["📅 <b>Ближайшие события</b>", ""]
                if events
                else ["📅 <b>Календарь клуба</b>", "", "Ближайших событий пока нет.", ""]
            )
            for event in events:
                going = [row for row in rsvps if row["event_id"] == event["id"]]
                mine = alumni and any(row["alumni_id"] == alumni["id"] for row in going)
                place = "онлайн" if event["format"] == "online" else event["location"] or "очно"
                when = parse_date(event["starts_at"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
                lines.extend(
                    [
                        "▸ <b>" + html.escape(event["title"]) + "</b>",
                        f"  {when} · {html.escape(place)} · записались: {len(going)}{' · вы записаны ✓' if mine else ''}",
                    ]
                )
                if event["reg_url"]:
                    lines.append(f'  📝 <a href="{html.escape(event["reg_url"])}">Регистрация</a>')
                lines.append("")
            return "\n".join([*lines, "🌐 " + self.link("/events", "Все события и запись")])
        routes = {
            "/cabinet": ("/lk", "Личный кабинет"),
            "/dpo": ("/dpo", "Программы ДПО"),
            "/support": ("/support", "Обратиться в поддержку"),
        }
        return self.link(*routes[command]) if command in routes else None

    async def handle(self, update):
        if update.get("message_reaction"):
            await record_reaction(self.state, update["message_reaction"], update["update_id"])
            return
        message = update.get("message") or {}
        text, sender, chat = message.get("text"), message.get("from") or {}, message.get("chat") or {}
        if not text or not sender.get("id") or chat.get("type") != "private":
            return
        addressed = re.match(r"/[^\s@]+@([^\s]+)", text.strip())
        if addressed and addressed[1].lower() != self.state.settings.TELEGRAM_BOT_USERNAME.lower():
            return
        command, argument = parse_command(text)
        reply = (
            await self.reply(command, argument, str(sender["id"])) if command else await answer_faq(self.state, text)
        )
        if reply:
            await self.api(
                "sendMessage",
                {"chat_id": chat["id"], "text": reply[:4096], "parse_mode": "HTML", "disable_web_page_preview": True},
            )
        elif command:
            await self.api(
                "sendMessage",
                {
                    "chat_id": chat["id"],
                    "text": "Такой команды нет. Нажмите /help или напишите вопрос о клубе и ДПО.",
                },
            )

    async def safe_handle(self, update):
        try:
            await self.handle(update)
        except Exception:
            logger.error("Не удалось обработать команду Telegram")

    async def commands(self):
        try:
            await self.api("setMyCommands", {"commands": COMMANDS})
            if self.state.settings.APP_ENV == "production":
                await self.api(
                    "setChatMenuButton",
                    {
                        "menu_button": {
                            "type": "web_app",
                            "text": "Клуб",
                            "web_app": {"url": self.state.settings.PUBLIC_URL.rstrip("/") + "/tg"},
                        }
                    },
                )
        except Exception:
            logger.warning("Не удалось настроить меню Telegram")

    async def poll(self):
        try:
            info = await self.api("getWebhookInfo")
            if not isinstance(info, dict) or info.get("url"):
                logger.error("Polling не запущен: проверьте отсутствие webhook")
                return
        except Exception:
            logger.error("Не удалось проверить webhook; polling не запущен")
            return
        offset = 0
        while True:
            try:
                updates = await self.api(
                    "getUpdates",
                    {"offset": offset, "timeout": 25, "allowed_updates": ["message", "message_reaction"]},
                    timeout=30,
                )
                from club_api.modules.telegram.routes import UpdateBody

                for update in updates or []:
                    validated = UpdateBody.model_validate(update).model_dump(by_alias=True, exclude_none=True)
                    await self.handle(validated)
                    offset = validated["update_id"] + 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Не удалось получить или обработать обновление Telegram")
                await asyncio.sleep(5)

    async def stop(self):
        if self.polling:
            self.polling.cancel()
            await asyncio.gather(self.polling, return_exceptions=True)
