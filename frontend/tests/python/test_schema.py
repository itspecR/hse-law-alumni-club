import json
import re

import pytest
from test_pages import PORTFOLIO
from test_seo import SITE, site

from club_web.schema import length

ORGANIZATION = {"@id": SITE + "/#organization"}


async def graph(path, **replaced):
    async with site(**replaced) as client:
        page = (await client.get(path)).text
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert len(blocks) == 1 and "</" not in blocks[0]
    document = json.loads(blocks[0])
    assert document["@context"] == "https://schema.org"
    return {node["@type"]: node for node in document["@graph"]}


@pytest.mark.parametrize("path", ["/", "/news", "/join", "/lk", "/no-such-page"])
async def test_every_page_names_the_organization(path):
    club = (await graph(path))["Organization"]
    assert club["@id"] == ORGANIZATION["@id"] and club["url"] == SITE + "/"
    assert club["name"] == "Клуб выпускников факультета права НИУ ВШЭ" and club["email"] == "club@pravohse.ru"
    assert club["legalName"].startswith("Автономная некоммерческая организация") and club["taxID"] == "9707041865"
    assert club["address"]["@type"] == "PostalAddress" and club["address"]["addressCountry"] == "RU"
    assert club["logo"] == SITE + "/icon-512.png" and club["sameAs"] == ["https://t.me/AlumniLawHSE"]


async def test_home_describes_the_site_and_missing_page_only_the_organization():
    home = await graph("/")
    assert home["WebSite"]["url"] == SITE + "/" and home["WebSite"]["publisher"] == ORGANIZATION
    assert set(await graph("/no-such-page")) == {"Organization"}


async def test_news_is_marked_as_article_with_breadcrumbs():
    nodes = await graph("/news/news-one")
    article = nodes["NewsArticle"]
    assert article["headline"] == "Новость клуба" and article["datePublished"] == "2026-10-01T09:00:00Z"
    assert article["author"] == ORGANIZATION == article["publisher"] and "isBasedOn" not in article
    assert article["mainEntityOfPage"] == SITE + "/news/news-one"
    assert [(row["position"], row["name"], row["item"]) for row in nodes["BreadcrumbList"]["itemListElement"]] == [
        (1, "Главная", SITE + "/"),
        (2, "Новости", SITE + "/news"),
        (3, "Новость клуба", SITE + "/news/news-one"),
    ]


async def test_faculty_news_names_the_faculty_and_the_original():
    article = (await graph("/news/faculty-one"))["NewsArticle"]
    assert article["author"] == {
        "@type": "Organization",
        "name": "Факультет права НИУ ВШЭ",
        "url": "https://pravo.hse.ru/",
    }
    assert article["isBasedOn"] == "https://pravo.hse.ru/news/1.html"
    assert article["image"] == SITE + "/api/media/11111111-2222-3333-4444-555555555555"


async def test_event_has_time_place_and_status():
    event = (await graph("/events/event-1"))["Event"]
    assert event["name"] == "Встреча клуба" and event["startDate"] == "2026-11-01T12:00:00Z"
    assert event["eventAttendanceMode"] == "https://schema.org/OfflineEventAttendanceMode"
    assert event["eventStatus"] == "https://schema.org/EventScheduled" and event["organizer"] == ORGANIZATION
    assert event["location"] == {
        "@type": "Place",
        "name": "Москва, Покровский бульвар",
        "address": "Москва, Покровский бульвар",
    }
    assert event["url"] == SITE + "/events/event-1"


async def test_online_canceled_event_says_so():
    online = {
        "id": "event-1",
        "title": "Вебинар",
        "starts_at": "2026-11-01T12:00:00Z",
        "status": "canceled",
        "format": "online",
        "reg_url": "https://example.org/room",
    }
    event = (await graph("/events/event-1", **{"/events": [online]}))["Event"]
    assert event["eventStatus"] == "https://schema.org/EventCancelled"
    assert event["eventAttendanceMode"] == "https://schema.org/OnlineEventAttendanceMode"
    assert event["location"] == {"@type": "VirtualLocation", "url": "https://example.org/room"}


async def test_program_is_a_course_with_runs_and_price():
    program = {
        **PORTFOLIO,
        "description": "Практика переговоров.",
        "cohorts": [{"starts_on": "2026-12-01", "price": 3900000}, {"starts_on": "скоро", "price": 0}],
    }
    course = (await graph("/portfolio/club-one", **{"/portfolio/club-one": program}))["Course"]
    assert course["name"] == "Переговоры для юристов" and course["provider"] == ORGANIZATION
    assert course["description"] == "Практика переговоров."
    assert course["hasCourseInstance"] == [
        {"@type": "CourseInstance", "startDate": "2026-12-01", "courseMode": "blended"}
    ]
    assert course["offers"] == {
        "@type": "Offer",
        "price": "39000.00",
        "priceCurrency": "RUB",
        "url": SITE + "/portfolio/club-one",
    }


async def test_program_without_price_has_no_offer():
    course = (await graph("/portfolio/club-one", **{"/portfolio/club-one": {**PORTFOLIO, "price": 0}}))["Course"]
    assert "offers" not in course and course["description"] == "Практический курс."


async def test_podcast_series_and_episode():
    listing = await graph("/podcasts")
    nodes = await graph("/podcasts/episode-1")
    assert listing["PodcastSeries"]["name"] == "Подкасты клуба и факультета"
    assert listing["PodcastSeries"]["url"] == SITE + "/podcasts"
    episode = nodes["PodcastEpisode"]
    assert episode["partOfSeries"] == {"@id": nodes["PodcastSeries"]["@id"]} and episode["duration"] == "PT30M0S"
    assert episode["isAccessibleForFree"] is True and episode["url"] == SITE + "/podcasts/episode-1"


async def test_product_has_price_and_availability():
    product = (await graph("/merch/bag-one"))["Product"]
    assert product["name"] == "Сумка клуба" and product["brand"] == ORGANIZATION
    assert product["offers"]["price"] == "500.00" and product["offers"]["priceCurrency"] == "RUB"
    assert product["offers"]["availability"] == "https://schema.org/InStock"


async def test_support_lists_questions_and_campaign_has_breadcrumbs():
    questions = (await graph("/support"))["FAQPage"]["mainEntity"]
    assert questions[0]["@type"] == "Question" and questions[0]["name"] == "Как вступить в клуб?"
    assert questions[0]["acceptedAnswer"]["text"].startswith("Отправьте заявку на сайте.")
    trail = (await graph("/give/intensive"))["BreadcrumbList"]["itemListElement"]
    assert [row["name"] for row in trail] == ["Главная", "Поддержать факультет", "Искусство влияния"]


@pytest.mark.parametrize(
    ("clock", "iso"), [("30:00", "PT30M0S"), ("1:05:30", "PT1H5M30S"), ("", None), ("полчаса", None), (None, None)]
)
def test_episode_length_becomes_iso_duration(clock, iso):
    assert length(clock) == iso
