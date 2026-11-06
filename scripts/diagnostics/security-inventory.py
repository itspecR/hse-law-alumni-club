import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 30
ROLES = {"api", "web", "operator", "nginx"}
PYTHON_PACKAGES = """import importlib.metadata,json,platform
print(json.dumps({'python':platform.python_version(),'packages':sorted(
    [{'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()],
    key=lambda d:d['name'].lower())}))
"""
CONTAINER_FORMAT = (
    '{"running":{{json .State.Running}},"image_id":{{json .Image}},'
    '"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}'
)


def command(arguments, *, stderr=False):
    try:
        result = subprocess.run(
            arguments, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Команда недоступна или превысила время ожидания") from error
    if result.returncode:
        raise RuntimeError(f"Команда завершилась с кодом {result.returncode}")
    return (result.stdout + (result.stderr if stderr else "")).strip()


def source_inventory(root=ROOT):
    projects = {}
    hashes = {}
    for name in ("backend", "frontend", "scripts"):
        lock = root / name / "uv.lock"
        manifest = root / name / "pyproject.toml"
        data = tomllib.loads(lock.read_text())
        projects[name] = {
            "evidence": "lockfile; не установленные пакеты",
            "direct_dependencies": tomllib.loads(manifest.read_text())["project"]["dependencies"],
            "packages": sorted(
                [{"name": package["name"], "version": package["version"]} for package in data["package"]],
                key=lambda package: (package["name"], package["version"]),
            ),
        }
        for path in (lock, manifest):
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    images = []
    for path in sorted((root / "deploy").glob("*.Dockerfile")):
        hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for line in path.read_text().splitlines():
            if line.startswith("FROM "):
                images.append({"file": str(path.relative_to(root)), "from": line.removeprefix("FROM ")})
    template = "deploy/nginx.conf.template"
    hashes[template] = hashlib.sha256((root / template).read_bytes()).hexdigest()
    return {"python_projects": projects, "docker_build_stages": images, "sha256": hashes}


def container_inventory(role, identifier, run=command):
    metadata = json.loads(run(["docker", "inspect", "--format", CONTAINER_FORMAT, identifier]))
    if not metadata["running"]:
        raise RuntimeError("Контейнер остановлен; установленные компоненты не проверены")
    image_id = metadata["image_id"]
    metadata["repo_digests"] = json.loads(
        run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id])
    )
    prefix = ["docker", "exec", identifier]
    metadata["os_packages"] = run([*prefix, "apk", "info", "-v"]).splitlines()
    if role in {"api", "web", "operator"}:
        metadata.update(json.loads(run([*prefix, "python", "-c", PYTHON_PACKAGES])))
    else:
        version = run([*prefix, "nginx", "-v"], stderr=True)
        match = re.search(r"nginx/(\d+\.\d+\.\d+)", version)
        if not match:
            raise RuntimeError("Не удалось прочитать точную версию nginx")
        metadata["nginx"] = match[1]
        flags = run([*prefix, "nginx", "-V"], stderr=True)
        metadata["compiled_modules"] = re.findall(r"--with-[a-zA-Z0-9_]+(?:=dynamic)?", flags)
    return metadata


def host_inventory(run=command):
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, _, value = line.partition("=")
        if key in {"ID", "VERSION_ID"}:
            release[key] = value.strip('"')
    packages = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"]).splitlines()
    return {"os": release, "kernel": run(["uname", "-r"]), "installed_packages": packages}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Состав компонентов без env, секретов и данных пользователей")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", action="append", default=[], metavar="ROLE=ID")
    parser.add_argument("--host", action="store_true", help="Пакеты ОС Ubuntu на текущем сервере")
    args = parser.parse_args()
    selected = {}
    for value in args.container:
        role, separator, identifier = value.partition("=")
        if (
            not separator
            or role not in ROLES
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identifier)
            or role in selected
        ):
            parser.error("Нужна уникальная роль api/web/operator/nginx и имя либо ID контейнера")
        selected[role] = identifier
    report = {
        "schema_version": 1,
        "collected_at": datetime.now(UTC).isoformat(),
        "assessment_performed": False,
        "requested_containers": sorted(selected),
        "host_requested": args.host,
        "containers": {},
        "errors": [],
    }
    try:
        report["checkout_revision"] = command(["git", "rev-parse", "HEAD"])
        report["checkout_modified"] = bool(command(["git", "status", "--porcelain", "--untracked-files=normal"]))
        report["source"] = source_inventory()
    except (RuntimeError, OSError, ValueError, KeyError):
        report["errors"].append({"scope": "source", "error": "Не удалось собрать состав checkout"})
    for role, identifier in selected.items():
        try:
            report["containers"][role] = container_inventory(role, identifier)
        except (RuntimeError, OSError, ValueError, KeyError) as error:
            message = str(error) if isinstance(error, RuntimeError) else "Не удалось разобрать состав контейнера"
            report["errors"].append({"scope": role, "error": message})
    if args.host:
        try:
            report["host"] = host_inventory()
        except (RuntimeError, OSError, ValueError):
            report["errors"].append({"scope": "host", "error": "Не удалось собрать пакеты ОС Ubuntu"})
    report["collection_complete"] = not report["errors"]
    try:
        write_report(args.output, report)
    except OSError:
        print("Не удалось создать новый файл отчёта; существующие файлы не перезаписываются", file=sys.stderr)
        return 1
    print(
        "Состав собран; оценка защищённости не выполнена"
        if report["collection_complete"]
        else "Состав собран частично; см. errors"
    )
    return 0 if report["collection_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
