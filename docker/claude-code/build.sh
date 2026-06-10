#!/bin/bash
# Build the Claude Code container image.
#
# mcp_bridge.py and pawflow_sdk/pawflow.py are NOT copied into the
# image — they are dev-mounted onto /opt/pawflow/*.py at container run
# time by core/claude_code_pool.py. This build only provisions
# the Claude Code binary and system deps, so iterating on the bridge
# does not require rebuilding the image.
#
# Run from the PawFlow root: bash docker/claude-code/build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(printenv PAWFLOW_DOCKER_PLATFORM || true)"
IMAGE="$(printenv PAWFLOW_CLI_LLM_IMAGE || true)"
if [[ -z "$IMAGE" ]]; then IMAGE="pawflow-claude-code:latest"; fi
BUILD_ARGS=()
if [[ -n "$PLATFORM" ]]; then BUILD_ARGS+=(--platform "$PLATFORM"); fi

# Resolve the latest published npm version of each agent CLI and pin it via a
# build arg. The pinned version is part of the npm-install layer's cache key, so
# an unchanged version reuses the cache (no reinstall) and a new upstream release
# invalidates the layer and triggers a fresh install. The resolver runs in the
# image's own base (node:22-slim), so the host does not need npm installed.
RESOLVER_IMAGE="node:22-slim"
RESOLVER_ARGS=()
if [[ -n "$PLATFORM" ]]; then RESOLVER_ARGS+=(--platform "$PLATFORM"); fi

resolve_version() {
  # Prints the registry 'latest' version of $1, or nothing if resolution fails.
  docker run --rm "${RESOLVER_ARGS[@]}" "$RESOLVER_IMAGE" \
    npm view "$1" version 2>/dev/null | tr -d '\r' | tail -n1
}

# Each spec is "<npm-package> <build-arg-name>", split on whitespace.
for spec in \
  "@anthropic-ai/claude-code CLAUDE_CODE_VERSION" \
  "@openai/codex CODEX_VERSION" \
  "@google/gemini-cli GEMINI_VERSION"; do
  set -- $spec
  pkg="$1"
  arg="$2"
  ver="$(resolve_version "$pkg")"
  if [[ -z "$ver" ]]; then
    echo "WARNING: could not resolve latest version of $pkg; falling back to 'latest' (layer may stay cached)" >&2
    ver="latest"
  else
    echo "Resolved $pkg -> $ver"
  fi
  BUILD_ARGS+=(--build-arg "$arg=$ver")
done

docker build "${BUILD_ARGS[@]}" -t "$IMAGE" "$SCRIPT_DIR"

echo "Built $IMAGE"
