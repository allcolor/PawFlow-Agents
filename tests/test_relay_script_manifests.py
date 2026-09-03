"""Relay script manifests must ship every module ``fs_actions`` imports.

Regression for the remote relay outage of 2026-09-03: ``fs_actions.py`` gained
module-level imports of ``fs_http`` and ``fs_archive``, the server synced the
new facade to an already-running containerized relay, that relay's
``/opt/pawflow`` had no ``fs_archive.py``, and every filesystem action
(``list_dir``, uploads, the file explorer) failed with
``No module named fs_archive``.

Two invariants are enforced here:

* every sibling module the facade imports is listed in every manifest that
  distributes relay scripts (server push list, relay accept list, dev mounts,
  relay image generator, MCP client installer, desktop runtime copy);
* the facade keeps its base actions importable when one of the optional
  sibling modules is absent, so an old relay runtime degrades to explicit
  per-action errors instead of losing the whole filesystem surface.
"""
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from pawflow_relay import _relay_actions as ra
from services import _relay_ws

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

# Modules added after the base relay bundle; an installed relay may lack them.
OPTIONAL_SIBLINGS = ("fs_http", "fs_archive")

# Source manifests that copy or mount relay scripts by file name.
SOURCE_MANIFESTS = (
    ROOT / "pawflow_relay" / "_thread_docker.py",
    ROOT / "scripts" / "generate-relay-image.py",
    ROOT / "scripts" / "build-mcp-client-installer.py",
    ROOT / "pawflow-relay-desktop" / "scripts" / "prepare-runtime.js",
)


def _facade_sibling_files():
    """``<module>.py`` for every local module ``fs_actions.py`` imports."""
    src = (TOOLS / "fs_actions.py").read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*from (\w+) import", src, re.MULTILINE))
    files = {f"{name}.py" for name in names if (TOOLS / f"{name}.py").is_file()}
    assert "fs_archive.py" in files and "fs_http.py" in files
    return files


def test_server_push_list_and_relay_accept_list_cover_every_facade_import():
    siblings = _facade_sibling_files()
    assert siblings <= set(_relay_ws._RELAY_SCRIPT_FILES)
    assert siblings <= set(ra._RELAY_SCRIPTS)
    # What the server pushes is exactly what a relay accepts: anything else
    # is silently dropped by update_scripts and the hashes never converge.
    assert set(_relay_ws._RELAY_SCRIPT_FILES) == set(ra._RELAY_SCRIPTS)


def test_facade_dependencies_are_reloaded_before_the_facade():
    siblings = {name[:-3] for name in _facade_sibling_files()}
    # fs_common, fs_exec, fs_screen and fs_mcp are reloaded on their own; the
    # modules whose *names* fs_actions re-exports must precede the facade.
    assert set(ra._FACADE_DEPENDENCIES) <= siblings
    for name in OPTIONAL_SIBLINGS:
        assert name in ra._FACADE_DEPENDENCIES


@pytest.mark.parametrize("manifest", SOURCE_MANIFESTS, ids=lambda p: p.name)
def test_source_manifests_list_every_facade_import(manifest):
    text = manifest.read_text(encoding="utf-8")
    for fname in sorted(_facade_sibling_files()):
        assert re.search(rf"[\"']{re.escape(fname)}[\"']", text), (
            f"{manifest.relative_to(ROOT)} does not ship {fname}")


def test_cli_dev_mounts_reuse_the_relay_accept_list():
    text = (ROOT / "pawflow_relay" / "cli.py").read_text(encoding="utf-8")
    assert "for _relay_file in _RELAY_SCRIPTS:" in text
    assert '"fs_common.py"' not in text


def _stage_tools(tmp_path, drop=(), overrides=None):
    staged = tmp_path / "tools"
    staged.mkdir()
    for src in TOOLS.glob("*.py"):
        if src.name in drop:
            continue
        shutil.copy2(src, staged / src.name)
    for name, content in (overrides or {}).items():
        (staged / name).write_text(content, encoding="utf-8")
    return staged


def _run_in_staged_tools(staged, code):
    # Like /opt/pawflow on a relay: the scripts plus the pawflow_relay package
    # side by side; fs_common imports pawflow_relay.utils.
    return subprocess.run(  # nosec B603
        [sys.executable, "-c", code], cwd=str(staged), capture_output=True,
        text=True, timeout=120, check=False,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": f"{staged}{os.pathsep}{ROOT}",
             "PYTHONDONTWRITEBYTECODE": "1"})


_PROBE = """
import json
import fs_actions
out = {"list_dir": "list_dir" in fs_actions.ACTIONS,
       "read_file": "read_file" in fs_actions.ACTIONS}
for name in ("extract_zip_subtree", "http_fetch", "http_fetch_to_file"):
    try:
        fs_actions.ACTIONS[name]("/tmp", "/tmp/x", {})
        out[name] = "no error"
    except RuntimeError as exc:
        out[name] = str(exc)
print(json.dumps(out))
"""


def test_facade_survives_a_relay_runtime_without_the_optional_modules(tmp_path):
    staged = _stage_tools(
        tmp_path, drop={f"{name}.py" for name in OPTIONAL_SIBLINGS})
    proc = _run_in_staged_tools(staged, _PROBE)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["list_dir"] and out["read_file"]
    assert "fs_archive.py" in out["extract_zip_subtree"]
    assert "upgrade the relay runtime" in out["extract_zip_subtree"]
    assert "fs_http.py" in out["http_fetch"]
    assert "fs_http.py" in out["http_fetch_to_file"]


def test_facade_still_fails_loudly_when_an_optional_module_is_broken(tmp_path):
    # The guard covers exactly "the sibling file is absent". A sibling that
    # exists but cannot import must surface its own error, not be masked.
    staged = _stage_tools(tmp_path, overrides={
        "fs_archive.py": "import pawflow_missing_dependency_xyz\n"})
    proc = _run_in_staged_tools(staged, "import fs_actions")
    assert proc.returncode != 0
    assert "pawflow_missing_dependency_xyz" in proc.stderr
