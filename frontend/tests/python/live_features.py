import json
from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect


def submit(page, form, path, method="POST", status=200):
    with page.expect_response(
        lambda response: response.url.endswith("/api" + path) and response.request.method == method
    ) as response:
        form.locator("button").click()
    assert response.value.status == status, f"{path}: {response.value.status}"
    if path == "/support" or path.startswith("/admin/news-sources/") and path.endswith("/import"):
        expect(form).to_have_count(0)
    else:
        expect(form.locator("button")).to_be_enabled()
    return response.value.json()


def content_forms(office, request, headers, name):
    definitions = {
        "programs": {"direction": "Право", "duration": "16 часов", "price": "0", "start": "2027-01-10"},
        "products": {"category": "Одежда", "price": "0", "stock": "5", "images": "[]", "variants_json": "[]"},
        "news": {"excerpt": "Краткое описание", "body": "Текст новости для проверки"},
        "events": {"starts_at": (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"), "points": "0"},
        "podcasts": {"duration": "15 минут", "sort": "0"},
        "timeline": {"year": "2026", "text": "Проверка истории клуба", "sort": "0"},
    }
    for section, fields in definitions.items():
        path = "/admin/" + section
        title = f"Проверка формы {section} {name}"
        office.goto(path)
        office.get_by_text("Создать запись", exact=True).click()
        form = office.locator(f'form[data-api="{path}"]')
        form.locator('[name="title"]').fill(title)
        for field, value in fields.items():
            form.locator(f'[name="{field}"]').fill(value)
        form.locator('[name="status"]').select_option("published")
        if section == "podcasts":
            form.locator('[name="is_free"]').check()
        record = submit(office, form, path)
        row = office.locator("article.site-panel").filter(has_text=title)
        expect(row).to_be_visible()
        row.get_by_text("Редактировать", exact=True).click()
        form = row.locator("form")
        for field in ("price", "sort", "points"):
            if field in fields:
                expect(form.locator(f'[name="{field}"]')).to_have_value(fields[field])
        if section == "programs":
            expect(form.locator('[name="start"]')).to_have_value(fields["start"])
        if section == "events":
            expect(form.locator('[name="starts_at"]')).to_have_value(fields["starts_at"])
        edited = title + " Изменено"
        form.locator('[name="title"]').fill(edited)
        submit(office, form, path + "/" + record["id"], "PATCH")
        rows = request.get("/api" + path, headers=headers).json()
        saved = next(item for item in rows if item["id"] == record["id"])
        assert saved["title"] == edited
        row = office.locator("article.site-panel").filter(has_text=edited)
        expect(row).to_be_visible()
        office.once("dialog", lambda dialog: dialog.accept())
        with office.expect_response(
            lambda response, endpoint=path + "/" + record["id"]: (
                response.url.endswith("/api" + endpoint) and response.request.method == "DELETE"
            )
        ) as deleted:
            row.get_by_role("button", name="Удалить", exact=True).click()
        assert deleted.value.status == 200
        expect(row).to_have_count(0)
        assert all(item["id"] != record["id"] for item in request.get("/api" + path, headers=headers).json())


def home_form(office, name):
    office.goto("/admin/pages")
    form = office.locator('form[data-api="/admin/pages/home"]')
    original = {field.get_attribute("name"): field.input_value() for field in form.locator("input").all()}
    form.locator('[name="hero.subtitle"]').fill("Проверка главной " + name)
    submit(office, form, "/admin/pages/home", "PATCH")
    office.goto("/")
    expect(office.locator("#main")).to_contain_text("Проверка главной " + name)
    office.goto("/admin/pages")
    form = office.locator('form[data-api="/admin/pages/home"]')
    for field, value in original.items():
        form.locator(f'[name="{field}"]').fill(value)
    submit(office, form, "/admin/pages/home", "PATCH")


def news_sources(office, request, headers, name, directory):
    office.goto("/admin/news-sources")
    expect(office.locator("#main")).to_contain_text("Тестовая ошибка загрузки источника")
    row = office.locator("article.site-panel").filter(has_text="Скрыть новость " + name)
    expect(row.get_by_role("link", name="Открыть источник", exact=False)).to_have_attribute(
        "href", "https://pravo.hse.ru/news/" + ("1000000001" if name == "desktop" else "1000000003") + ".html"
    )
    with office.expect_response(
        lambda response: "/api/admin/news-sources/" in response.url and response.request.method == "PATCH"
    ) as hidden:
        row.get_by_role("button", name="Скрыть", exact=True).click()
    assert hidden.value.status == 200
    expect(office.locator("article.site-panel").filter(has_text="Скрыть новость " + name)).to_contain_text("Скрыто")
    row = office.locator("article.site-panel").filter(has_text="Опубликовать новость " + name)
    form = row.locator("form")
    title = "Опубликованная новость " + name
    form.get_by_label("Заголовок", exact=True).fill(title)
    path = form.get_attribute("data-api")
    imported = submit(office, form, path)
    expect(office.locator("article.site-panel").filter(has_text="Опубликовать новость " + name)).to_contain_text(
        "Импортировано"
    )
    office.screenshot(path=str(directory / (name + ".news-sources.png")), full_page=True)
    assert all(item["title"] != title for item in request.get("/api/news").json())
    office.goto("/admin/news")
    row = office.locator("article.site-panel").filter(has_text=title)
    row.get_by_text("Редактировать", exact=True).click()
    form = row.locator("form")
    form.locator('[name="status"]').select_option("published")
    submit(office, form, "/admin/news/" + imported["id"], "PATCH")
    published = next(item for item in request.get("/api/news").json() if item["title"] == title)
    office.goto("/news/" + published["slug"])
    expect(office.get_by_role("heading", name=title, exact=True)).to_be_visible()
    assert request.delete("/api/admin/news/" + imported["id"], headers=headers).status == 200


def member_verification(office, member_id):
    office.goto("/admin/members")
    form = office.locator(f'form[data-api="/admin/members/{member_id}"]')
    form.locator('[name="verification_status"]').select_option("verified")
    submit(office, form, "/admin/members/" + member_id, "PATCH")
    expect(office.locator(f'form[data-api="/admin/members/{member_id}"] [name="verification_status"]')).to_have_value(
        "verified"
    )


def member_points_discount(office, request, headers, member_headers, member_id):
    original = request.get("/api/me", headers=member_headers).json()["level"]["discount"]
    form = office.locator(f'form[data-api="/admin/members/{member_id}"]')
    form.locator('[name="personal_discount"]').fill("5")
    submit(office, form, "/admin/members/" + member_id, "PATCH")
    assert request.get("/api/me", headers=member_headers).json()["level"]["discount"] == original + 5
    form = office.locator(f'form[data-api="/admin/members/{member_id}"]')
    form.locator('[name="personal_discount"]').fill("0")
    submit(office, form, "/admin/members/" + member_id, "PATCH")
    assert request.get("/api/me", headers=member_headers).json()["level"]["discount"] == original
    for delta, total in ((12, 12), (-12, 0)):
        office.goto("/admin/members")
        row = office.locator("article.site-panel").filter(
            has=office.locator(f'form[data-api="/admin/members/{member_id}"]')
        )
        row.get_by_text("Начислить или списать баллы", exact=True).click()
        form = row.locator(f'form[data-api="/admin/members/{member_id}/points"]')
        form.get_by_label("Изменение баллов", exact=True).fill(str(delta))
        form.get_by_label("Причина", exact=True).fill("Проверка начисления")
        submit(office, form, "/admin/members/" + member_id + "/points")
        members = request.get("/api/admin/members", headers=headers).json()["items"]
        assert next(item for item in members if item["id"] == member_id)["points_cached"] == total


def friends_after_restart(page, name, other_id):
    page.goto("/lk?section=community")
    row = page.locator("#community article").filter(has=page.locator(f'[data-body*="{other_id}"]'))
    action = "Добавить в друзья" if name == "desktop" else "Принять приглашение"
    with page.expect_response(
        lambda response: response.url.endswith("/api/me/friends") and response.request.method == "POST"
    ) as sent:
        row.get_by_role("button", name=action, exact=True).click()
    assert sent.value.status == 200
    assert sent.value.json()["status"] == ("pending" if name == "desktop" else "accepted")
    row = page.locator("#community article").filter(has=page.locator(f'[data-body*="{other_id}"]'))
    expect(
        row.get_by_role("button", name="Отменить приглашение" if name == "desktop" else "Убрать из друзей", exact=True)
    ).to_be_visible()
    if name == "mobile":
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/me/friends/" + other_id) and response.request.method == "DELETE"
            )
        ) as removed:
            row.get_by_role("button", name="Убрать из друзей", exact=True).click()
        assert removed.value.status == 200
        expect(page.locator(f'#community [data-body*="{other_id}"]')).to_contain_text("Добавить в друзья")


def profile_tools(page, request, headers, image):
    page.goto("/lk/profile")
    form = page.locator('form[data-api="/me/avatar"]')
    form.locator('[name="file"]').set_input_files({"name": "avatar.png", "mimeType": "image/png", "buffer": image})
    submit(page, form, "/me/avatar")
    avatar = page.locator("img.site-avatar")
    expect(avatar).to_be_visible()
    expect(avatar).to_have_js_property("naturalWidth", 1)
    path = avatar.get_attribute("src")
    assert request.get(path).status == 200
    assert request.get("/api/media/" + path.rsplit("/", 1)[1]).status == 404
    form = page.locator('form[data-api="/me/profile"]')
    interest = form.locator('[name="interests"]').first
    selected = interest.get_attribute("value")
    interest.check()
    submit(page, form, "/me/profile", "PATCH")
    assert request.get("/api/me", headers=headers).json()["alumni"]["interests"] == [selected]
    form = page.locator('form[data-api="/me/profile"]')
    form.locator('[name="interests"]').first.uncheck()
    submit(page, form, "/me/profile", "PATCH")
    assert request.get("/api/me", headers=headers).json()["alumni"]["interests"] == []
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Скачать мои данные", exact=True).click()
    exported = json.loads(downloaded.value.path().read_text())
    assert exported["profile"]["fio"] and "password" not in json.dumps(exported)
    with page.expect_response(lambda response: response.url.endswith("/api/me/tg-link")) as link:
        page.get_by_role("button", name="Подключить Telegram", exact=True).click()
    assert link.value.status == 503
    expect(page.locator("#toast")).to_contain_text("Подключение Telegram пока недоступно")


def office_exports(office):
    for path, filename in (("/admin/orders", "orders.csv"), ("/admin/analytics?range=7d", "analytics.csv")):
        office.goto(path)
        with office.expect_download() as downloaded:
            office.get_by_role("button", name="Скачать CSV", exact=True).click()
        assert downloaded.value.suggested_filename == filename
        assert len(downloaded.value.path().read_bytes()) > 20


def section_walk(page, office):
    for target, paths in (
        (
            page,
            [
                "/",
                "/tg",
                "/dpo",
                "/merch",
                "/events",
                "/news",
                "/podcasts",
                "/changes",
                "/saved",
                "/search",
                "/join",
                "/forgot",
                "/reset",
                "/confirm",
                "/privacy",
                "/confidential",
                "/requisites",
                "/support",
                "/support/consent",
                "/cart",
                "/lk",
                "/lk/profile",
            ],
        ),
        (
            office,
            [
                "/admin/" + section
                for section in (
                    "overview",
                    "analytics",
                    "orders",
                    "members",
                    "subs",
                    "programs",
                    "products",
                    "news",
                    "news-sources",
                    "events",
                    "podcasts",
                    "timeline",
                    "pages",
                    "media",
                    "audit",
                    "support",
                )
            ],
        ),
    ):
        for path in paths:
            response = target.goto(path)
            assert response.status == 200, path
            expect(target.locator("#main")).to_be_visible()
            if path.startswith("/admin/"):
                expect(target.locator("#main")).not_to_contain_text("Вход для сотрудников учебного офиса")
            elif path in ("/lk", "/lk/profile"):
                expect(target.locator("#main")).not_to_contain_text("Вход для выпускников")
            assert target.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), path


def order_form(office, order_id):
    office.goto("/admin/orders")
    form = office.locator(f'form[data-api="/admin/orders/{order_id}"]')
    form.locator('[name="status"]').select_option("in_progress")
    submit(office, form, "/admin/orders/" + order_id, "PATCH")
    expect(office.locator(f'form[data-api="/admin/orders/{order_id}"] [name="status"]')).to_have_value("in_progress")


def event_actions(page, office, event_id):
    page.goto("/events/" + event_id)
    button = page.get_by_role("button", name="Записаться", exact=True)
    with page.expect_response(lambda response: response.url.endswith(f"/api/events/{event_id}/rsvp")) as registered:
        button.click()
    assert registered.value.status == 200
    expect(page.get_by_role("button", name="Отменить запись", exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith(f"/api/events/{event_id}/rsvp")) as canceled:
        page.get_by_role("button", name="Отменить запись", exact=True).click()
    assert canceled.value.status == 200
    expect(page.get_by_role("button", name="Записаться", exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith(f"/api/events/{event_id}/rsvp")):
        page.get_by_role("button", name="Записаться", exact=True).click()
    expect(page.get_by_role("button", name="Отменить запись", exact=True)).to_be_visible()
    office.goto("/admin/events?id=" + event_id)
    with office.expect_response(
        lambda response: "/api/admin/events/rsvp/" in response.url and response.url.endswith("/attend")
    ) as attended:
        office.get_by_role("button", name="Отметить участие", exact=True).click()
    assert attended.value.status == 200
    expect(office.get_by_role("button", name="Отметить участие", exact=True)).to_have_count(0)


def support_conversation(page, office, request, headers, name):
    page.goto("/support")
    form = page.locator("form[data-support-create]")
    form.get_by_label("Сообщение", exact=True).fill("Тестовое обращение " + name)
    form.locator('[name="consent"]').check()
    ticket = submit(page, form, "/support")
    expect(page.locator("#support-thread")).to_contain_text("Тестовое обращение " + name)
    access = page.evaluate("JSON.parse(sessionStorage.getItem('club-support-access'))")
    assert request.get("/api/support/" + ticket["id"]).status == 400
    assert request.get("/api/support/" + ticket["id"], headers={"x-support-key": "0" * 64}).status == 404
    office.goto("/admin/support")
    form = office.locator(f'form[data-api="/admin/support/{ticket["id"]}"]')
    form.get_by_label("Ответ", exact=True).fill("Тестовый ответ офиса " + name)
    submit(office, form, "/admin/support/" + ticket["id"], "PATCH")
    page.reload()
    page.get_by_label("Номер обращения", exact=True).fill(access["id"])
    page.get_by_label("Код доступа", exact=True).fill(access["key"])
    page.get_by_role("button", name="Открыть переписку", exact=True).click()
    expect(page.locator("#support-thread")).to_contain_text("Тестовый ответ офиса " + name)
    page.get_by_label("Сообщение в поддержку", exact=True).fill("Спасибо, дополнительный вопрос " + name)
    submit(page, page.locator("[data-support-message]"), "/support/" + ticket["id"] + "/messages")
    expect(page.locator("#support-thread")).to_contain_text("дополнительный вопрос " + name)
    office.goto("/admin/support")
    form = office.locator(f'form[data-api="/admin/support/{ticket["id"]}"]')
    form.locator('[name="status"]').select_option("closed")
    submit(office, form, "/admin/support/" + ticket["id"], "PATCH")
    assert request.get("/api/admin/support", headers=headers).status == 200
    page.get_by_role("button", name="Открыть переписку", exact=True).click()
    expect(page.locator("[data-support-message]")).to_have_count(0)
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith("/api/support/" + ticket["id"]) and response.request.method == "DELETE"
    ) as deleted:
        page.get_by_role("button", name="Удалить обращение", exact=True).click()
    assert deleted.value.status == 200
    expect(page.locator("#support-thread")).to_contain_text("Обращение удалено")
