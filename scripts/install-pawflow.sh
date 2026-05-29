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
#   bash scripts/install-pawflow.sh --pull-server --image ghcr.io/allcolor/pawflow:latest --port PORT
#   bash scripts/install-pawflow.sh --pull-images --version 1.0.0 --port PORT

set -euo pipefail

IMAGE="$(printenv PAWFLOW_IMAGE || true)"
IMAGE_REPO="$(printenv PAWFLOW_IMAGE_REPO || true)"
RELAY_MINIMAL_IMAGE_REPO="$(printenv PAWFLOW_RELAY_MINIMAL_IMAGE_REPO || true)"
RELAY_DEV_IMAGE_REPO="$(printenv PAWFLOW_RELAY_DEV_IMAGE_REPO || true)"
REPO_URL="$(printenv PAWFLOW_REPO_URL || true)"
INSTALL_DIR="$(printenv PAWFLOW_INSTALL_DIR || true)"
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
CLI_LLM_IMAGE="$(printenv PAWFLOW_CLI_LLM_IMAGE || true)"

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

RUN_DOCTOR=1
START_SERVER=1

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
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; HOST_SET=1; shift 2 ;;
    --home) PAWFLOW_HOME="$2"; shift 2 ;;
    --platform) DOCKER_PLATFORM="$2"; shift 2 ;;
    --native) START_TARGET="native"; shift ;;
    --container) START_TARGET="container"; shift ;;
    --skip-doctor) RUN_DOCTOR=0; shift ;;
    --no-start) START_SERVER=0; shift ;;
    --help|-h)
      sed -n '1,14p' "$0"
      cat <<'HELP'

Options:
  --version VERSION  Install this PawFlow version. Checkout this git tag before building runtime images; prebuilt server image tag uses this value.
  --from-source      Build the server from source. With --version, checkout that exact git tag; without it, checkout main.
  --pull-server      Require pulling the server image. Fails if the image is not available.
  --pull-images      Require pulling server + redistributable relay images.
  --build-images     Build server + relay images from source.
  --runtime-image-mode MODE
                     Relay image mode: auto, pull, or source (default: auto).
  --image TAG        Full server image tag to build, pull, or run.
  --image-repo REPO  Server image repository when --image is not set (default: ghcr.io/allcolor/pawflow).
  --relay-minimal-image TAG
                     Minimal relay image tag (default: ghcr.io/allcolor/pawflow-relay-minimal:<version|latest>).
  --relay-dev-image TAG
                     Full relay image tag (default: ghcr.io/allcolor/pawflow-relay-dev:<version|latest>).
  --cli-llm-image TAG
                     Local CLI LLM image tag (default: pawflow-claude-code:latest).
  --repo URL         Git repository to clone when the script is not run from a checkout.
  --dir PATH         Source checkout directory for cloned installs.
  --port PORT        Host/server port selected for this install.
  --host HOST        Server bind host. Container default is 0.0.0.0; native default is 127.0.0.1.
  --home PATH        Persistent PawFlow home (default: ~/pawflow).
  --platform VALUE   Docker build platform, for example linux/amd64.
  --native           Start PawFlow natively in a Python venv after building runtime images.
  --container        Start PawFlow server in Docker after building runtime images (default).
  --no-start         Build images but do not start the server container.

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

if [[ -z "$PORT" ]]; then
  echo "ERROR: choose a port with --port PORT or PAWFLOW_PORT=PORT." >&2
  exit 2
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
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
  docker pull "${pull_args[@]}" "$IMAGE"
}

pull_image() {
  local image="$1"
  local pull_args=()
  if [[ -n "$DOCKER_PLATFORM" ]]; then pull_args+=(--platform "$DOCKER_PLATFORM"); fi
  echo "Pulling image: $image"
  docker pull "${pull_args[@]}" "$image"
}

build_minimal_relay_image() {
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
  mkdir -p "$PAWFLOW_HOME/data" "$PAWFLOW_HOME/logs"
  if [[ ! -d "$PAWFLOW_HOME/data/repository" ]]; then
    echo "Seeding native PawFlow repository data: $PAWFLOW_HOME/data/repository"
    mkdir -p "$PAWFLOW_HOME/data"
    cp -R "$REPO_DIR/data/repository" "$PAWFLOW_HOME/data/repository"
  fi
}

run_native_server() {
  local py venv_python bootstrap_gateway_key bootstrap_gateway_label native_host
  py="$(find_python)"
  native_host="$HOST"
  if [[ "$HOST_SET" == "0" ]]; then native_host="127.0.0.1"; fi
  bootstrap_gateway_key="$(printenv PAWFLOW_BOOTSTRAP_GATEWAY_KEY || true)"
  if [[ -z "$bootstrap_gateway_key" ]]; then
    bootstrap_gateway_key="RoyBetty"
    bootstrap_gateway_label="RoyBetty"
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

HOST_OS="$(detect_host)"

need_cmd docker
PYTHON_BIN="$(find_python)"
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but the daemon is not reachable." >&2
  echo "Start Docker Desktop/Engine, then rerun the installer." >&2
  exit 1
fi

if [[ -z "$IMAGE" ]]; then
  if [[ -n "$VERSION" ]]; then
    IMAGE="$IMAGE_REPO:$VERSION"
  else
    IMAGE="$IMAGE_REPO:latest"
  fi
fi
if [[ -z "$RELAY_MINIMAL_IMAGE" ]]; then
  if [[ -n "$VERSION" ]]; then
    RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE_REPO:$VERSION"
  else
    RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE_REPO:latest"
  fi
fi
if [[ -z "$RELAY_DEV_IMAGE" ]]; then
  if [[ -n "$VERSION" ]]; then
    RELAY_DEV_IMAGE="$RELAY_DEV_IMAGE_REPO:$VERSION"
  else
    RELAY_DEV_IMAGE="$RELAY_DEV_IMAGE_REPO:latest"
  fi
fi

REPO_DIR="$(ensure_checkout)"
prepare_checkout_ref "$REPO_DIR"

if [[ -z "$DOCKER_PLATFORM" && "$HOST_OS" == "macos" ]]; then
  DOCKER_PLATFORM="linux/amd64"
fi
if [[ -n "$DOCKER_PLATFORM" ]]; then
  export PAWFLOW_DOCKER_PLATFORM="$DOCKER_PLATFORM"
fi

if [[ "$RUN_DOCTOR" == "1" ]]; then
  bash "$REPO_DIR/scripts/doctor-pawflow.sh" --port "$PORT" --source
fi

echo "PawFlow source: $REPO_DIR"
echo "Host: $HOST_OS"
echo "Version: ${VERSION}"
echo "Server image: $IMAGE ($SERVER_MODE)"
echo "Runtime image mode: $RUNTIME_IMAGE_MODE"
echo "Minimal relay image: $RELAY_MINIMAL_IMAGE"
echo "Full relay image: $RELAY_DEV_IMAGE"
echo "CLI LLM image: $CLI_LLM_IMAGE (local build)"
echo "Start target: $START_TARGET"
if [[ -n "$DOCKER_PLATFORM" ]]; then
  echo "Docker build platform: $DOCKER_PLATFORM"
fi

if [[ "$SERVER_MODE" == "pull" ]]; then
  pull_server_image
elif [[ "$SERVER_MODE" == "source" ]]; then
  build_server_image
elif [[ "$SERVER_MODE" == "auto" ]]; then
  if pull_server_image; then
    echo "Using prebuilt PawFlow server image: $IMAGE"
  else
    echo "Prebuilt PawFlow server image unavailable, building from source: $IMAGE"
    SERVER_MODE="source" prepare_checkout_ref "$REPO_DIR"
    build_server_image
  fi
else
  echo "Invalid PAWFLOW_SERVER_MODE: $SERVER_MODE (expected auto, source, or pull)" >&2
  exit 2
fi

echo "Building PawFlow CLI LLM image locally: $CLI_LLM_IMAGE"
PAWFLOW_CLI_LLM_IMAGE="$CLI_LLM_IMAGE" bash "$REPO_DIR/docker/claude-code/build.sh"

ensure_runtime_image "server minimal relay" "$RELAY_MINIMAL_IMAGE" build_minimal_relay_image

ensure_runtime_image "full server relay" "$RELAY_DEV_IMAGE" build_full_relay_image

if [[ "$START_SERVER" != "1" ]]; then
  echo "Image build complete. Server start skipped because --no-start was set."
  exit 0
fi

if [[ "$START_TARGET" == "native" ]]; then
  run_native_server
elif [[ "$START_TARGET" == "container" ]]; then
  PAWFLOW_IMAGE="$IMAGE" PAWFLOW_PORT="$PORT" PAWFLOW_HOST="$HOST" PAWFLOW_HOME="$PAWFLOW_HOME" PAWFLOW_SERVER_RELAY_IMAGE="$RELAY_DEV_IMAGE" PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE="$RELAY_MINIMAL_IMAGE" bash "$REPO_DIR/scripts/run-pawflow-docker.sh"
else
  echo "Invalid PAWFLOW_START_TARGET: $START_TARGET (expected container or native)" >&2
  exit 2
fi

