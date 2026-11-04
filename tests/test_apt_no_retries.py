"""Every image build disables APT download retries before its first apt call.

CVE-2026-109518: an unpatched APT that retries a failed HTTP download can be
tricked into installing a .deb that fails signature verification. The base
images (ubuntu:24.04, python:3.12-slim, node:22-slim) ship such an APT, and
the builds fetch packages over HTTP, so every build stage that uses apt must
set Acquire::Retries to 0 and upgrade APT before installing anything else.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "Acquire::Retries \"0\";"
DOCKERFILES = [
    "Dockerfile",
    "docker/claude-code/Dockerfile",
    "docker/livekit-worker/Dockerfile",
    "docker/relay-dev/Dockerfile",
]


def _runs_per_stage(text):
    """Split a Dockerfile into stages, each a list of its RUN instructions."""
    joined = re.sub(r"\\\n", " ", text)
    stages = []
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            stages.append([])
        elif stripped.upper().startswith("RUN ") and stages:
            stages[-1].append(stripped)
    return stages


def _assert_hardened(text, name):
    for index, runs in enumerate(_runs_per_stage(text)):
        apt_runs = [run for run in runs if "apt-get" in run or "apt " in run]
        if not apt_runs:
            continue
        first = apt_runs[0]
        assert MARKER in first, f"{name} stage {index}: first apt call keeps retries"
        assert "--only-upgrade apt" in first, f"{name} stage {index}: APT not upgraded first"


@pytest.mark.parametrize("path", DOCKERFILES)
def test_checked_in_dockerfiles_disable_apt_retries_first(path):
    _assert_hardened((ROOT / path).read_text(encoding="utf-8"), path)


def test_generated_relay_dockerfile_disables_apt_retries_first():
    spec = importlib.util.spec_from_file_location(
        "relay_image_generator", ROOT / "scripts" / "generate-relay-image.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    catalog = generator._load_catalog(ROOT / "config" / "relay_image_catalog.json")
    for profile in ("server-minimal", "server-full", "client-desktop"):
        features = generator._resolve_features(catalog, profile, [])
        text = generator._render_dockerfile(catalog, features, f"img:{profile}")
        _assert_hardened(text, profile)
