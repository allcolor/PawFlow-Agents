#!/bin/bash
# Build the relay dev environment image
# Run from the PyFi2 root: bash docker/relay-dev/build.sh
#
# Languages included:
#   Python 3, Node.js 22 + TypeScript, Rust, Go, C/C++ (gcc/g++/cmake),
#   Java 21 + Kotlin + Scala, C# (.NET 9), Ruby, PHP, Perl, Lua, Zig
#
# Tools: git, make, cmake, curl, wget, jq, sqlite, ssh, zip/unzip
#
# Size: ~3-4GB (multi-language dev environment)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Copy relay + SDK files to build context
cp "$ROOT_DIR/tools/pawflow_relay.py" "$SCRIPT_DIR/pawflow_relay.py"
cp "$ROOT_DIR/tools/fs_common.py" "$SCRIPT_DIR/fs_common.py"
cp "$ROOT_DIR/tools/fs_actions.py" "$SCRIPT_DIR/fs_actions.py"
cp "$ROOT_DIR/tools/fs_exec.py" "$SCRIPT_DIR/fs_exec.py"
cp "$ROOT_DIR/tools/fs_screen.py" "$SCRIPT_DIR/fs_screen.py"
cp "$ROOT_DIR/tools/fs_mcp.py" "$SCRIPT_DIR/fs_mcp.py"
cp "$ROOT_DIR/tools/fs_http.py" "$SCRIPT_DIR/fs_http.py"
cp "$ROOT_DIR/docker/pawflow_sdk/pawflow.py" "$SCRIPT_DIR/pawflow.py"
cp "$ROOT_DIR/tools/audio_capture.py" "$SCRIPT_DIR/audio_capture.py"

docker build -t pawflow-relay-dev:latest "$SCRIPT_DIR"

# Cleanup
rm -f "$SCRIPT_DIR/pawflow_relay.py" "$SCRIPT_DIR/fs_common.py" "$SCRIPT_DIR/fs_actions.py" "$SCRIPT_DIR/fs_exec.py" "$SCRIPT_DIR/fs_screen.py" "$SCRIPT_DIR/fs_mcp.py" "$SCRIPT_DIR/fs_http.py" "$SCRIPT_DIR/pawflow.py" "$SCRIPT_DIR/audio_capture.py"

echo ""
echo "Built pawflow-relay-dev:latest"
echo ""
echo "Usage:"
echo "  python tools/pawflow_relay.py --dir /path/to/project --allow-exec --docker-image pawflow-relay-dev:latest"
echo ""
echo "Languages: python, node/ts, rust, go, c/c++, java, kotlin, c#, ruby, php, perl, lua, zig"
