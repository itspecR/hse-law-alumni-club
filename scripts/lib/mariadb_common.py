import contextlib
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import pathlib
import re
import runpy
import shlex
import socket
import ssl
import subprocess
import tarfile
import time
from urllib.parse import parse_qs, urlsplit

REPO = pathlib.Path(__file__).resolve().parents[2]

HELPERS = runpy.run_path(str(REPO / "scripts/lib/install-config.py"))

IDENTIFIER = re.compile(r"club_[a-z0-9_]{1,48}")

REVISION = re.compile(r"[a-f0-9]{40}")

FILES = ("runtime.env", "operator.env", "compose.env", "operations.env", "initial-admin.txt", "install.json")

NETWORK_ATTEMPTS = 3

NETWORK_PAUSE_SECONDS = 15


def read_env(path):
    path = pathlib.Path(path)
    HELPERS["private_file"](path)
    result = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        result[name] = " ".join(shlex.split(value, comments=True)).replace("$$", "$")
    return result


def run(arguments, **options):
    return subprocess.run(arguments, check=True, **options)


def output(arguments, **options):
    return run(arguments, capture_output=True, text=True, **options).stdout.strip()


def database_name(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("Имя отдельной базы должно начинаться с club_ и содержать только a-z, 0-9 и _")
    return value


def sql(query, database=None):
    args = ["mariadb", "--protocol=socket", "--batch", "--skip-column-names"]
    if database:
        args.append(database_name(database))
    return output(args, input=query)


def connection_url(user, password, database):
    return f"mariadb://{user}:{password}@localhost/{database}?unix_socket=/run/mysqld/mysqld.sock"


def create_database(database, operator, password):
    for value in (database, operator):
        database_name(value)
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise ValueError("Нужен отдельный случайный пароль оператора")
    if sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='" + database + "';") != "0":
        raise ValueError("Целевая база уже существует")
    if sql(f"SELECT COUNT(*) FROM mysql.user WHERE User='{operator}';") != "0":
        raise ValueError("Аккаунт оператора уже существует")
    sql(
        f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n"
        f"CREATE USER '{operator}'@'localhost' IDENTIFIED BY '{password}';\n"
        f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{operator}'@'localhost' WITH GRANT OPTION;\n"
        f"GRANT CREATE USER ON *.* TO '{operator}'@'localhost';\n"
    )


def revision():
    if output(["git", "-C", str(REPO), "status", "--porcelain"]):
        raise ValueError("Checkout содержит изменения; сначала сохраните проверяемую версию")
    value = output(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    if not REVISION.fullmatch(value):
        raise ValueError("Нужна проверенная ревизия Git")
    return value


def directory(path, owner=0, group=0, mode=0o700):
    path = pathlib.Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError("Нужен абсолютный каталог без симлинков")
    if path == REPO or REPO in path.parents:
        raise ValueError("Данные должны находиться вне checkout")
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    os.chown(path, owner, group)
    path.chmod(mode)
    return path


class Context:
    def __init__(self, path):
        self.path = pathlib.Path(path).resolve()
        self.values = read_env(path)
        if self.values.get("CLUB_STACK") != "mariadb":
            raise ValueError("Нужен Compose env-файл контура MariaDB")
        self.runtime = read_env(self.values["CLUB_RUNTIME_ENV"])
        self.operator = read_env(self.values["CLUB_OPERATOR_ENV"])
        public = urlsplit(self.runtime.get("PUBLIC_URL", ""))
        if (
            public.scheme != "https"
            or not public.hostname
            or public.username
            or public.password
            or public.path not in ("", "/")
            or public.query
            or public.fragment
        ):
            raise ValueError("Нужен корневой HTTPS-адрес сайта")
        self.database = database_name(self.values["CLUB_DATABASE"])
        self.project = self.values["CLUB_PROJECT"]
        if not re.fullmatch(r"club-mariadb(?:-[a-z0-9-]{1,40})?", self.project):
            raise ValueError("Нужен отдельный проект club-mariadb-*")
        for values, key in ((self.runtime, "CHECKOUT_DATABASE_URL"), (self.operator, "DATABASE_URL")):
            parsed = urlsplit(values.get(key, ""))
            if (
                parsed.scheme != "mariadb"
                or parsed.hostname != "localhost"
                or parsed.path != "/" + self.database
                or parsed.port
                or parse_qs(parsed.query) != {"unix_socket": ["/run/mysqld/mysqld.sock"]}
            ):
                raise ValueError("Разрешена только своя локальная MariaDB через сокет")
        if urlsplit(self.runtime["CHECKOUT_DATABASE_URL"]).username == urlsplit(self.operator["DATABASE_URL"]).username:
            raise ValueError("Оператор и API должны использовать разные аккаунты")
        self.state = directory(self.values["CLUB_STATE_DIR"])
        self.backups = directory(self.values["CLUB_BACKUP_DIR"])
        self.compose = [
            "docker",
            "compose",
            "--env-file",
            str(self.path),
            "-f",
            str(REPO / "deploy/compose.mariadb.yml"),
        ]
        self.env = {"PATH": os.environ["PATH"]}

    def execute(self, *args, **options):
        return run([*self.compose, *args], env=self.env, **options)

    def capture(self, *args):
        return output([*self.compose, *args], env=self.env)

    def operator_command(self, command, image=None):
        environment = self.env if image is None else {**self.env, "CLUB_OPERATOR_IMAGE": image}
        return output(
            [*self.compose, "run", "--rm", "-T", "operator", "python", "-m", "club_ops.cli", command], env=environment
        )

    @contextlib.contextmanager
    def lock(self):
        with (self.state / "maintenance.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Другая операция обслуживания уже выполняется") from None
            yield

    def stop_writers(self):
        running = bool(self.capture("ps", "--status", "running", "-q", "api"))
        if running:
            self.execute("stop", "--timeout", "60", "api")
        return running

    def resume(self):
        self.execute("start", "api")
        container = self.capture("ps", "-q", "api")
        if not container:
            raise ValueError("Не удалось запустить существующий API")
        for attempt in range(30):
            status = output(["docker", "inspect", "-f", "{{.State.Health.Status}}", container])
            if status == "healthy":
                return
            if status == "unhealthy" or attempt == 29:
                raise ValueError("API не стал готов после копирования")
            time.sleep(2)

    def verify_http(self):
        context = ssl.create_default_context(cafile=self.values.get("HTTPS_CA_FILE") or None)
        base = urlsplit(self.runtime["PUBLIC_URL"])
        address = self.values.get("CLUB_BIND_ADDRESS", "127.0.0.1")
        host = "127.0.0.1" if ipaddress.ip_address(address).is_unspecified else address
        target = (host, int(self.values.get("CLUB_HTTPS_PORT", "8443")))
        for attempt in range(20):
            route, status = "/api/ready", None
            try:
                for route in ("/api/ready", "/"):
                    status = None
                    connection = https_connection(target, base, context)
                    try:
                        connection.request("GET", route)
                        response = connection.getresponse()
                        status = response.status
                        if response.status != 200:
                            raise ValueError("Не удалось открыть сайт или API")
                        body = response.read()
                        if route == "/api/ready":
                            if json.loads(body).get("status") != "ok":
                                raise ValueError("API не готов")
                        elif b"<html" not in body.lower():
                            raise ValueError("Страница сайта не открывается")
                    finally:
                        connection.close()
                return
            except (OSError, ValueError, http.client.HTTPException) as error:
                if attempt == 19 or isinstance(error, ssl.SSLCertVerificationError):
                    raise ValueError(
                        "Не удалось проверить TLS, сайт и готовность API: "
                        + route
                        + ", "
                        + failure_detail(error, status)
                    ) from None
                time.sleep(2)


def https_connection(target, base, context):
    raw = socket.create_connection(target, timeout=10)
    connection = http.client.HTTPSConnection(base.hostname, base.port or 443, context=context, timeout=10)
    try:
        connection.sock = context.wrap_socket(raw, server_hostname=base.hostname)
    except BaseException:
        raw.close()
        raise
    return connection


def failure_detail(error, status):
    if isinstance(error, ssl.SSLCertVerificationError):
        reason = "сертификат: " + str(error.verify_message)
    elif isinstance(error, ValueError):
        reason = str(error)
    else:
        reason = type(error).__name__
    return reason if status is None else reason + " (HTTP " + str(status) + ")"


def is_ip(address):
    try:
        ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return True


def certificate_current(certificate, address, seconds):
    check = ["openssl", "x509", "-in", str(certificate), "-noout"]
    try:
        matches = output([*check, "-checkip" if is_ip(address) else "-checkhost", address])
        output([*check, "-checkend", str(seconds)])
    except (OSError, subprocess.CalledProcessError):
        return False
    return "does match" in matches


def with_retries(action, label):
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        try:
            return action()
        except subprocess.CalledProcessError:
            if attempt == NETWORK_ATTEMPTS:
                raise
            print(
                label + ": попытка " + str(attempt) + " не удалась, повтор через " + str(NETWORK_PAUSE_SECONDS) + " с"
            )
            time.sleep(NETWORK_PAUSE_SECONDS)


def record(context, name, values):
    destination = context.state / (name + ".json")
    temporary = destination.with_suffix(".partial")
    temporary.write_text(json.dumps(values) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, destination)


def image_revision(context):
    versions = []
    for service in ("api", "web"):
        container = context.capture("ps", "-aq", service)
        if not container:
            raise ValueError("Для копии нужны созданные API и web")
        versions.append(
            output(
                ["docker", "inspect", "-f", '{{index .Config.Labels "org.opencontainers.image.revision"}}', container]
            )
        )
    if versions[0] != versions[1] or not REVISION.fullmatch(versions[0]):
        raise ValueError("API и web должны иметь одинаковую revision label проверенного коммита")
    return versions[0]


def file_hashes(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Симлинки в копии запрещены")
        if path.is_file():
            with path.open("rb") as source:
                result[str(path.relative_to(directory))] = hashlib.file_digest(source, "sha256").hexdigest()
    return result


def extract_archive(path, destination, allowed=None, maximum=512 * 1024**3):
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > maximum:
            raise ValueError("Архив превышает допустимый размер")
        names = set()
        for member in members:
            name = pathlib.PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or str(name) in names
                or not (member.isfile() or member.isdir())
                or allowed is not None
                and (str(name) not in allowed or not member.isfile())
            ):
                raise ValueError("Недопустимый путь или ссылка в архиве")
            names.add(str(name))
        if allowed is not None and names != allowed:
            raise ValueError("Неполный состав снимка")
        archive.extractall(destination, filter="data")


def file_hashes_for_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
