"""Regression test: both relay docker-run builders activate the server-fs FUSE.

Phase 3 drops `/workspace` in favor of `/cc_sessions/<conv>/<agent>`, which
the relay reaches via a FUSE-over-WS mount at `/cc_sessions`. For the mount
to come up inside the relay container, the docker-run command MUST carry:

    --cap-add SYS_ADMIN
    --device /dev/fuse
    --security-opt apparmor:unconfined

and the worker inside the container MUST receive the mountpoint — either via
`--server-mount /cc_sessions` on the CLI or via `PAWFLOW_SERVER_MOUNT=/cc_sessions`
in the env.

Two call sites build a docker-run for the relay:
  - pawflow_relay/thread.py     (user-side: `pawflow_cli --docker-image ...`)
  - core/server_relay_manager.py (server-spawned per-conversation relays)

Both are tested here by inspecting the module source — the commands are
assembled inline inside long methods, so a source-level assertion is the
least-invasive way to lock the config without extracting helpers.
"""

import inspect
from pathlib import Path
import unittest


class RelayFuseLaunchTests(unittest.TestCase):

    _FUSE_DOCKER_FLAGS = (
        '"--cap-add", "SYS_ADMIN"',
        '"--device", "/dev/fuse"',
    )

    def _assert_all_present(self, src: str, needles: tuple, where: str):
        missing = [n for n in needles if n not in src]
        self.assertFalse(
            missing,
            f'{where}: missing FUSE launch flags: {missing}',
        )

    # ── user-side relay (pawflow_cli --docker-image) ──────────────────

    def test_thread_py_docker_run_has_fuse_flags(self):
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self._assert_all_present(src, self._FUSE_DOCKER_FLAGS,
                                  'pawflow_relay/thread.py docker run')

    def test_thread_py_resolves_apparmor_profile(self):
        """AppArmor: pawflow-relay when loaded on the host, unconfined
        fallback otherwise — never the hardcoded unconfined literal."""
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self.assertIn('*_relay_apparmor_security_opts(', src)
        self.assertNotIn('"--security-opt", "apparmor:unconfined"', src)

    def test_thread_py_passes_server_mount_to_launcher(self):
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self.assertIn('"--server-mount", "/cc_sessions"', src,
                      'pawflow_relay/thread.py must pass --server-mount '
                      '/cc_sessions to pawflow_relay_launcher.py')

    def test_thread_py_passes_filestore_mount_to_launcher(self):
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self.assertIn('"--filestore-mount", "/filestore"', src,
                      'pawflow_relay/thread.py must pass --filestore-mount '
                      '/filestore to pawflow_relay_launcher.py so the FileStore '
                      'sister-protocol (ffs.*) FUSE comes up alongside /cc_sessions')

    def test_thread_py_passes_skills_mount_to_launcher(self):
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self.assertIn('"--skills-mount", "/skills"', src,
                      'pawflow_relay/thread.py must pass --skills-mount '
                      '/skills to pawflow_relay_launcher.py so the skills '
                      'sister-protocol (skfs.*) FUSE comes up alongside '
                      '/cc_sessions')

    # ── server-spawned relay (core/server_relay_manager.py) ───────────

    def test_server_relay_manager_has_fuse_flags(self):
        from core import server_relay_manager
        src = inspect.getsource(server_relay_manager)
        self._assert_all_present(src, self._FUSE_DOCKER_FLAGS,
                                  'core/server_relay_manager.py docker run')

    def test_server_relay_manager_resolves_apparmor_profile(self):
        from core import server_relay_manager
        src = inspect.getsource(server_relay_manager)
        self.assertIn('*relay_apparmor_security_opts(', src)
        self.assertNotIn('"--security-opt", "apparmor:unconfined"', src)

    def test_server_relay_manager_sets_server_mount_env(self):
        from core import server_relay_manager
        src = inspect.getsource(server_relay_manager)
        self.assertIn('PAWFLOW_SERVER_MOUNT=/cc_sessions', src,
                      'core/server_relay_manager.py must pass '
                      'PAWFLOW_SERVER_MOUNT=/cc_sessions env to relay '
                      'container (picked up by pawflow_relay.cli default)')

    def test_server_relay_manager_sets_filestore_mount_env(self):
        from core import server_relay_manager
        src = inspect.getsource(server_relay_manager)
        self.assertIn('PAWFLOW_FILESTORE_MOUNT=/filestore', src,
                      'core/server_relay_manager.py must pass '
                      'PAWFLOW_FILESTORE_MOUNT=/filestore env so the\n'
                      'FileStore FUSE (ffs.*) is mounted alongside /cc_sessions '
                      'in the server-spawned per-conversation relay container')

    def test_server_relay_manager_sets_skills_mount_env(self):
        from core import server_relay_manager
        src = inspect.getsource(server_relay_manager)
        self.assertIn('PAWFLOW_SKILLS_MOUNT=/skills', src,
                      'core/server_relay_manager.py must pass '
                      'PAWFLOW_SKILLS_MOUNT=/skills env so the skills FUSE '
                      '(skfs.*) is mounted alongside /cc_sessions in the '
                      'server-spawned per-conversation relay container')

    # ── Dockerfile prep (mountpoints created at build time) ───────────

    def test_dockerfile_precreates_fuse_mountpoints(self):
        # /cc_sessions, /filestore, and /skills must exist + be writable by
        # pawflow. The image creates the root-level mountpoints at build time,
        # then init.sh repairs ownership after runtime UID/GID remapping so
        # bind mounts with host-owned groups remain usable.
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'docker', 'relay-dev', 'Dockerfile')
        with open(path, 'r') as f:
            src = f.read()
        for needle in ('mkdir -p /workspace /cc_sessions /filestore /skills',
                       'chown pawflow:pawflow /workspace /cc_sessions /filestore /skills',
                       'chown -R pawflow:$(id -gn pawflow) "$d"',
                       'usermod -aG "$group" pawflow',
                       'chmod g+rwx "$d"'):
            self.assertIn(needle, src,
                          f'docker/relay-dev/Dockerfile must contain: {needle}')

    def test_dockerfile_installs_codex_cli(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'docker', 'relay-dev', 'Dockerfile')
        with open(path, 'r') as f:
            src = f.read()
        self.assertIn('@openai/codex', src)

    def test_dockerfile_does_not_build_rtk_by_default(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'docker', 'relay-dev', 'Dockerfile')
        with open(path, 'r') as f:
            src = f.read()
        self.assertNotIn('cargo install --git https://github.com/rtk-ai/rtk', src)
        self.assertNotIn('/usr/local/bin/rtk', src)

    def test_worker_forwards_local_flag_to_host_helper(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'pawflow_relay', 'worker.py')
        with open(path, 'r') as f:
            src = f.read()
        self.assertIn('msg.get("local", False)', src)
        self.assertIn('Start relay with --allow-local', src)
        self.assertIn('Local execution requested but host helper is unavailable', src)
        self.assertIn('_fwd = dict(msg)', src)

    def test_host_helper_executes_forwarded_filesystem_actions(self):
        from pawflow_relay import thread
        src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path("pawflow_relay").glob("*thread*.py")))
        self.assertIn('from fs_actions import ACTIONS as _FS_ACTIONS', src)
        self.assertIn('handler(self.directory, abs_path, req, allow_exec=True)', src)


if __name__ == '__main__':
    unittest.main()
