from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_PORT = 3306
CONNECT_TIMEOUT_SECONDS = 5


def database_config(url):
    if not url:
        return {"ENGINE": "django.db.backends.dummy"}
    parsed = urlsplit(url)
    if parsed.scheme not in ("mysql", "mariadb") or not parsed.path.strip("/"):
        raise ValueError("Некорректный адрес базы данных")
    config = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": parsed.port or DEFAULT_PORT,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "init_command": "SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION,ANSI_QUOTES',time_zone='+00:00'",
            "isolation_level": "read committed",
        },
    }
    options = parse_qs(parsed.query, strict_parsing=True)
    if set(options) - {"unix_socket"}:
        raise ValueError("Неподдерживаемые параметры подключения MariaDB")
    if socket := options.get("unix_socket"):
        if len(socket) != 1 or not PurePosixPath(socket[0]).is_absolute() or parsed.hostname != "localhost":
            raise ValueError("Для сокета MariaDB нужен localhost и абсолютный путь")
        config["OPTIONS"]["unix_socket"] = socket[0]
    return config
