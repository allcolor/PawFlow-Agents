#!/usr/bin/env bash
# Install and start PawFlow from scratch with all required Docker images.
#
# This installer is meant to be run from Linux, macOS, WSL2, or a native
# Windows Bash shell backed by Docker Desktop Linux containers.
#
# Usage:
#   bash scripts/install-pawflow.sh --port PORT
#   bash scripts/install-pawflow.sh --version 1.0.0 --port PORT
#   bash scripts/install-pawflow.sh --from-source --version 1.0.0 --port PORT
#   bash scripts/install-pawflow.sh --native --port PORT
#   bash scripts/install-pawflow.sh --dir ~/pawflow-src --port PORT
#   bash scripts/install-pawflow.sh --runtime-dir ~/.pawflow/runtime/latest --port PORT
#   bash scripts/install-pawflow.sh --pull-server --image ghcr.io/allcolor/pawflow:latest --port PORT
#   bash scripts/install-pawflow.sh --pull-images --version 1.0.0 --port PORT
#   bash scripts/install-pawflow.sh --check-updates
#   bash scripts/install-pawflow.sh --self-update

set -euo pipefail

IMAGE="$(printenv PAWFLOW_IMAGE || true)"
IMAGE_REPO="$(printenv PAWFLOW_IMAGE_REPO || true)"
RELAY_MINIMAL_IMAGE_REPO="$(printenv PAWFLOW_RELAY_MINIMAL_IMAGE_REPO || true)"
RELAY_DEV_IMAGE_REPO="$(printenv PAWFLOW_RELAY_DEV_IMAGE_REPO || true)"
REPO_URL="$(printenv PAWFLOW_REPO_URL || true)"
INSTALL_DIR="$(printenv PAWFLOW_INSTALL_DIR || true)"
RUNTIME_DIR="$(printenv PAWFLOW_RUNTIME_DIR || true)"
PORT="$(printenv PAWFLOW_PORT || true)"
HOST="$(printenv PAWFLOW_HOST || true)"
HOST_SET=0
PAWFLOW_HOME="$(printenv PAWFLOW_HOME || true)"
VERSION="$(printenv PAWFLOW_VERSION || true)"
SERVER_MODE="$(printenv PAWFLOW_SERVER_MODE || true)"
RUNTIME_IMAGE_MODE="$(printenv PAWFLOW_RUNTIME_IMAGE_MODE || true)"
START_TARGET="$(printenv PAWFLOW_START_TARGET || true)"
DOCKER_PLATFORM="$(printenv PAWFLOW_DOCKER_PLATFORM || true)"
PYTHON_BIN="$(printenv PAWFLOW_PYTHON || true)"
VENV_DIR="$(printenv PAWFLOW_VENV_DIR || true)"
RELAY_MINIMAL_IMAGE="$(printenv PAWFLOW_RELAY_MINIMAL_IMAGE || printenv PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE || true)"
RELAY_DEV_IMAGE="$(printenv PAWFLOW_RELAY_DEV_IMAGE || true)"
RELAY_IMAGE_VERSION="$(printenv PAWFLOW_RELAY_IMAGE_VERSION || true)"
CLI_LLM_IMAGE="$(printenv PAWFLOW_CLI_LLM_IMAGE || true)"
GHCR_USER="$(printenv PAWFLOW_GHCR_USER || printenv GHCR_USER || true)"
GHCR_TOKEN="$(printenv PAWFLOW_GHCR_TOKEN || printenv GHCR_TOKEN || true)"
CONTAINER="$(printenv PAWFLOW_CONTAINER || true)"
CLEAN_OLD_IMAGES="$(printenv PAWFLOW_CLEAN_OLD_IMAGES || true)"
NETWORK_MODE="$(printenv PAWFLOW_NETWORK_MODE || true)"

if [[ -z "$IMAGE_REPO" ]]; then IMAGE_REPO="ghcr.io/allcolor/pawflow"; fi
if [[ -z "$RELAY_MINIMAL_IMAGE_REPO" ]]; then RELAY_MINIMAL_IMAGE_REPO="ghcr.io/allcolor/pawflow-relay-minimal"; fi
if [[ -z "$RELAY_DEV_IMAGE_REPO" ]]; then RELAY_DEV_IMAGE_REPO="ghcr.io/allcolor/pawflow-relay-dev"; fi
if [[ -z "$REPO_URL" ]]; then REPO_URL="https://github.com/allcolor/PawFlow-Agents.git"; fi
if [[ -z "$INSTALL_DIR" ]]; then INSTALL_DIR="$HOME/pawflow-src"; fi
if [[ -n "$HOST" ]]; then HOST_SET=1; fi
if [[ -z "$HOST" ]]; then HOST="0.0.0.0"; fi
if [[ -z "$PAWFLOW_HOME" ]]; then PAWFLOW_HOME="$HOME/pawflow"; fi
if [[ -z "$SERVER_MODE" ]]; then SERVER_MODE="auto"; fi
if [[ -z "$RUNTIME_IMAGE_MODE" ]]; then RUNTIME_IMAGE_MODE="auto"; fi
if [[ -z "$START_TARGET" ]]; then START_TARGET="container"; fi
if [[ -z "$CLI_LLM_IMAGE" ]]; then CLI_LLM_IMAGE="pawflow-claude-code:latest"; fi
if [[ -z "$CONTAINER" ]]; then CONTAINER="pawflow-server"; fi
if [[ -z "$CLEAN_OLD_IMAGES" ]]; then CLEAN_OLD_IMAGES="1"; fi

RUN_DOCTOR=1
START_SERVER=1
CHECK_UPDATES=0
SELF_UPDATE=0
SKIP_APPARMOR="$(printenv PAWFLOW_SKIP_APPARMOR || true)"
OLD_PAWFLOW_IMAGE_IDS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-source|--source|--build-server) SERVER_MODE="source"; shift ;;
    --pull-server) SERVER_MODE="pull"; shift ;;
    --pull-images) SERVER_MODE="pull"; RUNTIME_IMAGE_MODE="pull"; shift ;;
    --build-images) SERVER_MODE="source"; RUNTIME_IMAGE_MODE="source"; shift ;;
    --runtime-image-mode) RUNTIME_IMAGE_MODE="$2"; shift 2 ;;
    --relay-minimal-image) RELAY_MINIMAL_IMAGE="$2"; shift 2 ;;
    --relay-dev-image) RELAY_DEV_IMAGE="$2"; shift 2 ;;
    --cli-llm-image) CLI_LLM_IMAGE="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --image-repo) IMAGE_REPO="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --runtime-dir) RUNTIME_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; HOST_SET=1; shift 2 ;;
    --network-host) NETWORK_MODE="host"; shift ;;
    --network) NETWORK_MODE="$2"; shift 2 ;;
    --home) PAWFLOW_HOME="$2"; shift 2 ;;
    --platform) DOCKER_PLATFORM="$2"; shift 2 ;;
    --native) START_TARGET="native"; shift ;;
    --container) START_TARGET="container"; shift ;;
    --skip-doctor) RUN_DOCTOR=0; shift ;;
    --skip-apparmor) SKIP_APPARMOR=1; shift ;;
    --no-start) START_SERVER=0; shift ;;
    --check-updates) CHECK_UPDATES=1; shift ;;
    --self-update) SELF_UPDATE=1; shift ;;
    --keep-old-images) CLEAN_OLD_IMAGES=0; shift ;;
    --help|-h)
      sed -n '1,17p' "$0"
      cat <<'HELP'

Options:
  --version VERSION  Install this PawFlow server version. Relay image tags come from the selected release catalog.
                     Defaults to the latest published release when omitted (resolved from GitHub for image installs).
  --from-source      Build the server from source. With --version, checkout that exact git tag; without it, checkout main.
  --pull-server      Require pulling the server image. Fails if the image is not available.
  --pull-images      Require pulling server + redistributable relay images.
  --build-images     Build server + relay images from source.
  --runtime-image-mode MODE
                     Relay image mode: auto, pull, or source (default: auto).
  --image TAG        Full server image tag to build, pull, or run.
  --image-repo REPO  Server image repository when --image is not set (default: ghcr.io/allcolor/pawflow).
  --relay-minimal-image TAG
                     Minimal relay image tag (default: ghcr.io/allcolor/pawflow-relay-minimal:<relay_image_version|latest>).
  --relay-dev-image TAG
                     Full relay image tag (default: ghcr.io/allcolor/pawflow-relay-dev:<relay_image_version|latest>).
  --cli-llm-image TAG
                     Local CLI LLM image tag (default: pawflow-claude-code:latest).
  --repo URL         Git repository to clone when the script is not run from a checkout.
  --dir PATH         Source checkout directory for cloned installs.
  --runtime-dir PATH Host directory used for artifacts extracted from the server image in image installs.
  PAWFLOW_GHCR_USER / PAWFLOW_GHCR_TOKEN
                     Optional GHCR credentials for private image pulls. Token needs read:packages.
  --port PORT        Host/server port selected for this install.
  --host HOST        Server bind host. Container default is 0.0.0.0; native default is 127.0.0.1.
  --network-host     Run the container with host networking so every port it
                     opens (incl. dynamic httpListener flow ports, unknown in
                     advance) is reachable on the host. This is the DEFAULT on
                     Linux; the in-container bind defaults to 0.0.0.0 so sibling
                     bridge containers (managed relays) can reach the listener
                     via the host-gateway IP. Keep ports off the public internet
                     with the host firewall, and/or front them with a reverse
                     proxy (e.g. Caddy).
  --network MODE     Container network mode: 'host' or 'bridge'. Default: host on
                     Linux, bridge on macOS/Windows (host networking only binds
                     the Docker VM there, not the host).
  --home PATH        Persistent PawFlow home (default: ~/pawflow).
  --platform VALUE   Docker build platform, for example linux/amd64.
  --native           Start PawFlow natively in a Python venv after building runtime images.
  --container        Start PawFlow server in Docker after building runtime images (default).
  --no-start         Build images but do not start the server container.
  --skip-apparmor    Do not install/load the PawFlow AppArmor profiles on Linux hosts.
  --check-updates    Query GitHub releases, show the installed server image tag, and print the recommended update command.
  --self-update      Replace installer scripts from the latest release zip, then exit. Rerun the installer afterward.
  --keep-old-images  Do not remove older PawFlow server/relay image tags after a successful container start.

Default server/runtime mode is auto: try prebuilt images first, then build from
source if an image is unavailable. The Claude/Codex/Gemini/Antigravity CLI image
is always built locally because Claude Code and Antigravity are not redistributed
by PawFlow images.

Images:
  pawflow-claude-code:latest   local build: Claude Code, Codex, Gemini, and Antigravity CLIs
  ghcr.io/allcolor/pawflow-relay-minimal:<tag> prebuilt/build fallback: protected server minimal relay
  ghcr.io/allcolor/pawflow-relay-dev:<tag>     prebuilt/build fallback: full server relay image
HELP
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

optional_python_cmd() {
  if [[ -n "$PYTHON_BIN" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf '%s' "$PYTHON_BIN"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "python"
    return 0
  fi
}

http_get() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url"
    return 0
  fi
  local py
  py="$(optional_python_cmd)"
  if [[ -n "$py" ]]; then
    "$py" - "$url" <<'PY'
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=30) as response:
    sys.stdout.write(response.read().decode("utf-8"))
PY
    return 0
  fi
  echo "Missing required command: curl or python for GitHub requests." >&2
  return 1
}

download_url() {
  local url="$1" dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL -o "$dest" "$url"
    return 0
  fi
  local py
  py="$(optional_python_cmd)"
  if [[ -n "$py" ]]; then
    "$py" - "$url" "$dest" <<'PY'
import shutil
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=120) as response, open(sys.argv[2], "wb") as out:
    shutil.copyfileobj(response, out)
PY
    return 0
  fi
  echo "Missing required command: curl or python for downloads." >&2
  return 1
}

normalize_version() {
  local value="$1"
  value="${value#v}"
  printf '%s' "$value"
}

github_latest_version() {
  local api="https://api.github.com/repos/allcolor/PawFlow-Agents/releases?per_page=20" json latest py
  json="$(http_get "$api")"
  py="$(optional_python_cmd)"
  if [[ -n "$py" ]]; then
    latest="$(printf '%s' "$json" | "$py" -c '
import json
import sys

releases = json.load(sys.stdin)
published = [r for r in releases if not r.get("draft") and r.get("tag_name")]
if published:
    latest = max(published, key=lambda r: r.get("published_at") or r.get("created_at") or "")
    print(latest.get("tag_name", ""))
')"
  else
    latest="$(printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  fi
  if [[ -z "$latest" ]]; then
    echo "Could not parse latest PawFlow release from GitHub." >&2
    return 1
  fi
  normalize_version "$latest"
}

installed_server_image() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  docker inspect -f '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true
}

image_tag() {
  local image="$1"
  if [[ -z "$image" || "$image" != *:* ]]; then
    return 0
  fi
  normalize_version "${image##*:}"
}

relay_image_version() {
  local repo_dir="$1" catalog="$1/config/relay_image_catalog.json" py
  if [[ -n "$RELAY_IMAGE_VERSION" ]]; then
    printf '%s' "$RELAY_IMAGE_VERSION"
    return 0
  fi
  if [[ -f "$catalog" ]]; then
    py="$(optional_python_cmd)"
    if [[ -n "$py" ]]; then
      "$py" - "$catalog" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("relay_image_version", ""))
PY
      return 0
    fi
    sed -n 's/.*"relay_image_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$catalog" | head -n 1
    return 0
  fi
  if [[ -n "$VERSION" ]]; then
    printf '%s' "$VERSION"
  else
    printf '%s' latest
  fi
}

check_updates() {
  local latest installed_image installed_tag selected_port
  latest="$(github_latest_version)"
  installed_image="$(installed_server_image)"
  installed_tag="$(image_tag "$installed_image")"
  selected_port="$PORT"
  if [[ -z "$selected_port" ]]; then selected_port="PORT"; fi

  echo "Latest PawFlow release: $latest"
  if [[ -n "$installed_image" ]]; then
    echo "Installed server image: $installed_image"
    echo "Installed version: ${installed_tag}"
  else
    echo "Installed server image: none detected for container '$CONTAINER'"
  fi

  if [[ -n "$installed_tag" && "$installed_tag" == "$latest" ]]; then
    echo "Server update: already on the latest release."
  else
    echo "Server update available. Recommended command:"
    echo "  bash $0 --version $latest --port $selected_port --pull-images"
  fi

  echo "Installer refresh command:"
  echo "  bash $0 --self-update"
}

self_update_installer() {
  local latest url tmp zip py script_dir rel
  latest="$(github_latest_version)"
  url="https://github.com/allcolor/PawFlow-Agents/releases/download/$latest/pawflow-install-$latest.zip"
  tmp="$(mktemp -d)"
  zip="$tmp/pawflow-install-$latest.zip"
  trap "rm -rf '$tmp'" EXIT
  echo "Downloading PawFlow installer $latest: $url"
  download_url "$url" "$zip"
  py="$(optional_python_cmd)"
  if [[ -z "$py" ]]; then
    echo "Missing required command: python for installer zip extraction." >&2
    exit 1
  fi
  "$py" - "$zip" "$tmp/extracted" <<'PY'
from pathlib import Path
import sys
import zipfile

archive = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
with zipfile.ZipFile(archive) as zf:
    for name in ("scripts/install-pawflow.sh", "scripts/install-pawflow.ps1"):
        try:
            zf.extract(name, out_dir)
        except KeyError:
            pass
PY
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for rel in scripts/install-pawflow.sh scripts/install-pawflow.ps1; do
    if [[ -f "$tmp/extracted/$rel" ]]; then
      cp "$tmp/extracted/$rel" "$script_dir/$(basename "$rel")"
      if [[ "$rel" == "scripts/install-pawflow.sh" ]]; then chmod +x "$script_dir/$(basename "$rel")"; fi
      echo "Updated $script_dir/$(basename "$rel")"
    fi
  done
  echo "Installer scripts updated to release $latest. Rerun the installer command you wanted to execute."
}

cleanup_old_pawflow_images() {
  if [[ "$CLEAN_OLD_IMAGES" != "1" && "$CLEAN_OLD_IMAGES" != "true" && "$CLEAN_OLD_IMAGES" != "yes" ]]; then
    return 0
  fi
  local current_images image repo tag ref id
  current_images=" $IMAGE $RELAY_MINIMAL_IMAGE $RELAY_DEV_IMAGE "
  echo "Cleaning older PawFlow GHCR image tags not used by this install."
  while read -r repo tag id; do
    if [[ -z "$repo" || "$repo" == "<none>" || "$tag" == "<none>" ]]; then continue; fi
    case "$repo" in
      "$IMAGE_REPO"|"$RELAY_MINIMAL_IMAGE_REPO"|"$RELAY_DEV_IMAGE_REPO") ;;
      *) continue ;;
    esac
    ref="$repo:$tag"
    if [[ "$current_images" == *" $ref "* ]]; then continue; fi
    echo "Removing old image tag: $ref"
    docker rmi "$ref" >/dev/null 2>&1 || true
  done < <(docker images --format '{{.Repository}} {{.Tag}} {{.ID}}')
}

capture_existing_pawflow_image_ids() {
  local cli_repo="$CLI_LLM_IMAGE"
  if [[ "${CLI_LLM_IMAGE##*/}" == *:* ]]; then cli_repo="$(printf '%s' "$CLI_LLM_IMAGE" | sed 's/:[^/]*$//')"; fi
  OLD_PAWFLOW_IMAGE_IDS="$(docker images --format '{{.Repository}} {{.Tag}} {{.ID}}' | while read -r repo tag id; do
    case "$repo" in
      "$IMAGE_REPO"|"$RELAY_MINIMAL_IMAGE_REPO"|"$RELAY_DEV_IMAGE_REPO") printf '%s\n' "$id" ;;
      "$cli_repo") printf '%s\n' "$id" ;;
    esac
  done | sort -u)"
}

cleanup_retagged_pawflow_images() {
  if [[ "$CLEAN_OLD_IMAGES" != "1" && "$CLEAN_OLD_IMAGES" != "true" && "$CLEAN_OLD_IMAGES" != "yes" ]]; then
    return 0
  fi
  if [[ -z "$OLD_PAWFLOW_IMAGE_IDS" ]]; then
    return 0
  fi
  local current_ids old_id repo tag
  current_ids="$(docker images --format '{{.ID}}' | sort -u)"
  while read -r old_id; do
    if [[ -z "$old_id" ]]; then continue; fi
    if ! grep -qx "$old_id" <<<"$current_ids"; then continue; fi
    read -r repo tag < <(docker image inspect -f '{{index .RepoTags 0}}' "$old_id" 2>/dev/null | awk -F: '{print $1, $2}') || true
    if [[ "$repo" != "<none>" && "$tag" != "<none>" && -n "$repo" && -n "$tag" ]]; then continue; fi
    echo "Removing old untagged PawFlow image id: $old_id"
    if ! docker rmi -f "$old_id" >/dev/null 2>&1; then
      echo "Warning: failed to remove old untagged PawFlow image id: $old_id" >&2
    fi
  done <<<"$OLD_PAWFLOW_IMAGE_IDS"
  docker image prune -f --filter "dangling=true" >/dev/null 2>&1 || true
}

if [[ "$SELF_UPDATE" == "1" ]]; then
  self_update_installer
  exit 0
fi

if [[ "$CHECK_UPDATES" == "1" ]]; then
  check_updates
  exit 0
fi

if [[ -z "$PORT" ]]; then
  echo "ERROR: choose a port with --port PORT or PAWFLOW_PORT=PORT." >&2
  exit 2
fi

if [[ "$SERVER_MODE" == "auto" && -n "$VERSION" ]]; then
  SERVER_MODE="pull"
fi
if [[ "$RUNTIME_IMAGE_MODE" == "auto" && -n "$VERSION" ]]; then
  RUNTIME_IMAGE_MODE="pull"
fi

# Default to the latest published release when no explicit version or image is
# requested, so `--pull-images` (and other image installs) pin a concrete tag
# instead of relying on a floating `:latest`. Source builds keep their `main`
# default and are left untouched.
if [[ -z "$VERSION" && -z "$IMAGE" && "$SERVER_MODE" != "source" ]]; then
  echo "No --version specified; resolving the latest PawFlow release from GitHub." >&2
  VERSION="$(github_latest_version)"
  echo "Using latest PawFlow version: $VERSION" >&2
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

maybe_login_ghcr() {
  if [[ -z "$GHCR_TOKEN" ]]; then
    return 0
  fi
  if [[ -z "$GHCR_USER" ]]; then
    echo "ERROR: PAWFLOW_GHCR_TOKEN is set but PAWFLOW_GHCR_USER is missing." >&2
    exit 2
  fi
  echo "Logging in to GHCR as $GHCR_USER"
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
}

find_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "Configured Python not found: $PYTHON_BIN" >&2; exit 1; }
    printf '%s' "$PYTHON_BIN"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "python"
    return 0
  fi
  echo "Missing required command: python3 or python" >&2
  exit 1
}

find_optional_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      printf '%s' "$PYTHON_BIN"
    fi
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "python"
    return 0
  fi
}

require_python() {
  PYTHON_BIN="$(find_python)"
}

detect_host() {
  local kernel os
  kernel="$(uname -s 2>/dev/null || echo unknown)"
  os="unknown"
  case "$kernel" in
    Linux*) os="linux" ;;
    Darwin*) os="macos" ;;
    MINGW*|MSYS*|CYGWIN*) os="windows-shell" ;;
  esac
  if [[ "$os" == "linux" ]] && grep -qi microsoft /proc/version 2>/dev/null; then
    os="wsl"
  fi
  printf '%s' "$os"
}

ensure_checkout() {
  local script_dir candidate
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  candidate="$(cd "$script_dir/.." && pwd)"
  if [[ -f "$candidate/Dockerfile" && -f "$candidate/docker/claude-code/build.sh" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  need_cmd git
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "Using existing PawFlow checkout: $INSTALL_DIR" >&2
  elif [[ -e "$INSTALL_DIR" ]]; then
    echo "Install directory exists but is not a git checkout: $INSTALL_DIR" >&2
    exit 1
  else
    echo "Cloning PawFlow: $REPO_URL -> $INSTALL_DIR" >&2
    git clone "$REPO_URL" "$INSTALL_DIR" >&2
  fi
  printf '%s' "$INSTALL_DIR"
}

image_runtime_dir() {
  local image="$1" tag safe
  tag="${image##*:}"
  if [[ "$tag" == "$image" || -z "$tag" ]]; then tag="latest"; fi
  safe="${tag//[^A-Za-z0-9._-]/_}"
  if [[ -n "$RUNTIME_DIR" ]]; then
    printf '%s' "$RUNTIME_DIR"
  else
    printf '%s' "$HOME/.pawflow/runtime/$safe"
  fi
}

extract_image_artifacts() (
  local image="$1" out_dir="$2" cid rel
  echo "Extracting PawFlow runtime artifacts from image: $image -> $out_dir" >&2
  mkdir -p "$out_dir"
  cid="$(docker create "$image" true)"
  trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT

  for rel in \
    scripts/run-pawflow-docker.sh \
    scripts/doctor-pawflow.sh \
    scripts/doctor-pawflow.ps1 \
    scripts/install-pawflow.ps1 \
    scripts/test_apparmor_profile.sh \
    scripts/test_apparmor_relay_profile.sh \
    config/relay_image_catalog.json \
    docker/apparmor \
    docker/claude-code \
    docker/pawflow_sdk \
    tools/mcp_bridge.py \
    core/tool_json.py \
    pawflow_relay
  do
    mkdir -p "$out_dir/$(dirname "$rel")"
    rm -rf "$out_dir/$rel"
    if ! docker cp "$cid:/app/$rel" "$out_dir/$rel"; then
      case "$rel" in
        scripts/install-pawflow.ps1|docker/apparmor|scripts/test_apparmor_profile.sh|scripts/test_apparmor_relay_profile.sh)
          echo "Warning: $image does not contain $rel; continuing without this artifact." >&2
          continue
          ;;
      esac
      return 1
    fi
  done

  chmod +x \
    "$out_dir/scripts/run-pawflow-docker.sh" \
    "$out_dir/scripts/doctor-pawflow.sh" \
    "$out_dir/docker/claude-code/build.sh"
  chmod +x \
    "$out_dir/scripts/test_apparmor_profile.sh" \
    "$out_dir/scripts/test_apparmor_relay_profile.sh" 2>/dev/null || true
)

prepare_checkout_ref() {
  local repo_dir="$1"
  if [[ ! -d "$repo_dir/.git" ]]; then
    if [[ "$SERVER_MODE" == "source" ]]; then
      echo "Source checkout is not a git repository, so the requested ref cannot be selected: $repo_dir" >&2
      exit 1
    fi
    return 0
  fi

  if [[ "$SERVER_MODE" != "source" && -z "$VERSION" ]]; then
    return 0
  fi

  if [[ -n "$VERSION" ]]; then
    echo "Selecting PawFlow git tag: $VERSION" >&2
    git -C "$repo_dir" fetch --tags origin >&2
    if ! git -C "$repo_dir" rev-parse -q --verify "refs/tags/$VERSION^{commit}" >/dev/null; then
      echo "PawFlow git tag not found: $VERSION" >&2
      exit 1
    fi
    git -C "$repo_dir" checkout --detach "$VERSION" >&2
    return 0
  fi

  echo "Selecting PawFlow source branch: main" >&2
  git -C "$repo_dir" fetch origin main >&2
  git -C "$repo_dir" checkout main >&2
  git -C "$repo_dir" pull --ff-only origin main >&2
}

build_server_image() {
  PAWFLOW_IMAGE="$IMAGE" bash "$REPO_DIR/scripts/build-pawflow-docker.sh"
}

pull_server_image() {
  local pull_args=()
  if [[ -n "$DOCKER_PLATFORM" ]]; then pull_args+=(--platform "$DOCKER_PLATFORM"); fi
  echo "Pulling PawFlow server image: $IMAGE"
  if ! docker pull "${pull_args[@]}" "$IMAGE"; then
    cat >&2 <<MSG

Failed to pull PawFlow server image: $IMAGE

If this GHCR package is private, create a GitHub token with read:packages and rerun:
  export PAWFLOW_GHCR_USER='your-github-user'
  export PAWFLOW_GHCR_TOKEN='ghp_...'
  bash scripts/install-pawflow.sh --version ${VERSION} --port $PORT

MSG
    return 1
  fi
}

pull_image() {
  local image="$1"
  local pull_args=()
  if [[ -n "$DOCKER_PLATFORM" ]]; then pull_args+=(--platform "$DOCKER_PLATFORM"); fi
  echo "Pulling image: $image"
  docker pull "${pull_args[@]}" "$image"
}

build_minimal_relay_image() {
  require_python
  PAWFLOW_PYTHON="$PYTHON_BIN" PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE="$RELAY_MINIMAL_IMAGE" bash "$REPO_DIR/scripts/build-server-minimal-relay.sh"
}

build_full_relay_image() {
  PAWFLOW_RELAY_DEV_IMAGE="$RELAY_DEV_IMAGE" bash "$REPO_DIR/docker/relay-dev/build.sh"
}

ensure_runtime_image() {
  local label="$1"
  local image="$2"
  local build_func="$3"

  if [[ "$RUNTIME_IMAGE_MODE" == "pull" ]]; then
    pull_image "$image"
  elif [[ "$RUNTIME_IMAGE_MODE" == "source" ]]; then
    "$build_func"
  elif [[ "$RUNTIME_IMAGE_MODE" == "auto" ]]; then
    if pull_image "$image"; then
      echo "Using prebuilt $label image: $image"
    else
      echo "Prebuilt $label image unavailable, building from source: $image"
      "$build_func"
    fi
  else
    echo "Invalid PAWFLOW_RUNTIME_IMAGE_MODE: $RUNTIME_IMAGE_MODE (expected auto, source, or pull)" >&2
    exit 2
  fi
}

seed_native_data() {
  local py
  mkdir -p "$PAWFLOW_HOME/data" "$PAWFLOW_HOME/logs"
  if [[ ! -d "$PAWFLOW_HOME/data/repository" ]]; then
    echo "Seeding native PawFlow repository data: $PAWFLOW_HOME/data/repository"
    mkdir -p "$PAWFLOW_HOME/data"
    cp -R "$REPO_DIR/data/repository" "$PAWFLOW_HOME/data/repository"
  fi
  py="$(find_optional_python)"
  if [[ -n "$py" && -d "$REPO_DIR/data/repository" ]]; then
    "$py" - "$REPO_DIR/data/repository" "$PAWFLOW_HOME/data/repository" "$PAWFLOW_HOME/data/system/default_repository_manifest.json" <<'PY'
import json
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
manifest = Path(sys.argv[3])
managed_roots = [
    "agents/global", "configs", "flows/global/default",
    "flows/global/telegram", "flows/global/github",
    "flows/global/cryptos", "flows/global/http_bots",
    "private_gateway_skin/global", "prompts/global", "skills/global",
    "tasks/global", "theme/global",
]
legacy_removed_dirs = ["flows/global/default/pawflow_admin"]

def files_under(root):
    if not root.exists():
        return set()
    return {p.relative_to(src).as_posix() for p in root.rglob("*") if p.is_file()}

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
manifest.write_text(json.dumps({"files": sorted(current_files)}, indent=2) + "\n", encoding="utf-8")
PY
  fi
}

run_native_server() {
  local py venv_python bootstrap_gateway_key bootstrap_gateway_label native_host
  require_python
  py="$(find_python)"
  native_host="$HOST"
  if [[ "$HOST_SET" == "0" ]]; then native_host="127.0.0.1"; fi
  bootstrap_gateway_key="$(printenv PAWFLOW_BOOTSTRAP_GATEWAY_KEY || true)"
  if [[ -z "$bootstrap_gateway_key" ]]; then
    bootstrap_gateway_key="RoyBatty"
    bootstrap_gateway_label="RoyBatty"
  else
    bootstrap_gateway_label="custom value from PAWFLOW_BOOTSTRAP_GATEWAY_KEY"
  fi
  if [[ -z "$VENV_DIR" ]]; then VENV_DIR="$REPO_DIR/.venv-pawflow"; fi
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating native PawFlow virtualenv: $VENV_DIR"
    "$py" -m venv "$VENV_DIR"
  fi
  if [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then
    venv_python="$VENV_DIR/Scripts/python.exe"
  else
    venv_python="$VENV_DIR/bin/python"
  fi
  "$venv_python" -m pip install --upgrade pip
  "$venv_python" -m pip install -e "$REPO_DIR"
  seed_native_data
  cat <<MSG

Starting PawFlow natively.

URL:
  https://localhost:$PORT

Initial bootstrap Private Gateway key:
  $bootstrap_gateway_label

MSG
  cd "$REPO_DIR"
  PAWFLOW_DATA_DIR="$PAWFLOW_HOME/data" \
  PAWFLOW_SERVER_RELAY_IMAGE="$RELAY_DEV_IMAGE" \
  PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE" \
  PAWFLOW_BOOTSTRAP_GATEWAY_KEY="$bootstrap_gateway_key" \
  "$venv_python" "$REPO_DIR/cli.py" start --host "$native_host" --port "$PORT"
}

apparmor_manual_instructions() {
  # $@: profile names that still need installing/updating.
  local name
  echo "AppArmor profiles not loaded ($*). PawFlow still works (containers fall back to apparmor=unconfined). To confine them, run as root:" >&2
  for name in "$@"; do
    echo "  install -m 644 $REPO_DIR/docker/apparmor/$name /etc/apparmor.d/$name && apparmor_parser -r -W /etc/apparmor.d/$name" >&2
  done
}

install_apparmor_profiles() {
  # Load the PawFlow AppArmor profiles (pawflow-mount for provider pools,
  # pawflow-relay for relay containers) so the server confines those
  # containers instead of falling back to apparmor:unconfined. Best-effort:
  # a failure prints manual instructions and never blocks the install.
  # Never runs sudo without asking first: a user without sudo rights would
  # otherwise burn password attempts on a prompt that cannot succeed.
  local profiles_dir="$REPO_DIR/docker/apparmor" name src dest run_root=() pending=() answer=""
  if [[ "$SKIP_APPARMOR" == "1" || "$SKIP_APPARMOR" == "true" || "$SKIP_APPARMOR" == "yes" ]]; then
    echo "AppArmor profiles: skipped (--skip-apparmor)."
    return 0
  fi
  if [[ "$HOST_OS" != "linux" ]]; then
    echo "AppArmor profiles: not applicable on $HOST_OS (no AppArmor in this kernel); PawFlow falls back to apparmor=unconfined, which is a no-op there."
    return 0
  fi
  if [[ ! -d /sys/kernel/security/apparmor ]]; then
    echo "AppArmor profiles: AppArmor is not enabled in this kernel (SELinux distro or AppArmor disabled); skipping. PawFlow containers fall back to apparmor=unconfined."
    return 0
  fi
  if [[ ! -d "$profiles_dir" ]]; then
    echo "Warning: AppArmor profiles not found at $profiles_dir (older image?). Load them manually from a source checkout: sudo install -m 644 docker/apparmor/pawflow-mount docker/apparmor/pawflow-relay /etc/apparmor.d/ && sudo apparmor_parser -r -W /etc/apparmor.d/pawflow-mount /etc/apparmor.d/pawflow-relay" >&2
    return 0
  fi
  # Only touch profiles whose installed copy differs from the shipped one.
  for name in pawflow-mount pawflow-relay; do
    src="$profiles_dir/$name"
    if [[ ! -f "$src" ]]; then
      echo "Warning: AppArmor profile missing from artifacts: $src" >&2
      continue
    fi
    if cmp -s "$src" "/etc/apparmor.d/$name" 2>/dev/null; then
      echo "AppArmor profile already installed and up to date: $name"
      continue
    fi
    pending+=("$name")
  done
  if [[ ${#pending[@]} -eq 0 ]]; then
    return 0
  fi
  if [[ "$(id -u)" != "0" ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      apparmor_manual_instructions "${pending[@]}"
      return 0
    fi
    if [[ -r /dev/tty && -w /dev/tty ]]; then
      printf "Install/update AppArmor profiles (%s) in /etc/apparmor.d via sudo? [y/N] " "${pending[*]}" > /dev/tty
      IFS= read -r answer < /dev/tty || answer=""
      case "$answer" in
        y|Y|yes|YES) run_root=(sudo) ;;
        *)
          apparmor_manual_instructions "${pending[@]}"
          return 0
          ;;
      esac
    else
      # No terminal to ask on: never trigger a blind sudo password prompt.
      apparmor_manual_instructions "${pending[@]}"
      return 0
    fi
  fi
  echo "Installing PawFlow AppArmor profiles: ${pending[*]}"
  for name in "${pending[@]}"; do
    src="$profiles_dir/$name"
    dest="/etc/apparmor.d/$name"
    if "${run_root[@]}" install -m 644 "$src" "$dest" \
        && "${run_root[@]}" apparmor_parser -r -W "$dest"; then
      echo "AppArmor profile loaded and persisted: $name -> $dest"
    else
      echo "Warning: failed to load AppArmor profile '$name'. PawFlow still works (containers fall back to apparmor=unconfined). To confine them, run as root:" >&2
      echo "  install -m 644 $src $dest && apparmor_parser -r -W $dest" >&2
    fi
  done
  echo "Optional validation (replays the confined mounts): sh $REPO_DIR/scripts/test_apparmor_profile.sh and sh $REPO_DIR/scripts/test_apparmor_relay_profile.sh"
}

HOST_OS="$(detect_host)"
REPO_DIR=""
INSTALL_SOURCE=""

# Default container network mode. Host networking exposes EVERY port the
# container opens (including the dynamic ports of deployed httpListener flows,
# unknown in advance) directly on the host, so it is the default. It is only
# effective on Linux: Docker Desktop on macOS/Windows runs containers inside a
# VM where --network host binds the VM, not the host, leaving ports unreachable
# — so default those to bridge (-p publishing). Override with --network host
# or PAWFLOW_NETWORK_MODE=host on those platforms if you know what you want.
if [[ -z "$NETWORK_MODE" ]]; then
  if [[ "$HOST_OS" == "linux" ]]; then NETWORK_MODE="host"; else NETWORK_MODE="bridge"; fi
fi

need_cmd docker
PYTHON_BIN="$(find_optional_python)"
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but the daemon is not reachable." >&2
  echo "Start Docker Desktop/Engine, then rerun the installer." >&2
  exit 1
fi
maybe_login_ghcr

if [[ -z "$IMAGE" ]]; then
  if [[ -n "$VERSION" ]]; then
    IMAGE="$IMAGE_REPO:$VERSION"
  else
    IMAGE="$IMAGE_REPO:latest"
  fi
fi
if [[ -z "$DOCKER_PLATFORM" && "$HOST_OS" == "macos" ]]; then
  DOCKER_PLATFORM="linux/amd64"
fi

if [[ -n "$DOCKER_PLATFORM" ]]; then
  export PAWFLOW_DOCKER_PLATFORM="$DOCKER_PLATFORM"
fi
capture_existing_pawflow_image_ids

if [[ "$SERVER_MODE" == "source" ]]; then
  REPO_DIR="$(ensure_checkout)"
  prepare_checkout_ref "$REPO_DIR"
  INSTALL_SOURCE="source"
fi

echo "Host: $HOST_OS"
echo "Version: ${VERSION}"
echo "Server image: $IMAGE ($SERVER_MODE)"
echo "Runtime image mode: $RUNTIME_IMAGE_MODE"
echo "CLI LLM image: $CLI_LLM_IMAGE (local build)"
echo "Start target: $START_TARGET"
if [[ -n "$DOCKER_PLATFORM" ]]; then
  echo "Docker build platform: $DOCKER_PLATFORM"
fi

if [[ "$SERVER_MODE" == "pull" ]]; then
  pull_server_image
  REPO_DIR="$(image_runtime_dir "$IMAGE")"
  extract_image_artifacts "$IMAGE" "$REPO_DIR"
  INSTALL_SOURCE="image"
elif [[ "$SERVER_MODE" == "source" ]]; then
  build_server_image
elif [[ "$SERVER_MODE" == "auto" ]]; then
  if pull_server_image; then
    echo "Using prebuilt PawFlow server image: $IMAGE"
    REPO_DIR="$(image_runtime_dir "$IMAGE")"
    extract_image_artifacts "$IMAGE" "$REPO_DIR"
    INSTALL_SOURCE="image"
  else
    echo "Prebuilt PawFlow server image unavailable, building from source: $IMAGE"
    REPO_DIR="$(ensure_checkout)"
    SERVER_MODE="source" prepare_checkout_ref "$REPO_DIR"
    build_server_image
    INSTALL_SOURCE="source"
  fi
else
  echo "Invalid PAWFLOW_SERVER_MODE: $SERVER_MODE (expected auto, source, or pull)" >&2
  exit 2
fi

if [[ "$INSTALL_SOURCE" == "image" && "$RUNTIME_IMAGE_MODE" == "auto" ]]; then
  RUNTIME_IMAGE_MODE="pull"
fi
if [[ "$INSTALL_SOURCE" == "image" && "$RUNTIME_IMAGE_MODE" == "source" ]]; then
  echo "Image installs cannot build relay images from source. Use --from-source or --runtime-image-mode pull." >&2
  exit 2
fi
if [[ "$INSTALL_SOURCE" == "image" && "$START_TARGET" == "native" ]]; then
  echo "Native installs require a source checkout. Use --from-source --native, or omit --native to run the pulled server image." >&2
  exit 2
fi

_relay_tag="$(relay_image_version "$REPO_DIR")"
if [[ -z "$_relay_tag" ]]; then
  echo "ERROR: relay_image_version is missing from $REPO_DIR/config/relay_image_catalog.json." >&2
  exit 2
fi
if [[ -z "$RELAY_MINIMAL_IMAGE" ]]; then
  RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE_REPO:$_relay_tag"
fi
if [[ -z "$RELAY_DEV_IMAGE" ]]; then
  RELAY_DEV_IMAGE="$RELAY_DEV_IMAGE_REPO:$_relay_tag"
fi

if [[ "$RUN_DOCTOR" == "1" ]]; then
  if [[ "$INSTALL_SOURCE" == "source" ]]; then
    bash "$REPO_DIR/scripts/doctor-pawflow.sh" --port "$PORT" --source
  else
    bash "$REPO_DIR/scripts/doctor-pawflow.sh" --port "$PORT"
  fi
fi

echo "PawFlow install artifacts: $REPO_DIR ($INSTALL_SOURCE)"
echo "Effective runtime image mode: $RUNTIME_IMAGE_MODE"
echo "Relay image version: $_relay_tag"
echo "Minimal relay image: $RELAY_MINIMAL_IMAGE"
echo "Full relay image: $RELAY_DEV_IMAGE"

echo "Building PawFlow CLI LLM image locally: $CLI_LLM_IMAGE"
PAWFLOW_CLI_LLM_IMAGE="$CLI_LLM_IMAGE" bash "$REPO_DIR/docker/claude-code/build.sh"

ensure_runtime_image "server minimal relay" "$RELAY_MINIMAL_IMAGE" build_minimal_relay_image

ensure_runtime_image "full server relay" "$RELAY_DEV_IMAGE" build_full_relay_image

# Load the AppArmor profiles BEFORE the server starts: at boot the server
# probes the host for them and confines pool/relay containers when present.
install_apparmor_profiles

if [[ "$START_SERVER" != "1" ]]; then
  cleanup_old_pawflow_images
  cleanup_retagged_pawflow_images
  echo "Image build complete. Server start skipped because --no-start was set."
  exit 0
fi

if [[ "$START_TARGET" == "native" ]]; then
  cleanup_old_pawflow_images
  cleanup_retagged_pawflow_images
  run_native_server
elif [[ "$START_TARGET" == "container" ]]; then
  PAWFLOW_IMAGE="$IMAGE" PAWFLOW_CONTAINER="$CONTAINER" PAWFLOW_PORT="$PORT" PAWFLOW_HOST="$HOST" PAWFLOW_NETWORK_MODE="$NETWORK_MODE" PAWFLOW_HOME="$PAWFLOW_HOME" PAWFLOW_SERVER_RELAY_IMAGE="$RELAY_DEV_IMAGE" PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE" bash "$REPO_DIR/scripts/run-pawflow-docker.sh"
  cleanup_old_pawflow_images
  cleanup_retagged_pawflow_images
else
  echo "Invalid PAWFLOW_START_TARGET: $START_TARGET (expected container or native)" >&2
  exit 2
fi

