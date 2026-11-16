from club_web import features
from club_web.formatting import address, date, media, program_start, safe_url

SITE_NAME = "Клуб выпускников факультета права НИУ ВШЭ"
PODCAST_NAME = "подкаст «Правовая грамотность»"
PROGRAM_SUFFIX = "программа клуба выпускников факультета права НИУ ВШЭ"
MERCH_SUFFIX = "мерч клуба выпускников факультета права НИУ ВШЭ"
SEPARATOR = " - "
DESCRIPTION_LIMIT = 160
INDEX = "index,follow"
HIDDEN = "noindex,nofollow"
EMPTY_LIST = "noindex,follow"
SHARE_IMAGE = {"path": "/og-card.png", "width": 1200, "height": 630}
FACULTY_ORIGIN = "faculty"
HIDDEN_PREFIXES = (
    "/lk",
    "/admin",
    "/cart",
    "/reset",
    "/confirm",
    "/forgot",
    "/saved",
    "/search",
    "/pay/",
    "/support/consent",
    "/give/thanks",
)
LIST_PATHS = ("/portfolio", "/news", "/merch", "/events", "/podcasts")
PAGES = {
    "/": (
        SITE_NAME,
        "Сообщество выпускников факультета права НИУ ВШЭ. Встречи, программы клуба, подкасты и новости факультета. "
        "Вступить может выпускник после подтверждения статуса.",
    ),
    "/portfolio": (
        "Программы клуба" + SEPARATOR + SITE_NAME,
        "Образовательные программы клуба выпускников факультета права НИУ ВШЭ. "
        "Даты потоков, стоимость и заявка на участие.",
    ),
    "/events": (
        "События клуба" + SEPARATOR + SITE_NAME,
        "Встречи и мероприятия клуба выпускников факультета права НИУ ВШЭ. "
        "Календарь, запись на события и добавление в свой календарь.",
    ),
    "/news": ("Новости" + SEPARATOR + SITE_NAME, "Новости клуба выпускников и факультета права НИУ ВШЭ."),
    "/podcasts": (
        "Подкаст «Правовая грамотность»" + SEPARATOR + SITE_NAME,
        "Подкаст клуба выпускников факультета права НИУ ВШЭ. "
        "Пробный выпуск открыт всем, остальные доступны по подписке на год.",
    ),
    "/merch": (
        "Мерч клуба" + SEPARATOR + SITE_NAME,
        "Вещи с символикой клуба выпускников факультета права НИУ ВШЭ. Каталог и заявка на заказ.",
    ),
    "/give": (
        "Поддержать факультет права НИУ ВШЭ" + SEPARATOR + "Клуб выпускников",
        "Пожертвования клуба выпускников идут на развитие факультета права НИУ ВШЭ: учебный процесс и "
        "исследования, студенты и преподаватели, традиции факультета.",
    ),
    "/join": (
        "Вступить в клуб выпускников факультета права НИУ ВШЭ",
        "Как вступить в клуб: заявка, проверка статуса выпускника и доступ в личный кабинет.",
    ),
    "/support": (
        "Поддержка и частые вопросы" + SEPARATOR + SITE_NAME,
        "Ответы на частые вопросы о клубе выпускников факультета права НИУ ВШЭ и форма обращения в поддержку.",
    ),
    "/documents": (
        "Документы" + SEPARATOR + SITE_NAME,
        "Документы клуба: политика обработки персональных данных, согласия, оферта о пожертвовании и "
        "договор об образовании.",
    ),
    "/requisites": (
        "Реквизиты" + SEPARATOR + SITE_NAME,
        "Реквизиты АНО «Клуб выпускников факультета Права НИУ ВШЭ»: ОГРН, ИНН, КПП и адрес.",
    ),
}


def brief(*parts):
    text = " ".join(" ".join(str(part).split()) for part in parts if part)
    if len(text) <= DESCRIPTION_LIMIT:
        return text
    return text[: DESCRIPTION_LIMIT + 1].rsplit(" ", 1)[0].rstrip(".,;:")


def sentence(text):
    text = " ".join(str(text or "").split())
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def news_texts(item):
    return item["title"] + SEPARATOR + SITE_NAME, brief(item.get("excerpt"))


def event_texts(item):
    day = date(item.get("starts_at"))
    title = ", ".join(part for part in (item["title"], day) if part)
    place = "Место: " + item["location"] if item.get("location") else ""
    return title + SEPARATOR + SITE_NAME, brief(sentence(item.get("description")), sentence(day), sentence(place))


def program_texts(item):
    start = date(program_start(item))
    return item["title"] + SEPARATOR + PROGRAM_SUFFIX, brief(
        sentence(item.get("description")), sentence("Старт " + start if start else "")
    )


def episode_texts(item):
    return item["title"] + SEPARATOR + PODCAST_NAME, brief(item.get("description"))


def product_texts(item):
    return item["title"] + SEPARATOR + MERCH_SUFFIX, brief(item.get("description"))


def campaign_texts(item):
    return item["title"] + SEPARATOR + SITE_NAME, brief(sentence(item.get("subtitle")), sentence(item.get("lede")))


ITEM_TEXTS = {
    "article.html": news_texts,
    "events.html": event_texts,
    "portfolio-program.html": program_texts,
    "episode.html": episode_texts,
    "product.html": product_texts,
    "campaign.html": campaign_texts,
}


def texts(data):
    item = data.get("item")
    if item and item.get("title") and data["template"] in ITEM_TEXTS:
        title, description = ITEM_TEXTS[data["template"]](item)
    elif data["path"] in PAGES and data["status"] == 200:
        title, description = PAGES[data["path"]]
    else:
        title, description = data["title"] + SEPARATOR + SITE_NAME, ""
    return title, description or sentence(data["title"]) + " " + SITE_NAME + "."


def robots(data):
    path = data["path"]
    if data["status"] != 200 or path.startswith(HIDDEN_PREFIXES) or not features.section_enabled(path):
        return HIDDEN
    if path in LIST_PATHS and not data.get("items") and not data.get("item"):
        return EMPTY_LIST
    return INDEX


def page_path(data):
    item = data.get("item")
    if data["template"] == "events.html" and item:
        return "/events/" + address(item)
    return data["path"]


def original(data):
    item = data.get("item") or {}
    if data["template"] == "article.html" and item.get("origin") == FACULTY_ORIGIN:
        return safe_url(item.get("source_url"))
    return ""


def cover(data):
    item = data.get("item") or {}
    images = item.get("images") if isinstance(item.get("images"), list) else []
    return media(item.get("cover")) or next((media(image) for image in images if media(image)), "")


def absolute(site_url, address):
    return address if address.startswith("https://") else site_url + address


def image(data, site_url):
    address = cover(data)
    if address:
        return {"url": absolute(site_url, address)}
    return {"url": site_url + SHARE_IMAGE["path"], "width": SHARE_IMAGE["width"], "height": SHARE_IMAGE["height"]}


def page_meta(data, site_url):
    title, description = texts(data)
    indexing = robots(data)
    url = site_url + page_path(data)
    return {
        "title": title,
        "description": description,
        "robots": indexing,
        "url": url,
        "canonical": "" if indexing == HIDDEN else original(data) or url,
        "type": "article" if data["template"] == "article.html" else "website",
        "site_name": SITE_NAME,
        "image": image(data, site_url),
    }
