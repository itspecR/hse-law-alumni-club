import logging
from datetime import UTC, datetime, timedelta

from club_api.modules.checkout.store import digest
from club_api.resources import shared_data

logger = logging.getLogger("club.support")
FAQ = shared_data("faq-data.json")


def support_config(settings):
    draft = settings.APP_ENV != "production"
    configured = bool(
        settings.SUPPORT_OPERATOR_NAME and settings.SUPPORT_OPERATOR_CONTACT and settings.SUPPORT_OPERATOR_ADDRESS
    )
    enabled = draft or (settings.SUPPORT_ENABLED == "true" and configured)
    operator = (
        f"{settings.SUPPORT_OPERATOR_NAME}, {settings.SUPPORT_OPERATOR_ADDRESS}. Контакт: {settings.SUPPORT_OPERATOR_CONTACT}."
        if configured
        else "Локальный тестовый стенд. Оператор и его реквизиты для публикации ещё не определены. Используйте только вымышленные данные."
    )
    consent = f"{operator} Цель: рассмотрение моего обращения и предоставление ответа в поддержке сайта. Данные: тема и содержание сообщений, включая добровольно указанные мной сведения, технический номер обращения и время отправки. Действия: сбор, запись, хранение, чтение, уточнение и удаление с использованием средств автоматизации. Доступ: уполномоченные сотрудники поддержки. Передача в сторонние чат-сервисы и использование для рекламы не предусмотрены. Срок: {settings.SUPPORT_RETENTION_DAYS} дней после последнего сообщения или закрытия обращения. Отозвать согласие и удалить переписку можно кнопкой «Удалить обращение» с кодом доступа; также можно обратиться к оператору по указанному контакту. Не сообщайте данные третьих лиц, сведения о здоровье, документы и платёжные реквизиты."
    return {
        "enabled": enabled,
        "draft": draft,
        "consent": consent,
        "version": digest(consent),
        "retentionDays": settings.SUPPORT_RETENTION_DAYS,
    }


async def log_faq(state, *, kind, gap_id=None, channel="site"):
    if not state.database.pool:
        return
    try:
        await state.database.execute(
            "INSERT INTO club_faq_events(kind,gap_id,channel) VALUES(%s,%s,%s)", (kind, gap_id, channel)
        )
    except Exception:
        logger.warning("Не удалось сохранить счётчик FAQ")


async def faq_stats(state, since=None):
    result = {"gap_hits": 0, "none_hits": 0, "by_gap": [], "by_channel": []}
    if not state.database.pool:
        return result
    since = since or datetime.now(UTC) - timedelta(days=30)
    try:
        groups = await state.database.rows(
            "SELECT kind,count(*) AS count FROM club_faq_events WHERE created_at>=%s GROUP BY kind",
            (since,),
        )
        counts = {row["kind"]: row["count"] for row in groups}
        result["gap_hits"], result["none_hits"] = counts.get("gap", 0), counts.get("none", 0)
        result["by_gap"] = await state.database.rows(
            "SELECT COALESCE(gap_id,'(без id)') AS gap_id,count(*) AS count FROM club_faq_events WHERE kind='gap' AND created_at>=%s GROUP BY 1 ORDER BY count DESC LIMIT 20",
            (since,),
        )
        result["by_channel"] = await state.database.rows(
            "SELECT channel,count(*) AS count FROM club_faq_events WHERE created_at>=%s GROUP BY channel ORDER BY count DESC",
            (since,),
        )
    except Exception:
        logger.warning("Не удалось получить счётчики FAQ")
    return result
