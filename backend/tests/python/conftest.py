import os
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from club_api.core.config import Settings
from club_api.main import create_app


@pytest_asyncio.fixture
async def database_app(tmp_path, django_db_blocker):
    url = os.environ.get("CLUB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Нужна одноразовая база из сценария интеграционных тестов")
    if urlsplit(url).path != "/django_migration_test" or urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Интеграционные тесты разрешены только в локальной временной БД")
    settings = Settings(
        AUTH_SECRET="synthetic-session-secret-for-tests-only",
        ADMIN_AUTH_SECRET="synthetic-independent-admin-secret-for-tests",
        CHECKOUT_DATABASE_URL=url,
        UPLOADS_PATH=tmp_path,
        JOBS_ENABLED="false",
    )
    app = create_app(settings)
    with django_db_blocker.unblock():
        async with app.lifespan(app):
            yield app
            if app.state.database.vendor == "mysql":
                from django.apps import apps

                async with app.state.database.connection() as connection:
                    await connection.execute("SET FOREIGN_KEY_CHECKS=0")
                    try:
                        for model in apps.get_app_config("club_data").get_models():
                            await connection.execute(f"TRUNCATE TABLE `{model._meta.db_table}`")
                    finally:
                        await connection.execute("SET FOREIGN_KEY_CHECKS=1")
            else:
                await app.state.database.execute(
                    "TRUNCATE directus_roles,directus_users,directus_files,alumni,programs,program_trainers,trainers,products,news,orders,carts,events,podcasts,podcast_plays,timeline_items,pages,points_ledger,levels,point_rules,achievements,club_settings,club_support_tickets,club_faq_events,club_auth_revocations,club_mail_outbox,club_checkout_commits,audit_log CASCADE"
                )
