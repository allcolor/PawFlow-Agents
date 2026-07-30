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
    assert f"_pf_start_dir={new_dir}" in script
    assert 'cd "$_pf_start_dir"' in script
    # The label follows whichever directory is actually started from, so it can
    # never name one the server did not come up out of.
    assert 'PAWFLOW_SOURCE_DIR="$_pf_start_dir"' in script
    assert f"PAWFLOW_SOURCE_DIR={new_dir}" not in script


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
    # Default: a non-zero refresh ends the updater, so the server it has not
    # touched yet keeps running on its old version.
    assert '[ "$_pf_rc" -eq 0 ] || exit "$_pf_rc"' in strict
    assert "WARNING host artifacts were not refreshed" not in strict
    # Forced: the same status only warns, and the start script still runs.
    assert '[ "$_pf_rc" -eq 0 ] || exit' not in forced
    assert "WARNING host artifacts were not refreshed" in forced
    assert forced.rstrip().splitlines()[-1].endswith(
        "bash scripts/run-pawflow-docker.sh")


def test_extracted_files_are_chowned_to_the_uid_the_server_runs_as():
    """The updater is root; files it leaves root-owned block the next install."""
    new_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    script = update_manager._installer_updater_script(
        _info(env={"PAWFLOW_RUN_UID": "1000", "PAWFLOW_RUN_GID": "1000"}),
        IMAGE, False, artifact_dir=new_dir)
    assert f"chown -R 1000:1000 {new_dir}" in script


def test_half_a_uid_pair_still_hands_the_files_over():
    """The silent way this went wrong on a real deployment.

    A container created by an older start script carries PAWFLOW_RUN_UID and no
    PAWFLOW_RUN_GID. Requiring both meant no chown at all -- and no warning
    either, so the updater's log showed a clean run while the new directory was
    left root-owned and the operator's own installer could no longer write it.
    The install directory being replaced is on the host and has the right
    owner, so it is read there instead of being guessed here.
    """
    old_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.44"
    new_dir = "/home/pawflow/.pawflow/runtime/1.0.0-beta.45"
    for env in ({"PAWFLOW_RUN_UID": "1000"}, {}, {"PAWFLOW_RUN_GID": "1000"}):
        script = update_manager._installer_updater_script(
            _info(app_dir=old_dir, env=env), IMAGE, False, artifact_dir=new_dir)
        assert f'chown -R "$(stat -c %u:%g {old_dir})" {new_dir}' in script, env


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
    """Forced past a failed refresh, the server starts -- on ONE set of files.

    It used to start on a mixture: artifacts were deleted and replaced one at a
    time, so a failure partway left the new directory holding everything up to
    the one that failed and nothing after it, and the server came up on that.
    The warning says "the host-side files of the version it is replacing", and
    now that is what it gets: nothing is swapped in until every artifact has
    been copied, so a failed refresh leaves the previous version whole.
    """
    old, new = dirs
    (old / "scripts" / "run-pawflow-docker.sh").write_text(
        "#!/usr/bin/env bash\n"
        'echo STARTED "$PAWFLOW_IMAGE" "$PAWFLOW_SOURCE_DIR" >> "$START_LOG"\n',
        encoding="utf-8")

    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new),
        force_artifacts=True)

    result = _run_script(tmp_path, script, fail_rel="tools/mcp_bridge.py")

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    started = (tmp_path / "start.log").read_text(encoding="utf-8")
    assert IMAGE in started
    # The whole set it starts on is the old one, not half of each.
    assert str(old) in started
    assert not (new / "tools" / "mcp_bridge.py").exists()
    assert not (new / "scripts" / "run-pawflow-docker.sh").exists()


def test_a_failed_refresh_does_not_destroy_what_it_was_replacing(tmp_path):
    """The in-place refresh that ate the operator's installation.

    Each artifact was deleted just before its replacement was copied in. One
    failed `docker cp` -- a missing path, a full disk, a daemon that went away
    -- and that artifact was simply gone from a directory the operator was
    still running from.
    """
    target = tmp_path / "install"
    (target / "scripts").mkdir(parents=True)
    (target / "tools").mkdir()
    (target / "scripts" / "run-pawflow-docker.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (target / "tools" / "mcp_bridge.py").write_text(
        "the version that works\n", encoding="utf-8")

    script = "\n".join(
        ["set -eu"]
        + update_manager._artifact_refresh_lines(
            IMAGE, str(target), {}, force=False))

    result = _run_script(tmp_path, script, fail_rel="core/tool_json.py")

    assert result.returncode != 0
    # Not a single one of the artifacts already there was destroyed, including
    # the ones the loop had passed before it hit the failure.
    assert (target / "tools" / "mcp_bridge.py").read_text(
        encoding="utf-8") == "the version that works\n"
    assert (target / "scripts" / "run-pawflow-docker.sh").read_text(
        encoding="utf-8").endswith("exit 0\n")
    # And the staging directory does not survive the failure.
    assert not list(target.glob(".pawflow-refresh.*"))


def test_refresh_rejects_an_intermediate_symlink_inside_target(tmp_path):
    target = tmp_path / "install"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "scripts").symlink_to(outside, target_is_directory=True)
    sentinel = outside / "run-pawflow-docker.sh"
    sentinel.write_text("outside must survive\n", encoding="utf-8")
    script = "\n".join(
        ["set -eu"] + update_manager._artifact_refresh_lines(
            IMAGE, str(target), {}, force=False))

    result = _run_script(tmp_path, script)

    assert result.returncode != 0
    assert "symlink parent" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "outside must survive\n"


def test_refresh_rejects_a_symlink_in_the_target_path_itself(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "runtime"
    linked_parent.symlink_to(outside, target_is_directory=True)
    target = linked_parent / "new-version"
    script = "\n".join(
        ["set -eu"] + update_manager._artifact_refresh_lines(
            IMAGE, str(target), {}, force=False))

    result = _run_script(tmp_path, script)

    assert result.returncode != 0
    assert "symlink parent" in result.stderr
    assert not (outside / "new-version").exists()


def test_the_chown_runs_and_the_files_stay_reachable(tmp_path, dirs):
    """The chown is executed, not merely emitted, and it names a real owner.

    Run as an ordinary user the chown is a no-op -- everything is already ours
    -- but it still exercises the whole line: `stat -c` must parse on this
    shell, the substitution must produce a usable owner, and the warning must
    stay silent. On the deployment it is the difference between an install
    directory the operator can still write and one they cannot.
    """
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new))

    result = _run_script(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert "could not chown" not in result.stderr
    assert (new / "scripts" / "run-pawflow-docker.sh").stat().st_uid == os.getuid()


def test_force_falls_back_to_the_directory_that_has_the_start_script(tmp_path, dirs):
    """The failure that left a server on its old version with nothing to show.

    When the refresh fails on the *first* artifact -- the start script itself --
    the new directory exists and is empty. Forcing past that used to cd into it
    and run a script that is not there: the updater died, the server was never
    touched, and the UI reported a launched update. The directory being replaced
    still carries a working script, so the server comes up on the new image.
    """
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new),
        force_artifacts=True)

    result = _run_script(tmp_path, script,
                         fail_rel="scripts/run-pawflow-docker.sh")

    assert result.returncode == 9, result.stderr  # the old script's own exit
    assert "carries no scripts/run-pawflow-docker.sh" in result.stderr


def test_a_failed_refresh_still_hands_the_directory_back_to_its_owner(tmp_path, dirs):
    """What broke the operator's own install-pawflow.sh after a failed update.

    The updater runs as root, so everything it creates is root-owned. Chowning
    only after a successful copy left a half-written root-owned directory that
    the operator's next command-line install could not overwrite -- it failed on
    unlinkat with permission denied, and the command line stopped being the way
    out of a broken update.
    """
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old),
              env={"PAWFLOW_RUN_UID": "1000", "PAWFLOW_RUN_GID": "1000"}),
        IMAGE, False, artifact_dir=str(new))

    lines = script.splitlines()
    chown = next(i for i, ln in enumerate(lines) if ln.startswith("chown -R"))
    rc = next(i for i, ln in enumerate(lines) if "_pf_rc=$?" in ln)
    fail = next(i for i, ln in enumerate(lines) if '"$_pf_rc" -eq 0' in ln)
    # The chown sits between capturing the status and acting on it: outside the
    # subshell, and on the failure path as much as on the success one.
    assert rc < chown < fail


def test_an_artifact_an_older_image_lacks_is_only_a_warning(tmp_path, dirs):
    """The installer warns and continues for these; so does the update."""
    old, new = dirs
    script = update_manager._installer_updater_script(
        _info(app_dir=str(old)), IMAGE, False, artifact_dir=str(new))

    result = _run_script(tmp_path, script, fail_rel="docker/apparmor")

    assert result.returncode == 0, result.stderr
    assert "does not carry docker/apparmor" in result.stderr
    assert (tmp_path / "start.log").exists()
