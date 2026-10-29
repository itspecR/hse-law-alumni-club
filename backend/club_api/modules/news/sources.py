import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from club_api.modules.news.article import clean

SOURCES = [
    {
        "id": "alumni",
        "title": "Страница клуба на сайте факультета",
        "url": "https://pravo.hse.ru/businessandlaw/alumni",
    },
    {
        "id": "alumni-tag",
        "title": "Новости факультета с темой «выпускники»",
        "url": "https://pravo.hse.ru/news/tags/alumni/",
    },
]
SOURCE_IDS = {source["id"] for source in SOURCES}
LIST_LINKS = ".plate_news__title a[href], .posts .post__content h2 a[href]"
NEWS_URL = re.compile(r"https://pravo\.hse\.ru/(?:[a-z0-9_-]+/)?news/[0-9]+\.html")
MAX_TITLE = 255
MAX_ITEMS = 60


def canonical_news_url(value):
    value = re.split(r"[?#]", value or "")[0]
    return value if NEWS_URL.fullmatch(value) else None


def parse_source(html, url):
    items = {}
    for anchor in BeautifulSoup(html, "html.parser").select(LIST_LINKS):
        link = canonical_news_url(urljoin(url, anchor["href"]))
        title = clean(anchor.get("title") or anchor.get_text(" "))[:MAX_TITLE]
        if link and title:
            items.setdefault(link, {"source_url": link, "title": title})
    return list(items.values())[:MAX_ITEMS]
