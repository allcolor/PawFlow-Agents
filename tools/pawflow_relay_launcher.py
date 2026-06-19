#!/usr/bin/env python3
"""PawFlow relay worker — launcher script.

This file is the Python entry point the user (or the PawFlow server)
executes to start a relay. All logic now lives in the pawflow_relay
package:

  pawflow_relay/
    cli.py       — argparse + main() (worker_main)
    worker.py    — _ws_connect, action dispatch
    auth.py      — claude auth login + host-helper bridge
    register.py  — OAuth auto-registration + service (un)install
    ws_frame.py  — stdlib WebSocket frame codec
    utils.py     — docker/path/host helpers + api_call

The script does three things: emit a BOOT banner (same line as before,
for log tooling that scrapes it), ensure the launching dir is on
sys.path so sibling `fs_*` modules resolve in-container, then call
`pawflow_relay.cli.worker_main()`.
"""

import os
import sys

sys.stderr.write(
    f"[FSRelay] BOOT: script={__file__!r}, argv[0]={sys.argv[0]!r}, "
    f"python={sys.version.split()[0]}\n")
sys.stderr.flush()

# This launcher's name (`pawflow_relay.py`) collides with the package name
# (`pawflow_relay/`). Python's FileFinder picks the package over a module
# when both sit in the SAME directory (the container layout: package is
# bind-mounted at /opt/pawflow/pawflow_relay/ next to /opt/pawflow/pawflow_relay.py).
# Outside the container (local dev: script in tools/, package at repo root)
# we need to make the repo root visible BEFORE the script dir so the
# package wins at `from pawflow_relay.cli import ...`. Script dir stays on
# path for the sibling `fs_*` modules the worker uses.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
if _script_dir not in sys.path:
    sys.path.insert(1, _script_dir)

# Import + call kept INSIDE the __main__ guard on purpose.
#
# Other files in tools/ (fs_common, fs_actions, fs_exec) do
# `from pawflow_relay.utils import ...`. If tools/ is in sys.path and the
# package dir sits elsewhere (test environment), Python's finder may walk
# sys.path and pick THIS file as the `pawflow_relay` module first. Running
# its top-level import of pawflow_relay.cli would then re-enter THIS file
# (now being imported, not executed) and fail because a single-file module
# has no submodule `.cli`.
#
# When actually executed as a script, __name__ is "__main__", never
# "pawflow_relay", so the re-entry path is unreachable.
if __name__ == "__main__":
    from pawflow_relay.cli import worker_main
    worker_main()
