import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

from mariadb_backup import backup, restore
from mariadb_common import (
    HELPERS,
    Context,
    certificate_current,
    output,
    read_env,
    record,
    revision,
    sql,
    with_retries,
)
from mariadb_install import install as install
from mariadb_install import prepare_restore

MIN_BUILD_FREE_BYTES = 5 * 1024**3
CERTIFICATE_WARNING_SECONDS = 14 * 24 * 3600
DEPLOY_STEPS = 7


def step(number, text):
    print("[" + str(number) + "/" + str(DEPLOY_STEPS) + "] " + text, flush=True)


def verify_certificate(context, seconds):
    address = urlsplit(context.runtime["PUBLIC_URL"]).hostname
    if not certificate_current(pathlib.Path(context.values["CLUB_TLS_DIR"]) / "fullchain.pem", address, seconds):
        raise ValueError(
            "Сертификат nginx не подходит к адресу "
            + address
            + " или истекает; для самоподписанного повторите установщик, публичный замените у поставщика"
        )


def prefetch_images(context):
    configuration = json.loads(context.capture("config", "--format", "json"))
    for name, service in configuration["services"].items():
        if "build" in service or not service.get("image"):
            continue
        if not image_present(service["image"]):
            with_retries(lambda name=name: context.execute("pull", "--quiet", name), "Загрузка образа " + name)


def image_present(image):
    try:
        output(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    except subprocess.CalledProcessError:
        return False
    return True


def verify_build_space():
    storage = pathlib.Path(output(["docker", "info", "--format", "{{.DockerRootDir}}"])).resolve(strict=True)
    if shutil.disk_usage(storage).free < MIN_BUILD_FREE_BYTES:
        raise ValueError("Для сборки требуется не менее 5 GiB свободного места на диске Docker")


def preflight(context):
    revision()
    configuration = json.loads(context.capture("--profile", "operator", "config", "--format", "json"))
    for name in ("api", "web", "operator"):
        if configuration["services"][name].get("ports"):
            raise ValueError("Порты API, web и оператора не должны публиковаться")
    if context.runtime.get("APP_ENV") != "production" or context.runtime.get("SEED_DEMO") != "false":
        raise ValueError("Нужны APP_ENV=production и SEED_DEMO=false")
    verify_build_space()
    output(["systemctl", "is-active", "mariadb"])
    sql("SELECT 1", context.database)
    verify_certificate(context, 0)
    print("Preflight: закрытая конфигурация, локальная база, сертификат и чистый checkout проверены")


def monitor(context):
    context.verify_http()
    verify_certificate(context, CERTIFICATE_WARNING_SECONDS)
    for service in ("api", "web", "nginx"):
        container = context.capture("ps", "-q", service)
        if not container or output(["docker", "inspect", "-f", "{{.State.Health.Status}}", container]) != "healthy":
            raise ValueError("Сервис не готов: " + service)
    previous = context.state / "last-backup.json"
    if not previous.exists() or time.time() - json.loads(previous.read_text())["completed_at"] > 36 * 3600:
        raise ValueError("Нет успешной копии за последние 36 часов")
    print("Мониторинг: сайт, API, TLS, контейнеры и свежесть копии проверены")


def deploy(context):
    commit = revision()
    if os.geteuid() != 0:
        raise ValueError("Обслуживание нативной MariaDB выполняется через sudo")
    step(1, "Проверяем конфигурацию, базу, место на диске и сертификат")
    preflight(context)
    previous = context.state / "last-deploy.json"
    if previous.exists():
        deployed = json.loads(previous.read_text())
        output(
            [
                "docker",
                "image",
                "tag",
                deployed["operator_image"],
                context.project + "-operator:retained-" + deployed["commit"],
            ]
        )
    step(2, "Собираем образы: первая сборка – несколько минут, обновление только кода – секунды")
    with_retries(
        lambda: context.execute(
            "--profile", "operator", "build", "--build-arg", "VCS_REF=" + commit, "api", "web", "operator", "nginx"
        ),
        "Сборка образов",
    )
    context.execute(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "api",
        "python",
        "-c",
        "from club_api.core.config import load_settings; s=load_settings(); e=s.production_errors(); "
        'e += [] if s.APP_ENV == "production" else ["Нужен production"]; '
        'assert not e, "\\n".join(e)',
    )
    step(3, "Загружаем вспомогательные образы до остановки сайта")
    prefetch_images(context)
    running = bool(context.capture("ps", "--status", "running", "-q", "api"))
    step(4, "Создаём резервную копию" if running else "Резервная копия не нужна: API ещё не запущен")
    if running:
        backup(context, resume=False)
    context.stop_writers()
    try:
        step(5, "Применяем миграции, права базы и начальные данные")
        context.operator_command("migrate")
        context.operator_command("configure-mariadb-role")
        context.operator_command("bootstrap")
        step(6, "Запускаем API, сайт и nginx")
        if context.values.get("COMPOSE_PROFILES") == "local":
            context.execute("up", "-d", "mailpit")
        context.execute("up", "-d", "--wait", "api", "web")
        context.execute("up", "-d", "--wait", "--force-recreate", "nginx")
        step(7, "Проверяем TLS, сайт и /api/ready")
        context.verify_http()
        operator_image = output(["docker", "image", "inspect", "-f", "{{.Id}}", context.values["CLUB_OPERATOR_IMAGE"]])
        output(["docker", "image", "tag", operator_image, context.project + "-operator:retained-" + commit])
        record(
            context,
            "last-deploy",
            {"commit": commit, "operator_image": operator_image, "finished_at": int(time.time()), "status": "ok"},
        )
    except BaseException:
        record(context, "last-deploy-attempt", {"commit": commit, "finished_at": int(time.time()), "status": "failed"})
        context.stop_writers()
        print("API остановлен, чтобы не принимать записи в непроверенной версии; исправьте причину ниже и повторите")
        raise
    print("Обновление проверено: TLS, сайт и /api/ready; ревизия " + commit)


def main():
    parser = argparse.ArgumentParser(description="Обслуживание MariaDB/nginx")
    parser.add_argument(
        "command", choices=("detect", "preflight", "monitor", "deploy", "backup", "prepare-restore", "restore")
    )
    parser.add_argument("--snapshot", type=pathlib.Path)
    parser.add_argument("--target-env", type=pathlib.Path)
    parser.add_argument("--config-dir", type=pathlib.Path)
    parser.add_argument("--port", type=int, default=9543)
    parser.add_argument("--mail-port", type=int, default=8125)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        path = os.environ.get("ENV_FILE", "")
        if args.command == "detect":
            return 0 if path and read_env(path).get("CLUB_STACK") == "mariadb" else 1
        if os.geteuid() != 0:
            raise ValueError("Запустите обслуживание через sudo")
        context = Context(path)
        with context.lock():
            if args.command == "deploy":
                deploy(context)
            elif args.command == "preflight":
                preflight(context)
            elif args.command == "monitor":
                monitor(context)
            elif args.command == "backup":
                backup(context)
            elif args.command == "prepare-restore":
                if args.config_dir is None:
                    raise ValueError("Укажите --config-dir вне checkout")
                prepare_restore(context, args.config_dir, args.port, args.mail_port)
            else:
                restore(context, args.snapshot, args.target_env)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print("Операция не завершена: " + HELPERS["failure_message"](error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
