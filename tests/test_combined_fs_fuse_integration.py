"""Real-kernel regression tests for the combined server-fs FUSE lifecycle.

Incident (2026-08-23 -> 2026-09-23): four threads of the relay worker sat in
`D` state in `request_wait_answer` (readdir on /tmp/pf_combined_fs) for a
month, because the FUSE responder was a thread of that same process: it died
with the process while the waiting threads kept the /dev/fuse file -- and so
the connection -- alive. These tests mount a real FUSE filesystem on a scratch
mountpoint (never the relay's own /tmp/pf_combined_fs) and check that every
failure mode now ends in bounded time.

Skipped where FUSE cannot be mounted (no pyfuse3, /dev/fuse or fusermount3).
Inside the relay container the AppArmor profile only lets FUSE mount under
/tmp/pf_combined_fs, /remote and /workspace: point PAWFLOW_FUSE_TEST_ROOT at
a directory under /remote you own to run them there.
"""
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SUBTREES = ["cc_sessions", "filestore", "skills"]


def _fuse_available() -> bool:
    try:
        import pyfuse3  # noqa: F401
        import trio  # noqa: F401
    except ImportError:
        return False
    return os.path.exists("/dev/fuse") and bool(shutil.which("fusermount3"))


pytestmark = pytest.mark.skipif(not _fuse_available(),
                                reason="FUSE not available")


class _SlowBackend:
    """Directories everywhere; readdir takes `delay` seconds to answer."""

    def __init__(self, delay):
        self.delay = delay

    def request(self, method, args, timeout=None):
        op = method.split(".", 1)[1]
        now = time.time()
        if op == "getattr":
            return {"data": {"st_mode": stat.S_IFDIR | 0o755, "st_size": 0,
                             "st_nlink": 2, "st_uid": os.getuid(),
                             "st_gid": os.getgid(), "st_atime": now,
                             "st_mtime": now, "st_ctime": now}}
        if op == "readdir":
            time.sleep(self.delay)
            return {"data": {"entries": ["a", "b"]}}
        return {"error": "ENOSYS", "errno": 38}


# The relay worker of the incident, reduced to its essence: it owns the mount
# and four of its own threads keep enumerating it.
_HARNESS = '''
import os, sys, threading, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from test_combined_fs_fuse_integration import _SlowBackend
from pawflow_relay.combined_fs import CombinedServerFsMount
mnt = sys.argv[3]
backend = _SlowBackend(3.0)
mount = CombinedServerFsMount(mnt, backend, backend, backend)
mount.start()
print("READY", mount._proc.pid, flush=True)
def scan(path):
    while True:
        try:
            os.listdir(path)
        except OSError:
            pass
targets = ["cc_sessions", "filestore", "skills", "cc_sessions/a"]
for t in targets:
    threading.Thread(target=scan, args=(os.path.join(mnt, t),)).start()
time.sleep(1.0)
print("SCANNING", flush=True)
time.sleep(3600)
'''

_ACCESSOR = '''
import os, sys
try:
    os.listdir(sys.argv[1])
    print("OK", flush=True)
except OSError as exc:
    print("ERR", exc.errno, flush=True)
'''


def _mounted(mnt) -> bool:
    with open("/proc/self/mountinfo", encoding="utf-8") as fh:
        return any(line.split()[4] == str(mnt) for line in fh)


def _gone(pid) -> bool:
    """Exited: no /proc entry, or a zombie waiting for its reaper."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


def _wait_for(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _fuse_waiters(pid) -> int:
    """Threads of `pid` blocked in the kernel on a FUSE answer."""
    count = 0
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            count += (task / "wchan").read_text() == "request_wait_answer"
        except OSError:
            continue
    return count


@pytest.fixture
def mnt(tmp_path):
    root = os.environ.get("PAWFLOW_FUSE_TEST_ROOT")
    path = (Path(root) / uuid.uuid4().hex) if root else (tmp_path / "mnt")
    yield path
    subprocess.run(["fusermount3", "-u", "-z", str(path)],
                   capture_output=True, check=False)
    if root and path.is_dir():
        path.rmdir()


@pytest.fixture
def mount(mnt):
    from pawflow_relay.combined_fs import CombinedServerFsMount
    backend = _SlowBackend(3.0)
    m = CombinedServerFsMount(str(mnt), backend, backend, backend)
    try:
        m.start()
    except RuntimeError as exc:
        pytest.skip(f"FUSE mount refused here: {exc}")
    yield m
    m.stop()


def _accessor(path):
    return subprocess.Popen([sys.executable, "-c", _ACCESSOR, str(path)],
                            stdout=subprocess.PIPE, text=True)


def test_killed_relay_with_its_own_readers_mid_readdir_exits(mnt):
    """The incident topology: SIGKILL the relay while four of its own
    threads wait on readdir. It must die, its responder must follow, and
    the mount must go away -- no D-state threads, no leaked connection."""
    relay = subprocess.Popen(
        [sys.executable, "-c", _HARNESS, str(REPO), str(REPO / "tests"),
         str(mnt)], stdout=subprocess.PIPE, text=True)
    try:
        first = relay.stdout.readline().split()
        if first[:1] != ["READY"]:
            pytest.skip("FUSE mount refused here")
        responder = int(first[1])
        assert relay.stdout.readline().strip() == "SCANNING"
        # The incident's kernel state: relay threads parked on FUSE answers.
        assert _wait_for(lambda: _fuse_waiters(relay.pid) >= 1, 10), \
            "readers never reached request_wait_answer"
        relay.kill()
        # Before the fix this wait never returned: the process stayed
        # <defunct> with its readers in D state for as long as the kernel ran.
        relay.wait(timeout=40)
        assert _wait_for(lambda: _gone(responder), 30), "responder outlived relay"
        assert _wait_for(lambda: not _mounted(mnt), 30), "mount outlived relay"
    finally:
        if relay.poll() is None:
            relay.kill()


def test_responder_crash_releases_callers_and_mount_recovers(mount, mnt):
    reader = _accessor(mnt / "cc_sessions")
    time.sleep(1.0)  # the readdir is now waiting on the backend
    crashed = mount._proc
    os.kill(crashed.pid, signal.SIGKILL)
    out, _ = reader.communicate(timeout=15)
    assert out.startswith("ERR"), out  # released, not stuck

    def _recovered():
        try:
            return (mount._proc is not crashed
                    and sorted(os.listdir(mnt)) == SUBTREES)
        except OSError:
            return False
    assert _wait_for(_recovered, 30), "responder not restarted"


def test_stop_with_readdir_in_flight_is_bounded(mount, mnt):
    reader = _accessor(mnt / "filestore")
    time.sleep(1.0)
    responder = mount._proc
    started = time.monotonic()
    mount.stop()
    assert time.monotonic() - started < 20
    reader.communicate(timeout=15)  # the caller returned, whatever it got
    assert responder.poll() is not None
    assert not _mounted(mnt)
