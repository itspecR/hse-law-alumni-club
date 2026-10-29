#!/usr/bin/env bash
set -euo pipefail
native="$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py"
if python3 "$native" detect; then exec python3 "$native" monitor; fi
source "$(dirname "${BASH_SOURCE[0]}")/lib/ops-common.sh"
ops_init
export MONITOR_URL="$(ops_value PUBLIC_URL)"
export MONITOR_JOBS="$(ops_value JOBS_ENABLED true)"
export MONITOR_DPO="$([[ "$(ops_value DPO_SYNC_ENABLED true)" == true && -n "$(ops_value DPO_SITE_URL)" ]] && echo true || echo false)"
export MONITOR_NEWS="$(ops_value NEWS_SYNC_ENABLED false)"
export STATE_DIR
ids="$("${compose[@]}" ps -aq postgres api web caddy)"
[[ -n "$ids" ]] || { echo 'Контейнеры проекта не найдены' >&2; exit 1; }
python3 "$REPO_DIR/scripts/lib/monitor.py" $ids
