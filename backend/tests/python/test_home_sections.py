import pytest
from pydantic import ValidationError
from test_community_integration import account
from test_portfolio_applications import client_for

from club_api.modules.content.admin import PageBody
from club_api.modules.content.home_sections import (
    SECTION_LEDE_LIMIT,
    SECTION_TITLE_LIMIT,
    SECTIONS,
    resolve_sections,
)

HOME = {"slug": "home", "title": "Главная", "status": "published"}
ABOUT = {"slug": "about", "title": "О клубе", "status": "published"}


def keys(sections):
    return [section["key"] for section in sections]


def test_without_saved_layout_sections_keep_code_order_and_read_is_hidden():
    sections = resolve_sections(None)
    assert keys(sections) == list(SECTIONS)
    assert [section["key"] for section in sections if not section["visible"]] == ["read"]
    assert all(section["title"] is None and section["lede"] is None for section in sections)


def test_saved_order_comes_first_and_missing_or_unknown_sections_are_handled():
    saved = [
        {"key": "people", "visible": False, "title": "Наши люди"},
        {"key": "gone"},
        {"key": "join", "lede": "Текст"},
    ]
    sections = resolve_sections(saved)
    assert keys(sections)[:2] == ["people", "join"] and set(keys(sections)) == set(SECTIONS)
    assert sections[0] == {"key": "people", "visible": False, "title": "Наши люди", "lede": None}
    assert sections[1]["lede"] == "Текст" and sections[-1]["key"] == "give"


def test_stored_texts_of_fixed_sections_are_ignored():
    [campaign] = [
        item for item in resolve_sections([{"key": "campaign", "title": "Свой"}]) if item["key"] == "campaign"
    ]
    assert campaign["title"] is None


@pytest.mark.parametrize(
    "sections",
    [
        [{"key": "nowhere"}],
        [{"key": "join"}, {"key": "join"}],
        [{"key": "campaign", "title": "Заголовок сбора"}],
        [{"key": "people", "lede": "Вступление"}],
        [{"key": "join", "title": "а" * (SECTION_TITLE_LIMIT + 1)}],
        [{"key": "join", "lede": "а" * (SECTION_LEDE_LIMIT + 1)}],
    ],
)
def test_page_body_rejects_wrong_sections(sections):
    with pytest.raises(ValidationError):
        PageBody.model_validate({"sections": sections})


def test_page_body_accepts_texts_up_to_limits():
    body = PageBody.model_validate(
        {"sections": [{"key": "join", "title": "а" * SECTION_TITLE_LIMIT, "lede": "а" * SECTION_LEDE_LIMIT}]}
    )
    assert body.sections[0].title == "а" * SECTION_TITLE_LIMIT


async def test_staff_arrange_home_sections_and_the_site_reads_them(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    _, alumnus = await account(app)
    layout = [
        {"key": "events", "title": "Встречи."},
        {"key": "read", "visible": True},
        {"key": "goals", "visible": False},
    ]
    async with client_for(app) as client:
        await app.state.store.create("pages", HOME)
        await app.state.store.create("pages", ABOUT)
        before = (await client.get("/pages/home")).json()
        saved = await client.patch("/admin/pages/home", headers=admin, json={"sections": layout})
        denied = await client.patch("/admin/pages/home", headers=alumnus, json={"sections": []})
        elsewhere = await client.patch("/admin/pages/about", headers=admin, json={"sections": layout})
        public = (await client.get("/pages/home")).json()
        office = (await client.get("/admin/pages/home", headers=admin)).json()
        about = (await client.get("/pages/about")).json()
    assert keys(before["sections"]) == list(SECTIONS)
    assert saved.status_code == 200 and denied.status_code in (401, 403) and elsewhere.status_code == 400
    assert keys(public["sections"])[:3] == ["events", "read", "goals"] and public["sections"] == office["sections"]
    assert public["sections"][0]["title"] == "Встречи." and public["sections"][1]["visible"] is True
    assert public["sections"][2]["visible"] is False and len(public["sections"]) == len(SECTIONS)
    assert [spec["key"] for spec in office["section_specs"]] == list(SECTIONS)
    assert "sections" not in about
