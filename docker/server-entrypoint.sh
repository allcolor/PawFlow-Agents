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

sync_managed_repository_defaults() {
  local src="/app/default-data/repository"
  local dest="/app/data/repository"
  local manifest="/app/data/system/default_repository_manifest.json"
  if [[ ! -d "$src" ]]; then
    return 0
  fi
  python3 - "$src" "$dest" "$manifest" <<'PY'
import json
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
manifest = Path(sys.argv[3])

# Only image-owned default scopes are mirrored. User/conversation scopes and
# third-party/global flow packages are intentionally outside this list.
managed_roots = [
    "agents/global",
    "configs",
    "flows/global/default",
    "private_gateway_skin/global",
    "prompts/global",
    "skills/global",
    "tasks/global",
    "theme/global",
]

legacy_removed_dirs = [
    "flows/global/default/pawflow_admin",
]

def files_under(root):
    out = set()
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out.add(path.relative_to(src).as_posix())
    return out

current_files = set()
for rel in managed_roots:
    current_files.update(files_under(src / rel))

old_files = set()
if manifest.exists():
    try:
        old_files = set(json.loads(manifest.read_text(encoding="utf-8")).get("files", []))
    except (OSError, json.JSONDecodeError):
        old_files = set()

for rel in sorted(old_files - current_files, reverse=True):
    target = dest / rel
    if target.exists() and target.is_file():
        target.unlink()

for rel in legacy_removed_dirs:
    target = dest / rel
    source = src / rel
    if target.exists() and not source.exists():
        shutil.rmtree(target)

for rel in managed_roots:
    source = src / rel
    target = dest / rel
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)

for rel in managed_roots:
    root = dest / rel
    if not root.exists():
        continue
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass

manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(
    json.dumps({"files": sorted(current_files)}, indent=2) + "\n",
    encoding="utf-8",
)
PY
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
  sync_managed_repository_defaults
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
