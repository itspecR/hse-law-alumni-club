import base64
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from live_features import (
    content_forms,
    event_actions,
    friends_after_restart,
    home_form,
    member_points_discount,
    member_verification,
    news_sources,
    office_exports,
    order_form,
    profile_tools,
    section_walk,
    support_conversation,
)
from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import expect, sync_playwright
from test_browser import wait_for_offline_page

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_LIVE_AUTHORIZED") != "club-ci-live", reason="Требуется отдельный live-стенд"
)
IMAGE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=")
AUDIO = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(1024)


def value(name):
    return os.environ[name]


def auth(token):
    return {"authorization": "Bearer " + token}


def launch_browser(playwright):
    pin = os.environ.get("E2E_TLS_SPKI", "")
    assert not pin or re.fullmatch(r"[A-Za-z0-9+/]{43}=", pin)
    return playwright.chromium.launch(args=["--ignore-certificate-errors-spki-list=" + pin] if pin else [])


def result(response, status=200):
    assert response.status == status, f"{urlsplit(response.url).path}: {response.status}"
    return response.json()


def mail_link(request, email, path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        inbox = result(request.get(value("E2E_MAIL_URL") + "/api/v1/messages"))
        for message in inbox["messages"]:
            if not any(recipient["Address"] == email for recipient in message["To"]):
                continue
            body = result(request.get(value("E2E_MAIL_URL") + "/api/v1/message/" + message["ID"]))
            for link in re.findall(r"https?://[^\s<>]+", body["Text"]):
                if urlsplit(link).path == path:
                    assert link.startswith(value("E2E_BASE_URL") + "/")
                    return link
        time.sleep(0.25)
    raise AssertionError("Не получено тестовое письмо " + path)


def login(page, email, password, office=False):
    page.goto("/admin" if office else "/lk")
    page.get_by_label("Почта", exact=True).fill(email)
    page.get_by_label("Пароль", exact=True).fill(password)
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/auth/" + ("admin-login" if office else "login"))
            and response.request.method == "POST"
        )
    ) as response:
        page.get_by_role("button", name="Войти" if office else "Войти в кабинет", exact=True).click()
    result(response.value)
    key = "club_admin_token" if office else "club_token"
    expect(page.locator("#page")).not_to_contain_text(
        "Только для сотрудников клуба" if office else "Вход для выпускников"
    )
    token = page.evaluate("key => localStorage.getItem(key)", key)
    assert token
    return token


def media(office, request, token, name):
    headers = auth(token)
    office.goto("/admin/media")
    office.get_by_label("Файл", exact=True).set_input_files(
        {"name": f"pixel-{name}.png", "mimeType": "image/png", "buffer": IMAGE}
    )
    with office.expect_response(
        lambda response: response.url.endswith("/api/admin/media") and response.request.method == "POST"
    ) as uploaded:
        office.get_by_role("button", name="Загрузить", exact=True).click()
    image = result(uploaded.value, 201)
    row = office.locator("article.site-panel").filter(has_text=f"pixel-{name}.png")
    expect(row).to_be_visible()
    expect(row.locator("input[readonly]")).to_have_value("/api/media/" + image["id"])
    row.get_by_role("button", name="Предпросмотр", exact=True).click()
    expect(office.locator("#site-dialog img")).to_be_visible()
    expect(office.locator("#site-dialog img")).to_have_js_property("naturalWidth", 1)
    office.get_by_role("button", name="Закрыть окно", exact=True).click()
    assert request.get("/api/media/" + image["id"]).status == 404
    assert request.get(f"/api/admin/media/{image['id']}/content").status == 401
    assert (
        request.post(
            "/api/admin/media",
            headers=headers,
            multipart={
                "file": {
                    "name": "not-photo.png",
                    "mimeType": "image/png",
                    "buffer": b"<html><script>bad()</script></html>",
                }
            },
        ).status
        == 415
    )
    audio = result(
        request.post(
            "/api/admin/media",
            headers=headers,
            multipart={"file": {"name": "sample.mp3", "mimeType": "audio/mpeg", "buffer": AUDIO}},
        ),
        201,
    )
    news = result(
        request.post(
            "/api/admin/news",
            headers=headers,
            data={
                "title": "Медиа проверка " + name,
                "status": "draft",
                "body": f'<img src="/api/media/{image["id"]}"><a href="/api/media/{audio["id"]}">Аудио</a>',
            },
        )
    )
    assert request.get("/api/media/" + image["id"]).status == 404
    result(request.patch("/api/admin/news/" + news["id"], headers=headers, data={"status": "published"}))
    published = request.get("/api/media/" + image["id"])
    assert published.status == 200 and "image/png" in published.headers["content-type"]
    assert published.body() == IMAGE
    assert request.get("/assets/" + image["id"]).status == 200
    assert request.get("/api/media/" + audio["id"]).status == 404
    office.once("dialog", lambda dialog: dialog.accept())
    with office.expect_response(
        lambda response: (
            response.url.endswith("/api/admin/media/" + image["id"]) and response.request.method == "DELETE"
        )
    ) as blocked:
        row.get_by_role("button", name="Удалить", exact=True).click()
    assert blocked.value.status == 409
    expect(office.locator("#toast")).to_contain_text("Файл используется")
    expect(row).to_be_visible()
    result(request.delete("/api/admin/news/" + news["id"], headers=headers))
    office.once("dialog", lambda dialog: dialog.accept())
    with office.expect_response(
        lambda response: (
            response.url.endswith("/api/admin/media/" + image["id"]) and response.request.method == "DELETE"
        )
    ) as deleted:
        row.get_by_role("button", name="Удалить", exact=True).click()
    result(deleted.value)
    expect(row).to_have_count(0)
    assert request.get(f"/api/admin/media/{image['id']}/content", headers=headers).status == 404
    result(request.delete("/api/admin/media/" + audio["id"], headers=headers))
    assert (
        request.post(
            "/api/admin/news",
            headers=headers,
            data={"title": "Удалённый файл", "body": f'<img src="/api/media/{image["id"]}">'},
        ).status
        == 400
    )


@pytest.mark.parametrize("name,width,height", [("desktop", 1440, 1000), ("mobile", 390, 844)])
def test_live_system(name, width, height):
    state_path = Path(value("E2E_STATE_DIR")) / (name + ".json")
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(
            base_url=value("E2E_BASE_URL"), viewport={"width": width, "height": height}, reduced_motion="reduce"
        )
        context.add_init_script(
            "localStorage.setItem('club_cookie_consent','essential');localStorage.setItem('club_pwa_dismiss','1');"
        )
        page = context.new_page()
        request = context.request
        errors = []
        pending_requests = set()
        page.on("request", lambda request: pending_requests.add(urlsplit(request.url).path))
        page.on("requestfinished", lambda request: pending_requests.discard(urlsplit(request.url).path))
        page.on("requestfailed", lambda request: pending_requests.discard(urlsplit(request.url).path))
        page.on("pageerror", lambda error: errors.append(str(error)))
        assert request.get("/api/ready").status == 200
        assert result(request.get("/api/payments/config"))["enabled"] is False
        if value("E2E_LIVE_PHASE") == "read":
            state = json.loads(state_path.read_text())
            assert request.get("/api/me", headers=auth(state["previousToken"])).status == 401
            assert request.get("/api/admin/overview", headers=auth(state["revokedAdmin"])).status == 401
            assert result(request.get("/api/me", headers=auth(state["token"])))["alumni"]["fio"] == state["name"]
            orders = result(request.get("/api/me/orders", headers=auth(state["token"])))
            assert next(order for order in orders if order["number"] == state["order"])["status"] == "in_progress"
            events = result(request.get("/api/events", headers=auth(state["token"])))
            event = next(event for event in events if event["id"] == state["event"])
            assert event["my_rsvp"] and event["my_attended"]
            assert result(request.get("/api/podcasts", headers=auth(state["token"])))["subscribed"]
            page.add_init_script("localStorage.setItem('club_token'," + json.dumps(state["token"]) + ");")
            page.goto("/lk?section=orders")
            expect(page.locator("#orders")).to_contain_text(state["order"])
            page.screenshot(path=str(state_path.with_suffix(".orders.png")), full_page=True)
            page.goto("/lk/profile")
            expect(page.get_by_label("ФИО", exact=True)).to_have_value(state["name"])
            page.screenshot(path=str(state_path.with_suffix(".profile.png")), full_page=True)
            peer = json.loads(
                (state_path.parent / ("mobile.json" if name == "desktop" else "desktop.json")).read_text()
            )
            friends_after_restart(page, name, peer["member"])
            assert not errors
            browser.close()
            return
        assert value("E2E_LIVE_PHASE") == "write"
        email = f"graduate-{name}@live.example.com"
        person = "Тест Выпускник " + name
        try:
            page.goto("/join")
        except BrowserTimeout:
            pytest.fail("Не завершена загрузка страницы вступления: " + ", ".join(sorted(pending_requests)))
        page.get_by_label("ФИО", exact=True).fill(person)
        page.get_by_label("Почта", exact=True).fill(email)
        page.get_by_label("Год выпуска", exact=True).fill("2020")
        page.get_by_label("Образовательная программа", exact=True).fill("Право")
        page.get_by_label("Пароль, не меньше 8 символов", exact=True).fill(value("E2E_LIVE_PASSWORD"))
        page.locator("input[name=consent_pdn]").check()
        with page.expect_response(
            lambda response: response.url.endswith("/api/auth/register") and response.request.method == "POST"
        ) as registration:
            page.get_by_role("button", name="Отправить заявку", exact=True).click()
        result(registration.value)
        page.goto(mail_link(request, email, "/confirm"))
        with page.expect_response(
            lambda response: response.url.endswith("/api/auth/confirm") and response.request.method == "POST"
        ) as confirmed:
            page.get_by_role("button", name="Подтвердить почту", exact=True).click()
        result(confirmed.value)
        previous_token = login(page, email, value("E2E_LIVE_PASSWORD"))
        assert request.patch("/api/me/profile", headers=auth(previous_token), data={"fio": person}).status == 403
        office = context.new_page()
        office.on("pageerror", lambda error: errors.append(str(error)))
        admin_token = login(office, value("ADMIN_EMAIL"), value("ADMIN_PASSWORD"), office=True)
        headers = auth(admin_token)
        assert result(request.get("/api/auth/admin-session", headers=headers))["role"] in ("admin", "Administrator")
        assert request.get("/api/auth/admin-session", headers=auth(previous_token)).status == 401
        media(office, request, admin_token, name)
        content_forms(office, request, headers, name)
        news_sources(office, request, headers, name, state_path.parent)
        home_form(office, name)
        members = result(request.get("/api/admin/members", headers=headers))["items"]
        member = next(member for member in members if member["email"] == email)
        member_verification(office, member["id"])
        member_points_discount(office, request, headers, auth(previous_token), member["id"])
        updated_name = person + " Проверен"
        page.goto("/lk/profile")
        page.get_by_label("ФИО", exact=True).fill(updated_name)
        with page.expect_response(
            lambda response: response.url.endswith("/api/me/profile") and response.request.method == "PATCH"
        ) as updated:
            page.get_by_role("button", name="Сохранить профиль", exact=True).click()
        result(updated.value)
        page.reload()
        expect(page.get_by_label("ФИО", exact=True)).to_have_value(updated_name)
        profile_tools(page, request, auth(previous_token), IMAGE)
        support_conversation(page, office, request, headers, name)
        program = result(
            request.post(
                "/api/admin/programs",
                headers=headers,
                data={
                    "title": "Локальный курс " + name,
                    "direction": "Право",
                    "format": "online",
                    "duration": "16 часов",
                    "price": 100000,
                    "description": "Синтетическая запись для проверки заявки.",
                    "status": "published",
                },
            )
        )
        page.goto("/dpo")
        course = page.locator("article.site-catalog-card").filter(has_text="Локальный курс " + name)
        with page.expect_response(
            lambda response: response.url.endswith("/api/cart") and response.request.method == "POST"
        ) as added:
            course.get_by_role("button", name="В корзину", exact=True).click()
        result(added.value)
        page.goto("/cart")
        page.get_by_label("ФИО", exact=True).fill(updated_name)
        page.get_by_label("Телефон", exact=True).fill("+7 000 000-00-00")
        page.get_by_label("Почта", exact=True).fill(email)
        page.locator("input[name=consent_pdn]").check()
        with page.expect_response(
            lambda response: response.url.endswith("/api/orders") and response.request.method == "POST"
        ) as submitted:
            page.get_by_role("button", name="Отправить заявку", exact=True).click()
        order = result(submitted.value)
        assert order["total_estimate"] == 95000
        assert order["notified"]["ok"]
        expect(page.get_by_role("heading", name="Заявка отправлена")).to_be_visible()
        page.goto("/lk?section=orders")
        expect(page.locator("#orders")).to_contain_text(order["number"])
        orders = result(request.get("/api/admin/orders", headers=headers))["items"]
        order_id = next(row["id"] for row in orders if row["number"] == order["number"])
        order_form(office, order_id)
        office_exports(office)
        event = result(
            request.post(
                "/api/admin/events",
                headers=headers,
                data={
                    "title": "Тестовая встреча " + name,
                    "starts_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "points": 60,
                    "status": "published",
                },
            )
        )
        event_actions(page, office, event["id"])
        roster = result(request.get(f"/api/admin/events/{event['id']}/rsvps", headers=headers))
        assert len(roster) == 1
        for _ in range(2):
            result(request.post(f"/api/admin/events/rsvp/{roster[0]['id']}/attend", headers=headers, data={}))
        ledger = result(request.get("/api/me/ledger", headers=auth(previous_token)))
        assert sum(row["reason"] == "event" for row in ledger) == 1
        page.goto("/podcasts")
        with page.expect_response(lambda response: response.url.endswith("/api/podcasts/subscribe")) as subscribed:
            page.get_by_role("button", name="Оформить подписку", exact=True).click()
        subscription = result(subscribed.value)
        repeated = result(request.post("/api/podcasts/subscribe", headers=auth(previous_token), data={}))
        assert repeated["number"] == subscription["number"] and repeated["already"]
        office.goto("/admin/members")
        with office.expect_response(
            lambda response: response.url.endswith(f"/api/admin/members/{member['id']}/podcast-sub")
        ) as granted:
            office.locator(f'[data-api="/admin/members/{member["id"]}/podcast-sub"]').click()
        result(granted.value)
        assert result(request.get("/api/podcasts", headers=auth(previous_token)))["subscribed"]
        editor = result(
            request.post(
                "/api/auth/admin-login",
                data={"email": value("TEST_EDITOR_EMAIL"), "password": value("TEST_EDITOR_PASSWORD")},
            )
        )["token"]
        assert result(request.get("/api/auth/admin-session", headers=auth(editor)))["role"] == "editor"
        assert request.get("/api/admin/programs", headers=auth(editor)).status == 200
        assert (
            request.patch(
                f"/api/admin/members/{member['id']}", headers=auth(editor), data={"verification_status": "rejected"}
            ).status
            == 403
        )
        assert request.get("/api/admin/support", headers=auth(editor)).status == 403
        section_walk(page, office)
        page.goto("/lk")
        page.get_by_role("button", name="Выйти", exact=True).click()
        expect(page.get_by_role("heading", name="Вход для выпускников", exact=True)).to_be_visible()
        assert page.evaluate("localStorage.getItem('club_token')") is None
        page.goto("/forgot")
        page.get_by_label("Почта", exact=True).fill(email)
        with page.expect_response(
            lambda response: response.url.endswith("/api/auth/forgot") and response.request.method == "POST"
        ):
            page.get_by_role("button", name="Отправить ссылку", exact=True).first.click()
        page.goto(mail_link(request, email, "/reset"))
        page.get_by_label("Новый пароль, не меньше 8 символов", exact=True).fill(value("E2E_LIVE_NEW_PASSWORD"))
        page.get_by_label("Повторите пароль", exact=True).fill(value("E2E_LIVE_NEW_PASSWORD"))
        with page.expect_response(
            lambda response: response.url.endswith("/api/auth/reset") and response.request.method == "POST"
        ) as reset:
            page.get_by_role("button", name="Сохранить пароль", exact=True).click()
        result(reset.value)
        assert request.get("/api/me", headers=auth(previous_token)).status == 401
        token = login(page, email, value("E2E_LIVE_NEW_PASSWORD"))
        office.goto("/admin")
        expect(office.locator(".office-page-header")).to_be_visible()
        if width <= 900:
            office.get_by_role("button", name="Разделы панели", exact=True).click()
        office.get_by_role("button", name="Выйти", exact=True).click()
        expect(office.get_by_role("heading", name="Вход в панель", exact=True)).to_be_visible()
        assert request.get("/api/admin/overview", headers=headers).status == 401
        state = {
            "member": member["id"],
            "token": token,
            "previousToken": previous_token,
            "revokedAdmin": admin_token,
            "order": order["number"],
            "name": updated_name,
            "product": program["slug"],
            "event": event["id"],
        }
        descriptor = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(state, output)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not errors
        browser.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_live_offline_reading(width):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(base_url=value("E2E_BASE_URL"), viewport={"width": width, "height": 1000})
        context.add_init_script("localStorage.setItem('club_cookie_consent','essential');")
        published_home = context.request.get("/").headers.get("x-club-offline") == "public"
        items = result(context.request.get("/api/news?limit=1"))
        prefix = "/news/"
        if not items:
            items = result(context.request.get("/api/programs"))
            prefix = "/dpo/"
        assert items, "Для офлайн-проверки нужен опубликованный материал"
        item = items[0]
        path = prefix + item["slug"]
        page = context.new_page()
        page.goto(path)
        page.evaluate("navigator.serviceWorker.ready")
        wait_for_offline_page(page, path)
        wait_for_offline_page(page, "/saved")
        if published_home:
            wait_for_offline_page(page, "/")
        page.get_by_role("button", name="Сохранить", exact=True).click()
        context.set_offline(True)
        page.reload()
        expect(page.get_by_role("heading", name=item["title"], exact=True)).to_be_visible()
        expect(page.locator("#offline-notice")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.goto("/saved")
        expect(page.locator("[data-reading-list='saved']")).to_contain_text(item["title"])
        page.goto("/?source=pwa")
        if published_home:
            expect(page.locator("#main")).to_be_visible()
            expect(page.locator("#offline-notice")).to_be_visible()
            assert page.evaluate("document.documentElement.classList.contains('pwa-shell')")
        else:
            expect(page.get_by_role("heading", name="Нет подключения", exact=True)).to_be_visible()
        page.goto("/lk")
        expect(page.get_by_role("heading", name="Нет подключения", exact=True)).to_be_visible()
        context.set_offline(False)
        page.goto(path)
        expect(page.locator("#offline-notice")).to_be_hidden()
        browser.close()
