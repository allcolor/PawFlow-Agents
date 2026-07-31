"""The documented package version must be the one that actually ships.

PROJECT_SUMMARY.md states a package version. It is written by hand and drifted
ten betas behind pyproject.toml before anyone noticed -- a reader taking the
summary at face value was reading a description of a release that no longer
existed. The version is a fact the repository already holds, so the summary is
checked against it rather than trusted.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    src = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', src, re.MULTILINE)
    assert match, "pyproject.toml declares no version"
    return match.group(1)


def test_project_summary_states_the_shipped_version():
    summary = (ROOT / "PROJECT_SUMMARY.md").read_text(encoding="utf-8")
    match = re.search(r"\*\*Package version\*\*:\s*`([^`]+)`", summary)
    assert match, "PROJECT_SUMMARY.md no longer states a package version"
    assert match.group(1) == _pyproject_version(), (
        f"PROJECT_SUMMARY.md says {match.group(1)}, "
        f"pyproject.toml ships {_pyproject_version()}"
    )


def test_project_summary_beta_label_matches_the_version():
    """`1.0.0b69` and `(beta.69)` are the same number written twice."""
    summary = (ROOT / "PROJECT_SUMMARY.md").read_text(encoding="utf-8")
    line = re.search(r"\*\*Package version\*\*:.*", summary).group(0)
    version = re.search(r"b(\d+)`", line)
    label = re.search(r"beta\.(\d+)", line)
    assert version and label, f"unreadable version line: {line}"
    assert version.group(1) == label.group(1), line
