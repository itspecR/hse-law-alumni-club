#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temp="$(sudo mktemp -d /tmp/club-install.XXXXXX)"
config="$temp/config"
restore_config="$temp/restore"
compose=(sudo docker compose --env-file "$config/compose.env" -f "$temp/repo/deploy/compose.mariadb.yml")
cleanup() {
  result=$?
  trap - EXIT
  if sudo test -f "$config/install.json"; then
    if ! "${compose[@]}" down --volumes --remove-orphans --timeout 30; then result=1; fi
  fi
  if sudo test -f "$restore_config/compose.env"; then
    if ! sudo docker compose --env-file "$restore_config/compose.env" -f "$temp/repo/deploy/compose.mariadb.yml" down --volumes --remove-orphans --timeout 30; then result=1; fi
  fi
  if ! sudo python3 - "$config" "$restore_config" "$temp/repo/scripts/lib" <<'PY'
import json,pathlib,sys
from urllib.parse import urlsplit
sys.path.insert(0,sys.argv[3])
from mariadb_common import read_env,sql,database_name
for directory in map(pathlib.Path,sys.argv[1:3]):
    metadata=directory/'install.json'
    if metadata.exists():
        values=json.loads(metadata.read_text())
        names=[values['database'],values['operator'],values['api']]
    elif (directory/'compose.env').exists():
        values=read_env(directory/'compose.env')
        names=[values['CLUB_DATABASE'],urlsplit(read_env(directory/'operator.env')['DATABASE_URL']).username,
               urlsplit(read_env(directory/'runtime.env')['CHECKOUT_DATABASE_URL']).username]
    else: continue
    for name in names: database_name(name)
    sql(f"DROP DATABASE `{names[0]}`; DROP USER IF EXISTS '{names[1]}'@'localhost','{names[2]}'@'localhost';")
PY
  then result=1; fi
  if ! sudo rm -rf -- "$temp"; then result=1; fi
  exit "$result"
}
trap cleanup EXIT
sudo git -c safe.directory="$repo" clone --local --no-hardlinks "$repo" "$temp/repo"
sudo "$temp/repo/scripts/install.sh" --local --config-dir "$config"
verify() {
  sudo python3 - "$config" "$1" <<'PY'
import hashlib, json, pathlib, ssl, sys, urllib.request
directory = pathlib.Path(sys.argv[1])
context = ssl.create_default_context(cafile=str(directory / 'local-ca.crt'))
credentials = dict(line.split(': ', 1) for line in (directory / 'initial-admin.txt').read_text().splitlines())
request = urllib.request.Request('https://localhost:9443/api/auth/admin-login',
    data=json.dumps({'email': credentials['Email'], 'password': credentials['Password']}).encode(),
    headers={'Content-Type': 'application/json', 'Origin': 'https://localhost:9443'})
with urllib.request.urlopen(request, context=context) as response:
    assert response.status == 200, 'Первичный пароль не даёт войти'
with urllib.request.urlopen('https://localhost:9443/api/ready', context=context) as response:
    assert json.load(response)['status'] == 'ok'
fingerprints = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                for name in ('runtime.env', 'operator.env', 'compose.env', 'operations.env', 'initial-admin.txt', 'install.json')}
snapshot = directory / 'test-config-fingerprints.json'
if sys.argv[2] == 'first': snapshot.write_text(json.dumps(fingerprints))
else: assert fingerprints == json.loads(snapshot.read_text()), 'Повторный запуск заменил конфигурацию'
print('Установка: доверенный TLS, readiness и вход администратора проверены; секреты не выведены')
PY
}
verify first
if [[ "${CLUB_NATIVE_BROWSER:-false}" == true ]]; then
  sudo python3 "$temp/repo/scripts/tests/prepare-native-live.py" prepare "$config" "$temp/browser"
  verify first
  native_browser() {
    sudo env PATH="$PATH" PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" \
      bash -c 'set -euo pipefail; set -a; source "$1"; set +a; export E2E_LIVE_PHASE="$2"; uv run --directory "$3/frontend" --frozen pytest -q tests/python/test_live.py --tb=short' \
      native-browser "$temp/browser/browser.env" "$1" "$temp/repo"
  }
  native_browser write
  sudo python3 "$temp/repo/scripts/tests/prepare-native-live.py" restart "$config"
  native_browser read
fi
sudo "$temp/repo/scripts/install.sh" --local --config-dir "$config"
verify repeated
echo 'Повторная установка сохранила конфигурацию и доступ администратора'
api_before="$("${compose[@]}" ps -q api)"
sudo env ENV_FILE="$config/compose.env" bash "$temp/repo/scripts/backup.sh"
[[ "$("${compose[@]}" ps -q api)" = "$api_before" ]] || { echo 'Копирование заменило действующий контейнер API' >&2; exit 1; }
snapshot="$(sudo python3 - "$config/state/last-backup.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['snapshot'])
PY
)"
sudo env ENV_FILE="$config/compose.env" python3 "$temp/repo/scripts/lib/mariadb_native.py" prepare-restore --config-dir "$restore_config"
sudo env ENV_FILE="$config/compose.env" SNAPSHOT_FILE="$snapshot" RESTORE_ENV_FILE="$restore_config/compose.env" bash "$temp/repo/scripts/restore.sh"
sudo env ENV_FILE="$restore_config/compose.env" bash "$temp/repo/scripts/deploy.sh" --current
sudo python3 - "$restore_config" "$config/initial-admin.txt" <<'PY'
import json,pathlib,ssl,sys,urllib.request
directory=pathlib.Path(sys.argv[1])
credentials=dict(line.split(': ',1) for line in pathlib.Path(sys.argv[2]).read_text().splitlines())
request=urllib.request.Request('https://localhost:9543/api/auth/admin-login',data=json.dumps({'email':credentials['Email'],'password':credentials['Password']}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,context=ssl.create_default_context(cafile=str(directory/'local-ca.crt'))) as response:
    assert response.status==200
print('Восстановление MariaDB: все таблицы и uploads сверены, прежний пароль открывает панель управления')
PY
