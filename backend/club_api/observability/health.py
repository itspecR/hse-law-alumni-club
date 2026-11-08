import asyncio
import os
import time
from datetime import UTC, datetime

DATABASE_READY_SQL = (
    "SELECT c.key_hash,s.id,p.path,o.status,o.owner_user_id,f.kind,t.token_hash,r.token_key,a.id,u.password,l.id,m.filename_disk "
    "FROM club_checkout_commits c,club_support_tickets s,club_page_views p,club_mail_outbox o,club_faq_events f,"
    "club_telegram_links t,club_auth_revocations r,alumni a,directus_users u,levels l,directus_files m LIMIT 0"
)


async def build_health(state):
    start = time.monotonic()
    try:
        if not await asyncio.to_thread(os.access, state.settings.UPLOADS_PATH, os.R_OK | os.W_OK):
            raise OSError
        storage = {"id": "storage", "name": "Файлы сайта", "status": "ok", "detail": "Контрольный запрос выполнен"}
    except OSError:
        storage = {
            "id": "storage",
            "name": "Файлы сайта",
            "status": "error",
            "detail": "Контрольный запрос не выполнен",
        }
    storage["latency_ms"] = round((time.monotonic() - start) * 1000)
    database = {"id": "database", "name": "База сайта · MariaDB"}
    if state.database.pool:
        start = time.monotonic()
        try:
            async with asyncio.timeout(3):
                await state.database.rows(DATABASE_READY_SQL)
            database.update(status="ok", detail="Контрольный запрос выполнен")
        except Exception:
            database.update(status="error", detail="Контрольный запрос не выполнен")
        database["latency_ms"] = round((time.monotonic() - start) * 1000)
    else:
        database.update(status="disabled", detail="Подключение не настроено; оформление недоступно")
    checks = [{"id": "api", "name": "API сайта", "status": "ok", "detail": "Обработал этот запрос"}, storage, database]
    for id, name, configured, text in (
        (
            "telegram",
            "Telegram-бот",
            bool(state.settings.secret("TELEGRAM_BOT_TOKEN")),
            "Токен задан; работа обработчика и доставка не проверены",
        ),
        (
            "email",
            "Электронная почта",
            bool(state.settings.SMTP_HOST),
            "SMTP задан; соединение и доставка не проверены",
        ),
        (
            "push",
            "Push-уведомления",
            bool(state.settings.VAPID_PUBLIC_KEY and state.settings.secret("VAPID_PRIVATE_KEY")),
            "Ключи заданы; доставка на устройства не проверена",
        ),
    ):
        checks.append(
            {
                "id": id,
                "name": name,
                "status": "unknown" if configured else "disabled",
                "detail": text if configured else "Не настроено",
            }
        )
    return {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "uptime_seconds": int(time.monotonic() - state.started_at),
        "status": "degraded" if storage["status"] == "error" or database["status"] != "ok" else "partial",
        "checks": checks,
    }
