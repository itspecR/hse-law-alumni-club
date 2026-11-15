import re
from uuid import uuid4

from test_portfolio_applications import client_for, program

from club_api.modules.content.sitemap import robots_text, site_urls, sitemap_xml

SITE = "https://club.example.org"
EMPTY = {"news": [], "portfolio": [], "events": [], "podcasts": [], "products": [], "campaigns": []}


def paths(found):
    return [path for path, *_ in site_urls(found)]


def test_empty_site_lists_only_pages_that_always_have_text():
    assert paths(EMPTY) == ["/", "/join", "/give", "/support", "/documents", "/requisites"]


def test_records_bring_their_section_and_own_pages():
    found = {
        "news": [
            {"slug": "own", "published_at": "2026-11-10T09:00:00Z", "origin": "club"},
            {"slug": "copied", "published_at": "2026-11-11T09:00:00Z", "origin": "faculty"},
        ],
        "portfolio": [{"slug": "talks"}],
        "events": [{"id": "e-1"}],
        "podcasts": [{"id": "p-1"}],
        "products": [{"slug": "bag"}],
        "campaigns": [{"slug": "intensive"}],
    }
    urls = site_urls(found)
    listed = [path for path, *_ in urls]
    for path in ("/portfolio", "/events", "/news", "/podcasts", "/merch"):
        assert path in listed
    for path in ("/news/own", "/portfolio/talks", "/events/e-1", "/podcasts/p-1", "/merch/bag", "/give/intensive"):
        assert path in listed
    assert "/news/copied" not in listed and "/dpo" not in listed and len(listed) == len(set(listed))
    assert dict((path, modified) for path, _, _, modified in urls)["/news/own"] == "2026-11-10"
    assert dict((path, modified) for path, _, _, modified in urls)["/events/e-1"] == ""


def test_section_with_only_faculty_news_still_lists_the_section():
    found = {**EMPTY, "news": [{"slug": "copied", "published_at": None, "origin": "faculty"}]}
    assert "/news" in paths(found) and "/news/copied" not in paths(found)


def test_xml_puts_date_only_where_it_is_known_and_sorts_by_priority():
    xml = sitemap_xml(SITE, [("/news/a&b", "0.6", "monthly", "2026-11-10"), ("/", "1.0", "weekly", "")])
    locations = re.findall(r"<loc>(.*?)</loc>", xml)
    assert locations == [SITE + "/", SITE + "/news/a&amp;b"]
    assert xml.count("<lastmod>") == 1 and "<lastmod>2026-11-10</lastmod>" in xml
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>') and xml.endswith("</urlset>")


def test_robots_opens_media_hides_service_pages_and_names_the_sitemap():
    lines = robots_text(SITE).splitlines()
    assert lines[:3] == ["User-agent: *", "Allow: /", "Allow: /api/media/"]
    for path in ("/api/", "/lk", "/admin", "/cart", "/views/", "/search", "/saved", "/pay/", "/reset", "/forgot"):
        assert "Disallow: " + path in lines
    assert "Disallow: /join" not in lines and "Disallow: /give" not in lines
    assert "Clean-param: utm_source&utm_medium&utm_campaign&utm_content&utm_term&source" in lines
    assert lines[-1] == "Sitemap: " + SITE + "/sitemap.xml"


async def test_sitemap_reads_published_records_from_the_database(database_app):
    app = database_app
    store = app.state.store
    shown = await program(app)
    await program(app, status="draft", slug="hidden-program")
    event = await store.create("events", {"title": "Встреча", "status": "published"})
    await store.create("events", {"title": "Черновик", "status": "draft"})
    await store.create("news", {"slug": "own-news", "title": "Новость", "status": "published", "origin": "club"})
    await store.create("news", {"slug": "copy-news", "title": "Копия", "status": "published", "origin": "faculty"})
    episode = await store.create("podcasts", {"title": "Выпуск", "status": "published"})
    await store.create("products", {"slug": "bag-" + str(uuid4()), "title": "Сумка", "status": "draft"})
    async with client_for(app) as client:
        response = await client.get("/sitemap.xml")
        robots = (await client.get("/robots.txt")).text
    locations = re.findall(r"<loc>(.*?)</loc>", response.text)
    base = app.state.settings.PUBLIC_URL.rstrip("/")
    assert response.headers["content-type"] == "application/xml"
    for path in ("/", "/portfolio", "/portfolio/" + shown["slug"], "/events", "/events/" + event["id"]):
        assert base + path in locations
    assert base + "/news/own-news" in locations and base + "/podcasts/" + episode["id"] in locations
    assert base + "/news/copy-news" not in locations and base + "/portfolio/hidden-program" not in locations
    assert base + "/merch" not in locations and base + "/dpo" not in locations
    assert "Sitemap: " + base + "/sitemap.xml" in robots
