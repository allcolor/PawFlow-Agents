"""The capture logs the interactive proxies append to stay bounded.

Both proxies are started by a shell that appends their stderr to a file. For
claude-code-interactive that file lives on the container's 512 MB /tmp tmpfs,
shared with every tool call, so an unbounded log does not merely waste space --
it eventually breaks the tool calls that need to write there.
"""

from pathlib import Path

from tools.ag_observer_proxy import (
    _truncate_log_if_oversized as ag_truncate,
)
from tools.cc_interactive_common import (
    PROXY_LOG_FILE,
    _truncate_log_if_oversized as cci_truncate,
    start_log_guard,
)


def test_a_log_under_the_threshold_is_left_exactly_as_it_was(tmp_path):
    log = tmp_path / "proxy.log"
    log.write_text("a line worth keeping\n", encoding="utf-8")

    assert cci_truncate(str(log), 1024) is False
    assert log.read_text(encoding="utf-8") == "a line worth keeping\n"


def test_a_log_past_the_threshold_is_zeroed(tmp_path):
    log = tmp_path / "proxy.log"
    log.write_text("x" * 5000, encoding="utf-8")

    assert cci_truncate(str(log), 1024) is True
    assert log.stat().st_size == 0


def test_a_log_exactly_at_the_threshold_is_not_touched(tmp_path):
    log = tmp_path / "proxy.log"
    log.write_text("x" * 1024, encoding="utf-8")

    assert cci_truncate(str(log), 1024) is False
    assert log.stat().st_size == 1024


def test_the_writer_keeps_writing_from_offset_zero_after_a_truncate(tmp_path):
    """O_APPEND is why truncating under the live writer is safe."""
    log = tmp_path / "proxy.log"
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("y" * 5000)
        handle.flush()

        assert cci_truncate(str(log), 1024) is True

        handle.write("after\n")
        handle.flush()

    # No sparse hole: the file holds the new bytes and nothing else.
    assert log.read_text(encoding="utf-8") == "after\n"


def test_a_missing_or_disabled_path_is_a_quiet_no_op(tmp_path):
    assert cci_truncate("", 1024) is False
    assert cci_truncate(str(tmp_path / "absent.log"), 1024) is False
    # A non-positive budget disables the guard rather than truncating always.
    present = tmp_path / "present.log"
    present.write_text("kept", encoding="utf-8")
    assert cci_truncate(str(present), 0) is False
    assert present.read_text(encoding="utf-8") == "kept"


def test_the_guard_thread_truncates_on_its_very_first_pass(tmp_path):
    """Startup counts as a check: a container restarted onto an already huge
    log must not wait a full interval before reclaiming the space."""
    log = tmp_path / "proxy.log"
    log.write_text("z" * 5000, encoding="utf-8")

    thread = start_log_guard(str(log), max_bytes=1024, interval=3600)
    thread.join(timeout=5)  # returns immediately: the thread loops forever

    deadline = 50
    while log.stat().st_size and deadline:
        deadline -= 1
        import time
        time.sleep(0.02)
    assert log.stat().st_size == 0
    assert thread.daemon is True


def test_the_antigravity_guard_behaves_the_same(tmp_path):
    log = tmp_path / "proxy.stderr.log"
    log.write_text("x" * 5000, encoding="utf-8")
    assert ag_truncate(str(log), 1024) is True
    assert log.stat().st_size == 0

    keep = tmp_path / "small.stderr.log"
    keep.write_text("small", encoding="utf-8")
    assert ag_truncate(str(keep), 1024) is False
    assert keep.read_text(encoding="utf-8") == "small"


def test_the_antigravity_observer_jsonl_is_never_the_guarded_file():
    """The pool reads that jsonl back for the ``proxy_start`` event to decide a
    session is usable. Zeroing it would make a live observer look dead."""
    src = Path("tools/ag_observer_proxy.py").read_text(encoding="utf-8")
    guard = src[src.index("def _start_stderr_log_guard"):src.index("def main(")]
    assert "STDERR_LOG_FILE" in guard
    assert "LOG_FILE" not in guard.replace("STDERR_LOG_FILE", "")


def test_the_antigravity_guard_stays_off_when_no_capture_file_was_named(monkeypatch):
    import tools.ag_observer_proxy as proxy

    started = []
    monkeypatch.setattr(proxy, "STDERR_LOG_FILE", "")
    monkeypatch.setattr(proxy.threading, "Thread",
                        lambda *a, **k: started.append(k) or _NeverStarted())
    proxy._start_stderr_log_guard()
    assert started == []


class _NeverStarted:
    def start(self):  # pragma: no cover - reaching this is the failure
        raise AssertionError("guard thread started without a capture file")


def test_the_proxy_and_the_shell_redirect_name_the_same_file():
    """One literal, both sides: the redirect that creates the file and the env
    var that tells the proxy which file to guard."""
    src = Path("core/_cci_pool_spawn.py").read_text(encoding="utf-8")
    assert 'CCI_PROXY_LOG = "/tmp/cci_proxy.log"' in src
    assert 'f"PAWFLOW_CCI_PROXY_LOG={CCI_PROXY_LOG}"' in src
    assert "shlex.quote(CCI_PROXY_LOG)" in src
    # The old hard-coded redirect is gone, so the two cannot drift apart.
    assert ">> /tmp/cci_proxy.log" not in src
    assert PROXY_LOG_FILE == "/tmp/cci_proxy.log"


def test_the_cci_proxy_starts_its_guard_before_serving():
    src = Path("tools/cc_interactive_proxy.py").read_text(encoding="utf-8")
    main_body = src[src.index("def main():"):]
    assert "start_log_guard()" in main_body
    assert main_body.index("start_log_guard()") < main_body.index("lsock.accept()")


def test_the_antigravity_pool_hands_the_proxy_its_capture_path():
    src = Path("core/antigravity_observer_pool.py").read_text(encoding="utf-8")
    assert 'f"PAWFLOW_AG_OBSERVER_STDERR={container_stderr}"' in src
