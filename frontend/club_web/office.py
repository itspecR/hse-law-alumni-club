from club_web.formatting import date, rub

GROUPS = [
    ("Работа клуба", ("overview", "orders", "donations", "campaigns", "promo-codes", "members", "subs", "support")),
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
        ),
    ),
    ("Управление", ("analytics", "pages", "media", "audit")),
]
FULL_ADMIN_SECTIONS = frozenset(("donations", "audit"))
PORTFOLIO_BLOCKS = (
    (
        "program-main",
        "Основное",
        ("title", "direction", "trainer_ids", "format", "duration", "start", "capacity", "price", "document"),
    ),
    ("program-about", "Описание", ("description", "audience", "competencies", "communication")),
    ("program-course", "Программа курса", ("modules",)),
    ("program-publish", "Публикация", ("cover", "enrollment", "status")),
)
CAMPAIGN_BLOCKS = (
    (
        "campaign-main",
        "Основное",
        ("title", "subtitle", "slug", "lede", "date_text", "time_text", "place_text", "duration_text"),
    ),
    (
        "campaign-goal",
        "Цель и суммы",
        ("goal_title", "goal_text", "goal_amount", "extra_amount", "invite_threshold", "amounts"),
    ),
    ("campaign-about", "О мероприятии", ("about_title", "about", "highlights", "trainer_ids")),
    (
        "campaign-program",
        "Программа",
        ("program_title", "modules", "feature_note", "feature_title", "feature_text", "results_title", "results"),
    ),
    ("campaign-rules", "Как устроен сбор", ("steps", "faq", "note")),
    ("campaign-letters", "Платеж и приглашение", ("payment_purpose", "invite_subject", "invite_body")),
    ("campaign-publish", "Публикация", ("status",)),
)
PROGRAM_EDITOR_LEDE = (
    "Карточка программы показывает, как она будет выглядеть на сайте. Изменения появятся на сайте после сохранения."
)
CAMPAIGN_EDITOR_LEDE = "Сбор виден на сайте только со статусом «Опубликовано». Изменения появятся после сохранения."
EDITORS = {
    "portfolio": {
        "list": "office/portfolio.html",
        "editor": "office/portfolio-editor.html",
        "blocks": PORTFOLIO_BLOCKS,
        "lede": PROGRAM_EDITOR_LEDE,
        "new": "Новая программа",
        "missing": "Программа не найдена",
    },
    "campaigns": {
        "list": "office/campaigns.html",
        "editor": "office/campaign-editor.html",
        "blocks": CAMPAIGN_BLOCKS,
        "lede": CAMPAIGN_EDITOR_LEDE,
        "new": "Новый сбор",
        "missing": "Сбор не найден",
    },
}
ROW_EDITORS = {
    "modules": {
        "new": "Новый модуль",
        "add": "Добавить модуль",
        "remove": "Удалить модуль",
        "count": "Модулей",
        "required": "Укажите тему каждого модуля.",
        "hours": True,
        "meta": ("hours", "format"),
        "fields": (
            ("title", "Тема", "text", True, ""),
            ("hours", "Часы", "text", False, "Например, 2 ак. ч."),
            ("format", "Формат", "text", False, ""),
            ("points", "Содержание, каждый пункт с новой строки", "lines", True, ""),
        ),
    },
    "schedule": {
        "new": "Новый блок",
        "add": "Добавить блок или перерыв",
        "remove": "Удалить блок",
        "count": "Блоков",
        "required": "Укажите название каждого блока.",
        "hours": False,
        "meta": ("time",),
        "fields": (
            ("title", "Название блока или перерыва", "text", True, ""),
            ("time", "Время", "text", False, "Например, 10:00-11:30"),
            ("note", "Что получит участник", "text", True, ""),
            ("points", "Содержание, каждый пункт с новой строки", "lines", True, ""),
        ),
    },
    "highlights": {
        "new": "Новая цифра",
        "add": "Добавить цифру",
        "remove": "Удалить цифру",
        "count": "Цифр",
        "required": "Укажите значение каждой цифры.",
        "hours": False,
        "meta": ("label",),
        "fields": (
            ("value", "Число или короткое значение", "text", False, "Например, 6"),
            ("label", "Подпись", "text", False, "Например, часов практики"),
        ),
    },
    "steps": {
        "new": "Новый шаг",
        "add": "Добавить шаг",
        "remove": "Удалить шаг",
        "count": "Шагов",
        "required": "Укажите название каждого шага.",
        "hours": False,
        "meta": (),
        "fields": (("title", "Название шага", "text", True, ""), ("text", "Описание", "textarea", True, "")),
    },
    "faq": {
        "new": "Новый вопрос",
        "add": "Добавить вопрос",
        "remove": "Удалить вопрос",
        "count": "Вопросов",
        "required": "Укажите текст каждого вопроса.",
        "hours": False,
        "meta": (),
        "fields": (("question", "Вопрос", "text", True, ""), ("answer", "Ответ", "textarea", True, "")),
    },
}
DESCRIPTIONS = {
    "overview": "Заявки, проверка выпускников и состояние сервисов клуба.",
    "analytics": "Участники, заявки и активность за выбранный период.",
    "orders": "Заявки на программы, мерч и подписки.",
    "donations": "Пожертвования на развитие факультета: оплаты через ЮKassa и итог за период.",
    "campaigns": "Сборы на отдельную цель: страница на сайте, счетчик собранного и приглашение дарителям.",
    "promo-codes": "Скидки по коду для программ клуба: срок, лимиты и программы.",
    "members": "Профили выпускников, проверка статуса и возможности клуба.",
    "subs": "Доступ к подкастам и прослушивания выпусков.",
    "portfolio": "Программы клуба: карточки, тренеры, цена и набор.",
    "cohorts": "Потоки программ клуба: старт, цена, места и набор.",
    "trainers": "Тренеры программ клуба: ФИО, фото и справка.",
    "programs": "Программы Центра ДПО, загружаются с его сайта автоматически.",
    "products": "Товары клуба, варианты и остатки.",
    "news": "Публикации на сайте и черновики.",
    "news-sources": "Автоматическая загрузка новостей с сайта факультета права.",
    "events": "Встречи клуба, запись и отметки об участии.",
    "podcasts": "Выпуски, аудио и доступ по подписке.",
    "pages": "Заголовки, тексты и кнопки главной страницы.",
    "media": "Изображения, аудио и файлы публикаций.",
    "audit": "Действия сотрудников и события системы.",
    "support": "Обращения посетителей и работа бота клуба.",
}

SEARCH_KEYS = ("title", "fio", "code", "note", "direction", "category", "year")
CHOICES = {
    "trainers": ("fio", "Тренеров пока нет."),
    "portfolio": ("title", "Программ портфеля пока нет."),
}
SINGLE_CHOICES = {"program": "portfolio"}
READ_ONLY_SECTIONS = {"programs"}
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


def editor_blocks(section):
    fields = {field[0]: field for field in FIELDS[section]}
    return [(anchor, title, [fields[name] for name in names]) for anchor, title, names in EDITORS[section]["blocks"]]


def visible_groups(full_admin):
    return [
        (group, tuple(key for key in keys if full_admin or key not in FULL_ADMIN_SECTIONS)) for group, keys in GROUPS
    ]


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
        ("price", "Цена, ₽", "rubles"),
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
    "campaigns": [
        ("title", "Название", "text"),
        ("subtitle", "Подзаголовок", "text"),
        ("slug", "Адрес страницы: латиница, цифры и дефис. Страница откроется по адресу /give/адрес", "text"),
        ("lede", "Короткое вступление для первого экрана и блока на главной", "textarea"),
        ("date_text", "Когда", "text"),
        ("time_text", "Время", "text"),
        ("place_text", "Где", "text"),
        ("duration_text", "Длительность", "text"),
        ("goal_title", "Цель сбора", "text"),
        ("goal_text", "Рассказ о цели", "textarea"),
        ("goal_amount", "Сумма цели, ₽", "rubles"),
        ("extra_amount", "Добавить к собранному вручную, ₽. Например, переводы мимо сайта", "rubles"),
        ("invite_threshold", "Порог приглашения, ₽. Приглашение можно отправить дарителю от этой суммы", "rubles"),
        ("amounts", "Суммы в форме, ₽, каждая с новой строки", "ruble-lines"),
        ("about_title", "Заголовок описания", "text"),
        ("about", "Описание", "textarea"),
        ("highlights", "Цифры", "highlights"),
        ("trainer_ids", "Спикеры", "trainers"),
        ("program_title", "Заголовок программы", "text"),
        ("modules", "Программа по блокам", "schedule"),
        ("feature_note", "Надпись над выделенным блоком", "text"),
        ("feature_title", "Заголовок выделенного блока", "text"),
        ("feature_text", "Текст выделенного блока", "textarea"),
        ("results_title", "Заголовок результатов", "text"),
        ("results", "Результаты, каждый с новой строки", "lines"),
        ("steps", "Шаги", "steps"),
        ("faq", "Вопросы и ответы", "faq"),
        ("note", "Пояснение под формой пожертвования", "textarea"),
        ("payment_purpose", "Назначение платежа после слова «Пожертвование». Пусто - на развитие факультета", "text"),
        ("invite_subject", "Тема письма-приглашения", "text"),
        ("invite_body", "Текст письма-приглашения", "textarea"),
        ("status", "Статус", "draft|published|archived"),
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
    "programs": [("status", "Статус", "published|draft|archived")],
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
    "awaiting": "Ожидает перевода",
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
