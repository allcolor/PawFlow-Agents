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
def test_the_login_container_is_labelled_so_the_reaper_can_find_it(
        monkeypatch, action, expected):
    """The label is the only thing that makes a container reapable.

    The name is checked too, but only because operators grep for it -- the
    reaper never reads it. A login spawned without the label would outlive
    every shutdown and every boot with nothing to notice.
    """
    from core.docker_utils import PAWFLOW_SERVER_LABEL

    captured, _flowfile = _run_gemini_login(monkeypatch, action)
    docker_run = [a for a in (captured.get("argv") or [])
                  if "run" in a and "--detach" in a][0]
    name = docker_run[docker_run.index("--name") + 1]
    assert name.startswith(expected)

    labels = [docker_run[i + 1] for i, a in enumerate(docker_run)
              if a == "--label"]
    assert any(lbl.startswith(f"{PAWFLOW_SERVER_LABEL}=") for lbl in labels), (
        f"{expected}* is spawned without the server-id label, so no reaper "
        f"at either end of the process can ever remove it")


# ── The reaper itself, at both ends of the process ──────────────────────────

class _Rm:
    """A `docker rm` result: it echoes what it removed, errors go to stderr."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _reaper_with(monkeypatch, ps_ids, rm_result):
    import core.docker_utils as du

    monkeypatch.setattr(du, "get_server_id", lambda: "server-aaaa")
    monkeypatch.setattr(du, "docker_cmd", lambda: ["docker"])
    seen = {"rm": [], "filters": []}

    def fake_run(cmd, **kwargs):
        if "rm" in cmd:
            seen["rm"].extend(cmd[cmd.index("-f") + 1:])
            return rm_result
        seen["filters"].extend(a for a in cmd if str(a).startswith(("label=", "name=")))
        return _Rm(stdout=ps_ids)

    monkeypatch.setattr(du.subprocess, "run", fake_run)
    return du, seen


def test_the_reaper_selects_on_the_label_and_nothing_else(monkeypatch):
    """One call, used by boot and by shutdown alike.

    Selecting on NAME was tried and removed. Most families are named
    `pf-cc-pool-*` or `pawflow-relay-srv-*` and carry no server id, so on a
    shared Docker daemon the name match also selects another LIVE PawFlow
    server's containers -- and an unlabelled container is not an old build of
    ours, it is somebody else's. The label is stamped at the spawn and is the
    only evidence of ownership there is.
    """
    du, seen = _reaper_with(
        monkeypatch, "labelled-1 labelled-2\n",
        _Rm(stdout="labelled-1\nlabelled-2\n"))

    assert du.reap_spawned_containers() == 2
    assert seen["rm"] == ["labelled-1", "labelled-2"]
    assert seen["filters"] == ["label=org.pawflow.server-id=server-aaaa"], (
        "the reaper asked Docker for something other than its own label")
    assert not any(f.startswith("name=") for f in seen["filters"])


def test_a_refused_removal_is_not_counted_as_one(monkeypatch):
    """Docker refuses with a non-zero exit and stderr, never an exception.

    Counting the request instead of the receipt is worse than a miscount: the
    reaper reports a clean sweep while the zombie is still up, still holding
    the credential slot the new process is about to hand out again.
    """
    du, seen = _reaper_with(
        monkeypatch, "gone stuck\n",
        _Rm(stdout="gone\n", stderr="Error response from daemon: stuck\n",
            returncode=1))

    assert du.reap_spawned_containers() == 1, "a refusal was counted as a kill"
    assert seen["rm"] == ["gone", "stuck"], "both were still attempted"


def test_nothing_to_reap_runs_no_removal(monkeypatch):
    du, seen = _reaper_with(monkeypatch, "\n", _Rm())
    assert du.reap_spawned_containers() == 0
    assert seen["rm"] == []


def test_the_reaper_survives_a_docker_that_is_not_there(monkeypatch):
    """Boot must not fail to start because the daemon is unreachable."""
    import core.docker_utils as du

    monkeypatch.setattr(du, "get_server_id", lambda: "server-aaaa")
    monkeypatch.setattr(du, "docker_cmd", lambda: ["docker"])

    def boom(*_a, **_k):
        raise OSError("no docker here")

    monkeypatch.setattr(du.subprocess, "run", boom)
    assert du.reap_spawned_containers() == 0
