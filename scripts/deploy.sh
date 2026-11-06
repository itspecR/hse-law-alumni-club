#!/usr/bin/env bash
set -euo pipefail
DEFAULT_BRANCH=main
INSTALLED_ENV_FILE=/etc/club/compose.env
CURRENT=--current
CHECKOUT_UMASK=022
script="$(realpath "${BASH_SOURCE[0]}")"
repo="$(dirname "$(dirname "$script")")"

usage() {
  echo "Использование: ./scripts/deploy.sh [ветка] — без ветки выкатывается $DEFAULT_BRANCH; ./scripts/deploy.sh $CURRENT — папка как есть" >&2
  exit 2
}

run_as_root() {
  [[ "$(id -u)" = 0 ]] && return
  exec sudo env ${ENV_FILE:+ENV_FILE="$ENV_FILE"} ${DEPLOY_COMMIT:+DEPLOY_COMMIT="$DEPLOY_COMMIT"} bash "$script" "$@"
}

use_installed_config() {
  if [[ -z "${ENV_FILE:-}" && -f "$INSTALLED_ENV_FILE" ]]; then export ENV_FILE="$INSTALLED_ENV_FILE"; fi
}

switch_to() {
  (
    umask "$CHECKOUT_UMASK"
    git -C "$repo" checkout -q -f --detach "$1"
    git -C "$repo" clean -fdq
  )
}

pinned_target() {
  local branch="refs/remotes/origin/$1"
  [[ -n "${DEPLOY_COMMIT:-}" ]] || { echo "$branch"; return; }
  if [[ ! "$DEPLOY_COMMIT" =~ ^[0-9a-f]{40}$ ]] || ! git -C "$repo" merge-base --is-ancestor "$DEPLOY_COMMIT" "$branch" 2>/dev/null; then
    echo "Коммит DEPLOY_COMMIT=$DEPLOY_COMMIT не входит в ветку $1" >&2
    return 1
  fi
  echo "$DEPLOY_COMMIT"
}

sync_branch() {
  local target
  echo "Получаем ветку $1 из GitHub; локальные правки в $repo стираются"
  git -C "$repo" fetch -q origin "+refs/heads/$1:refs/remotes/origin/$1"
  target="$(pinned_target "$1")"
  switch_to "$target"
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
exec python3 "$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py" deploy
