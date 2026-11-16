import pytest
from club_ops.bootstrap import ABOUT_PAGE
from pydantic import ValidationError
from test_community_integration import account
from test_portfolio_applications import client_for

from club_api.modules.content.admin import PAGE_BODY_LIMIT, PAGE_META_DESCRIPTION_LIMIT, PAGE_TITLE_LIMIT, PageBody

TEXTS = {"title": "Наш клуб", "body": "Первый абзац.", "meta_title": "Клуб в поиске", "meta_description": "Описание"}


@pytest.mark.parametrize(
    "field,limit",
    [
        ("title", PAGE_TITLE_LIMIT),
        ("body", PAGE_BODY_LIMIT),
        ("meta_title", PAGE_TITLE_LIMIT),
        ("meta_description", PAGE_META_DESCRIPTION_LIMIT),
    ],
)
def test_page_texts_have_limits(field, limit):
    assert PageBody.model_validate({field: "а" * limit}).model_dump(exclude_unset=True) == {field: "а" * limit}
    with pytest.raises(ValidationError):
        PageBody.model_validate({field: "а" * (limit + 1)})


async def test_staff_edit_the_about_page_and_the_site_reads_it(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    _, alumnus = await account(app)
    async with client_for(app) as client:
        missing = await client.get("/pages/about")
        await app.state.store.create("pages", ABOUT_PAGE)
        empty = (await client.get("/pages/about")).json()
        saved = await client.patch("/admin/pages/about", headers=admin, json=TEXTS)
        partial = await client.patch("/admin/pages/about", headers=admin, json={"body": "Другой текст."})
        denied = await client.patch("/admin/pages/about", headers=alumnus, json={"title": "Чужая правка"})
        public = (await client.get("/pages/about")).json()
        office = (await client.get("/admin/pages/about", headers=admin)).json()
        sitemap = (await client.get("/sitemap.xml")).text
    assert missing.status_code == 404 and denied.status_code in (401, 403)
    assert (empty["title"], empty["body"], empty["meta_title"]) == ("О клубе", None, None)
    assert saved.status_code == 200 and partial.status_code == 200
    expected = {**TEXTS, "body": "Другой текст."}
    assert {name: public[name] for name in TEXTS} == expected == {name: office[name] for name in TEXTS}
    assert "/about</loc>" in sitemap
