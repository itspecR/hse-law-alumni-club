import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time

from mariadb_common import (
    HELPERS,
    Context,
    extract_archive,
    file_hashes,
    file_hashes_for_file,
    image_revision,
    output,
    record,
    revision,
    run,
    sql,
)

MAX_SNAPSHOT_BYTES = 512 * 1024**3
RESTORE_RESERVE_BYTES = 1024**3
RESTORE_CHUNK_BYTES = 64 * 1024


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


def backup(context, resume=True):
    commit = image_revision(context)
    previous = context.state / "last-deploy.json"
    operator_image = json.loads(previous.read_text()).get("operator_image") if previous.exists() else None
    if not operator_image:
        raise ValueError("Для копии нужен закреплённый образ оператора из проверенного развёртывания")
    if (
        output(
            [
                "docker",
                "image",
                "inspect",
                "-f",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                operator_image,
            ]
        )
        != commit
    ):
        raise ValueError("Образ оператора не соответствует действующей версии API")
    key = context.values.get("BACKUP_ENCRYPTION_KEY", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", key):
        raise ValueError("Нужен отдельный BACKUP_ENCRYPTION_KEY: 64 hex-символа")
    keep = int(context.values.get("BACKUP_KEEP_DAYS", "14"))
    if not 1 <= keep <= 3650:
        raise ValueError("BACKUP_KEEP_DAYS должен быть от 1 до 3650")
    remote = context.values.get("BACKUP_OFFSITE_REMOTE", "")
    if remote and not shutil.which("rclone"):
        raise ValueError("Для offsite нужен rclone")
    stopped = context.stop_writers()
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=context.backups) as temporary:
            work = pathlib.Path(temporary)
            uploads = pathlib.Path(context.values["CLUB_UPLOADS_DIR"])
            upload_bytes = sum(path.stat().st_size for path in uploads.rglob("*") if path.is_file())
            database_bytes = int(
                sql(
                    "SELECT COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) "
                    "FROM information_schema.TABLES WHERE TABLE_SCHEMA='" + context.database + "';"
                )
            )
            if shutil.disk_usage(context.backups).free < 3 * (upload_bytes + database_bytes) + 268435456:
                raise ValueError("Недостаточно места для копии")
            with (work / "database.sql.gz").open("wb") as destination:
                with subprocess.Popen(
                    [
                        shutil.which("mariadb-dump"),
                        "--protocol=socket",
                        "--single-transaction",
                        "--hex-blob",
                        "--skip-add-drop-table",
                        "--skip-comments",
                        context.database,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                ) as source:
                    try:
                        run(["gzip", "-c"], stdin=source.stdout, stdout=destination)
                    finally:
                        source.stdout.close()
                    if source.wait() != 0:
                        raise ValueError("Не удалось создать дамп MariaDB")
            (work / "tables.json").write_text(
                context.operator_command("database-fingerprints", image=operator_image) + "\n"
            )
            with tarfile.open(work / "uploads.tar.gz", "w:gz") as archive:
                for path in sorted(uploads.rglob("*")):
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise ValueError("Uploads содержит ссылку или специальный файл")
                    archive.add(path, arcname=str(path.relative_to(uploads)), recursive=False)
            (work / "files.json").write_text(json.dumps(file_hashes(uploads), sort_keys=True) + "\n")
            metadata = {
                "format": "club-mariadb-backup-v1",
                "revision": commit,
                "created_at": int(time.time()),
                "database_bytes": database_bytes,
                "uploads_bytes": upload_bytes,
            }
            (work / "metadata.json").write_text(json.dumps(metadata) + "\n")
            (work / "checksums.json").write_text(json.dumps(file_hashes(work), sort_keys=True) + "\n")
            archive_path = work / "snapshot.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name in (
                    "database.sql.gz",
                    "tables.json",
                    "uploads.tar.gz",
                    "files.json",
                    "metadata.json",
                    "checksums.json",
                ):
                    archive.add(work / name, arcname=name)
            final = context.backups / (
                "snapshot-"
                + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                + "-"
                + secrets.token_hex(4)
                + ".tar.gz.enc"
            )
            partial = final.with_suffix(".partial")
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            try:
                run(
                    [
                        "openssl",
                        "enc",
                        "-aes-256-cbc",
                        "-pbkdf2",
                        "-iter",
                        "200000",
                        "-pass",
                        "env:BACKUP_ENCRYPTION_KEY",
                        "-in",
                        str(archive_path),
                        "-out",
                        str(partial),
                    ],
                    env={"PATH": os.environ["PATH"], "BACKUP_ENCRYPTION_KEY": key},
                )
                os.replace(partial, final)
            finally:
                partial.unlink(missing_ok=True)
            if remote:
                run(["rclone", "copyto", str(final), remote.rstrip("/") + "/" + final.name])
                run(["rclone", "check", str(final.parent), remote, "--include", final.name, "--one-way", "--download"])
            record(
                context,
                "last-backup",
                {"completed_at": int(time.time()), "snapshot": str(final), "offsite_verified": bool(remote)},
            )
            for old in context.backups.glob("snapshot-*.tar.gz.enc"):
                if (
                    old != final
                    and old.is_file()
                    and not old.is_symlink()
                    and old.stat().st_mtime < time.time() - keep * 86400
                ):
                    old.unlink()
            print("Снимок создан: " + str(final) + "; offsite: " + ("проверен" if remote else "не настроен"))
            completed = True
            return final
    finally:
        if stopped and (resume or not completed):
            context.resume()


def restore(context, snapshot, target):
    if snapshot is None or target is None:
        raise ValueError("Нужны --snapshot и --target-env отдельного пустого контура")
    HELPERS["private_file"](snapshot)
    snapshot_budget(snapshot.stat().st_size, context.backups)
    target = Context(target)
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
    for name in ("JOBS_ENABLED", "DPO_SYNC_ENABLED", "NEWS_SYNC_ENABLED", "SUPPORT_ENABLED", "TELEGRAM_POLLING"):
        if target.runtime.get(name) != "false":
            raise ValueError("Фоновые и внешние действия в восстановленном стенде должны быть выключены")
    for name in ("TELEGRAM_BOT_TOKEN", "YOOKASSA_SECRET_KEY", "VAPID_PRIVATE_KEY", "OFFICE_TG_BOT_TOKEN"):
        if target.runtime.get(name):
            raise ValueError("Рабочие ключи внешних сервисов в стенде восстановления запрещены")
    if target.runtime.get("SMTP_HOST") != "mailpit" or target.values.get("COMPOSE_PROFILES") != "local":
        raise ValueError("Письма восстановленного стенда должны оставаться в его Mailpit")
    key = context.values.get("BACKUP_ENCRYPTION_KEY", "")
    with target.lock(), tempfile.TemporaryDirectory(prefix=".restore-", dir=context.backups) as temporary:
        work = pathlib.Path(temporary)
        decrypt_snapshot(snapshot, work / "snapshot.tar.gz", key)
        allowed = {"database.sql.gz", "tables.json", "uploads.tar.gz", "files.json", "metadata.json", "checksums.json"}
        archive_budget = min(MAX_SNAPSHOT_BYTES, max(0, shutil.disk_usage(work).free - RESTORE_RESERVE_BYTES))
        extract_archive(work / "snapshot.tar.gz", work, allowed, maximum=archive_budget)
        for name, digest in json.loads((work / "checksums.json").read_text()).items():
            if name not in allowed - {"checksums.json"} or file_hashes_for_file(work / name) != digest:
                raise ValueError("Контрольная сумма снимка не совпала")
        if set(json.loads((work / "checksums.json").read_text())) != allowed - {"checksums.json"}:
            raise ValueError("Неполные контрольные суммы снимка")
        metadata = json.loads((work / "metadata.json").read_text())
        if metadata.get("format") != "club-mariadb-backup-v1" or metadata.get("revision") != revision():
            raise ValueError("Для восстановления нужен чистый checkout ревизии приложения из снимка")
        if any(
            type(metadata.get(name)) is not int or not 0 <= metadata[name] <= MAX_SNAPSHOT_BYTES
            for name in ("database_bytes", "uploads_bytes")
        ):
            raise ValueError("Некорректный размер данных в метаданных снимка")
        needed = 3 * (metadata["database_bytes"] + metadata["uploads_bytes"]) + RESTORE_RESERVE_BYTES
        if shutil.disk_usage(uploads).free < needed:
            raise ValueError("Недостаточно места для восстановления")
        with subprocess.Popen(
            [shutil.which("gzip"), "-dc", str(work / "database.sql.gz")], stdout=subprocess.PIPE
        ) as source:
            try:
                run(["mariadb", "--protocol=socket", target.database], stdin=source.stdout, stderr=subprocess.DEVNULL)
            finally:
                source.stdout.close()
            if source.wait() != 0:
                raise ValueError("Не удалось распаковать дамп")
        extract_archive(work / "uploads.tar.gz", uploads, maximum=metadata["uploads_bytes"])
        for path in [uploads, *uploads.rglob("*")]:
            os.chown(path, 1000, 1000)
        if file_hashes(uploads) != json.loads((work / "files.json").read_text()):
            raise ValueError("Файлы восстановлены не полностью")
        actual = json.loads(target.operator_command("database-fingerprints"))
        if actual != json.loads((work / "tables.json").read_text()):
            raise ValueError("Содержимое таблиц не совпало со снимком")
    print(
        "Все таблицы и файлы восстановлены и сверены; исходная база сохранена. Запустите отдельный стенд командой deploy.sh"
    )
