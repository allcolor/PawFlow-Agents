"""One-time combined FUSE mount setup for the relay worker.

Extracted verbatim from _ws_connect: mounts a single pyfuse3 CombinedServerFsMount
(serving the cc_sessions / filestore / skills subtrees) and symlinks the
canonical paths to it via sudo. Returns the three SwappableServerFsClient
handles (whose .set_inner the reconnect loop swaps per connection) and the
mount object (stopped on final exit). Returns Nones when no mount is requested
or the mount fails — identical to the in-closure behaviour.
"""
import logging
import os
import subprocess  # nosec B404
import sys

_log = logging.getLogger(__name__)


def setup_combined_fs(server_mount, filestore_mount, skills_mount):
    _server_fs_swap = None
    _server_fs_mount = None
    _filestore_fs_swap = None
    _skills_fs_swap = None
    if server_mount or filestore_mount or skills_mount:
        from pawflow_relay.server_fs_client import SwappableServerFsClient
        from pawflow_relay.server_fs_mount import CombinedServerFsMount
        # ONE pyfuse3 mount at /pawflow_fs serving both cc_sessions
        # (sfs.*) and filestore (ffs.*) subtrees. Required because
        # pyfuse3 keeps a single global session per process — two
        # separate mounts race on `pyfuse3.init()`, the second wins,
        # the first goes orphan, and any syscall on the orphan blocks
        # forever (no userspace daemon answers the kernel). The
        # canonical paths /cc_sessions and /filestore are restored
        # via `mount --bind` against the routed subtrees.
        _server_fs_swap = SwappableServerFsClient()
        _filestore_fs_swap = SwappableServerFsClient()
        _skills_fs_swap = SwappableServerFsClient()
        # Mountpoint under /tmp because it's tmpfs (always writable
        # by the relay user, no Dockerfile change required to grant
        # ownership). The bind-mounts that follow expose the canonical
        # /cc_sessions and /filestore paths so downstream consumers
        # don't see the temp location.
        _combined_root = "/tmp/pf_combined_fs"  # nosec B108 - relay-local FUSE mount root.
        try:
            _server_fs_mount = CombinedServerFsMount(
                _combined_root, _server_fs_swap, _filestore_fs_swap,
                _skills_fs_swap)
            _server_fs_mount.start()
            sys.stderr.write(
                f"[FSRelay] combined-fs mounted at {_combined_root}\n")
            # Expose each canonical path as a symlink to the routed
            # subtree of the combined FUSE mount. Symlinks rather than
            # `mount --bind` because they're cheaper and survive any
            # future restructuring; we route every filesystem op
            # through `sudo` because the canonical paths live in `/`
            # (root-owned) and pawflow can't rmdir entries there
            # without escalating privileges. The Dockerfile grants
            # pawflow NOPASSWD sudo precisely to enable this.
            _aliases = []
            if server_mount:
                _aliases.append((f"{_combined_root}/cc_sessions", server_mount))
            if filestore_mount:
                _aliases.append((f"{_combined_root}/filestore", filestore_mount))
            if skills_mount:
                _aliases.append((f"{_combined_root}/skills", skills_mount))

            def _sudo_run(argv: list, _what: str):
                _rc = subprocess.run(  # nosec B603
                    ["sudo", "-n"] + argv,
                    capture_output=True, text=True, timeout=5)
                if _rc.returncode != 0:
                    sys.stderr.write(
                        f"[FSRelay] {_what} FAILED rc={_rc.returncode} "
                        f"stdout={_rc.stdout.strip()!r} "
                        f"stderr={_rc.stderr.strip()!r}\n")
                return _rc.returncode == 0

            for _src, _dst in _aliases:
                try:
                    # Wipe whatever's at the canonical path (empty dir
                    # from the Dockerfile, leftover symlink from a
                    # previous run, …). `rm -rf` covers all cases.
                    if not _sudo_run(["rm", "-rf", _dst],
                                     f"sudo rm -rf {_dst}"):
                        continue
                    # Re-create the parent dir if `rm -rf` removed it
                    # (it shouldn't for top-level paths like /cc_sessions
                    # but be defensive).
                    _parent = os.path.dirname(_dst) or "/"
                    if not os.path.isdir(_parent):
                        _sudo_run(["mkdir", "-p", _parent],
                                  f"sudo mkdir -p {_parent}")
                    if not _sudo_run(["ln", "-s", _src, _dst],
                                     f"sudo ln -s {_src} {_dst}"):
                        continue
                    sys.stderr.write(
                        f"[FSRelay] symlinked {_dst} → {_src}\n")
                except Exception as _serr:
                    sys.stderr.write(
                        f"[FSRelay] symlink {_dst} → {_src} "
                        f"FAILED: {_serr}\n")
        except Exception as _smerr:
            import traceback as _tb
            _full_tb = _tb.format_exc()
            sys.stderr.write(
                f"[FSRelay] combined-fs mount FAILED: {_smerr}\n"
                "  Likely cause: missing pyfuse3 / libfuse3, or no "
                "CAP_SYS_ADMIN. Continuing without combined-fs.\n"
                f"  full traceback follows:\n{_full_tb}")
            # Also write the traceback into the FUSE trace file so
            # users diagnosing a mount failure don't have to dig
            # through relay.log to find the cause.
            try:
                from pawflow_relay.server_fs_mount import _fuse_trace_emit
                _fuse_trace_emit(
                    f"[FSRelay] combined-fs mount FAILED err={_smerr}\n"
                    f"--- traceback ---\n{_full_tb}--- end ---")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            _server_fs_mount = None
            _server_fs_swap = None
            _filestore_fs_swap = None
            _skills_fs_swap = None
    return _server_fs_swap, _filestore_fs_swap, _skills_fs_swap, _server_fs_mount
