#!/usr/bin/env bash
set -euo pipefail

seed_missing_tree() {
  local src="$1"
  local dest="$2"
  if [[ ! -d "$src" ]]; then
    return 0
  fi
  mkdir -p "$dest"
  cp -a -n "$src"/. "$dest"/
}

if [[ "$(id -u)" == "0" ]]; then
  mkdir -p /app/data /app/config /app/certs /app/logs /app/plugins

  # /app/data is normally a persistent host bind mount. That mount masks the
  # repository templates copied into the image, so seed missing defaults before
  # the Python process looks for data/repository/flows.
  seed_missing_tree /app/default-data/repository /app/data/repository
  seed_missing_tree /app/default-config /app/config

  if ! chown -R pawflow:pawflow /app/data /app/config /app/certs /app/logs /app/plugins 2>/dev/null; then
    echo "WARN  Could not chown PawFlow bind mounts; container user may not be able to write persistent data." >&2
  fi

  if [[ -S /var/run/docker.sock ]]; then
    docker_gid="$(stat -c '%g' /var/run/docker.sock)"
    docker_group="$(getent group "${docker_gid}" | cut -d: -f1 || true)"
    if [[ -z "${docker_group}" ]]; then
      docker_group="pawflow-docker"
      groupadd -g "${docker_gid}" "${docker_group}"
    fi
    usermod -aG "${docker_group}" pawflow
  fi

  exec gosu pawflow "$@"
fi

exec "$@"
