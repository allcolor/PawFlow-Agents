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

docker build -t pawflow-relay-dev:latest "$SCRIPT_DIR"

echo ""
echo "Built pawflow-relay-dev:latest"
echo ""
echo "Usage:"
echo "  python tools/pawflow_relay.py --dir /path/to/project --allow-exec --docker-image pawflow-relay-dev:latest"
echo ""
echo "Languages: python, node/ts, rust, go, c/c++, java, kotlin, c#, ruby, php, perl, lua, zig"
