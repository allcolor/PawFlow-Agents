"""Updating from the UI reclaims disk, like updating from the command line.

The installer has always pruned the PawFlow image tags an install stopped
using. The update launched from the UI never did, so an instance that only ever
updated from the UI kept every version it had run -- beta.49, .50, .53, .57,
.59, .61 -- at a couple of gigabytes each, until a command-line reinstall
removed them all at once and made the omission visible.

These tests run the generated shell for real, against a fake ``docker`` on
PATH, because the interesting part is *which* refs it decides to remove. A test
that only grepped the generated text would pass on a script that removes the
image the server is running.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import core.update_manager as um

DOCKER_SHIM = """#!/bin/sh
case "$1" in
  ps) cat "$PF_PS" ;;
  images) cat "$PF_IMAGES" ;;
  rmi) shift; echo "$1" >> "$PF_RMI" ;;
  image) echo pruned >> "$PF_PRUNE" ;;
esac
exit 0
"""


class CleanupSelectsTheRightTags(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shim = self.tmp / "docker"
        shim.write_text(DOCKER_SHIM, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
        self.rmi = self.tmp / "rmi.log"
        self.prune = self.tmp / "prune.log"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, keep, images, containers=()):
        # `docker images` is asked for "<ref> <id>"; a fixture may give either
        # a bare ref (id irrelevant to that test) or the pair.
        (self.tmp / "images").write_text("\n".join(images) + "\n",
                                         encoding="utf-8")
        (self.tmp / "ps").write_text(
            ("\n".join(containers) + "\n") if containers else "",
            encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.tmp}{os.pathsep}{env.get('PATH', '')}",
            "PF_IMAGES": str(self.tmp / "images"),
            "PF_PS": str(self.tmp / "ps"),
            "PF_RMI": str(self.rmi),
            "PF_PRUNE": str(self.prune),
        })
        script = "set -eu\n" + "\n".join(um._image_cleanup_lines(keep))
        proc = subprocess.run(["sh", "-c", script], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        removed = (self.rmi.read_text(encoding="utf-8").split()
                   if self.rmi.exists() else [])
        return removed, proc.stdout

    def test_superseded_tags_go_and_the_current_one_stays(self):
        removed, out = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=[
                "ghcr.io/allcolor/pawflow:1.0.0-beta.62",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.61",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.59",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.49",
            ])
        self.assertNotIn("ghcr.io/allcolor/pawflow:1.0.0-beta.62", removed)
        self.assertEqual(sorted(removed), [
            "ghcr.io/allcolor/pawflow:1.0.0-beta.49",
            "ghcr.io/allcolor/pawflow:1.0.0-beta.59",
            "ghcr.io/allcolor/pawflow:1.0.0-beta.61",
        ])
        self.assertIn("Removing old image tag", out)

    def test_an_image_some_container_uses_is_never_removed(self):
        """The daemon overrules the keep list.

        The relay a user pinned by hand, an older server still referenced by a
        stopped container: this code cannot know about them, so it asks.
        """
        removed, _ = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=[
                "ghcr.io/allcolor/pawflow:1.0.0-beta.62",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.61",
                "ghcr.io/allcolor/pawflow-relay-dev:2026.05.01",
            ],
            containers=[
                "ghcr.io/allcolor/pawflow:1.0.0-beta.61",
                "ghcr.io/allcolor/pawflow-relay-dev:2026.05.01",
            ])
        self.assertEqual(removed, [])

    def test_images_outside_the_pawflow_repositories_are_untouchable(self):
        """Only what this project publishes is a candidate. A locally built or
        third-party image is never re-pullable on the operator's behalf."""
        removed, _ = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=[
                "postgres:16",
                "pawflow-claude-code:latest",
                "docker:cli",
                "ghcr.io/someone-else/pawflow:1.0.0",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.61",
            ])
        self.assertEqual(removed, ["ghcr.io/allcolor/pawflow:1.0.0-beta.61"])

    def test_untagged_pawflow_layers_go_by_id_and_no_global_prune_runs(self):
        """``repo:<none>`` cannot be removed by name, only by id.

        It used to be left to ``docker image prune --filter dangling=true``,
        which is daemon-wide: updating PawFlow deleted the untagged layers of
        every other project sharing the host's Docker daemon -- the one thing
        the repository filter above exists to prevent. The layer is still
        reclaimed; the blast radius is now the same as every other removal.
        """
        removed, out = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=[
                "ghcr.io/allcolor/pawflow:<none> sha256deadbeef",
                "ghcr.io/allcolor/pawflow:1.0.0-beta.62 sha256current",
            ])
        self.assertEqual(removed, ["sha256deadbeef"])
        self.assertIn("Removing untagged image", out)
        self.assertFalse(self.prune.exists(),
                         "no daemon-wide prune may run: it reaches every "
                         "other project on the host")

    def test_an_untagged_layer_a_container_still_uses_is_spared(self):
        # `docker ps --format {{.Image}}` reports an untagged image by id, so
        # the keep list has to be matched against the id too.
        removed, _ = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=["ghcr.io/allcolor/pawflow:<none> sha256inuse"],
            containers=["sha256inuse"])
        self.assertEqual(removed, [])

    def test_another_projects_untagged_layer_is_never_touched(self):
        removed, _ = self._run(
            keep=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"],
            images=[
                "postgres:<none> sha256postgres",
                "ghcr.io/someone-else/pawflow:<none> sha256other",
            ])
        self.assertEqual(removed, [])
        self.assertFalse(self.prune.exists())

    def test_the_relay_images_survive_even_with_no_relay_running(self):
        """A relay is spawned on demand, so it is normally referenced by no
        container when an update ends. Pruning it would cost a multi-gigabyte
        pull the next time an agent touches a file."""
        removed, _ = self._run(
            keep=[
                "ghcr.io/allcolor/pawflow:1.0.0-beta.62",
                "ghcr.io/allcolor/pawflow-relay-dev:2026.07.01",
                "ghcr.io/allcolor/pawflow-relay-minimal:2026.07.01",
            ],
            images=[
                "ghcr.io/allcolor/pawflow:1.0.0-beta.62",
                "ghcr.io/allcolor/pawflow-relay-dev:2026.07.01",
                "ghcr.io/allcolor/pawflow-relay-minimal:2026.07.01",
                "ghcr.io/allcolor/pawflow-relay-dev:2026.01.01",
            ],
            containers=[])
        self.assertEqual(removed,
                         ["ghcr.io/allcolor/pawflow-relay-dev:2026.01.01"])

    def test_a_failing_removal_does_not_fail_the_update(self):
        """The cleanup is the last step and costs only disk. Under ``set -e`` a
        bare rmi would abort the script; the update itself is already done by
        then, but the updater's exit status is what an operator reads."""
        (self.tmp / "docker").write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  ps) exit 1 ;;\n"
            "  images) echo 'ghcr.io/allcolor/pawflow:1.0.0-beta.61' ;;\n"
            "  rmi) exit 1 ;;\n"
            "  image) exit 1 ;;\n"
            "esac\n"
            "exit 1\n", encoding="utf-8")
        (self.tmp / "docker").chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{self.tmp}{os.pathsep}{env.get('PATH', '')}"
        script = "set -eu\n" + "\n".join(um._image_cleanup_lines(
            ["ghcr.io/allcolor/pawflow:1.0.0-beta.62"]))
        proc = subprocess.run(["sh", "-c", script], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("could not remove", proc.stderr)


class BothUpdatePathsClean(unittest.TestCase):
    """Neither deployment shape may be the one that keeps leaking disk."""

    MARKER = "Cleaning older PawFlow image tags"

    def test_the_compose_updater_cleans_after_bringing_the_stack_up(self):
        script = um._updater_script(
            "/srv/pawflow", pull_source=False,
            keep_images=["ghcr.io/allcolor/pawflow:1.0.0-beta.62"])
        self.assertIn(self.MARKER, script)
        self.assertLess(script.index("docker compose up -d --build"),
                        script.index(self.MARKER),
                        "cleanup must run after the stack is back up")

    def test_the_installer_updater_delegates_cleanup_to_the_installer(self):
        # ``install-pawflow.sh`` cleans old images itself (CLEAN_OLD_IMAGES
        # defaults on), so the installer updater emits no cleanup of its own —
        # a second cleanup here would be the drift the handover removed.
        info = {"host_app_dir": "/srv/pawflow"}
        script = um._installer_updater_script(
            info, "ghcr.io/allcolor/pawflow:1.0.0-beta.62", pull_source=False)
        self.assertNotIn(self.MARKER, script)
        self.assertIn('PAWFLOW_INSTALLER="scripts/install-pawflow.sh"', script)
        self.assertIn('bash "$PAWFLOW_INSTALLER"', script)
        self.assertIn("--pull-images", script)

    def test_the_image_being_installed_is_pinned_for_the_installer(self):
        target = "ghcr.io/allcolor/pawflow:1.0.0-beta.62"
        script = um._installer_updater_script(
            {"host_app_dir": "/srv/pawflow"}, target, pull_source=False)
        self.assertIn(f"PAWFLOW_IMAGE={target}", script)

    def test_every_generated_script_is_valid_posix_shell(self):
        """The updater runs under Alpine ash, not bash."""
        for script in (
            um._updater_script("/srv/pawflow", pull_source=False,
                               keep_images=["ghcr.io/allcolor/pawflow:1"]),
            um._updater_script("/srv/pawflow", pull_source=True,
                               keep_images=["ghcr.io/allcolor/pawflow:1"]),
            um._installer_updater_script(
                {"host_app_dir": "/srv/pawflow"},
                "ghcr.io/allcolor/pawflow:1", pull_source=False),
        ):
            proc = subprocess.run(["sh", "-n"], input=script,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class BuildLeftoversAreReclaimed(unittest.TestCase):
    """Each CLI/relay/server rebuild leaves a superseded untagged image and
    gigabytes of build cache; 41 GB of cache filled a VPS before this ran."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = self.tmp / "calls.log"
        self._shim("exit 0")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _shim(self, body):
        shim = self.tmp / "docker"
        shim.write_text(f'#!/bin/sh\necho "$*" >> "{self.calls}"\n{body}\n',
                        encoding="utf-8")
        shim.chmod(0o755)

    def _run(self, limit):
        env = dict(os.environ)
        env["PATH"] = f"{self.tmp}{os.pathsep}{env.get('PATH', '')}"
        script = "set -eu\n" + "\n".join(um.build_cleanup_shell(limit))
        proc = subprocess.run(["sh", "-c", script], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return self.calls.read_text(encoding="utf-8").splitlines()

    def test_only_pawflow_labelled_images_are_pruned_and_cache_is_capped(self):
        calls = self._run("10GB")
        self.assertEqual(calls, [
            f"image prune -f --filter label={um.BUILD_LABEL}",
            "builder prune -f --max-used-space 10GB",
        ])

    def test_an_older_daemon_falls_back_to_keep_storage(self):
        self._shim('case "$*" in *max-used-space*) exit 1 ;; esac\nexit 0')
        calls = self._run("10GB")
        self.assertEqual(calls[-1], "builder prune -f --keep-storage 10GB")

    def test_a_failing_cleanup_never_fails_the_build(self):
        self._shim("exit 1")
        self._run("10GB")

    def test_cache_trim_can_be_disabled(self):
        for value in ("0", "off", ""):
            with unittest.mock.patch.dict(os.environ,
                                          {"PAWFLOW_BUILD_CACHE_MAX": value}):
                self.assertEqual(um.build_cache_limit(), "")
        self.calls.unlink(missing_ok=True)
        self.assertEqual(self._run(""), [
            f"image prune -f --filter label={um.BUILD_LABEL}"])

    def test_cache_limit_defaults_and_overrides(self):
        env = {k: v for k, v in os.environ.items()
               if k != "PAWFLOW_BUILD_CACHE_MAX"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(um.build_cache_limit(), um.DEFAULT_BUILD_CACHE_MAX)
        with unittest.mock.patch.dict(os.environ,
                                      {"PAWFLOW_BUILD_CACHE_MAX": "25GB"}):
            self.assertEqual(um.build_cache_limit(), "25GB")

    def test_the_ui_update_script_reclaims_build_leftovers_last(self):
        script = um._updater_script("/srv/pawflow", pull_source=True,
                                    keep_images=["ghcr.io/allcolor/pawflow:1"])
        self.assertIn("builder prune", script)
        self.assertGreater(script.index("builder prune"),
                           script.index("docker compose up -d --build"))

    def test_local_builds_carry_the_label_the_prune_filters_on(self):
        import yaml
        compose = yaml.safe_load(Path("docker-compose.yml").read_text(
            encoding="utf-8"))
        for name, service in compose["services"].items():
            if "build" in service:
                self.assertEqual(
                    service["build"].get("labels", {}).get("org.pawflow.build"),
                    "1", name)
        source = Path(um.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('["--label", BUILD_LABEL, "-t", image'), 2)

    def test_both_installers_trim_the_cache_after_every_cleanup(self):
        sh = Path("scripts/install-pawflow.sh").read_text(encoding="utf-8")
        ps1 = Path("scripts/install-pawflow.ps1").read_text(encoding="utf-8")
        self.assertEqual(sh.count("  cleanup_retagged_pawflow_images\n"),
                         sh.count("  cleanup_retagged_pawflow_images\n  trim_build_cache\n"))
        self.assertEqual(ps1.count("Cleanup-RetaggedPawFlowImages\n"),
                         ps1.count("Cleanup-RetaggedPawFlowImages\n    Trim-BuildCache\n")
                         + ps1.count("Cleanup-RetaggedPawFlowImages\nTrim-BuildCache\n"))
        for src in (sh, ps1):
            self.assertIn("PAWFLOW_BUILD_CACHE_MAX", src)
            self.assertIn("--max-used-space", src)
            self.assertIn("--keep-storage", src)


if __name__ == "__main__":
    unittest.main()
