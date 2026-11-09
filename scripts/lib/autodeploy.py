import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
BRANCH = "main"
REQUIRED_CHECKS = ("build-test", "mariadb-integration", "live-stack", "secrets")
MAX_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 300
API_TIMEOUT_SECONDS = 20
API_URL = "https://api.github.com"
INSTALLED_ENV_FILE = pathlib.Path("/etc/club/compose.env")
DEFAULT_STATE_DIR = pathlib.Path("/var/lib/club-ops")
STATE_FILE = "autodeploy.json"
DEFAULT_PROJECT = "club-mariadb"
DEPLOYED_SERVICES = ("api", "web", "nginx")
REVISION_LABEL = "org.opencontainers.image.revision"
LOCK_FILE = "maintenance.lock"
REVISION = re.compile(r"[0-9a-f]{40}")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
PASSED = {"success", "neutral", "skipped"}

MESSAGES = {
    "current": "Выкачен последний коммит {target}, обновлений нет.",
    "stale": "Коммит {target} в папке, но api, web или nginx работают не из него: нужна выкладка.",
    "wait-ci": "Новый коммит {target}: CI ещё не завершён, ждём.",
    "ci-failed": "Новый коммит {target}: CI красный, не выкатываем.",
    "ci-unknown": "Новый коммит {target}: GitHub не ответил о CI, проверим на следующем запуске.",
    "exhausted": "Коммит {target} не выкатился за {limit} попытки, ждём следующий коммит в {branch}.",
    "pause": "Коммит {target}: следующая попытка после паузы {pause} с.",
    "busy": "Идёт другая операция обслуживания, проверим на следующем запуске.",
    "deploy": "Выкатываем {target}, попытка {attempt} из {limit}.",
}


def git(*args):
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def remote_head():
    line = git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()
    if not line or not REVISION.fullmatch(line[0]):
        raise ValueError(f"Ветка {BRANCH} не найдена в origin")
    return line[0]


def repository(url):
    match = GITHUB_REMOTE.search(url)
    if not match:
        raise ValueError(f"origin не указывает на GitHub: {url}")
    return f"{match[1]}/{match[2]}"


def check_runs(slug, revision, token):
    request = urllib.request.Request(
        f"{API_URL}/repos/{slug}/commits/{revision}/check-runs?filter=latest&per_page=100",
        headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
        return json.load(response)["check_runs"]


def ci_state(runs):
    latest = {run["name"]: run for run in runs if run["name"] in REQUIRED_CHECKS}
    if any(run["status"] == "completed" and run["conclusion"] not in PASSED for run in latest.values()):
        return "red"
    if len(latest) == len(REQUIRED_CHECKS) and all(run["conclusion"] in PASSED for run in latest.values()):
        return "green"
    return "pending"


def env_value(env_file, key):
    if env_file.is_file():
        for line in reversed(env_file.read_text().splitlines()):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return None


def state_dir(env_file):
    value = env_value(env_file, "CLUB_STATE_DIR")
    return pathlib.Path(value) if value else DEFAULT_STATE_DIR


def compose_project(env_file):
    return env_value(env_file, "CLUB_PROJECT") or DEFAULT_PROJECT


def running_revisions(project):
    return {
        service: subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                '{{.Label "' + REVISION_LABEL + '"}}',
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        for service in DEPLOYED_SERVICES
    }


def images_built_from(revision, revisions):
    return all(values == [revision] for values in revisions.values())


def deployed_revision(head):
    return head if images_built_from(head, running_revisions(compose_project(INSTALLED_ENV_FILE))) else None


def load_state(path):
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def save_state(path, state):
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(state) + "\n")
    os.replace(partial, path)


def maintenance_busy(path):
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def attempts_for(state, target):
    return state.get("attempts", 0) if state.get("commit") == target else 0


CI_ACTIONS = {"pending": "wait-ci", "red": "ci-failed", "unknown": "ci-unknown", "green": "deploy"}


def decide(deployed, target, state, now, busy, ci):
    if deployed == target:
        return "current"
    attempts = attempts_for(state, target)
    if attempts >= MAX_ATTEMPTS:
        return "exhausted"
    if attempts and now - state.get("last_attempt", 0) < RETRY_PAUSE_SECONDS:
        return "pause"
    if busy:
        return "busy"
    return CI_ACTIONS[ci()]


def target_ci(target):
    slug = repository(git("remote", "get-url", "origin"))
    try:
        runs = check_runs(slug, target, os.environ.get("GITHUB_TOKEN"))
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
        print(f"Ответ GitHub о CI: {error}", flush=True)
        return "unknown"
    return ci_state(runs)


def deploy(target):
    environment = {**os.environ, "DEPLOY_COMMIT": target}
    return subprocess.run(["bash", str(REPO / "scripts/deploy.sh"), BRANCH], env=environment).returncode == 0


def report(action, target, attempt=0):
    print(
        MESSAGES[action].format(
            target=target[:7], branch=BRANCH, limit=MAX_ATTEMPTS, pause=RETRY_PAUSE_SECONDS, attempt=attempt
        ),
        flush=True,
    )


def main():
    target = remote_head()
    head = git("rev-parse", "HEAD")
    deployed = deployed_revision(head)
    if deployed == target:
        report("current", target)
        return 0
    if head == target:
        report("stale", target)
    directory = state_dir(INSTALLED_ENV_FILE)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / STATE_FILE
    state = load_state(state_path)
    now = int(time.time())
    busy = maintenance_busy(directory / LOCK_FILE)
    action = decide(deployed, target, state, now, busy, lambda: target_ci(target))
    attempt = attempts_for(state, target) + 1
    report(action, target, attempt)
    if action != "deploy":
        return 0
    save_state(state_path, {"commit": target, "attempts": attempt, "last_attempt": now})
    return 0 if deploy(target) else 1


if __name__ == "__main__":
    sys.exit(main())
