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

    def test_the_installer_updater_cleans_after_the_start_script(self):
        info = {"host_app_dir": "/srv/pawflow"}
        script = um._installer_updater_script(
            info, "ghcr.io/allcolor/pawflow:1.0.0-beta.62", pull_source=False)
        self.assertIn(self.MARKER, script)
        self.assertLess(script.index("run-pawflow-docker.sh"),
                        script.index(self.MARKER),
                        "cleanup must run after the server is restarted")

    def test_the_image_being_installed_is_always_spared(self):
        target = "ghcr.io/allcolor/pawflow:1.0.0-beta.62"
        script = um._installer_updater_script(
            {"host_app_dir": "/srv/pawflow"}, target, pull_source=False)
        keep_line = [ln for ln in script.split("\n")
                     if ln.startswith("_pf_keep=")]
        self.assertTrue(keep_line, "no keep list in the generated script")
        self.assertIn(target, keep_line[0])

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


if __name__ == "__main__":
    unittest.main()
