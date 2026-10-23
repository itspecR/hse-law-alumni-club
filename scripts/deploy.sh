#!/usr/bin/env bash
set -euo pipefail
DEFAULT_BRANCH=main
INSTALLED_ENV_FILE=/etc/club/compose.env
CURRENT=--current
script="$(realpath "${BASH_SOURCE[0]}")"
repo="$(dirname "$(dirname "$script")")"

usage() {
  echo "Использование: ./scripts/deploy.sh [ветка] — без ветки выкатывается $DEFAULT_BRANCH; ./scripts/deploy.sh $CURRENT — папка как есть" >&2
  exit 2
}

run_as_root() {
  [[ "$(id -u)" = 0 ]] && return
  exec sudo env ${ENV_FILE:+ENV_FILE="$ENV_FILE"} bash "$script" "$@"
}

use_installed_config() {
  if [[ -z "${ENV_FILE:-}" && -f "$INSTALLED_ENV_FILE" ]]; then export ENV_FILE="$INSTALLED_ENV_FILE"; fi
}

switch_to() {
  git -C "$repo" checkout -q -f --detach "$1"
  git -C "$repo" clean -fdq
}

sync_branch() {
  echo "Получаем ветку $1 из GitHub; локальные правки в $repo стираются"
  git -C "$repo" fetch -q origin "+refs/heads/$1:refs/remotes/origin/$1"
  switch_to "refs/remotes/origin/$1"
  echo "Выкатываем $1, коммит $(git -C "$repo" rev-parse --short HEAD)"
}

deploy_checkout() {
  bash "$repo/scripts/deploy.sh" "$CURRENT"
}

restore_hint() {
  local state
  state="$(sed -n 's/^CLUB_STATE_DIR=//p' "${ENV_FILE:-/dev/null}" | tail -n 1)"
  echo "Если миграции успели примениться и сайт работает с ошибками, восстановите базу из копии перед выкладкой" >&2
  echo "по разделу README «Восстановление и откат»; путь к копии: sudo cat ${state:-/var/lib/club-ops}/last-backup.json" >&2
}

roll_back() {
  echo "Ошибка выкладки. Возвращаем прошлую версию, коммит ${1:0:7}" >&2
  switch_to "$1"
  if deploy_checkout; then
    echo "Прошлая версия ${1:0:7} снова работает." >&2
  else
    echo "Прошлую версию запустить не удалось: сайт остановлен, нужен разбор по выводу выше." >&2
  fi
  restore_hint
  exit 1
}

orchestrate() {
  (($# <= 1)) || usage
  local branch="${1:-$DEFAULT_BRANCH}" previous
  git check-ref-format --branch "$branch" >/dev/null 2>&1 || usage
  run_as_root "$@"
  use_installed_config
  previous="$(git -C "$repo" rev-parse HEAD)"
  sync_branch "$branch"
  deploy_checkout && return
  [[ "$previous" != "$(git -C "$repo" rev-parse HEAD)" ]] || { restore_hint; exit 1; }
  roll_back "$previous"
}

if [[ "${1:-}" != "$CURRENT" ]]; then orchestrate "$@"; exit 0; fi
(($# == 1)) || usage
run_as_root "$@"
use_installed_config
native="$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py"
if python3 "$native" detect; then exec python3 "$native" deploy; fi
source "$(dirname "${BASH_SOURCE[0]}")/backup.sh"
ops_init
ops_lock
bash "$REPO_DIR/scripts/preflight.sh"
started="$(date +%s)"
revision="$(git rev-parse HEAD)"
record() {
  python3 - "$STATE_DIR" "$revision" "$started" "$1" <<'PY'
import json, os, pathlib, sys, time
state, commit, started, status = sys.argv[1:]
record = {"commit": commit, "started_at": int(started), "finished_at": int(time.time()), "status": status}
path = pathlib.Path(state)/'last-deploy-attempt.json.partial'
path.write_text(json.dumps(record)+'\n')
os.replace(path, pathlib.Path(state)/'last-deploy-attempt.json')
if status == 'ok':
    path.write_text(json.dumps(record)+'\n')
    os.replace(path, pathlib.Path(state)/'last-deploy.json')
PY
}
trap 'result=$?; if [[ "$result" != 0 ]]; then record failed; echo "Обновление прервано. Не удаляйте тома: проверьте runbook и compose ps." >&2; fi' EXIT
"${compose[@]}" images --format json > "$STATE_DIR/pre-deploy-images.json"
"${compose[@]}" build --build-arg "VCS_REF=$revision"
"${compose[@]}" run --rm --no-deps api python -c '
from club_api.core.config import load_settings
settings = load_settings()
errors = settings.production_errors()
if settings.APP_ENV != "production": errors.append("Деплой требует APP_ENV=production")
if errors: raise SystemExit("\n".join(errors))
'

if [[ -n "$("${compose[@]}" ps -q postgres)" ]]; then backup_snapshot keep-stopped; fi
if "${compose[@]}" config --services | grep -qx mailpit; then "${compose[@]}" up -d --wait --no-deps mailpit; fi
"${compose[@]}" up -d --wait --no-deps postgres
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps bootstrap
"${compose[@]}" up -d --wait --no-deps api web caddy
"${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
if [[ -n "${HTTPS_CA_FILE:-}" && ! -f "$HTTPS_CA_FILE" ]]; then
  case "$(ops_value PUBLIC_URL)" in https://localhost:*|https://localhost)
    "${compose[@]}" cp caddy:/data/caddy/pki/authorities/local/root.crt "$HTTPS_CA_FILE";;
    *) echo 'HTTPS_CA_FILE отсутствует; нельзя подтвердить доверие сертификату' >&2; exit 1;;
  esac
fi
ops_http_check
record ok
echo "Обновление $revision проверено через Caddy: сайт и /api/ready. Пользовательские сценарии проверяются отдельно."
