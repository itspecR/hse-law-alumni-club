from pathlib import Path

from django.template import engines
from jinja2 import Environment

from club_web.formatting import (
    FORMAT_LABELS,
    ORDER_STATUS_LABELS,
    date,
    discount_label,
    media,
    open_cohorts,
    page_items,
    payment_url,
    plain_title,
    program_price,
    program_start,
    rub,
    safe_url,
)

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
        discount_label=discount_label,
        safe_url=safe_url,
        payment_url=payment_url,
        media=media,
        page_items=page_items,
        plain_title=plain_title,
        program_cohorts=open_cohorts,
        program_start=program_start,
        program_price=program_price,
    )
    renderer.globals.update(format_labels=FORMAT_LABELS, order_status_labels=ORDER_STATUS_LABELS)
    return renderer


def templates():
    return engines["web"]
