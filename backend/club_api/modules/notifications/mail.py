import logging
from email.message import EmailMessage
from urllib.parse import urlsplit

import aiosmtplib

from club_api.db.queries import Query, acquire_lock
from club_api.domain import rubles

logger = logging.getLogger("club.mail")
EMAIL_CONFIRMATION_KIND = "email_confirmation"
CLUB_NAME = "Клуб выпускников факультета права Вышки"


def club_subject(text):
    return f"{text} - {CLUB_NAME}"


def mail_user_lock(user_id):
    return "mail-user:" + str(user_id)


async def confirmation_owner(connection, *, user_id=None, email=None):
    query = (
        "SELECT u.id,u.email FROM directus_users u JOIN directus_roles r ON r.id=u.role WHERE r.name='alumni' AND u.status IN ('active','unverified') AND u.id=%s LIMIT 2"
        if user_id is not None
        else "SELECT u.id,u.email FROM directus_users u JOIN directus_roles r ON r.id=u.role WHERE r.name='alumni' AND u.status IN ('active','unverified') AND lower(u.email)=%s LIMIT 2"
    )
    rows = await (
        await connection.execute(
            query,
            (user_id if user_id is not None else email.lower().strip(),),
        )
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


APPLICANT_MAIL_KIND = "applicant"
MAIL_RETENTION_DAYS = 30


class Notifications:
    def __init__(self, settings, database, client):
        self.settings = settings
        self.database = database
        self.client = client

    @property
    def mail_enabled(self):
        return bool(self.settings.SMTP_HOST)

    async def send_email(self, to, subject, text):
        if not self.mail_enabled:
            logger.warning("SMTP не настроен; письмо не отправлено")
            return False
        message = EmailMessage()
        try:
            message["From"] = self.settings.SMTP_FROM or self.settings.SMTP_USER
            message["To"] = to
            message["Subject"] = subject
            message.set_content(text)
            implicit_tls = self.settings.SMTP_PORT == 465
            local_mailpit = (
                self.settings.SMTP_HOST == "mailpit"
                and self.settings.SMTP_PORT == 1025
                and not self.settings.SMTP_USER
                and not self.settings.secret("SMTP_PASS")
                and urlsplit(self.settings.PUBLIC_URL).hostname in {"localhost", "127.0.0.1", "::1"}
            )
            await aiosmtplib.send(
                message,
                hostname=self.settings.SMTP_HOST,
                port=self.settings.SMTP_PORT,
                username=self.settings.SMTP_USER or None,
                password=self.settings.secret("SMTP_PASS") or None,
                use_tls=implicit_tls,
                start_tls=not implicit_tls and not local_mailpit,
                validate_certs=True,
                timeout=15,
            )
            return True
        except Exception:
            logger.error("Ошибка доставки письма; содержимое и адрес скрыты")
            return False

    async def enqueue_mail(self, to, subject, body, *, kind="office", owner_user_id=None):
        if kind == EMAIL_CONFIRMATION_KIND:
            return await self.enqueue_confirmation(to, subject, body, owner_user_id)
        id, sent = None, None
        if self.database.pool:
            try:
                async with self.database.transaction() as connection:
                    row = await (
                        await connection.execute(
                            "INSERT INTO club_mail_outbox(kind,to_addr,subject,body) VALUES(%s,%s,%s,%s) RETURNING id",
                            (kind, to, subject, body),
                        )
                    ).fetchone()
                    id = row["id"]
                    sent = await self.send_email(to, subject, body)
                    await self.record_delivery(connection, {"id": id, "attempts": 0}, sent)
            except Exception:
                id = None
                logger.error("Не удалось сохранить письмо или статус доставки в очередь")
        if sent is None:
            sent = await self.send_email(to, subject, body)
        return {"id": id, "sent": sent, "blocked": not self.mail_enabled}

    async def mail_applicant(self, to, subject, body):
        return await self.enqueue_mail(to, subject, body, kind=APPLICANT_MAIL_KIND)

    async def enqueue_confirmation(self, to, subject, body, owner_user_id):
        if not owner_user_id or not self.database.pool:
            return {"id": None, "sent": False, "blocked": True}
        async with self.database.transaction() as connection:
            await acquire_lock(connection, mail_user_lock(owner_user_id))
            owner = await confirmation_owner(connection, user_id=owner_user_id)
            if not owner or owner["email"].lower() != to.lower().strip():
                return {"id": None, "sent": False, "blocked": True}
            row = await (
                await connection.execute(
                    "INSERT INTO club_mail_outbox(kind,to_addr,subject,body,owner_user_id) "
                    "VALUES(%s,%s,%s,%s,%s) RETURNING id",
                    (EMAIL_CONFIRMATION_KIND, owner["email"], subject, body, owner_user_id),
                )
            ).fetchone()
            sent = await self.send_email(owner["email"], subject, body)
            await self.record_delivery(connection, {"id": row["id"], "attempts": 0}, sent)
            return {"id": row["id"], "sent": sent, "blocked": not self.mail_enabled}

    async def record_delivery(self, connection, row, sent):
        attempts = row["attempts"] + 1
        if sent:
            await connection.execute(
                "UPDATE club_mail_outbox SET status='sent',attempts=%s,sent_at=now(),last_error=NULL,"
                "body=CASE WHEN kind=%s THEN '' ELSE body END WHERE id=%s",
                (attempts, EMAIL_CONFIRMATION_KIND, row["id"]),
            )
            return "sent"
        if attempts >= self.settings.MAIL_OUTBOX_MAX_ATTEMPTS:
            await connection.execute(
                "UPDATE club_mail_outbox SET status='failed',attempts=%s,last_error='max_attempts',"
                "body=CASE WHEN kind=%s THEN '' ELSE body END WHERE id=%s",
                (attempts, EMAIL_CONFIRMATION_KIND, row["id"]),
            )
            return "failed"
        await connection.execute(
            Query(
                "UPDATE club_mail_outbox SET attempts=%s,next_attempt_at=now() + (%s * interval '1 minute'),last_error=%s WHERE id=%s",
                "UPDATE club_mail_outbox SET attempts=%s,next_attempt_at=now() + INTERVAL %s MINUTE,last_error=%s WHERE id=%s",
            ),
            (attempts, min(60, 5 * attempts), "smtp_fail" if self.mail_enabled else "smtp_missing", row["id"]),
        )
        return "skipped"

    async def drain_mail(self, limit=20):
        result = {"sent": 0, "failed": 0, "skipped": 0}
        if not self.database.pool:
            return result
        rows = await self.database.rows(
            "SELECT id,kind,to_addr,owner_user_id FROM club_mail_outbox "
            "WHERE status='pending' AND next_attempt_at <= now() ORDER BY id LIMIT %s",
            (limit,),
        )
        for row in rows:
            owner_id = row["owner_user_id"]
            if row["kind"] == EMAIL_CONFIRMATION_KIND and not owner_id:
                async with self.database.connection() as connection:
                    owner = await confirmation_owner(connection, email=row["to_addr"])
                    owner_id = owner["id"] if owner else None
            async with self.database.transaction() as connection:
                if row["kind"] == EMAIL_CONFIRMATION_KIND and owner_id:
                    await acquire_lock(connection, mail_user_lock(owner_id))
                current = await (
                    await connection.execute(
                        "SELECT id,kind,to_addr,subject,body,attempts FROM club_mail_outbox "
                        "WHERE id=%s AND status='pending' AND next_attempt_at<=now() FOR UPDATE",
                        (row["id"],),
                    )
                ).fetchone()
                if not current:
                    continue
                if current["kind"] == EMAIL_CONFIRMATION_KIND:
                    owner = await confirmation_owner(connection, user_id=owner_id) if owner_id else None
                    if not owner or owner["email"].lower() != current["to_addr"].lower().strip():
                        await connection.execute("DELETE FROM club_mail_outbox WHERE id=%s", (current["id"],))
                        result["skipped"] += 1
                        continue
                sent = await self.send_email(current["to_addr"], current["subject"], current["body"])
                result[await self.record_delivery(connection, current, sent)] += 1
        return result

    async def telegram_office(self, text):
        settings = self.settings
        if not settings.secret("OFFICE_TG_BOT_TOKEN") or not settings.OFFICE_TG_CHAT_ID:
            return False
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{settings.secret('OFFICE_TG_BOT_TOKEN')}/sendMessage",
                json={"chat_id": settings.OFFICE_TG_CHAT_ID, "text": text},
            )
            return response.is_success and response.json().get("ok") is True
        except Exception:
            return False

    async def office_text(self, text):
        delivered = False
        if self.settings.OFFICE_NOTIFY_CHANNEL in ("email", "both") and self.settings.OFFICE_EMAIL:
            result = await self.enqueue_mail(
                self.settings.OFFICE_EMAIL, "Уведомление для сотрудников клуба", text, kind="office_event"
            )
            delivered = result["sent"]
        if self.settings.OFFICE_NOTIFY_CHANNEL in ("telegram", "both"):
            delivered = await self.telegram_office(text) or delivered
        if not delivered:
            logger.warning("Уведомление сотрудникам не доставлено; проверьте канал и очередь")

    async def order_notice(self, number, summary, total, discount):
        text = f"Новая заявка {number}\n{summary}\nСумма (справочно): {rubles(total)} (скидка −{discount}%)\nКонтакты и подробности в панели управления, раздел «Заявки»."
        results = []
        if self.settings.OFFICE_NOTIFY_CHANNEL in ("email", "both"):
            if self.settings.OFFICE_EMAIL:
                result = await self.enqueue_mail(
                    self.settings.OFFICE_EMAIL, "Новая заявка " + number, text, kind="office_order"
                )
                results.append({"channel": "email", "ok": result["sent"], "blocked": result["blocked"]})
            else:
                results.append({"channel": "email", "ok": False, "blocked": True})
        if self.settings.OFFICE_NOTIFY_CHANNEL in ("telegram", "both"):
            configured = bool(self.settings.secret("OFFICE_TG_BOT_TOKEN") and self.settings.OFFICE_TG_CHAT_ID)
            results.append({"channel": "telegram", "ok": await self.telegram_office(text), "blocked": not configured})
        return {
            "channel": "+".join(result["channel"] for result in results),
            "ok": all(result["ok"] for result in results),
            "blocked": any(result["blocked"] for result in results),
        }
