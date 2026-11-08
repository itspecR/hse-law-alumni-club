import copy

import pytest

from club_web.changes import parse_changes, select_changes, source_url, valid_date
from club_web.pages import resource
from club_web.sync_changes import FEED, fetch_text, parse_feed, synchronize

NOW = "2026-09-22T10:00:00.000Z"


def seed():
    return parse_changes(
        {"version": 1, "mode": "archive", "periodFrom": "2026-07-04", "periodTo": "2026-07-04", "items": []}
    )


def html(text=None, identifier=17):
    text = (
        text
        or '<b>Что меняется:</b><br>Текст справки о налогах. <a href="http://publication.pravo.gov.ru/document/0001202607040026">Официальный текст</a>'
    )
    return f'<div class="tgme_widget_message" data-post="LegisDigest/{identifier}"><div class="tgme_widget_message_text"><b>Обзор законодательства за неделю</b><br><br>{text}</div><a class="tgme_widget_message_date"><time datetime="2026-09-07T06:17:51+00:00"></time></a></div>'


def archive():
    data = resource("changes.json")
    return {
        "version": 1,
        "mode": "archive",
        "periodFrom": "2026-06-30",
        "periodTo": "2026-07-06",
        "items": [item for item in data["items"] if item["entryType"] == "act"],
    }


def test_archive_strips_unpublished_fields():
    raw = archive()
    raw["items"][0]["draft"] = "Не публиковать"
    parsed = parse_changes(raw)
    assert len(parsed["items"]) == 65
    assert "draft" not in parsed["items"][0]


@pytest.mark.parametrize("change", ["duplicate", "date", "effective", "url"])
def test_archive_rejects_invalid_record(change):
    raw = archive()
    if change == "duplicate":
        raw["items"].append(copy.deepcopy(raw["items"][0]))
    elif change == "date":
        raw["items"][0]["date"] = "2026-02-31"
    elif change == "effective":
        raw["items"][0]["effectiveDate"] = "2026-07-04"
    else:
        raw["items"][0]["url"] = "https://evil.test/document/1"
    with pytest.raises(ValueError):
        parse_changes(raw)


@pytest.mark.parametrize("kind", [None, "law", {}, []])
def test_rejects_unknown_entry_type(kind):
    raw = copy.deepcopy(resource("changes.json"))
    raw["items"][0]["entryType"] = kind
    with pytest.raises(ValueError, match="Неизвестный тип записи"):
        parse_changes(raw)


def test_archive_combined_filters():
    data = parse_changes(archive())
    found = select_changes(data["items"], {"q": "237-ФЗ", "from": "2026-07-04", "to": "2026-07-04"})
    assert len(found) == 1
    assert "акционерных обществах" in found[0]["title"]
    assert not select_changes(data["items"], {"q": "237-ФЗ", "to": "2026-07-03"})


def test_feed_preserves_source_paragraphs_and_attribution():
    page = parse_feed(html())
    item = page["posts"][0]
    assert item["id"] == "tg-17"
    assert item["url"] == "https://t.me/LegisDigest/17"
    assert item["blocks"][0]["heading"]
    assert any(
        segment.get("url", "").startswith("https://publication.pravo.gov.ru/")
        for segment in item["blocks"][1]["segments"]
    )


def test_feed_does_not_execute_html_and_decodes_once():
    item = parse_feed(
        html(
            '<script>throw new Error("executed")</script>Что меняется: &lt;script&gt;текст&lt;/script&gt; &amp;lt;b&amp;gt; <a href="javascript:alert(1)">безопасный текст</a> <a href="https://rg.ru.evil.test/">чужая ссылка</a>'
        )
    )["posts"][0]
    segments = [segment for block in item["blocks"] for segment in block["segments"]]
    text = "".join(segment["text"] for segment in segments)
    assert "<script>текст</script>" in text
    assert "&lt;b&gt;" in text
    assert "executed" not in text
    assert all("url" not in segment for segment in segments)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@rg.ru/article",
        "https://rg.ru:443/article",
        "https://rg.ru.evil.test/article",
        "https://rg.ru:bad/article",
        "javascript:alert(1)",
        "https://rg.ru\\@evil.test",
    ],
)
def test_source_link_boundary(url):
    assert source_url(url) is None


def test_feed_neighboring_posts_and_invalid_markup():
    assert len(parse_feed(html() + html(identifier=18))["posts"]) == 2
    for body in (
        html() + html(),
        html().replace('datetime="2026-09-07T06:17:51+00:00"', ""),
        html().replace("LegisDigest/17", "Other/17"),
    ):
        with pytest.raises(ValueError):
            parse_feed(body)


async def test_sync_idempotence_edits_and_old_records():
    async def original(url):
        return html()

    first = await synchronize(seed(), NOW, original)
    second = await synchronize(first, NOW, original)
    assert first["syncStatus"] == "ok"
    assert first == second

    async def edited(url):
        return html(
            "Исправленное пояснение о налоговых льготах. Текст сохранён из опубликованного сообщения без новой генерации."
        )

    updated = await synchronize(second, NOW, edited)
    assert len(updated["items"]) == 1
    assert len(select_changes(updated["items"], {"q": "налоговых льготах"})) == 1

    async def newer(url):
        return html(identifier=18)

    assert len((await synchronize(updated, NOW, newer))["items"]) == 2


@pytest.mark.parametrize(
    "body",
    [
        "<html>Temporarily unavailable</html>",
        "",
        html().replace("LegisDigest/17", "Other/17"),
        html().replace("2026-09-07T06:17:51", "2026-12-07T06:17:51"),
    ],
)
async def test_sync_failure_preserves_archive(body):
    async def original(url):
        return html()

    previous = await synchronize(seed(), NOW, original)

    async def broken(url):
        return body

    failed = await synchronize(previous, "2026-09-22T11:00:00.000Z", broken)
    assert failed["items"] == previous["items"]
    assert failed["lastSuccessAt"] == NOW
    assert failed["syncStatus"] == "unavailable"


async def test_sync_second_page_failure_is_atomic():
    async def original(url):
        return html()

    previous = await synchronize(seed(), NOW, original)

    async def broken(url):
        if url != FEED:
            raise ValueError("network")
        return html(identifier=19) + '<a class="tme_messages_more" data-before="19"></a>'

    assert (await synchronize(previous, NOW, broken))["items"] == previous["items"]


async def test_fetch_rejects_arbitrary_origin():
    with pytest.raises(ValueError, match="unexpected_fetch_url"):
        await fetch_text("http://127.0.0.1/admin")


@pytest.mark.parametrize("date", ["2026-02-29", "2026-13-01", "2026-02-31", "2026-1-1"])
def test_calendar_rejects_invalid_dates(date):
    assert not valid_date(date)
