from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.http import HttpRequest

from club_api.core.models import count, parse_date, query_choice, query_page, query_search
from club_api.core.views import api_view
from club_api.db.store import normalize
from club_api.modules.auth.service import require_full_admin
from club_api.modules.checkout.admin import csv_response
from club_api.modules.donations.service import DONATION_TABLE
from club_api.modules.office.analytics import sum_field
from club_api.observability.audit import audit

STATUS_LABELS = {
    "succeeded": "Оплачено",
    "pending": "Ожидает оплаты",
    "review": "На проверке",
    "canceled": "Отменено",
    "failed": "Не создан платеж",
    "new": "Новое",
}
PAID = "succeeded"
PERIOD_DAYS = {"7d": 7, "30d": 30, "365d": 365}
ALL_TIME = "all"
PERIOD_LABELS = {"7d": "7 дней", "30d": "30 дней", "365d": "Год", ALL_TIME: "Все время"}
DEFAULT_PERIOD = "30d"
LOCAL_ZONE = ZoneInfo("Europe/Moscow")
SEARCH_FIELDS = ("number", "email", "name")
LIST_FIELDS = ("number", "created_at", "amount", "status", "name", "email", "alumni_id", "payment_id", "paid_at")
CSV_HEADER = ("Номер", "Дата", "Сумма, ₽", "Статус", "Имя", "Почта", "Выпускник", "Платеж ЮKassa", "Оплачено")


def requested_period(request):
    return query_choice(request, "range", PERIOD_LABELS) or DEFAULT_PERIOD


def period_filter(period, now):
    if period == ALL_TIME:
        return {}
    return {"created_at": {"_gte": normalize(now - timedelta(days=PERIOD_DAYS[period]))}}


def list_filters(request, period, now):
    filters = [period_filter(period, now)]
    status = query_choice(request, "status", STATUS_LABELS)
    search = query_search(request)
    if status:
        filters.append({"status": {"_eq": status}})
    if search:
        filters.append({"_or": [{name: {"_icontains": search}} for name in SEARCH_FIELDS]})
    active = [item for item in filters if item]
    return {"_and": active} if active else None


async def paid_summary(store, period, now):
    paid = {**period_filter(period, now), "status": {"_eq": PAID}}
    return {
        "count": await count(store, DONATION_TABLE, paid),
        "amount": await sum_field(store, DONATION_TABLE, "amount", paid),
    }


@api_view(permission=require_full_admin)
async def donations(request: HttpRequest):
    await require_full_admin(request)
    store, now = request.services.store, datetime.now(UTC)
    page, limit = query_page(request)
    period = requested_period(request)
    where = list_filters(request, period, now)
    return {
        "items": await store.read(
            DONATION_TABLE,
            filters=where,
            fields=LIST_FIELDS,
            sort=("-created_at",),
            limit=limit,
            offset=(page - 1) * limit,
        ),
        "total": await count(store, DONATION_TABLE, where),
        "page": page,
        "limit": limit,
        "range": period,
        "statuses": STATUS_LABELS,
        "periods": PERIOD_LABELS,
        "summary": await paid_summary(store, period, now),
    }


def local_time(value):
    return parse_date(value).astimezone(LOCAL_ZONE).strftime("%d.%m.%Y %H:%M") if value else ""


def rubles(kopecks):
    return f"{kopecks // 100},{kopecks % 100:02d}"


def csv_row(donation):
    return [
        donation["number"],
        local_time(donation["created_at"]),
        rubles(donation["amount"]),
        STATUS_LABELS.get(donation["status"], donation["status"]),
        donation["name"],
        donation["email"],
        "Да" if donation["alumni_id"] else "",
        donation["payment_id"],
        local_time(donation["paid_at"]),
    ]


@api_view(permission=require_full_admin)
async def export_donations(request: HttpRequest):
    admin = await require_full_admin(request)
    period = requested_period(request)
    rows = await request.services.store.read(
        DONATION_TABLE,
        filters=list_filters(request, period, datetime.now(UTC)),
        fields=LIST_FIELDS,
        sort=("-created_at",),
        limit=-1,
    )
    await audit(
        request, "donations.export", actor="admin:" + admin["userId"], detail={"count": len(rows), "range": period}
    )
    return csv_response([CSV_HEADER, *map(csv_row, rows)], "donations")
