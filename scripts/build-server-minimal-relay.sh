#!/usr/bin/env bash
# Generate and build the protected server-side minimal relay image.
#
# This image is the default execution target for server-spawned minimal relays
# (`server_relay_minimal_image`), used when PawFlow needs a protected Docker
# relay for scripts, tools, and package runtimes without an explicit user relay.
#
# Environment:
#   PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE  Image tag to build (default: pawflow-relay-minimal:latest)
#   PAWFLOW_SERVER_MINIMAL_RELAY_OUT    Generated build directory (default: docker/relay-generated/server-minimal)
#   PAWFLOW_RELAY_GENERATE_ONLY         Set to 1 to generate files without docker build

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="$(printenv PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE || true)"
OUT_DIR="$(printenv PAWFLOW_SERVER_MINIMAL_RELAY_OUT || true)"
GENERATE_ONLY="$(printenv PAWFLOW_RELAY_GENERATE_ONLY || true)"

if [[ -z "$IMAGE" ]]; then IMAGE="pawflow-relay-minimal:latest"; fi
if [[ -z "$OUT_DIR" ]]; then OUT_DIR="$REPO_DIR/docker/relay-generated/server-minimal"; fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '1,14p' "$0"
      exit 0
      ;;
    --generate-only)
      GENERATE_ONLY="1"
      shift
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

echo "Generating server minimal relay image context: $OUT_DIR"
python3 "$REPO_DIR/scripts/generate-relay-image.py" \
  --profile server-minimal \
  --out "$OUT_DIR" \
  --image "$IMAGE"

if [[ "$GENERATE_ONLY" == "1" ]]; then
  echo "Generated server minimal relay context for $IMAGE"
  echo "Build manually with: $OUT_DIR/build.sh"
  exit 0
fi

echo "Building server minimal relay image: $IMAGE"
"$OUT_DIR/build.sh"
echo "Built $IMAGE"

