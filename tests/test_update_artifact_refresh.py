"""The UI update must be the full command-line update, not a subset of it.

``scripts/install-pawflow.sh --pull-images`` is what an operator runs on the
host to update everything: the server image, the host-side artifacts it
carries, the local CLI tools image, both redistributable relay images, and the
old-image cleanup. The updater used to duplicate a subset of that sequence
(pull + artifact refresh + start script), which is exactly how the UI update
drifted into refreshing only the server. It now hands over to the installer
itself; these tests pin the shape of that handover and the directory
derivations the preflight still uses.
"""

import subprocess

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


def test_the_script_hands_over_to_the_installer_with_the_full_update_flags():
    script = update_manager._installer_updater_script(_info(), IMAGE, False)

    lines = script.splitlines()
    assert lines[0] == "set -eu"
    assert "bash scripts/install-pawflow.sh --port 19990 --pull-images" in script
    # The installer is the single source of truth: nothing here duplicates
    # its pull, artifact extraction, relay/CLI image handling, or cleanup.
    assert "docker pull" not in script
    assert "run-pawflow-docker.sh" not in script
    assert "docker cp" not in script


def test_the_target_image_is_pinned_and_the_identity_replayed():
    script = update_manager._installer_updater_script(
        _info(env={"PAWFLOW_BOOTSTRAP_GATEWAY_KEY": "not-roy-batty"}),
        IMAGE, False)

    assert f"PAWFLOW_IMAGE={IMAGE}" in script
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY=not-roy-batty" in script
    # First-run flags reach the script as empty to override the container's.
    assert "PAWFLOW_BOOTSTRAP_RESET=''" in script


def test_the_installer_decides_the_source_directory_itself():
    """PAWFLOW_SOURCE_DIR must not be replayed.

    Exported, it would override what the installer passes to
    ``run-pawflow-docker.sh`` and stamp the OLD directory onto the new
    container — the next update would then refresh the wrong artifacts.
    """
    script = update_manager._installer_updater_script(_info(), IMAGE, False)
    assert "PAWFLOW_SOURCE_DIR" not in script


def test_apparmor_is_skipped_inside_the_updater_container():
    """No sudo and no tty in the updater: the interactive prompt would hang
    or burn password attempts, and profiles cannot load from a container."""
    script = update_manager._installer_updater_script(_info(), IMAGE, False)
    assert "PAWFLOW_SKIP_APPARMOR=1" in script


def test_a_git_checkout_pulls_before_the_installer_runs():
    script = update_manager._installer_updater_script(_info(), IMAGE, True)
    assert "git pull --ff-only" in script
    assert script.index("git pull") < script.index("install-pawflow.sh")


def test_the_artifact_directory_is_handed_back_to_its_owner():
    """The updater may run as root; the operator's next command-line install
    must still be able to overwrite the directory this update created."""
    art = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    with_ids = update_manager._installer_updater_script(
        _info(env={"PAWFLOW_RUN_UID": "1000", "PAWFLOW_RUN_GID": "1000"}),
        IMAGE, False, artifact_dir=art)
    assert f"chown -R 1000:1000 {art}" in with_ids
    # Without the pair, the owner of the directory being replaced is read on
    # the host at run time.
    without = update_manager._installer_updater_script(
        _info(), IMAGE, False, artifact_dir=art)
    assert 'stat -c %u:%g' in without
    # The chown runs after the installer and never aborts the update.
    assert without.index("install-pawflow.sh") < without.index("chown -R")
    assert "|| true" in without.splitlines()[-1]
    # No artifact directory (git checkout): nothing to hand back.
    plain = update_manager._installer_updater_script(_info(), IMAGE, False)
    assert "chown" not in plain


def test_every_generated_installer_script_is_valid_posix_shell():
    for script in (
        update_manager._installer_updater_script(_info(), IMAGE, False),
        update_manager._installer_updater_script(_info(), IMAGE, True),
        update_manager._installer_updater_script(
            _info(), IMAGE, False,
            artifact_dir="/home/pawflow/.pawflow/runtime/1.0.0-beta.45"),
    ):
        proc = subprocess.run(["sh", "-n"], input=script,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
