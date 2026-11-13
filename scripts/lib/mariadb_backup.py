import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time

from mariadb_common import (
    file_hashes,
    image_revision,
    output,
    record,
    run,
    sql,
)

SNAPSHOT_FORMAT = "club-mariadb-backup-v1"
SNAPSHOT_FILES = ("database.sql.gz", "tables.json", "uploads.tar.gz", "files.json", "metadata.json", "checksums.json")
ENCRYPTION_KEY = re.compile(r"[a-fA-F0-9]{64}")


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
    if not ENCRYPTION_KEY.fullmatch(key):
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
                "format": SNAPSHOT_FORMAT,
                "revision": commit,
                "created_at": int(time.time()),
                "database_bytes": database_bytes,
                "uploads_bytes": upload_bytes,
            }
            (work / "metadata.json").write_text(json.dumps(metadata) + "\n")
            (work / "checksums.json").write_text(json.dumps(file_hashes(work), sort_keys=True) + "\n")
            archive_path = work / "snapshot.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name in SNAPSHOT_FILES:
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
