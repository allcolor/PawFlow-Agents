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

docker build -t pawflow-claude-code:latest "$SCRIPT_DIR"

echo "Built pawflow-claude-code:latest"
