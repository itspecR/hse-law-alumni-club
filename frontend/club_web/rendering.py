from pathlib import Path

from django.template import engines
from jinja2 import Environment

from club_web.formatting import (
    FORMAT_LABELS,
    ORDER_STATUS_LABELS,
    PRICE_PENDING,
    date,
    discount_label,
    local_datetime,
    media,
    open_cohorts,
    page_items,
    payment_url,
    plain_title,
    price_or_pending,
    program_price,
    program_start,
    rub,
    safe_url,
)
from club_web.rich_text import rich_text

ROOT = Path(__file__).parent
VERSION_FILE = "assets/version.txt"
published_versions = {}


def published_version(public):
    path = Path(public) / VERSION_FILE
    try:
        stamp = path.stat().st_mtime_ns
    except FileNotFoundError:
        return ""
    cached = published_versions.get(path)
    if cached is None or cached[0] != stamp:
        cached = published_versions[path] = (stamp, path.read_text(encoding="utf-8").strip())
    return cached[1]


def environment(**options):
    options.pop("autoescape", None)
    renderer = Environment(autoescape=True, **options)
    renderer.filters.update(
        rub=rub,
        date=date,
        local_datetime=local_datetime,
        discount_label=discount_label,
        safe_url=safe_url,
        payment_url=payment_url,
        media=media,
        page_items=page_items,
        plain_title=plain_title,
        program_cohorts=open_cohorts,
        program_start=program_start,
        program_price=program_price,
        price_or_pending=price_or_pending,
        rich_text=rich_text,
    )
    renderer.globals.update(
        format_labels=FORMAT_LABELS, order_status_labels=ORDER_STATUS_LABELS, price_pending=PRICE_PENDING
    )
    return renderer


def templates():
    return engines["web"]
