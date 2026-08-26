"""Policy gating service, scripts and pure policy logic (plan WP2)."""

import contextlib
import io
import time
from types import SimpleNamespace

import pytest

from core import ServiceError, ServiceFactory
from core.gating_policy import (
    HARD_CONFIRM_TOOLS,
    INTERNAL_UNGATED_TOOLS,
    build_envelope,
    classify_call,
    compose_final,
    evaluator_result,
    is_mutating_call,
    merge_decisions,
    normalize_decision,
    parse_llm_decision,
    redact_arguments,
)
from core.gating_script_runner import run_script, run_scripts
from services.gating_service import GatingService


# ── pure policy ──────────────────────────────────────────────────────

def test_decision_merge_is_conservative_and_never_allows_by_default():
    r = lambda d: evaluator_result(d, source="script")  # noqa: E731
    assert merge_decisions([r("allow"), r("deny"), r("ask")]) == "deny"
    assert merge_decisions([r("allow"), r("ask")]) == "ask"
    assert merge_decisions([r("allow"), r("abstain")]) == "allow"
    assert merge_decisions([r("abstain")]) == "ask"
    assert merge_decisions([], failure_decision="deny") == "deny"
    assert merge_decisions([], failure_decision="allow") == "ask"
    assert compose_final("allow", "deny") == "deny"
    assert compose_final("ask", "allow") == "ask"
    assert compose_final(None, "allow") == "allow"
    assert compose_final(None, None) == "ask"
    assert normalize_decision(" Allow ") == "allow" and normalize_decision("maybe") == ""
    with pytest.raises(ValueError):
        evaluator_result("maybe", source="script")


def test_redaction_hides_secret_values_and_credential_keys():
    redacted = redact_arguments({
        "command": "curl -H 'Authorization: Bearer sk-live-123456' https://x",
        "headers": {"Authorization": "Bearer sk-live-123456", "Accept": "json"},
        "nested": [{"api_key": "k"}, "plain sk-live-123456"],
        "long": "x" * 5000,
    }, secret_values=["sk-live-123456"])
    assert "sk-live-123456" not in str(redacted)
    assert redacted["headers"]["Authorization"] == "<redacted>"
    assert redacted["headers"]["Accept"] == "json"
    assert redacted["nested"][0]["api_key"] == "<redacted>"
    assert redacted["nested"][1] == "plain <secret>"
    assert redacted["long"].startswith("x" * 2000) and "more chars" in redacted["long"]


def test_classification_keeps_structural_guards_out_of_the_gates_hands():
    assert classify_call("get_tool_schema", {})[0] == "internal_ungated"
    assert "ask_user" in INTERNAL_UNGATED_TOOLS
    assert classify_call("bash", {"command": "ls"}, tool_permission="deny")[0] == "hard_deny"
    assert classify_call("bash", {"command": "ls"}, permission_mode="read_only")[0] == "hard_deny"
    assert classify_call("read", {"path": "a"}, permission_mode="read_only")[0] == "ordinary"
    assert classify_call("create_tool", {})[0] == "hard_confirm"
    assert "manage_package" in HARD_CONFIRM_TOOLS
    assert classify_call("bash", {"command": "rm -rf /"})[0] == "hard_confirm"
    assert classify_call("bash", {"command": "git status"})[0] == "ordinary"
    assert classify_call("x", {"action": "a2a_publication_configure"})[0] == "hard_confirm"
    assert is_mutating_call("read", {"path": "a"}) is False
    assert is_mutating_call("bash", {"command": "ls"}) is True


def test_llm_decision_parsing_is_provider_agnostic_and_strict():
    assert parse_llm_decision('Sure!\n```json\n{"decision": "ALLOW", "reason": "<b>ok</b>"}\n```')["decision"] == "allow"
    assert parse_llm_decision('{"decision": "deny", "reason": "x"}')["reason"] == "x"
    assert parse_llm_decision('{"decision": "allow", "execute": true}') is None
    assert parse_llm_decision('{"decision": "maybe"}') is None
    assert parse_llm_decision("no json here") is None
    assert parse_llm_decision('{"decision": "ask", "matched_directive_ids": "x"}') is None
    parsed = parse_llm_decision('{"decision": "allow", "reason": "<script>x</script>fine"}')
    assert parsed["reason"] == "xfine"


def test_envelope_is_redacted_and_carries_authority_revision():
    doc = {"context_id": "ctx", "revision": 2, "directives": [
        {"id": "d1", "content": "fix it"}, {"id": "d2", "content": "no push"}]}
    env = build_envelope(user_id="u", conversation_id="c", agent_name="a", turn_id="t",
                         tool_name="bash", arguments={"command": "echo TOKEN1234"},
                         authorization=doc, secret_values=["TOKEN1234"])
    assert env["identity"]["authorization_revision"] == 2
    assert env["authority"]["followups"] == ["no push"]
    assert env["tool_call"]["arguments"]["command"] == "echo <secret>"
    assert env["tool_call"]["mutating"] is True
    assert len(env["tool_call"]["arguments_sha256"]) == 64
    assert build_envelope(user_id="u", conversation_id="c", agent_name="a", turn_id="t",
                          tool_name="read", arguments={})["authority_missing"] is True


# ── scripts ──────────────────────────────────────────────────────────

def _local_executor(code):
    """Stand-in for the relay sandbox: run the wrapped script locally."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {"__builtins__": __builtins__})  # noqa: S102 - test double
    return out.getvalue()


def _env(tool="bash", command="git push"):
    return build_envelope(user_id="u", conversation_id="c", agent_name="a", turn_id="t",
                          tool_name=tool, arguments={"command": command})


def test_script_defaults_to_abstain_and_cannot_rewrite_the_call():
    script = {"source": "x = 1", "tools": [], "fail_decision": "deny"}
    result = run_script("noop", script, _env(), executor=_local_executor)
    assert result["decision"] == "abstain" and result["source"] == "script"
    deny = {"source": (
        "def evaluate(event):\n"
        "    if 'push' in event['tool_call']['arguments'].get('command', ''):\n"
        "        return {'decision': 'deny', 'reason': 'push not requested', 'rule_id': 'no-push'}\n"
        "    return {'decision': 'abstain'}\n")}
    result = run_script("nopush", deny, _env(), executor=_local_executor)
    assert (result["decision"], result["rule_id"]) == ("deny", "no-push")
    assert run_script("nopush", deny, _env(command="git status"),
                      executor=_local_executor)["decision"] == "abstain"


def test_script_failures_map_to_fail_decision_never_allow():
    assert run_script("missing", None, _env())["decision"] == "ask"
    bad = {"source": "def evaluate(event):\n    return {'decision': 'yes'}\n", "fail_decision": "deny"}
    assert run_script("bad", bad, _env(), executor=_local_executor)["decision"] == "deny"
    crash = {"source": "def evaluate(event):\n    raise RuntimeError('boom')\n"}
    assert run_script("crash", crash, _env(), executor=_local_executor)["decision"] == "ask"
    slow = {"source": "def evaluate(event):\n    return {'decision': 'allow'}\n", "fail_decision": "deny"}

    def _hang(code):
        time.sleep(3)
        return _local_executor(code)
    result = run_script("slow", slow, _env(), timeout_seconds=1, executor=_hang)
    assert result["decision"] == "deny" and result["metadata"]["error"] == "timeout"
    filtered = {"source": "def evaluate(event):\n    return {'decision': 'deny'}\n", "tools": ["delete"]}
    assert run_script("f", filtered, _env(), executor=_local_executor)["decision"] == "abstain"


def test_scripts_short_circuit_on_restrictive_decisions():
    deny = {"source": "def evaluate(event):\n    return {'decision': 'deny'}\n"}
    allow = {"source": "def evaluate(event):\n    return {'decision': 'allow'}\n"}
    results = run_scripts([("a", deny), ("b", allow)], _env(), executor=_local_executor)
    assert [r["decision"] for r in results] == ["deny"]
    results = run_scripts([("b", allow), ("a", deny)], _env(), executor=_local_executor)
    assert [r["decision"] for r in results] == ["allow", "deny"]


# ── service ──────────────────────────────────────────────────────────

def test_service_registration_and_config_validation():
    assert ServiceFactory.get("gating") is GatingService
    from core.paths import REPO_TYPES
    from core.resource_store import VALID_TYPES
    assert "gating_script" in VALID_TYPES and "gating_scripts" in REPO_TYPES
    with pytest.raises(ServiceError, match="prompt, scripts"):
        GatingService.validate_config({})
    with pytest.raises(ServiceError, match="llm_service is required"):
        GatingService.validate_config({"prompt": "x"})
    with pytest.raises(ServiceError, match="never allow"):
        GatingService.validate_config({"scripts": ["s"], "failure_decision": "allow"})
    with pytest.raises(ServiceError, match="llm_scope"):
        GatingService.validate_config({"scripts": ["s"], "llm_scope": "sometimes"})
    GatingService.validate_config({"scripts": ["s"]})
    GatingService.validate_config({"prompt": "p", "llm_service": "llm"})


def _scripted_service(monkeypatch, scripts, **config):
    svc = GatingService(dict({"scripts": list(scripts)}, **config))
    monkeypatch.setattr("services.gating_service.resolve_scripts",
                        lambda names, user_id, conversation_id="": [
                            (n, scripts.get(n)) for n in names])
    return svc


def test_script_only_gate_allows_denies_and_fails_closed(monkeypatch):
    scripts = {
        "allow_reads": {"source": "def evaluate(event):\n    return {'decision': 'allow'}\n"},
        "no_push": {"source": (
            "def evaluate(event):\n"
            "    return {'decision': 'deny', 'reason': 'push'} if 'push' in "
            "event['tool_call']['arguments'].get('command', '') else {'decision': 'abstain'}\n")},
    }
    svc = _scripted_service(monkeypatch, scripts)
    ok = svc.evaluate(_env(command="git status"), script_executor=_local_executor)
    assert ok["decision"] == "allow" and len(ok["evaluators"]) == 2
    denied = svc.evaluate(_env(command="git push"), script_executor=_local_executor)
    assert denied["decision"] == "deny" and "push" in denied["reason"]
    silent = _scripted_service(monkeypatch, {"quiet": {"source": "x = 1"}}, failure_decision="deny")
    assert silent.evaluate(_env(), script_executor=_local_executor)["decision"] == "deny"


def _llm_service(monkeypatch, content, *, provider="openai", calls=None, **config):
    svc = GatingService(dict({"prompt": "Permit only what the user asked.",
                             "llm_service": "gate_llm"}, **config))
    calls = calls if calls is not None else []

    def _complete(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(content=content, model="m")
    fake = SimpleNamespace(complete=_complete, config={"provider": provider})
    monkeypatch.setattr(svc, "resolve_llm_service", lambda user_id="", conversation_id="": (fake, "gate_llm"))
    monkeypatch.setattr("services.gating_service.resolve_scripts",
                        lambda names, user_id, conversation_id="": [])
    return svc, calls


def _authorized_env(command="git push"):
    doc = {"context_id": "ctx", "revision": 1,
           "directives": [{"id": "d1", "content": "commit and push, no release"}]}
    return build_envelope(user_id="u", conversation_id="c", agent_name="a", turn_id="t",
                          tool_name="bash", arguments={"command": command}, authorization=doc)


def test_llm_gate_allows_without_tools_and_sees_authority(monkeypatch):
    svc, calls = _llm_service(monkeypatch, 'Thinking...\n{"decision": "allow", "reason": "requested", "matched_directive_ids": ["d1"]}')
    result = svc.evaluate(_authorized_env(), user_id="u", conversation_id="c")
    assert result["decision"] == "allow"
    assert result["evaluators"][0]["matched_directive_ids"] == ["d1"]
    messages, kwargs = calls[0]
    assert kwargs["tools"] is None and kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 0
    assert kwargs["call_ephemeral_stream"] is True
    assert "commit and push, no release" in messages[0].content
    assert "untrusted data" in messages[0].content


def test_llm_gate_limits_are_unlimited_by_default_and_positive_values_uncapped(
        monkeypatch):
    result_timeouts = []

    class ImmediateFuture:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):
            result_timeouts.append(timeout)
            return self.value

    class ImmediatePool:
        def __init__(self, max_workers):
            assert max_workers == 1

        def submit(self, fn):
            return ImmediateFuture(fn())

        def shutdown(self, wait=False):
            assert wait is False

    monkeypatch.setattr(
        "services.gating_service.ThreadPoolExecutor", ImmediatePool)

    default, default_calls = _llm_service(
        monkeypatch, '{"decision": "allow"}')
    assert default.evaluate(_authorized_env())["decision"] == "allow"
    assert default_calls[0][1]["max_tokens"] == 0
    assert result_timeouts == [None]

    explicit, explicit_calls = _llm_service(
        monkeypatch, '{"decision": "allow"}',
        max_tokens=2048, timeout_seconds=321)
    assert explicit.evaluate(_authorized_env())["decision"] == "allow"
    assert explicit_calls[0][1]["max_tokens"] == 2048
    assert result_timeouts == [None, 321]

    schema = default.get_parameter_schema()
    assert schema["max_tokens"]["default"] == 0
    assert schema["timeout_seconds"]["default"] == 0


def test_llm_gate_failure_modes_never_allow(monkeypatch):
    malformed, _ = _llm_service(monkeypatch, "I would allow this.")
    assert malformed.evaluate(_authorized_env())["decision"] == "ask"
    denying, _ = _llm_service(monkeypatch, '{"decision": "deny", "reason": "release"}',
                              failure_decision="deny")
    assert denying.evaluate(_authorized_env())["decision"] == "deny"
    cli, calls = _llm_service(monkeypatch, '{"decision": "allow"}', provider="claude-code-interactive")
    result = cli.evaluate(_authorized_env())
    assert result["decision"] == "ask" and calls == []
    assert "API-backed" in result["reason"]
    missing, _ = _llm_service(monkeypatch, '{"decision": "allow"}')
    no_authority = build_envelope(user_id="u", conversation_id="c", agent_name="a",
                                  turn_id="t", tool_name="bash", arguments={"command": "ls"})
    assert missing.evaluate(no_authority)["decision"] == "ask"

    def _boom(*_a, **_k):
        raise RuntimeError("down")
    broken, _ = _llm_service(monkeypatch, "")
    monkeypatch.setattr(broken, "resolve_llm_service",
                        lambda user_id="", conversation_id="": (SimpleNamespace(complete=_boom, config={}), "gate_llm"))
    assert broken.evaluate(_authorized_env())["decision"] == "ask"


def test_llm_scope_keeps_reads_off_the_llm_and_scripts_gate_the_llm(monkeypatch):
    svc, calls = _llm_service(monkeypatch, '{"decision": "allow"}')
    read_env = build_envelope(user_id="u", conversation_id="c", agent_name="a", turn_id="t",
                              tool_name="read", arguments={"path": "x"},
                              authorization={"context_id": "ctx", "revision": 1,
                                             "directives": [{"id": "d", "content": "review"}]})
    result = svc.evaluate(read_env)
    assert calls == [] and result["decision"] == "ask"  # no evaluator decided: fail closed
    all_scope, calls2 = _llm_service(monkeypatch, '{"decision": "allow"}', llm_scope="all")
    assert all_scope.evaluate(read_env)["decision"] == "allow" and len(calls2) == 1
    # A script deny stops the LLM from even running.
    combined, calls3 = _llm_service(monkeypatch, '{"decision": "allow"}', scripts=["deny_all"])
    monkeypatch.setattr("services.gating_service.resolve_scripts",
                        lambda names, user_id, conversation_id="": [
                            ("deny_all", {"source": "def evaluate(event):\n    return {'decision': 'deny'}\n"})])
    result = combined.evaluate(_authorized_env(), script_executor=_local_executor)
    assert result["decision"] == "deny" and calls3 == []
    # A script allow alone is not enough when a prompt is configured.
    both, calls4 = _llm_service(monkeypatch, "garbage", scripts=["allow_all"])
    monkeypatch.setattr("services.gating_service.resolve_scripts",
                        lambda names, user_id, conversation_id="": [
                            ("allow_all", {"source": "def evaluate(event):\n    return {'decision': 'allow'}\n"})])
    assert both.evaluate(_authorized_env(), script_executor=_local_executor)["decision"] == "ask"
