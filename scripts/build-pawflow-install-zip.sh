#!/usr/bin/env bash
# Build the minimal PawFlow install zip uploaded to GitHub releases.

set -euo pipefail

VERSION="$(printenv PAWFLOW_VERSION || true)"
OUT_DIR="$(printenv PAWFLOW_INSTALLER_DIST_DIR || true)"
PYTHON_BIN="$(printenv PAWFLOW_PYTHON || true)"

if [[ -z "$OUT_DIR" ]]; then OUT_DIR="dist/pawflow-installers"; fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --help|-h)
      cat <<'HELP'
Usage: bash scripts/build-pawflow-install-zip.sh --version VERSION [--out-dir DIR]

Builds a minimal release zip containing only the bootstrap installer, README,
and license. The installer pulls the PawFlow server image and extracts the
remaining runtime artifacts from that image.
HELP
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "ERROR: choose a version with --version VERSION or PAWFLOW_VERSION=VERSION." >&2
  exit 2
fi

if [[ -n "$PYTHON_BIN" ]]; then
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "Configured Python not found: $PYTHON_BIN" >&2; exit 1; }
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Missing required command: python3 or python" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="$OUT_DIR/pawflow-install-$VERSION.zip"

mkdir -p "$OUT_DIR"
"$PYTHON_BIN" - "$ROOT" "$ARCHIVE" <<'PY'
from pathlib import Path
import sys
import zipfile

root = Path(sys.argv[1])
archive = Path(sys.argv[2])
files = [
    Path("scripts/install-pawflow.sh"),
    Path("README.md"),
    Path("LICENSE"),
]

with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for rel in files:
        zf.write(root / rel, rel.as_posix())
PY

echo "Built $ARCHIVE"
