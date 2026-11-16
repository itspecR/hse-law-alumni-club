import re

from club_web.formatting import lowest_price, media, open_cohorts
from club_web.seo import PAGES, SITE_NAME, absolute, brief, cover

CONTEXT = "https://schema.org"
LANGUAGE = "ru"
COUNTRY = "RU"
CURRENCY = "RUB"
KOPECKS = 100
LOGO = "/icon-512.png"
TELEGRAM_CHANNEL = "https://t.me/AlumniLawHSE"
FACULTY_NAME = "Факультет права НИУ ВШЭ"
FACULTY_URL = "https://pravo.hse.ru/"
PODCAST_TITLE = "Правовая грамотность"
HOME_LABEL = "Главная"
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOCK = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})")
SECTIONS = {
    "/news": "Новости",
    "/events": "События клуба",
    "/portfolio": "Тренинги",
    "/podcasts": "Подкасты клуба",
    "/merch": "Мерч клуба",
    "/give": "Поддержать факультет",
}
ATTENDANCE = {
    "online": "https://schema.org/OnlineEventAttendanceMode",
    "offline": "https://schema.org/OfflineEventAttendanceMode",
}
EVENT_STATUS = {"canceled": "https://schema.org/EventCancelled"}
EVENT_SCHEDULED = "https://schema.org/EventScheduled"
COURSE_MODE = {"online": "online", "offline": "onsite", "blended": "blended"}
IN_STOCK = "https://schema.org/InStock"
OUT_OF_STOCK = "https://schema.org/OutOfStock"


def compact(node):
    return {key: value for key, value in node.items() if value not in (None, "", [], {})}


def organization_id(site_url):
    return site_url + "/#organization"


def organization(site_url, operator):
    return compact(
        {
            "@type": "Organization",
            "@id": organization_id(site_url),
            "name": SITE_NAME,
            "legalName": operator.get("name"),
            "url": site_url + "/",
            "logo": site_url + LOGO,
            "email": operator.get("email"),
            "taxID": operator.get("inn"),
            "address": compact(
                {"@type": "PostalAddress", "streetAddress": operator.get("address"), "addressCountry": COUNTRY}
            ),
            "sameAs": [TELEGRAM_CHANNEL],
        }
    )


def website(site_url):
    return {
        "@type": "WebSite",
        "@id": site_url + "/#website",
        "name": SITE_NAME,
        "url": site_url + "/",
        "inLanguage": LANGUAGE,
        "publisher": {"@id": organization_id(site_url)},
    }


def picture(data, site_url):
    address = cover(data)
    return absolute(site_url, address) if address else None


def article(data, site_url, url):
    item = data["item"]
    faculty = item.get("origin") == "faculty"
    return compact(
        {
            "@type": "NewsArticle",
            "headline": item["title"],
            "description": brief(item.get("excerpt")),
            "datePublished": item.get("published_at"),
            "image": picture(data, site_url),
            "inLanguage": LANGUAGE,
            "mainEntityOfPage": url,
            "author": {"@type": "Organization", "name": FACULTY_NAME, "url": FACULTY_URL}
            if faculty
            else {"@id": organization_id(site_url)},
            "publisher": {"@id": organization_id(site_url)},
            "isBasedOn": data["meta"]["canonical"] if faculty and data["meta"]["canonical"] != url else None,
        }
    )


def event_place(item):
    if item.get("format") == "online":
        return {"@type": "VirtualLocation", "url": item.get("reg_url")} if item.get("reg_url") else None
    return {"@type": "Place", "name": item["location"], "address": item["location"]} if item.get("location") else None


def event(data, site_url, url):
    item = data["item"]
    return compact(
        {
            "@type": "Event",
            "name": item["title"],
            "description": brief(item.get("description")),
            "startDate": item.get("starts_at"),
            "eventAttendanceMode": ATTENDANCE.get(item.get("format")),
            "eventStatus": EVENT_STATUS.get(item.get("status"), EVENT_SCHEDULED),
            "location": event_place(item),
            "image": picture(data, site_url),
            "url": url,
            "organizer": {"@id": organization_id(site_url)},
        }
    )


def money(kopecks):
    return f"{kopecks / KOPECKS:.2f}"


def offer(kopecks, url, availability=None):
    if not kopecks:
        return None
    return compact(
        {"@type": "Offer", "price": money(kopecks), "priceCurrency": CURRENCY, "url": url, "availability": availability}
    )


def course_runs(item):
    mode = COURSE_MODE.get(item.get("format"))
    return [
        compact({"@type": "CourseInstance", "startDate": cohort["starts_on"], "courseMode": mode})
        for cohort in open_cohorts(item)
        if ISO_DAY.fullmatch(str(cohort.get("starts_on") or ""))
    ]


def course(data, site_url, url):
    item = data["item"]
    return compact(
        {
            "@type": "Course",
            "name": item["title"],
            "description": brief(item.get("description")) or item["title"],
            "url": url,
            "image": picture(data, site_url),
            "inLanguage": LANGUAGE,
            "provider": {"@id": organization_id(site_url)},
            "hasCourseInstance": course_runs(item),
            "offers": offer(lowest_price(item), url),
        }
    )


def series(site_url):
    return {
        "@type": "PodcastSeries",
        "@id": site_url + "/podcasts#series",
        "name": PODCAST_TITLE,
        "description": PAGES["/podcasts"][1],
        "url": site_url + "/podcasts",
        "inLanguage": LANGUAGE,
        "publisher": {"@id": organization_id(site_url)},
    }


def length(value):
    found = CLOCK.fullmatch(str(value or ""))
    if not found:
        return None
    hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return "PT" + (f"{hours}H" if hours else "") + f"{minutes}M{seconds}S"


def episode(data, site_url, url):
    item = data["item"]
    return compact(
        {
            "@type": "PodcastEpisode",
            "name": item["title"],
            "description": brief(item.get("description")),
            "url": url,
            "image": picture(data, site_url),
            "duration": length(item.get("duration")),
            "isAccessibleForFree": bool(item.get("is_free")),
            "partOfSeries": {"@id": site_url + "/podcasts#series"},
        }
    )


def product(data, site_url, url):
    item = data["item"]
    images = item.get("images") if isinstance(item.get("images"), list) else []
    return compact(
        {
            "@type": "Product",
            "name": item["title"],
            "description": brief(item.get("description")),
            "image": [absolute(site_url, media(image)) for image in images if media(image)],
            "brand": {"@id": organization_id(site_url)},
            "offers": offer(item.get("price"), url, IN_STOCK if item.get("stock") else OUT_OF_STOCK),
        }
    )


def questions(data):
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": answer["question"],
                "acceptedAnswer": {"@type": "Answer", "text": answer["text"]},
            }
            for answer in (data.get("faq") or {}).get("answers", [])
        ],
    }


def about(data, site_url, url):
    return {
        "@type": "AboutPage",
        "name": data["about"]["title"],
        "description": data["about"]["meta_description"],
        "url": url,
        "inLanguage": LANGUAGE,
        "about": {"@id": organization_id(site_url)},
    }


def crumb(position, name, url):
    return {"@type": "ListItem", "position": position, "name": name, "item": url}


def trail(data, site_url, url):
    section = next((path for path in SECTIONS if data["path"].startswith(path + "/")), None)
    if not section:
        return None
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            crumb(1, HOME_LABEL, site_url + "/"),
            crumb(2, SECTIONS[section], site_url + section),
            crumb(3, data["item"]["title"], url),
        ],
    }


RECORDS = {
    "article.html": article,
    "events.html": event,
    "portfolio-program.html": course,
    "episode.html": episode,
    "product.html": product,
}


def page_nodes(data, site_url):
    url = data["meta"]["url"]
    item = data.get("item")
    if item and item.get("title") and data["template"] in RECORDS:
        nodes = [RECORDS[data["template"]](data, site_url, url), trail(data, site_url, url)]
        return [series(site_url), *nodes] if data["template"] == "episode.html" else nodes
    if item and item.get("title") and data["template"] == "campaign.html":
        return [trail(data, site_url, url)]
    if data["path"] == "/":
        return [website(site_url)]
    if data["path"] == "/podcasts":
        return [series(site_url)]
    if data["path"] == "/support":
        return [questions(data)]
    if data.get("about"):
        return [about(data, site_url, url)]
    return []


def structured_data(data, site_url):
    nodes = [organization(site_url, data["legal"]["operator"])]
    if data["status"] == 200:
        nodes += [node for node in page_nodes(data, site_url) if node]
    return {"@context": CONTEXT, "@graph": nodes}
