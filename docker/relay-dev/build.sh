#!/bin/bash
# Build the relay dev environment image.
#
# The relay + SDK Python files are NOT copied into the image — they are
# dev-mounted onto /opt/pawflow/*.py at container run time by
# pawflow_relay/thread.py. This build only provisions the system
# dependencies (languages, CLI tools, X server, …), so iterating on
# the relay scripts does not require rebuilding the image.
#
# Run from the PawFlow root: bash docker/relay-dev/build.sh
#
# Languages included:
#   Python 3, Node.js 22 + TypeScript, Rust, Go, C/C++ (gcc/g++/cmake),
#   Java 21, Ruby, PHP, Perl, Lua
#
# Tools: git, make, cmake, curl, wget, jq, sqlite, ssh, zip/unzip
#
# Size: large multi-language dev environment; optional desktop builds can add
# Kotlin, .NET, Zig and heavier GUI/media applications when needed.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLATFORM="$(printenv PAWFLOW_DOCKER_PLATFORM || true)"
IMAGE="$(printenv PAWFLOW_RELAY_DEV_IMAGE || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="pawflow-relay-dev:latest"; fi
BUILD_ARGS=()
if [[ -n "$PLATFORM" ]]; then BUILD_ARGS+=(--platform "$PLATFORM"); fi

docker build "${BUILD_ARGS[@]}" --build-arg "PAWFLOW_DOCKER_IMAGE=$IMAGE" -f "$SCRIPT_DIR/Dockerfile" -t "$IMAGE" "$REPO_DIR"

echo ""
echo "Built $IMAGE"
echo ""
echo "Usage:"
echo "  python tools/pawflow_relay.py --dir /path/to/project --allow-exec --docker-image $IMAGE"
echo ""
echo "Languages: python, node/ts, rust, go, c/c++, java, ruby, php, perl, lua"
