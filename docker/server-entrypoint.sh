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

configure_runtime_user() {
  local run_uid run_gid
  run_uid="$(printenv PAWFLOW_RUN_UID || true)"
  run_gid="$(printenv PAWFLOW_RUN_GID || true)"
  if [[ -z "$run_uid" ]]; then run_uid="1000"; fi
  if [[ -z "$run_gid" ]]; then run_gid="1000"; fi

  if [[ ! "$run_uid" =~ ^[0-9]+$ || ! "$run_gid" =~ ^[0-9]+$ ]]; then
    echo "WARN  Invalid PAWFLOW_RUN_UID/GID; keeping image defaults." >&2
    return 0
  fi

  local current_uid current_gid target_group
  current_uid="$(id -u pawflow)"
  current_gid="$(id -g pawflow)"

  if [[ "$current_gid" != "$run_gid" ]]; then
    target_group="$(getent group "$run_gid" | cut -d: -f1 || true)"
    if [[ -n "$target_group" && "$target_group" != "pawflow" ]]; then
      usermod -g "$target_group" pawflow
    else
      groupmod -g "$run_gid" pawflow
    fi
  fi

  if [[ "$current_uid" != "$run_uid" ]]; then
    if getent passwd "$run_uid" >/dev/null; then
      echo "ERROR PAWFLOW_RUN_UID $run_uid already exists in container; refusing to run with wrong bind-mount ownership." >&2
      exit 1
    fi
    usermod -u "$run_uid" pawflow
  fi
}

if [[ "$(id -u)" == "0" ]]; then
  configure_runtime_user
  mkdir -p /app/data /app/config /app/certs /app/logs /app/plugins

  # /app/data is normally a persistent host bind mount. That mount masks the
  # repository templates copied into the image, so seed missing defaults before
  # the Python process looks for data/repository/flows.
  seed_missing_tree /app/default-data/repository /app/data/repository
  seed_missing_tree /app/default-config /app/config

  if ! chown -R pawflow:"$(id -gn pawflow)" /app/data /app/config /app/certs /app/logs /app/plugins 2>/dev/null; then
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
