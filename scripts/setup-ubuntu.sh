#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" = 0 ]] || { echo 'Запустите через sudo' >&2; exit 1; }
source /etc/os-release
[[ "$ID" = ubuntu && "$VERSION_ID" = 24.04 ]] || { echo 'Поддерживается Ubuntu 24.04 LTS' >&2; exit 1; }
apt-get update
apt-get install -y --no-install-recommends git curl ca-certificates openssl tar gzip python3 util-linux rclone jq rsync gnupg
installed() {
  dpkg-query -W -f '${Status}' "$1" 2>/dev/null | grep -qx 'install ok installed'
}
install_docker_ce() {
  if installed podman-docker; then
    echo 'Обнаружен podman-docker; согласуйте его миграцию по docs.docker.com' >&2
    exit 1
  fi
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  source_file="$(mktemp)"
  trap 'rm -f "$source_file"' EXIT
  cat > "$source_file" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  if [[ -f /etc/apt/sources.list.d/docker.sources ]]; then
    cmp -s "$source_file" /etc/apt/sources.list.d/docker.sources || { echo 'Существующий Docker source отличается; проверьте его вручную' >&2; exit 1; }
  else
    install -m 0644 "$source_file" /etc/apt/sources.list.d/docker.sources
  fi
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
  if installed docker.io; then
    apt-get install -y --no-install-recommends docker-buildx docker-compose-v2
  else
    install_docker_ce
  fi
fi
systemctl enable --now docker
docker version --format '{{.Server.Version}}'
docker compose version
docker buildx version
echo 'Зависимости Ubuntu, Docker Engine, Compose и Buildx готовы.'
