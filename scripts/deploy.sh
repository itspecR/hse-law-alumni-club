#!/usr/bin/env bash
set -euo pipefail
if (($#)); then
  echo "deploy.sh выкатывает текущий коммит checkout и не принимает аргументы. Сначала: git fetch origin && git checkout --detach <SHA или origin/ветка>" >&2
  exit 2
fi
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
