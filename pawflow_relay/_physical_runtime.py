"""Static physical-container supervisor and private logical worker bootstrap.

Invoked by absolute script path with Python isolated mode, before any workspace
code is importable. The complete configuration arrives over stdin. No physical
service is registered here: each existing relay worker registers its logical
connection. A failed worker or network helper terminates the complete group.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import subprocess  # nosec B404 - fixed executables, argv only.
import sys
import tempfile
import threading
from pathlib import Path

ROOTFS = "/run/pawflow-rootfs"
SCRIPT = "/opt/pawflow/pawflow_relay/_physical_runtime.py"
SECCOMP_PROFILE = Path(__file__).with_name("physical-seccomp.json")


def _run(*args):
    subprocess.run(list(args), check=True, timeout=30)  # nosec B603


def _bind(source, target, *, readonly=False, recursive=False):
    target = Path(target)
    if Path(source).is_dir():
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    _run("mount", "--rbind" if recursive else "--bind", str(source), str(target))
    if readonly:
        _run("mount", "-o", "remount,bind,ro", str(target))


def prepare_root(export: dict) -> Path:
    """Build one disposable root without exposing the supervisor's mount table."""
    digest = hashlib.sha256(export["relay_id"].encode("utf-8")).hexdigest()
    source = Path("/run/pawflow-physical") / digest
    if export["root_mount"] != str(source / "workspace") or export["home_mount"] != str(source / "home"):
        raise ValueError("Workspace mounts do not match the logical relay identity")
    if export["mode"] not in ("ro", "rw"):
        raise ValueError("Workspace mode must be ro or rw")
    base = Path(tempfile.mkdtemp(prefix=digest + "-", dir=ROOTFS))
    upper, work, root = (base / name for name in ("upper", "work", "root"))
    for directory in (upper, work, root):
        directory.mkdir()
    _run("mount", "--make-rprivate", "/")
    _run("mount", "-t", "overlay", "overlay", "-o",
         f"lowerdir=/,upperdir={upper},workdir={work}", str(root))
    # Overlay lower layers do not traverse Docker's nested bind mounts.
    _bind("/opt/pawflow", root / "opt/pawflow", readonly=True, recursive=True)
    for filename in ("hosts", "hostname"):
        target = root / "etc" / filename
        if target.is_symlink():
            target.unlink()
        target.write_bytes((Path("/etc") / filename).read_bytes())
    resolver = root / "etc/resolv.conf"
    if resolver.is_symlink():
        resolver.unlink()
    resolver.write_text("nameserver 10.0.2.3\n", encoding="utf-8")
    home = source / "home"
    if not any(home.iterdir()):
        shutil.copytree("/home/pawflow", home, dirs_exist_ok=True, symlinks=True)
    _bind(source / "workspace", root / "workspace", readonly=export["mode"] == "ro")
    _bind(home, root / "home/pawflow")
    for name, mode in (("tmp", "1777"), ("run", "755"), ("dev", "755")):
        target = root / name
        target.mkdir(exist_ok=True)
        _run("mount", "-t", "tmpfs", "-o", f"mode={mode},nosuid", "tmpfs", str(target))
    for name in ("null", "zero", "full", "random", "urandom", "tty", "fuse"):
        _bind(Path("/dev") / name, root / "dev" / name)
    for name in ("pts", "shm"):
        (root / "dev" / name).mkdir()
    _run("mount", "-t", "devpts", "-o", "newinstance,ptmxmode=0666,mode=0620",
         "devpts", str(root / "dev/pts"))
    _run("mount", "-t", "tmpfs", "-o", "mode=1777,nosuid,nodev", "tmpfs", str(root / "dev/shm"))
    for name, target in (("ptmx", "pts/ptmx"), ("fd", "/proc/self/fd"),
                         ("stdin", "/proc/self/fd/0"), ("stdout", "/proc/self/fd/1"),
                         ("stderr", "/proc/self/fd/2")):
        (root / "dev" / name).symlink_to(target)
    (root / "proc").mkdir(exist_ok=True)
    _run("mount", "-t", "proc", "-o", "nosuid,nodev,noexec", "proc", str(root / "proc"))
    (root / "sys").mkdir(exist_ok=True)
    _run("mount", "-t", "sysfs", "-o", "ro,nosuid,nodev,noexec", "sysfs", str(root / "sys"))
    return root


def enter_root(root: Path) -> None:
    """Detach the old root so a privileged logical process cannot chroot back out."""
    old_root = root / ".physical-old-root"
    old_root.mkdir()
    os.chdir(root)
    _run("pivot_root", ".", ".physical-old-root")
    os.chdir("/")
    _run("umount", "-l", "/.physical-old-root")
    os.rmdir("/.physical-old-root")


def private_command(control_fd: int) -> list[str]:
    """Create the restricted view, then wait for privileged parent ID mapping."""
    return [
        "unshare", "--user", "--mount", "--pid", "--fork",
        "--kill-child=SIGKILL", "--mount-proc", "--setgroups=allow",
        sys.executable, "-I", "-u", SCRIPT, "--private-fd", str(control_fd),
    ]


def identity_maps() -> dict[str, str]:
    """Preserve every parent-visible ID range, including remapped Docker IDs."""
    result = {}
    for kind in ("uid", "gid"):
        mappings = Path(f"/proc/self/{kind}_map").read_text(encoding="ascii").splitlines()
        if not mappings:
            raise ValueError("The physical runtime requires mapped user and group IDs")
        lines = []
        for mapping in mappings:
            inner, _outer, count = map(int, mapping.split())
            lines.append(f"{inner} {inner} {count}\n")
        result[kind] = "".join(lines)
    return result


def private_init(control_fd: int) -> None:
    """Execute the image init only after both ID maps have been installed."""
    with socket.socket(fileno=control_fd) as control:
        control.settimeout(30)
        control.sendall(b"user-ready")
        command = json.loads(control.recv(1024 * 1024))
    # Fixed image init; the owned parent supplies an argv list, never shell text.
    os.execv("/usr/bin/tini", ["/usr/bin/tini", "--", "/usr/local/bin/init.sh", *command])  # nosec B606


def run_private(command: list[str], environment: dict[str, str]) -> int:
    """Map the owned child's IDs directly with the parent's SETUID/SETGID rights.

    No subordinate-ID helpers or delegation files are needed. The application
    gets capabilities only in its child user namespace and inherits locked
    read-only mounts. Keep the unshare process unreaped until mapping finishes,
    so its PID cannot be reused while addressing its proc mapping files.
    """
    mappings = identity_maps()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    process = None
    try:
        parent.settimeout(30)
        process = subprocess.Popen(  # nosec B603
            private_command(child.fileno()), env=environment,
            pass_fds=(child.fileno(),), close_fds=True)
        child.close()
        if parent.recv(16) != b"user-ready":
            raise RuntimeError("Private worker exited before user namespace mapping")
        for kind, mapping in mappings.items():
            Path(f"/proc/{process.pid}/{kind}_map").write_text(mapping, encoding="ascii")
        parent.sendall(json.dumps(command).encode("utf-8"))
        parent.close()
        return process.wait()
    finally:
        child.close()
        parent.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def worker(control_fd: int) -> int:
    """Keep PID 1 alive so killing its owner also kills daemonized descendants."""
    with socket.socket(fileno=control_fd) as control:
        control.settimeout(60)
        export = json.loads(control.recv(1024 * 1024))
        root = prepare_root(export)
        netfd = os.open("/proc/self/ns/net", os.O_RDONLY)
        try:
            enter_root(root)
            os.chdir("/workspace")
            control.sendmsg([b"view-ready"], [
                (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [netfd]))])
        finally:
            os.close(netfd)
        if control.recv(16) != b"start":
            raise RuntimeError("Physical relay startup was cancelled")
    environment = {**os.environ, **export["environment"]}
    environment["HOME"] = "/home/pawflow"
    environment["USER"] = "pawflow"
    environment["PYTHONPATH"] = "/opt/pawflow:/workspace/.pylib"
    # The image entrypoint retains its UID/HOME initialization inside the
    # restricted user namespace. Its private PID 1 reaps daemonized children.
    return run_private(export["command"], environment)


def namespace_command(control_fd: int) -> list[str]:
    return [
        "unshare", "--mount", "--pid", "--fork", "--kill-child=SIGKILL",
        "--ipc", "--uts", "--net",
        sys.executable, "-I", "-u", SCRIPT, "--worker-fd", str(control_fd),
    ]


def _receive_namespace(control) -> int:
    data, ancillary, flags, _ = control.recvmsg(64, socket.CMSG_SPACE(array.array("i").itemsize))
    descriptors = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            received = array.array("i")
            received.frombytes(payload[:len(payload) - len(payload) % received.itemsize])
            descriptors.extend(received)
    if data != b"view-ready" or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or len(descriptors) != 1:
        for descriptor in descriptors:
            os.close(descriptor)
        raise RuntimeError("Logical worker failed to provide its private network namespace")
    return descriptors[0]


def _stop_processes(processes):
    for process in reversed(processes):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for process in processes:
        process.wait(timeout=10)


def supervise(exports: list[dict], stop: threading.Event) -> int:
    """Launch a fixed set and terminate it together on any owned process exit."""
    if not isinstance(exports, list) or not exports:
        raise ValueError("At least one logical relay is required")
    identities = [export["relay_id"] for export in exports]
    if len(set(identities)) != len(identities):
        raise ValueError("Logical relay identities must be distinct")
    processes, controls = [], []
    try:
        for export in exports:
            parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            controls.append(parent)
            parent.settimeout(30)
            try:
                process = subprocess.Popen(  # nosec B603
                    namespace_command(child.fileno()), pass_fds=(child.fileno(),),
                    close_fds=True, start_new_session=True)
                processes.append(process)
            finally:
                child.close()
            parent.sendall(json.dumps(export).encode("utf-8"))
        for control in controls:
            netfd = _receive_namespace(control)
            ready_read, ready_write = os.pipe()
            try:
                # Resolve only through the supervisor image's trusted PATH;
                # per-workspace environments are applied in private workers.
                process = subprocess.Popen(  # nosec B603 B607
                    ["slirp4netns", "--configure", "--disable-host-loopback",
                     "--netns-type=path", f"--ready-fd={ready_write}",
                     f"/proc/self/fd/{netfd}", "tap0"],
                    pass_fds=(netfd, ready_write), close_fds=True, start_new_session=True)
                processes.append(process)
                os.close(ready_write)
                ready_write = -1
                ready, _, _ = select.select([ready_read], [], [], 15)
                if not ready or os.read(ready_read, 1) != b"1" or process.poll() is not None:
                    raise RuntimeError("Logical relay network did not become ready")
            finally:
                os.close(netfd)
                os.close(ready_read)
                if ready_write >= 0:
                    os.close(ready_write)
        for control in controls:
            control.sendall(b"start")
            control.close()
        controls.clear()
        while not stop.is_set():
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A logical worker or network helper exited; stopping physical relay")
            stop.wait(0.2)
        return 0
    finally:
        for control in controls:
            control.close()
        _stop_processes(processes)


def require_tun() -> None:
    """Fail with the remedy when the Docker host kernel has no tun driver."""
    try:
        os.close(os.open("/dev/net/tun", os.O_RDWR))
    except OSError as exc:
        raise RuntimeError(
            f"Logical relay networking needs the tun kernel module on the Docker host "
            f"(/dev/net/tun: {exc.strerror}). Load it with 'sudo modprobe tun', "
            f"or on Windows/WSL with 'wsl -u root -- modprobe tun'.") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker-fd", type=int)
    mode.add_argument("--private-fd", type=int)
    mode.add_argument("--config-file")
    args = parser.parse_args()
    if args.worker_fd is not None:
        return worker(args.worker_fd)
    if args.private_fd is not None:
        private_init(args.private_fd)
        return 0
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: stop.set())
    if args.config_file:
        with open(args.config_file, encoding="utf-8") as handle:
            config = json.load(handle)
    else:
        config = json.load(sys.stdin)
    Path(ROOTFS).mkdir(parents=True, exist_ok=True)
    require_tun()
    return supervise(config["exports"], stop)


if __name__ == "__main__":
    raise SystemExit(main())
