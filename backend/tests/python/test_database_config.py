import pytest

from club_api.core.config import Settings
from club_api.core.errors import ApiError
from club_api.db.config import DEFAULT_PORT, database_config
from club_api.db.mysql import Database


def database(url):
    return Database(Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only", CHECKOUT_DATABASE_URL=url))


@pytest.mark.parametrize(
    "url",
    (
        "postgresql://api:synthetic@postgres/club",
        "postgres://api:synthetic@postgres:5432/club",
        "sqlite:///club",
        "mariadb://api:synthetic@mariadb/",
    ),
)
def test_only_mariadb_address_with_database_name_is_accepted(url):
    with pytest.raises(ValueError):
        database_config(url)


def test_mariadb_address_uses_mysql_backend_and_default_port():
    config = database_config("mariadb://api:synthetic@mariadb/club")
    assert config["ENGINE"] == "django.db.backends.mysql"
    assert (config["NAME"], config["HOST"], config["PORT"]) == ("club", "mariadb", DEFAULT_PORT)
    assert config["OPTIONS"]["charset"] == "utf8mb4"


def test_empty_address_disables_database_backend():
    assert database_config("") == {"ENGINE": "django.db.backends.dummy"}


async def test_database_without_address_is_not_configured():
    unconfigured = database("")
    await unconfigured.open()
    assert not unconfigured.pool
    with pytest.raises(ApiError) as error:
        async with unconfigured.connection():
            pass
    assert error.value.status == 503
