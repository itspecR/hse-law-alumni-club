from pathlib import Path

from django.template import engines
from jinja2 import Environment

from club_web.formatting import FORMAT_LABELS, date, media, page_items, payment_url, plain_title, rub, safe_url

ROOT = Path(__file__).parent


def environment(**options):
    options.pop("autoescape", None)
    renderer = Environment(autoescape=True, **options)
    renderer.filters.update(
        rub=rub,
        date=date,
        safe_url=safe_url,
        payment_url=payment_url,
        media=media,
        page_items=page_items,
        plain_title=plain_title,
    )
    renderer.globals.update(format_labels=FORMAT_LABELS)
    return renderer


def templates():
    return engines["web"]
