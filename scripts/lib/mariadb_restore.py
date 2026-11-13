import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from collections import namedtuple
from urllib.parse import urlsplit

from mariadb_backup import ENCRYPTION_KEY, SNAPSHOT_FILES, SNAPSHOT_FORMAT
from mariadb_common import (
    HELPERS,
    Context,
    database_name,
    directory,
    extract_archive,
    file_hashes,
    file_hashes_for_file,
    output,
    read_env,
    record,
    revision,
    run,
    sql,
)

MAX_SNAPSHOT_BYTES = 512 * 1024**3
RESTORE_RESERVE_BYTES = 1024**3
RESTORE_CHUNK_BYTES = 64 * 1024
UPLOADS_OWNER = 1000
ISOLATED_SWITCHES = ("JOBS_ENABLED", "DPO_SYNC_ENABLED", "NEWS_SYNC_ENABLED", "SUPPORT_ENABLED", "TELEGRAM_POLLING")
ISOLATED_KEYS = ("TELEGRAM_BOT_TOKEN", "YOOKASSA_SECRET_KEY", "VAPID_PRIVATE_KEY", "OFFICE_TG_BOT_TOKEN")
TABLE_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")
BASE_TABLE = "BASE TABLE"
KEPT = "kept"
FAILED = "failed"

Place = namedtuple("Place", "database uploads")


def snapshot_budget(size, directory):
    if not 0 < size <= MAX_SNAPSHOT_BYTES:
        raise ValueError("Размер снимка должен быть от 1 байта до 512 GiB")
    if shutil.disk_usage(directory).free < size + RESTORE_RESERVE_BYTES:
        raise ValueError("Недостаточно места для расшифровки снимка с резервом 1 GiB")


def decrypt_snapshot(snapshot, destination, key):
    descriptor = os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as ciphertext:
        info = os.fstat(ciphertext.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Снимок должен быть закрытым обычным файлом текущего оператора")
        snapshot_budget(info.st_size, destination.parent)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as plaintext:
                with subprocess.Popen(
                    [
                        shutil.which("openssl") or "openssl",
                        "enc",
                        "-d",
                        "-aes-256-cbc",
                        "-pbkdf2",
                        "-iter",
                        "200000",
                        "-pass",
                        "env:BACKUP_ENCRYPTION_KEY",
                    ],
                    stdin=ciphertext,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={"PATH": os.environ["PATH"], "BACKUP_ENCRYPTION_KEY": key},
                ) as process:
                    try:
                        written = 0
                        while chunk := process.stdout.read(RESTORE_CHUNK_BYTES):
                            written += len(chunk)
                            if written > info.st_size:
                                raise ValueError("Расшифрованный снимок превысил исходный размер")
                            if shutil.disk_usage(destination.parent).free < RESTORE_RESERVE_BYTES + len(chunk):
                                raise ValueError("Исчерпан резерв места для восстановления")
                            plaintext.write(chunk)
                        if process.wait() != 0:
                            raise ValueError("Не удалось расшифровать снимок")
                    except BaseException:
                        process.kill()
                        raise
                    finally:
                        process.stdout.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def snapshot_key(context, source_env):
    values = context.values if source_env is None else read_env(source_env)
    key = values.get("BACKUP_ENCRYPTION_KEY", "")
    if not ENCRYPTION_KEY.fullmatch(key):
        raise ValueError("В env-файле сервера, где сделан снимок, нет BACKUP_ENCRYPTION_KEY из 64 hex-символов")
    return key


def read_json(path):
    return json.loads(path.read_text())


def verify_checksums(work):
    expected = set(SNAPSHOT_FILES) - {"checksums.json"}
    checksums = read_json(work / "checksums.json")
    for name, digest in checksums.items():
        if name not in expected or file_hashes_for_file(work / name) != digest:
            raise ValueError("Контрольная сумма снимка не совпала")
    if set(checksums) != expected:
        raise ValueError("Неполные контрольные суммы снимка")


def snapshot_metadata(work):
    metadata = read_json(work / "metadata.json")
    if metadata.get("format") != SNAPSHOT_FORMAT or metadata.get("revision") != revision():
        raise ValueError("Для восстановления нужен чистый checkout ревизии приложения из снимка")
    if any(
        type(metadata.get(name)) is not int or not 0 <= metadata[name] <= MAX_SNAPSHOT_BYTES
        for name in ("database_bytes", "uploads_bytes")
    ):
        raise ValueError("Некорректный размер данных в метаданных снимка")
    return metadata


def open_snapshot(snapshot, work, key):
    decrypt_snapshot(snapshot, work / "snapshot.tar.gz", key)
    budget = min(MAX_SNAPSHOT_BYTES, max(0, shutil.disk_usage(work).free - RESTORE_RESERVE_BYTES))
    extract_archive(work / "snapshot.tar.gz", work, set(SNAPSHOT_FILES), maximum=budget)
    verify_checksums(work)
    return snapshot_metadata(work)


def require_space(metadata, uploads):
    needed = 3 * (metadata["database_bytes"] + metadata["uploads_bytes"]) + RESTORE_RESERVE_BYTES
    if shutil.disk_usage(uploads).free < needed:
        raise ValueError("Недостаточно места для восстановления")


def load_database(work, database):
    with subprocess.Popen(
        [shutil.which("gzip"), "-dc", str(work / "database.sql.gz")], stdout=subprocess.PIPE
    ) as source:
        try:
            run(["mariadb", "--protocol=socket", database], stdin=source.stdout, stderr=subprocess.DEVNULL)
        finally:
            source.stdout.close()
        if source.wait() != 0:
            raise ValueError("Не удалось распаковать дамп")


def load_uploads(work, metadata, uploads):
    extract_archive(work / "uploads.tar.gz", uploads, maximum=metadata["uploads_bytes"])
    for path in [uploads, *uploads.rglob("*")]:
        os.chown(path, UPLOADS_OWNER, UPLOADS_OWNER)
    if file_hashes(uploads) != read_json(work / "files.json"):
        raise ValueError("Файлы восстановлены не полностью")


def verify_tables(work, contour):
    if json.loads(contour.operator_command("database-fingerprints")) != read_json(work / "tables.json"):
        raise ValueError("Содержимое таблиц не совпало со снимком")


def fill(work, metadata, contour, uploads):
    require_space(metadata, uploads)
    load_database(work, contour.database)
    load_uploads(work, metadata, uploads)
    verify_tables(work, contour)


def isolated_uploads(context, target):
    if target.database == context.database or target.project == context.project:
        raise ValueError("Восстановление разрешено только в отдельную новую базу и Compose-проект")
    if sql("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='" + target.database + "';") != "0":
        raise ValueError("База восстановления должна быть пустой")
    if output(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + target.project]):
        raise ValueError("Целевой Compose-проект уже содержит контейнеры")
    uploads = pathlib.Path(target.values["CLUB_UPLOADS_DIR"])
    if uploads.resolve() == pathlib.Path(context.values["CLUB_UPLOADS_DIR"]).resolve() or any(uploads.iterdir()):
        raise ValueError("Нужен отдельный пустой каталог uploads")
    if target.values.get("CLUB_BIND_ADDRESS", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Контур проверки восстановления должен слушать только loopback")
    if any(target.runtime.get(name) != "false" for name in ISOLATED_SWITCHES):
        raise ValueError("Фоновые и внешние действия в восстановленном стенде должны быть выключены")
    if any(target.runtime.get(name) for name in ISOLATED_KEYS):
        raise ValueError("Рабочие ключи внешних сервисов в стенде восстановления запрещены")
    if target.runtime.get("SMTP_HOST") != "mailpit" or target.values.get("COMPOSE_PROFILES") != "local":
        raise ValueError("Письма восстановленного стенда должны оставаться в его Mailpit")
    return uploads


def restore(context, snapshot, target):
    if snapshot is None or target is None:
        raise ValueError("Нужны --snapshot и --target-env отдельного пустого контура")
    HELPERS["private_file"](snapshot)
    snapshot_budget(snapshot.stat().st_size, context.backups)
    target = Context(target)
    uploads = isolated_uploads(context, target)
    key = context.values.get("BACKUP_ENCRYPTION_KEY", "")
    with target.lock(), tempfile.TemporaryDirectory(prefix=".restore-", dir=context.backups) as temporary:
        work = pathlib.Path(temporary)
        fill(work, open_snapshot(snapshot, work, key), target, uploads)
    print(
        "Все таблицы и файлы восстановлены и сверены; исходная база сохранена. Запустите отдельный стенд командой deploy.sh"
    )


def base_tables(database):
    rows = sql(
        "SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA='"
        + database_name(database)
        + "' ORDER BY TABLE_NAME;"
    )
    names = []
    for row in rows.splitlines():
        name, kind = row.split("\t")
        if kind != BASE_TABLE or not TABLE_NAME.fullmatch(name):
            raise ValueError("В базе есть представление или таблица с недопустимым именем; перенос остановлен")
        names.append(name)
    return names


def move_tables(source, destination):
    names = base_tables(source)
    if names:
        sql("RENAME TABLE " + ", ".join(f"`{source}`.`{name}` TO `{destination}`.`{name}`" for name in names) + ";")


def move_entries(source, destination):
    for entry in sorted(source.iterdir()):
        os.rename(entry, destination / entry.name)


def holding(uploads, label):
    mark = time.strftime("%Y%m%dt%H%M%S", time.gmtime()) + "_" + secrets.token_hex(2)
    folder = directory(uploads.parent / ("uploads-" + label + "-" + mark))
    database = database_name("club_" + label + "_" + mark)
    sql("CREATE DATABASE `" + database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;")
    return Place(database, folder)


def move_out(context, uploads, place):
    move_tables(context.database, place.database)
    move_entries(uploads, place.uploads)


def move_in(context, uploads, place):
    move_tables(place.database, context.database)
    move_entries(place.uploads, uploads)


def release(place):
    if not base_tables(place.database):
        sql("DROP DATABASE `" + place.database + "`;")
    if not any(place.uploads.iterdir()):
        place.uploads.rmdir()


def put_back(context, uploads, kept, emptied):
    print("Восстановление не удалось; возвращаем прежние данные из базы " + kept.database)
    if emptied:
        failed = holding(uploads, FAILED)
        move_out(context, uploads, failed)
        release(failed)
    move_in(context, uploads, kept)
    release(kept)


def replace_data(context, uploads, work, metadata):
    kept = holding(uploads, KEPT)
    emptied = False
    try:
        move_out(context, uploads, kept)
        emptied = True
        fill(work, metadata, context, uploads)
    except BaseException:
        put_back(context, uploads, kept, emptied)
        raise
    return kept


def replace_stopped(context, uploads, work, metadata):
    stopped = context.stop_writers()
    try:
        return replace_data(context, uploads, work, metadata)
    except BaseException:
        if stopped:
            context.resume()
        raise


def restore_site(context, snapshot, site, source_env):
    if snapshot is None or not site:
        raise ValueError("Нужны --snapshot и --site с адресом сайта этого контура")
    if site != urlsplit(context.runtime["PUBLIC_URL"]).hostname:
        raise ValueError("Адрес в RESTORE_SITE не совпадает с адресом сайта этого контура; данные не тронуты")
    HELPERS["private_file"](snapshot)
    snapshot_budget(snapshot.stat().st_size, context.backups)
    key = snapshot_key(context, source_env)
    uploads = pathlib.Path(context.values["CLUB_UPLOADS_DIR"])
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=context.backups) as temporary:
        work = pathlib.Path(temporary)
        metadata = open_snapshot(snapshot, work, key)
        require_space(metadata, uploads)
        kept = replace_stopped(context, uploads, work, metadata)
    record(
        context,
        "last-restore",
        {
            "finished_at": int(time.time()),
            "snapshot": snapshot.name,
            "revision": metadata["revision"],
            "kept_database": kept.database,
            "kept_uploads": str(kept.uploads),
        },
    )
    print(
        "Снимок восстановлен в базу сайта и сверен: все таблицы и файлы. Прежние данные сохранены в базе "
        + kept.database
        + " и каталоге "
        + str(kept.uploads)
        + ". API остановлен; запустите sudo ./scripts/deploy.sh --current"
    )
