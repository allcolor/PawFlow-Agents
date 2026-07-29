"""The update must refresh the host-side files the installer extracted.

``scripts/install-pawflow.sh`` copies a set of artifacts out of the server image
onto the host: the start script the update itself runs, the relay runtime, the
AppArmor profiles, the relay image catalog. The updater only pulled the image,
so those stayed frozen at whatever version was installed from the command line
-- across every update done from the UI.
"""

import os
import subprocess

import pytest

from core import installer_deployment, update_manager

IMAGE = "ghcr.io/allcolor/pawflow:1.0.0-beta.45"
OLD_IMAGE = "ghcr.io/allcolor/pawflow:1.0.0-beta.44"


# ── where the artifacts go ───────────────────────────────────────────

def test_image_tag_dirname_mirrors_the_installer():
    assert installer_deployment.image_tag_dirname(IMAGE) == "1.0.0-beta.45"
    # No tag at all: the installer calls that "latest", and so must we.
    assert installer_deployment.image_tag_dirname("ghcr.io/a/pawflow") == "latest"
    assert installer_deployment.image_tag_dirname(
        "ghcr.io/a/pawflow@sha256:" + "0" * 64) == "latest"
    # A registry port is not a tag.
    assert installer_deployment.image_tag_dirname("reg.local:5000/pawflow") == "latest"
    assert installer_deployment.image_tag_dirname("reg.local:5000/pawflow:1.2") == "1.2"


def test_a_versioned_install_moves_to_the_new_versions_directory():
    """The installer lays artifacts out per version; the update follows.

    The old directory is left intact, so the previous version stays on disk and
    no directory ever claims a version it does not hold.
    """
    target = installer_deployment.artifact_dir_for_update(
        "/home/pawflow/.pawflow/runtime/1.0.0-beta.44", OLD_IMAGE, IMAGE)
    assert target == "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"


def test_an_operator_chosen_directory_is_refreshed_in_place():
    """``--runtime-dir`` is the operator's path.

    Inventing a tag-named sibling next to it would be a directory PawFlow
    created behind their back, in a place they chose for their own reasons.
    """
    target = installer_deployment.artifact_dir_for_update(
        "/opt/pawflow-runtime", OLD_IMAGE, IMAGE)
    assert target == "/opt/pawflow-runtime"


def test_no_install_directory_means_nothing_to_refresh():
    assert installer_deployment.artifact_dir_for_update("", OLD_IMAGE, IMAGE) == ""
    assert installer_deployment.artifact_dir_for_update("/x/1.0", OLD_IMAGE, "") == ""


def test_the_updater_mounts_the_directory_that_holds_both():
    """A sibling cannot be created through a mount of its neighbour alone."""
    assert update_manager._artifact_mount_dir(
        "/home/pawflow/.pawflow/runtime/1.0.0-beta.44",
        "/home/pawflow/.pawflow/runtime/1.0.0-beta.45",
    ) == "/home/pawflow/.pawflow/runtime"
    # Refreshed in place: the directory itself is enough.
    assert update_manager._artifact_mount_dir("/opt/rt", "/opt/rt") == "/opt/rt"
    assert update_manager._artifact_mount_dir("/opt/rt", "") == "/opt/rt"


# ── the generated script ─────────────────────────────────────────────

def _info(app_dir="/home/pawflow/.pawflow/runtime/1.0.0-beta.44", env=None):
    return {
        "host_app_dir": app_dir,
        "container_name": "pawflow-server",
        "pawflow_home": "/home/pawflow/pawflow",
        "port": "19990",
        "env": dict(env or {}),
    }


def test_the_refresh_happens_after_the_pull_and_before_the_server_is_touched():
    """Ordering is the safety property on this whole path.

    Everything that can fail without consequence must happen while the server
    is still running.
    """
    script = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir="/home/pawflow/.pawflow/runtime/1.0.0-beta.45")
    pull = script.index("docker pull")
    refresh = script.index("_pf_refresh_artifacts")
    start = script.index("run-pawflow-docker.sh")
    assert pull < refresh < start


def test_the_start_script_is_run_from_the_new_directory_and_the_label_moves():
    """Without this the next update would look for artifacts in the old dir."""
    new_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    script = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir=new_dir)
    assert f"cd {new_dir}" in script
    assert f"PAWFLOW_SOURCE_DIR={new_dir}" in script


def test_a_git_checkout_is_never_overwritten_from_the_image():
    """Its host-side files are tracked; `git pull` is what moves them."""
    script = update_manager._installer_updater_script(_info(), IMAGE, True)
    assert "_pf_refresh_artifacts" not in script
    assert "docker create" not in script
    assert "git pull --ff-only" in script


def test_the_refresh_aborts_by_default_and_only_warns_when_forced():
    new_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    strict = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir=new_dir)
    forced = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir=new_dir, force_artifacts=True)
    # Last three lines: the refresh, then cd, then the start script.
    assert strict.rstrip().splitlines()[-3] == "_pf_refresh_artifacts"
    assert "_pf_refresh_artifacts || echo " in forced
    assert "WARNING host artifacts were not refreshed" in forced


def test_extracted_files_are_chowned_to_the_uid_the_server_runs_as():
    """The updater is root; files it leaves root-owned block the next install."""
    new_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    script = update_manager._installer_updater_script(
        _info(env={"PAWFLOW_RUN_UID": "1000", "PAWFLOW_RUN_GID": "1000"}),
        IMAGE, False, artifact_dir=new_dir)
    assert f"chown -R 1000:1000 {new_dir}" in script
    # Unknown uid/gid: nothing is guessed.
    unknown = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir=new_dir)
    assert "chown" not in unknown


def test_the_script_covers_every_artifact_the_installer_extracts():
    """The installer is the source of truth; drift here is silent staleness."""
    installer_src = open("scripts/install-pawflow.sh", encoding="utf-8").read()
    body = installer_src.split("extract_image_artifacts", 1)[1]
    known = set(installer_deployment.IMAGE_ARTIFACTS) | set(
        installer_deployment.OPTIONAL_IMAGE_ARTIFACTS)
    for rel in known:
        assert rel in body, f"{rel} is no longer extracted by the installer"


# ── the script actually runs ─────────────────────────────────────────

def _fake_docker(bin_dir, fail_rel=""):
    """A `docker` that serves artifacts from an imaginary image."""
    path = bin_dir / "docker"
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "case \"$1\" in\n"
        "  create) printf 'fakecid\\n'; exit 0 ;;\n"
        "  cp)\n"
        "    rel=\"${2#fakecid:/app/}\"; dst=\"$3\"\n"
        f"    if [[ -n \"{fail_rel}\" && \"$rel\" == \"{fail_rel}\" ]]; then\n"
        "      echo 'Error: no such path in container' >&2; exit 1\n"
        "    fi\n"
        "    mkdir -p \"$(dirname \"$dst\")\"\n"
        "    if [[ \"$rel\" == 'scripts/run-pawflow-docker.sh' ]]; then\n"
        "      printf '#!/usr/bin/env bash\\necho STARTED \"$PAWFLOW_IMAGE\" \"$PAWFLOW_SOURCE_DIR\" >> \"$START_LOG\"\\n' > \"$dst\"\n"
        "    elif [[ \"$rel\" == docker/* || \"$rel\" == 'pawflow_relay' ]]; then\n"
        "      mkdir -p \"$dst\"; printf 'x' > \"$dst/marker\"\n"
        "    else\n"
        "      printf 'artifact %s\\n' \"$rel\" > \"$dst\"\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run_script(tmp_path, script, fail_rel=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake_docker(bin_dir, fail_rel)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "START_LOG": str(tmp_path / "start.log"),
        "HOME": str(tmp_path),
    }
    return subprocess.run(["sh", "-c", script], env=env, text=True,
                          capture_output=True, timeout=30)


@pytest.fixture
def dirs(tmp_path):
    old = tmp_path / "runtime" / "1.0.0-beta.44"
    old.mkdir(parents=True)
    (old / "scripts").mkdir()
    (old / "scripts" / "run-pawflow-docker.sh").write_text(
        "#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
    return old, tmp_path / "runtime" / "1.0.0-beta.45"


def test_the_new_versions_files_land_on_the_host_and_start_the_server(tmp_path, dirs):
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new))

    result = _run_script(tmp_path, script)

    assert result.returncode == 0, result.stderr
    # Every required artifact is on the host, under the new version.
    for rel in installer_deployment.IMAGE_ARTIFACTS:
        assert (new / rel).exists(), rel
    # And the start script that ran is the new one, not the old one (which
    # exits 9), carrying the new directory as its source dir.
    started = (tmp_path / "start.log").read_text(encoding="utf-8")
    assert IMAGE in started
    assert str(new) in started
    # The old version is left alone to fall back to.
    assert (old / "scripts" / "run-pawflow-docker.sh").read_text(
        encoding="utf-8").endswith("exit 9\n")


def test_a_failed_refresh_leaves_the_server_alone(tmp_path, dirs):
    """Abort, by default: the server is still running and still answers."""
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new))

    result = _run_script(tmp_path, script, fail_rel="tools/mcp_bridge.py")

    assert result.returncode != 0
    assert not (tmp_path / "start.log").exists()
    # The throw-away container is still removed on the way out.
    assert "rm -f fakecid" in (tmp_path / "docker.log").read_text(encoding="utf-8")


def test_force_starts_the_server_anyway_and_says_so(tmp_path, dirs):
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new),
        force_artifacts=True)

    result = _run_script(tmp_path, script, fail_rel="tools/mcp_bridge.py")

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert (tmp_path / "start.log").exists()


def test_an_artifact_an_older_image_lacks_is_only_a_warning(tmp_path, dirs):
    """The installer warns and continues for these; so does the update."""
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new))

    result = _run_script(tmp_path, script, fail_rel="docker/apparmor")

    assert result.returncode == 0, result.stderr
    assert "does not carry docker/apparmor" in result.stderr
    assert (tmp_path / "start.log").exists()
