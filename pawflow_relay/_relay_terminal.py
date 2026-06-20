"""PTY terminal sessions for the relay worker.

Extracted from `pawflow_relay/worker.py`'s `_ws_connect` closure. The
output path is inverted: instead of writing frames straight to the WS
socket under a lock, `TerminalManager` calls an injected `send_frame`
callback with the already-encoded frame bytes. The worker passes a
callback that takes the send lock and writes to the live socket; tests
pass a capturing callback. This keeps the PTY logic free of any socket
dependency and unit-testable.

The frame shapes are unchanged from the original inline implementation:
  {"type": "terminal_data",  "session_id": <sid>, "data": <b64>}
  {"type": "terminal_exit",  "session_id": <sid>}

Unix-only (uses os.forkpty / fcntl / termios). Those modules are
imported lazily inside the methods so this file stays importable on a
Windows host (dual-context relay: the module is on the path both on the
host and inside the relay container).
"""
import base64
import json
import logging
import os
import threading

_log = logging.getLogger(__name__)


class TerminalManager:
    """Owns the PTY sessions for one WS connection.

    A fresh instance is created per (re)connection so sessions never
    outlive the socket they stream to — mirroring the per-connection
    `_terminal_sessions = {}` reset in the worker's reconnect loop.
    """

    def __init__(self, root_dir, send_frame):
        # send_frame: callable(bytes) -> None. MUST be safe to call from
        # the per-session reader thread (the worker's impl takes the
        # shared send lock before writing to the socket).
        self._root_dir = root_dir
        self._send_frame = send_frame
        self.sessions = {}  # session_id -> {master_fd, pid, reader, shell}

    def open(self, cols=80, rows=24, shell=None):
        """Fork a PTY running a shell; stream its output via send_frame.

        Returns the new session id.
        """
        import uuid as _uuid
        import fcntl
        import termios
        import array

        sid = _uuid.uuid4().hex[:12]
        _shell = shell or os.environ.get("SHELL", "/bin/bash")

        pid, master_fd = os.forkpty()
        if pid == 0:
            os.chdir(self._root_dir)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLUMNS"] = str(cols)
            env["LINES"] = str(rows)
            os.execvpe(_shell, [_shell], env)  # nosec B606

        try:
            winsize = array.array("H", [rows, cols, 0, 0])
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            _log.debug("Ignored exception", exc_info=True)

        reader = threading.Thread(
            target=self._pty_reader, args=(master_fd, sid),
            daemon=True, name=f"pty-reader-{sid}")
        reader.start()

        self.sessions[sid] = {
            "master_fd": master_fd,
            "pid": pid,
            "reader": reader,
            "shell": _shell,
        }
        _log.debug("Terminal opened: %s (shell=%s)", sid, _shell)
        return sid

    def _pty_reader(self, fd, sid):
        try:
            while True:
                data = os.read(fd, 4096)
                if not data:
                    break
                frame = json.dumps({
                    "type": "terminal_data",
                    "session_id": sid,
                    "data": base64.b64encode(data).decode("ascii"),
                }).encode("utf-8")
                self._send_frame(frame)
        except OSError:
            pass
        finally:
            try:
                frame = json.dumps({
                    "type": "terminal_exit",
                    "session_id": sid,
                }).encode("utf-8")
                self._send_frame(frame)
            except Exception:
                _log.debug("Ignored exception", exc_info=True)

    def write(self, session_id, data_b64):
        """Write base64-encoded bytes to the session's PTY master.

        Returns (ok, error). ok=False with a message when the session is
        unknown or the write fails.
        """
        sess = self.sessions.get(session_id)
        if not sess:
            return False, f"Terminal session not found: {session_id}"
        try:
            os.write(sess["master_fd"], base64.b64decode(data_b64 or ""))
            return True, ""
        except OSError as e:
            return False, str(e)

    def resize(self, session_id, cols=80, rows=24):
        """Resize the session's PTY. Returns (ok, error)."""
        import fcntl
        import termios
        import array

        sess = self.sessions.get(session_id)
        if not sess:
            return False, f"Terminal session not found: {session_id}"
        try:
            winsize = array.array("H", [rows, cols, 0, 0])
            fcntl.ioctl(sess["master_fd"], termios.TIOCSWINSZ, winsize)
            return True, ""
        except Exception as e:
            return False, str(e)

    def close(self, session_id):
        """Close one session (fd + kill child). Returns True if it existed."""
        sess = self.sessions.pop(session_id, None)
        if not sess:
            return False
        try:
            os.close(sess["master_fd"])
        except OSError:
            pass
        try:
            os.kill(sess["pid"], 9)
            os.waitpid(sess["pid"], os.WNOHANG)
        except (OSError, ChildProcessError):
            pass
        _log.debug("Terminal closed: %s", session_id)
        return True

    def close_all(self):
        for sid in list(self.sessions):
            self.close(sid)

    def list(self):
        """List open sessions as [{session_id, shell}]."""
        return [
            {"session_id": sid, "shell": s["shell"]}
            for sid, s in self.sessions.items()
        ]
