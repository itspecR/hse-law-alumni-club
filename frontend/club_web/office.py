from club_web.formatting import date, rub

GROUPS = [
    ("Работа клуба", ("overview", "orders", "promo-codes", "members", "subs", "support")),
    (
        "Публикации",
        (
            "portfolio",
            "cohorts",
            "trainers",
            "programs",
            "products",
            "news",
            "news-sources",
            "events",
            "podcasts",
            "timeline",
        ),
    ),
    ("Управление", ("analytics", "pages", "media", "audit")),
]
DESCRIPTIONS = {
    "overview": "Заявки, проверка выпускников и состояние сервисов клуба.",
    "analytics": "Участники, заявки и активность за выбранный период.",
    "orders": "Заявки на программы, мерч и подписки.",
    "promo-codes": "Скидки по коду для программ клуба: срок, лимиты и программы.",
    "members": "Профили выпускников, проверка статуса и возможности клуба.",
    "subs": "Доступ к подкастам и прослушивания выпусков.",
    "portfolio": "Программы клуба: карточки, тренеры, цена и набор.",
    "cohorts": "Потоки программ клуба: старт, цена, места и набор.",
    "trainers": "Тренеры программ клуба: ФИО, фото и справка.",
    "programs": "Каталог программ дополнительного образования.",
    "products": "Товары клуба, варианты и остатки.",
    "news": "Публикации на сайте и черновики.",
    "news-sources": "Автоматическая загрузка новостей с сайта факультета права.",
    "events": "Встречи клуба, запись и отметки об участии.",
    "podcasts": "Выпуски, аудио и доступ по подписке.",
    "timeline": "События и достижения в истории клуба.",
    "pages": "Заголовки, тексты и кнопки главной страницы.",
    "media": "Изображения, аудио и файлы публикаций.",
    "audit": "Действия сотрудников и события системы.",
    "support": "Обращения посетителей и работа бота клуба.",
}
ICONS = {
    "overview": "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    "orders": "M8 3h8v4H8zM8 5H5v16h14V5h-3M8 12h8M8 16h5",
    "promo-codes": "M20 12l-8 8-9-9V3h8l9 9M7.5 7.5h.01",
    "members": "M15 21v-3a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v3M21 21v-3a4 4 0 0 0-3-4M18 3a4 4 0 0 1 0 8M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    "subs": "M3 5h18v14H3zM3 10h18M7 15h4",
    "support": "M21 11a8 8 0 0 1-8 8H7l-4 3V7a4 4 0 0 1 4-4h6a8 8 0 0 1 8 8M7 8h10M7 12h7",
    "portfolio": "M3 7h18v13H3zM8 7V4h8v3M3 12h18M10 12v2h4v-2",
    "cohorts": "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 15h8",
    "trainers": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1",
    "programs": "m2 8 10-5 10 5-10 5-10-5M6 10v7c4 3 8 3 12 0v-7M22 8v8",
    "products": "M5 7h14l1 14H4L5 7M8 7V5a4 4 0 0 1 8 0v2",
    "news": "M4 3h16v18H4zM8 7h8M8 11h8M8 15h5",
    "news-sources": "M4 4v3a13 13 0 0 1 13 13h3A16 16 0 0 0 4 4M4 11v3a6 6 0 0 1 6 6h3a9 9 0 0 0-9-9M4 18h2v2H4z",
    "events": "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 18h2",
    "podcasts": "M8 5a4 4 0 0 1 8 0v6a4 4 0 0 1-8 0V5M5 10v1a7 7 0 0 0 14 0v-1M12 18v4M8 22h8",
    "timeline": "M3 12a9 9 0 1 0 3-7M3 3v6h6M12 7v5l3 2",
    "analytics": "M4 3v18h17M8 17v-5M13 17V8M18 17V4",
    "pages": "M4 3h16v18H4zM4 9h16M10 9v12",
    "media": "M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M14 7h2",
    "audit": "M8 3h8v4H8zM8 5H5v16h14V5h-3M8 12h8M8 16h8",
    "refresh": "M20 7V3h-4M20 7a9 9 0 1 0 1 8",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "logout": "M9 3H4v18h5M9 12h12M17 8l4 4-4 4",
    "arrow": "M7 17 17 7M7 7h10v10",
}

SEARCH_KEYS = ("title", "fio", "code", "note", "direction", "category", "year")
CHOICES = {
    "trainers": ("fio", "Тренеров пока нет."),
    "portfolio": ("title", "Программ портфеля пока нет."),
}
SINGLE_CHOICES = {"program": "portfolio"}
CREATE_DEFAULTS = {"promo-codes": {"per_person_limit": 1, "all_programs": True}}
SECTION_LABELS = {
    "news-sources": {
        "new": "Ожидает загрузки",
        "dismissed": "Не загружается",
        "deleted": "Удалена",
        "archived": "Снята с публикации",
    },
    "news": {"archived": "Снята с публикации", "club": "Клуб", "faculty": "Факультет права"},
    "promo-codes": {"active": "Действует", "disabled": "Отключен"},
    "cohorts": {"open": "Набор открыт", "closed": "Набор закрыт"},
}


def choice_sources(section):
    kinds = [kind for _, _, kind in FIELDS.get(section, [])]
    return [SINGLE_CHOICES.get(kind, kind) for kind in kinds if kind in CHOICES or kind in SINGLE_CHOICES]


def cohort_price_label(row):
    return "цена программы" if row.get("price") is None else rub(row["price"])


def cohort_title(row, names):
    program = names.get("portfolio", {}).get(row.get("program_id"), "Программа удалена")
    return f"{program} · старт {date(row.get('starts_on'))}"


def cohort_closure(row):
    if row.get("open"):
        return ""
    return "старт прошел" if row.get("enrollment") == "actual" else "набор закрыт вручную"


def cohort_record(row, names):
    return {
        **row,
        "title": cohort_title(row, names),
        "status": "open" if row.get("open") else "closed",
        "price_label": cohort_price_label(row),
        "closure": cohort_closure(row),
    }


RECORDS = {"cohorts": cohort_record}
STATUS_FILTERS = {"cohorts": ("open", "closed")}
SECTION_FILTERS = {"news": (("origin", "Откуда", ("club", "faculty")),)}
FILTER_KEYS = ("status", "origin")


def status_values(section):
    kinds = [kind.split("|") for name, _, kind in FIELDS.get(section, []) if name == "status"]
    return STATUS_FILTERS.get(section) or (kinds[0] if kinds else [])


def section_records(section, rows, names):
    record = RECORDS.get(section)
    return [record(row, names) for row in rows] if record else rows


def cohort_options(rows):
    grouped = {}
    for row in rows:
        label = " · ".join(
            ["старт " + date(row.get("starts_on")), cohort_price_label(row)]
            + ([] if row.get("open") else ["набор закрыт"])
        )
        grouped.setdefault(row.get("program_id"), []).append({"id": row.get("id"), "label": label})
    return grouped


def choice_options(source, rows):
    field = CHOICES[source][0]
    return [{"id": row["id"], "label": row[field], "status": row.get("status")} for row in rows]


def filter_content(rows, params):
    needle = params.get("q", "").strip().casefold().replace("ё", "е")
    return [
        row
        for row in rows
        if all(not params.get(key) or row.get(key) == params[key] for key in FILTER_KEYS)
        and (
            not needle
            or needle in " ".join(str(row.get(key) or "") for key in SEARCH_KEYS).casefold().replace("ё", "е")
        )
    ]


FIELDS = {
    "portfolio": [
        ("title", "Название", "text"),
        ("trainer_ids", "Тренеры", "trainers"),
        ("duration", "Продолжительность", "text"),
        ("format", "Формат обучения", "online|offline|blended"),
        ("document", "Документ по окончании", "text"),
        ("price", "Цена, копейки", "number"),
        ("start", "Начало", "text"),
        ("capacity", "Лимит мест", "limit"),
        ("enrollment", "Набор", "actual|nonactual"),
        ("direction", "Направление", "text"),
        ("description", "Описание", "textarea"),
        ("audience", "Для кого, каждый пункт с новой строки", "lines"),
        ("competencies", "Компетенции, каждая с новой строки", "lines"),
        ("communication", "Коммуникация, каждый канал с новой строки", "lines"),
        ("modules", "Программа курса", "modules"),
        ("cover", "Обложка", "text"),
        ("status", "Статус", "published|draft|archived"),
    ],
    "cohorts": [
        ("program_id", "Программа", "program"),
        ("starts_on", "Старт", "date"),
        ("price", "Цена потока в копейках. Пусто - цена программы", "optional"),
        ("capacity", "Лимит мест. Пусто - без лимита", "limit"),
        ("enrollment", "Набор", "actual|nonactual"),
    ],
    "promo-codes": [
        ("code", "Код: латиница, цифры и дефис", "text"),
        ("percent", "Скидка, % (до 50)", "number"),
        ("starts_on", "Действует с", "date"),
        ("ends_on", "Действует по", "date"),
        ("total_limit", "Всего использований. Пусто - без лимита", "limit"),
        ("per_person_limit", "На одного человека. Пусто - без лимита", "limit"),
        ("all_programs", "Все программы портфеля", "checkbox"),
        ("program_ids", "Программы, если не все", "portfolio"),
        ("note", "Служебная заметка", "textarea"),
        ("status", "Статус", "active|disabled"),
    ],
    "trainers": [
        ("fio", "ФИО", "text"),
        ("photo", "Фото", "text"),
        ("bio", "Справка", "textarea"),
        ("status", "Статус", "published|draft|archived"),
    ],
    "programs": [
        ("title", "Название", "text"),
        ("direction", "Направление", "text"),
        ("format", "Формат", "online|offline|blended"),
        ("duration", "Продолжительность", "text"),
        ("price", "Цена, копейки", "number"),
        ("description", "Описание", "textarea"),
        ("cover", "Обложка", "text"),
        ("start", "Начало", "text"),
        ("document", "Документ", "text"),
        ("status", "Статус", "published|draft|archived"),
    ],
    "products": [
        ("title", "Название", "text"),
        ("category", "Категория", "text"),
        ("price", "Цена, копейки", "number"),
        ("stock", "Остаток", "number"),
        ("description", "Описание", "textarea"),
        ("images", "Ссылки на фотографии, JSON", "json"),
        ("variants_json", "Размеры, цвета и остатки, JSON", "json"),
        ("status", "Статус", "published|draft|archived"),
    ],
    "news": [
        ("title", "Заголовок", "text"),
        ("published_at", "Дата и время публикации по Москве. Пусто - сейчас", "datetime-optional"),
        ("excerpt", "Краткое описание", "textarea"),
        ("body", "Текст", "rich"),
        ("cover", "Обложка: ID файла из «Медиа» или ссылка", "text"),
        ("origin", "Откуда новость", "club|faculty"),
        ("source_url", "Ссылка на источник", "text"),
        ("status", "Статус", "published|draft|archived"),
    ],
    "events": [
        ("title", "Название", "text"),
        ("description", "Описание", "textarea"),
        ("starts_at", "Дата и время начала по Москве", "datetime-local"),
        ("location", "Место", "text"),
        ("cover", "Обложка", "text"),
        ("reg_url", "Внешняя регистрация", "text"),
        ("format", "Формат", "offline|online"),
        ("points", "Баллы за участие", "number"),
        ("status", "Статус", "published|draft|done|canceled"),
    ],
    "podcasts": [
        ("title", "Название", "text"),
        ("description", "Описание", "textarea"),
        ("cover", "Обложка", "text"),
        ("audio_url", "Аудиофайл или ссылка", "text"),
        ("video_url", "RuTube", "text"),
        ("duration", "Продолжительность", "text"),
        ("is_free", "Пробный выпуск", "checkbox"),
        ("sort", "Порядок", "number"),
        ("status", "Статус", "published|draft"),
    ],
    "timeline": [
        ("year", "Год", "text"),
        ("title", "Название", "text"),
        ("text", "Текст", "textarea"),
        ("metric", "Показатель", "text"),
        ("sort", "Порядок", "number"),
        ("status", "Статус", "published|draft"),
    ],
}
LABELS = {
    "ok": "Работает",
    "error": "Ошибка",
    "unknown": "Не проверено",
    "disabled": "Не подключено",
    "dpo": "Программы ДПО",
    "portfolio": "Портфель программ",
    "merch": "Мерч",
    "podcast": "Подписка на подкасты",
    "online": "Онлайн",
    "offline": "Очно",
    "blended": "Смешанный",
    "actual": "Набор открыт",
    "nonactual": "Набор закрыт",
    "published": "Опубликовано",
    "draft": "Черновик",
    "archived": "В архиве",
    "done": "Завершено",
    "canceled": "Отменено",
    "new": "Новая",
    "confirmed": "Подтверждена",
    "in_progress": "В работе",
    "expired": "Истек срок резерва",
    "succeeded": "Оплачено",
    "pending": "На проверке",
    "waiting_for_capture": "Ожидает подтверждения",
    "review": "Требует сверки",
    "verified": "Подтверждено",
    "rejected": "Отклонено",
    "open": "Открыто",
    "answered": "Отвечено",
    "closed": "Закрыто",
    "dismissed": "Скрыто",
    "imported": "Импортировано",
}
METRICS = {
    "new_orders": "Новые заявки",
    "orders_count": "Всего заявок",
    "orders_paid": "Оплаченные заявки",
    "pending_verifications": "Ожидают проверки",
    "alumni_count": "Выпускники",
    "alumni_verified": "Подтверждены",
    "points_total": "Баллы клуба",
    "programs_actual": "Открытый набор ДПО",
    "programs_total": "Программы ДПО",
    "products_count": "Товары",
    "news_count": "Новости",
    "friendships": "Дружеские связи",
    "friend_requests": "Приглашения",
    "podcasts_count": "Подкасты",
    "podcast_subscribers": "Подписчики подкастов",
    "push_subs_count": "Уведомления",
    "joins": "Заявки на вступление",
    "verified_in_range": "Проверены за период",
    "registers": "Регистрации",
    "orders_created": "Оформлены заявки",
    "orders_new": "Новые заявки",
    "rsvps": "Записи на события",
    "podcast_plays": "Прослушивания",
    "achievements_granted": "Достижения",
    "friendships_new": "Новые связи",
    "push_subs_new": "Новые подписчики",
    "referrals_ledger": "Баллы за приглашения",
    "referrals_alumni": "Пришли по приглашению",
    "login_ok": "Успешные входы",
    "login_fail": "Неудачные входы",
    "login_locked": "Блокировки входа",
    "support_open": "Открытые обращения",
    "support_created": "Новые обращения",
    "verified_ratio": "Доля проверенных выпускников",
}
