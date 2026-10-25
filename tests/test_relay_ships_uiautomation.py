"""The relay must ship the Windows accessibility dependency by default.

screen_windows / screen_window_state now answer natively on Windows, but the
element tree needs ``uiautomation``. Asking the user to pip install it on their
relay host is not acceptable: the relay is a packaged binary and the Docker
images carry their own Python deps, so a missing module means the feature is
silently degraded to geometry-only.

These tests pin the dependency where it is actually installed, so a future
build cannot drop it: requirements-build.txt for the packaged desktop relay,
and the relay Dockerfile for container images.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_build_requires_uiautomation_on_windows():
    req = (ROOT / "pawflow-relay-desktop" / "requirements-build.txt").read_text()
    lines = [line.strip() for line in req.splitlines() if line.strip()]
    match = [line for line in lines if line.lower().startswith("uiautomation")]
    assert match, "uiautomation must be a desktop build requirement"
    # Windows-only: the module is Win32/UIA specific.
    assert "win32" in match[0], (
        "uiautomation must be marked sys_platform == 'win32'")


def test_pyinstaller_collects_uiautomation_on_windows():
    """A hidden-import alone is not enough: uiautomation ships data files."""
    script = (ROOT / "pawflow-relay-desktop" / "scripts"
              / "build-relay-bin.js").read_text()
    assert "uiautomation" in script, (
        "the PyInstaller build must collect uiautomation on win32")


def test_relay_docker_does_not_install_a_windows_only_module():
    """uiautomation depends on comtypes and cannot install on Linux.

    The container relay keeps the CUA backend for element addressing, so adding
    a Win32-only module to the image would break the build for no benefit.
    """
    dockerfile = (ROOT / "docker" / "relay-dev" / "Dockerfile").read_text()
    assert "uiautomation" not in dockerfile, (
        "uiautomation is Windows-only and must not be installed in the "
        "Linux relay image")
