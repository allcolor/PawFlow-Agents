#!/bin/bash
# Emit, as JSON on stdout, the versions of the agent CLIs actually installed in
# this image. Baked into /opt/pawflow/cli_versions.json at build time and read
# back by core/update_manager.py, so the server reports what is really in the
# image instead of re-deriving it from build args.
#
# Antigravity is installed from an unversioned install script: whatever its
# binary reports here is the only version signal that exists for it. A CLI that
# fails to report gets an empty string, which the server renders as unknown.

set -u

version_of() {
  local out
  out="$("$1" --version 2>/dev/null | head -n 5)" || true
  printf '%s' "$out" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*' | head -n1
}

printf '{\n'
printf '  "claude": "%s",\n' "$(version_of claude)"
printf '  "codex": "%s",\n' "$(version_of codex)"
printf '  "gemini": "%s",\n' "$(version_of gemini)"
printf '  "antigravity": "%s"\n' "$(version_of agy)"
printf '}\n'
