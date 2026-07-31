"""Two regressions from the beta.56 container-label work.

Both were invisible to the tests that shipped with it, because those asserted
on the SOURCE TEXT of the files rather than running the code: a name being
mentioned somewhere in a module says nothing about whether it is bound in the
function that uses it, and a reap loop's blast radius cannot be read off a
grep. These execute the paths instead.
"""

import json
import types

import pytest

from core.docker_utils import LEGACY_REAP_FORMAT, legacy_reap_ids


# ── The Gemini / Antigravity login container ────────────────────────────────

def _run_gemini_login(monkeypatch, action):
    """Drive the login action far enough to run its background docker thread."""
    from tasks.ai.actions import _sf_k8

    captured = {}

    class _Thread:
        def __init__(self, target=None, **kwargs):
            captured["target"] = target

        def start(self):
            # Run it inline so an exception in it is visible to the test rather
            # than swallowed by a daemon thread's logger.
            captured["error"] = None
            try:
                captured["target"]()
            except BaseException as exc:  # pragma: no cover - the bug path
                captured["error"] = exc

    monkeypatch.setattr(_sf_k8.threading, "Thread", _Thread)

    def _fake_run(argv, *a, **k):
        captured.setdefault("argv", []).append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(_sf_k8, "_credential_provider_for_service",
                        lambda *a, **k: "gemini")
    monkeypatch.setattr(_sf_k8, "_docker_published_host", lambda: "127.0.0.1")
    monkeypatch.setattr(_sf_k8, "_wait_for_vnc_login_backend",
                        lambda *a, **k: True)
    # Minting a real capability token needs an initialized auth DB; this test
    # is about what docker is asked to start, not about the VNC route.
    monkeypatch.setattr("services.vnc_proxy.register_session",
                        lambda *a, **k: "tok")

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve_definition(self, *a, **k):
            return types.SimpleNamespace(service_id="svc", config={})

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)

    class _FlowFile:
        def __init__(self):
            self._content = b""

        def get_attribute(self, _name):
            return ""

        def set_content(self, data):
            self._content = data

        def payload(self):
            return json.loads(self._content or b"{}")

    flowfile = _FlowFile()
    helpers = tuple(lambda *a, **k: "" for _ in range(6))
    _sf_k8._handle_sf_k8(
        None, action, {"service_id": "svc", "conversation_id": "conv"},
        None, "user", flowfile, helpers)
    return captured, flowfile


@pytest.mark.parametrize("action", ["gemini_server_login", "agy_server_login"])
def test_the_login_thread_reaches_docker_run_instead_of_a_name_error(
        monkeypatch, action):
    """beta.56 added the label call to this function but not its import.

    The nested `_bg_setup_gemini` has its own import block; the identical
    imports in the Claude and Codex branches are local to *their* nested
    functions and never bound here. The NameError surfaced as a generic
    "Login failed", so both VNC logins simply stopped starting a container.
    """
    captured, _flowfile = _run_gemini_login(monkeypatch, action)

    assert captured.get("error") is None, (
        f"login background thread raised: {captured.get('error')!r}")
    runs = captured.get("argv") or []
    docker_run = [a for a in runs if "run" in a and "--detach" in a]
    assert docker_run, "the login never reached `docker run`"
    assert "--label" in docker_run[0], "the login container carries no label"


@pytest.mark.parametrize("action,expected", [
    ("gemini_server_login", "pawflow-gemini-login-"),
    ("agy_server_login", "pawflow-agy-login-"),
])
def test_the_login_container_name_is_the_one_shutdown_looks_for(
        monkeypatch, action, expected):
    """The reaper's legacy list must actually name these containers."""
    captured, _flowfile = _run_gemini_login(monkeypatch, action)
    docker_run = [a for a in (captured.get("argv") or [])
                  if "run" in a and "--detach" in a][0]
    name = docker_run[docker_run.index("--name") + 1]
    assert name.startswith(expected)

    from core.docker_utils import LEGACY_REAP_PREFIXES
    assert expected in LEGACY_REAP_PREFIXES, (
        f"{expected} is spawned but the reaper never looks for it")


# ── The shutdown reaper's second pass ───────────────────────────────────────

def test_another_servers_containers_are_never_reaped_by_name():
    """The prefixes carry no server id, so the name match is host-wide.

    A second PawFlow instance on the same Docker daemon owns containers with
    exactly these names. They survive pass 1 because their label is not ours --
    and must survive pass 2 too, or stopping one server kills another's agents.
    """
    ours = "server-aaaa"
    ps_output = "\n".join([
        "c1 server-bbbb",   # another live server
        "c2 server-aaaa",   # ours
        "c3",               # unlabelled: predates the label, safe to reap
    ])
    assert legacy_reap_ids(ps_output, ours) == ["c2", "c3"]


def test_an_unlabelled_legacy_container_is_still_reaped():
    assert legacy_reap_ids("abc123\n", "server-aaaa") == ["abc123"]


def test_docker_missing_label_placeholder_counts_as_unlabelled():
    assert legacy_reap_ids("abc123 <no value>\n", "server-aaaa") == ["abc123"]


def test_blank_and_ragged_output_is_survivable():
    assert legacy_reap_ids("", "s") == []
    assert legacy_reap_ids("\n\n  \n", "s") == []


def test_the_reaper_asks_docker_for_the_label_column():
    """Without the label in the output there is nothing to discriminate on."""
    from pathlib import Path
    src = Path("core/docker_utils.py").read_text(encoding="utf-8")
    assert "LEGACY_REAP_FORMAT" in src
    assert "legacy_reap_ids(" in src
    # The old blanket "every id matching this name" collection is gone.
    assert '"ps", "-a", "-q",\n' not in src
    assert "org.pawflow.server-id" in LEGACY_REAP_FORMAT


# ── The reaper itself, at both ends of the process ──────────────────────────

def test_the_reaper_removes_by_label_and_leaves_another_server_alone(
        monkeypatch):
    """One call, used by boot and by shutdown alike.

    Pass 1 asks Docker for this server's label and removes what it names.
    Pass 2 walks the legacy name prefixes and may only remove a container
    that is unlabelled or already ours -- a second PawFlow instance on the
    same daemon owns containers with exactly those names.
    """
    import core.docker_utils as du

    monkeypatch.setattr(du, "get_server_id", lambda: "server-aaaa")
    monkeypatch.setattr(du, "docker_cmd", lambda: ["docker"])
    removed = []

    class _R:
        def __init__(self, stdout=""):
            self.stdout = stdout
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        if "rm" in cmd:
            removed.extend(cmd[cmd.index("-f") + 1:])
            return _R()
        if "-q" in cmd:                      # pass 1: the label filter
            assert "label=org.pawflow.server-id=server-aaaa" in cmd
            return _R("labelled-1 labelled-2\n")
        if any(str(a).startswith("name=pf-cc-pool-") for a in cmd):
            return _R("mine server-aaaa\nnot-mine server-bbbb\nold\n")
        return _R("")

    monkeypatch.setattr(du.subprocess, "run", fake_run)

    assert du.reap_spawned_containers() == 4
    assert removed == ["labelled-1", "labelled-2", "mine", "old"]
    assert "not-mine" not in removed, "reaped another server's container"


def test_the_reaper_survives_a_docker_that_is_not_there(monkeypatch):
    """Boot must not fail to start because the daemon is unreachable."""
    import core.docker_utils as du

    monkeypatch.setattr(du, "get_server_id", lambda: "server-aaaa")
    monkeypatch.setattr(du, "docker_cmd", lambda: ["docker"])

    def boom(*_a, **_k):
        raise OSError("no docker here")

    monkeypatch.setattr(du.subprocess, "run", boom)
    assert du.reap_spawned_containers() == 0
