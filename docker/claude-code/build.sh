#!/bin/bash
# Build the Claude Code container image
# Run from the PyFi2 root: bash docker/claude-code/build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Copy MCP bridge to build context
cp "$ROOT_DIR/tools/mcp_bridge.py" "$SCRIPT_DIR/mcp_bridge.py"

# Build
docker build -t pawflow-claude-code:latest "$SCRIPT_DIR"

# Cleanup
rm -f "$SCRIPT_DIR/mcp_bridge.py"

echo "Built pawflow-claude-code:latest"
