#!/usr/bin/env sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
  python_bin=python3
elif command -v python >/dev/null 2>&1; then
  python_bin=python
else
  printf '%s\n' 'Python 3.10 or newer is required.' >&2
  exit 2
fi
exec "$python_bin" "$script_dir/install.py" "$@"
