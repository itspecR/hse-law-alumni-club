import json
import re
from datetime import datetime
from urllib.parse import urlsplit

CHANNEL = "LegisDigest"
SOURCE_HOSTS = {
    "publication.pravo.gov.ru",
    "pravo.gov.ru",
    "rg.ru",
    "www.rg.ru",
    "pnp.ru",
    "www.pnp.ru",
    "consultant.ru",
    "www.consultant.ru",
    "sozd.duma.gov.ru",
    "duma.gov.ru",
    "government.ru",
    "kremlin.ru",
    "cbr.ru",
    "www.cbr.ru",
}
MAX_ITEMS = 10000
ARCHIVE_VERSIONS = (1, 2)
ARCHIVE_MODES = ("archive", "channel")
ITEM_TEXT_KEYS = ("title", "kind", "number", "topic", "url")
SYNC_KEYS = ("checkedAt", "lastSuccessAt", "lastPostAt")
SYNC_STATUSES = ("archive", "ok", "unavailable")
ID_MAX = 40
TEXT_MAX = 3000
SUMMARY_MAX = 1000
BLOCKS_MAX = 120
SEGMENTS_MAX = 100
SEGMENT_MAX = 10000
MESSAGE_MAX = 20000


def source_url(value):
    if not isinstance(value, str) or len(value) > 3000 or re.search(r"[\x00-\x20\\]", value):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme == "https"
            and parts.hostname in SOURCE_HOSTS
            and not parts.username
            and not parts.password
            and not parts.port
        ):
            return value
    except ValueError:
        pass
    return None


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        return datetime.fromisoformat(value).date().isoformat() == value
    except ValueError:
        return False


def valid_instant(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return valid_date(value[:10])
    except ValueError:
        return False


def check_archive(value):
    if (
        not isinstance(value, dict)
        or value.get("version") not in ARCHIVE_VERSIONS
        or value.get("mode") not in ARCHIVE_MODES
    ):
        raise ValueError("Некорректный архив изменений")
    if (
        not valid_date(value.get("periodFrom"))
        or not valid_date(value.get("periodTo"))
        or value["periodFrom"] > value["periodTo"]
    ):
        raise ValueError("Некорректный период архива")
    items = value.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Некорректные записи архива")


def check_item(item, archive, ids):
    if not isinstance(item, dict):
        raise ValueError("Некорректная запись")
    identifier = item.get("id")
    if not isinstance(identifier, str) or len(identifier) > ID_MAX or identifier in ids:
        raise ValueError("Некорректный номер записи")
    ids.add(identifier)
    if (
        not valid_date(item.get("date"))
        or not valid_date(item.get("published"))
        or not archive["periodFrom"] <= item["published"] <= archive["periodTo"]
        or item.get("effectiveDate") is not None
    ):
        raise ValueError("Некорректные реквизиты записи")
    for key in ITEM_TEXT_KEYS:
        if not isinstance(item.get(key), str) or len(item[key]) > TEXT_MAX:
            raise ValueError("Некорректный текст записи")
    if not item["title"].strip():
        raise ValueError("Нет названия материала")
    kind = "act" if archive["version"] == 1 else item.get("entryType")
    if not isinstance(kind, str) or kind not in ENTRY_CHECKS:
        raise ValueError("Неизвестный тип записи")
    ENTRY_CHECKS[kind](item, identifier)


def check_act(item, identifier):
    if (
        not re.fullmatch(r"\d{16}", identifier)
        or item["url"] != "https://publication.pravo.gov.ru/document/" + identifier
    ):
        raise ValueError("Некорректная ссылка акта")
    item.update(entryType="act", blocks=[], summary="", sourcePublishedAt=None)


def check_digest(item, identifier):
    if (
        not re.fullmatch(r"tg-[1-9]\d{0,11}", identifier)
        or item["url"] != f"https://t.me/{CHANNEL}/{identifier[3:]}"
        or not valid_instant(item.get("sourcePublishedAt"))
        or item["published"] != item["sourcePublishedAt"][:10]
    ):
        raise ValueError("Некорректная ссылка или дата сообщения")
    if (
        not isinstance(item.get("summary"), str)
        or len(item["summary"]) > SUMMARY_MAX
        or not isinstance(item.get("blocks"), list)
        or not 1 <= len(item["blocks"]) <= BLOCKS_MAX
    ):
        raise ValueError("Некорректный текст сообщения")
    if sum(block_size(block) for block in item["blocks"]) > MESSAGE_MAX:
        raise ValueError("Слишком длинное сообщение")


def block_size(block):
    if (
        not isinstance(block, dict)
        or type(block.get("heading")) is not bool
        or not isinstance(block.get("segments"), list)
        or not 1 <= len(block["segments"]) <= SEGMENTS_MAX
    ):
        raise ValueError("Некорректный абзац")
    return sum(segment_size(segment) for segment in block["segments"])


def segment_size(segment):
    if (
        not isinstance(segment, dict)
        or not isinstance(segment.get("text"), str)
        or len(segment["text"]) > SEGMENT_MAX
        or ("url" in segment and not source_url(segment["url"]))
    ):
        raise ValueError("Недопустимый текст или ссылка")
    return len(segment["text"])


def check_sync(result):
    for key in SYNC_KEYS:
        if result.get(key) is not None and not valid_instant(result[key]):
            raise ValueError("Некорректное время обновления")
    if result.get("syncStatus") not in SYNC_STATUSES or (
        result["syncStatus"] == "ok" and not all(result.get(key) for key in SYNC_KEYS)
    ):
        raise ValueError("Некорректный статус обновления")
    if (
        result.get("lastSuccessAt")
        and result.get("checkedAt")
        and datetime.fromisoformat(result["lastSuccessAt"]) > datetime.fromisoformat(result["checkedAt"])
    ):
        raise ValueError("Некорректное время последнего обновления")


ENTRY_CHECKS = {"act": check_act, "digest": check_digest}


def parse_changes(value):
    check_archive(value)
    result = json.loads(json.dumps(value))
    ids = set()
    for item in result["items"]:
        check_item(item, value, ids)
    if value["version"] == 1:
        result.update(version=2, checkedAt=None, lastSuccessAt=None, lastPostAt=None, syncStatus="archive")
    check_sync(result)
    result = {
        key: result.get(key)
        for key in (
            "version",
            "mode",
            "periodFrom",
            "periodTo",
            "checkedAt",
            "lastSuccessAt",
            "lastPostAt",
            "syncStatus",
            "items",
        )
    }
    keys = (
        "id",
        "title",
        "kind",
        "number",
        "date",
        "published",
        "url",
        "topic",
        "effectiveDate",
        "entryType",
        "summary",
        "blocks",
        "sourcePublishedAt",
    )
    result["items"] = [{key: item[key] for key in keys} for item in result["items"]]
    for item in result["items"]:
        item["blocks"] = [
            {
                "heading": block["heading"],
                "segments": [
                    {"text": segment["text"], **({"url": segment["url"]} if "url" in segment else {})}
                    for segment in block["segments"]
                ],
            }
            for block in item["blocks"]
        ]
    return result


def select_changes(items, params):
    words = params.get("q", "").casefold().replace("ё", "е").split()
    start, end = params.get("from"), params.get("to")
    selected = []
    for item in items:
        paragraphs = " ".join(segment["text"] for block in item.get("blocks", []) for segment in block["segments"])
        text = f"{item['title']} {item['number']} {item['summary']} {paragraphs}".casefold().replace("ё", "е")
        if (
            all(word in text for word in words)
            and (not params.get("view") or params["view"] == "all" or item["entryType"] == params["view"])
            and all(not params.get(key) or item[key] == params[key] for key in ("kind", "topic"))
            and (not valid_date(start) or item["published"] >= start)
            and (not valid_date(end) or item["published"] <= end)
        ):
            selected.append(item)
    return sorted(
        selected,
        key=lambda item: (
            item.get("sourcePublishedAt") or item["published"],
            tuple((1, int(part)) if part.isdigit() else (0, part) for part in re.split(r"(\d+)", item["id"])),
        ),
        reverse=params.get("sort") != "oldest",
    )
