"""Managed MCP: the extended lifecycle hook (tools/cc_interactive_hook.py).

Covers the WP1 contract: bounded ``last_assistant_message`` on Stop, the
supported local transcript fallback (Claude Code and Codex rollout shapes),
the unchanged ``hook`` envelope, injected-versus-manual prompt detection, one
bounded delivery retry, and the client-aware Antigravity normalization.
"""
import importlib
import json

import pytest


@pytest.fixture()
def hook(monkeypatch):
    monkeypatch.delenv("PAWFLOW_CCI_INJECTED_PROMPTS", raising=False)
    monkeypatch.delenv("PAWFLOW_CCI_HOOK_CLIENT", raising=False)
    return importlib.import_module("tools.cc_interactive_hook")


def _claude_transcript(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


class TestStopFinal:
    def test_hook_field_wins_and_is_marked(self, hook):
        out = hook._compact_input({
            "hook_event_name": "Stop",
            "last_assistant_message": "final answer",
            "model": "claude-opus-4",
            "stop_hook_active": False,
        })
        assert out["last_assistant_message"] == "final answer"
        assert out["final_source"] == "hook_field"
        assert out["model"] == "claude-opus-4"
        assert "transcript_path" not in out

    def test_empty_field_falls_back_to_claude_transcript(self, hook, tmp_path):
        transcript = tmp_path / "session.jsonl"
        _claude_transcript(transcript, [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "first"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "r"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "the real final"}]}},
        ])
        out = hook._compact_input({
            "hook_event_name": "Stop", "last_assistant_message": "",
            "transcript_path": str(transcript)})
        assert out["last_assistant_message"] == "the real final"
        assert out["final_source"] == "transcript"

    def test_codex_rollout_shape_is_supported(self, hook, tmp_path):
        rollout = tmp_path / "rollout-1.jsonl"
        _claude_transcript(rollout, [
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "q"}]}},
            {"type": "response_item", "payload": {
                "type": "function_call", "name": "shell", "arguments": "{}"}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "codex final"}]}},
            {"type": "event_msg", "payload": {"type": "token_count"}},
        ])
        assert hook._last_transcript_message(str(rollout), "assistant") == "codex final"

    def test_missing_transcript_yields_empty_and_unsourced(self, hook, tmp_path):
        out = hook._compact_input({
            "hook_event_name": "Stop", "last_assistant_message": "",
            "transcript_path": str(tmp_path / "nope.jsonl")})
        assert out["last_assistant_message"] == ""
        assert out["final_source"] == ""

    def test_final_text_is_bounded(self, hook):
        huge = "x" * (hook._MAX_FINAL_CHARS + 10)
        out = hook._compact_input({
            "hook_event_name": "Stop", "last_assistant_message": huge})
        assert len(out["last_assistant_message"]) <= hook._MAX_FINAL_CHARS
        assert out["last_assistant_message"].startswith("x" * 100)
        assert "truncated by the hook" in out["last_assistant_message"]

    def test_transcript_tail_read_skips_partial_first_row(self, hook, tmp_path, monkeypatch):
        transcript = tmp_path / "big.jsonl"
        rows = [{"role": "assistant", "content": f"old {i}"} for i in range(50)]
        rows.append({"role": "assistant", "content": "newest"})
        _claude_transcript(transcript, rows)
        monkeypatch.setattr(hook, "_TRANSCRIPT_TAIL_BYTES", 200)
        assert hook._last_transcript_message(str(transcript), "assistant") == "newest"


class TestPromptEvents:
    def test_user_prompt_submit_shape_unchanged(self, hook):
        out = hook._compact_input({"hook_event_name": "UserPromptSubmit",
                                   "prompt": "hello there"})
        assert out["prompt"] == "hello there"
        assert out["pawflow_injected_prompt"] is False
        assert out["prompt_len"] == len("hello there")
        assert "last_assistant_message" not in out

    def test_injected_prompt_is_consumed_not_echoed(self, hook, tmp_path, monkeypatch):
        import hashlib
        import time
        marker = tmp_path / "injected.jsonl"
        text = "PawFlow injected this prompt"
        marker.write_text(json.dumps({
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "length": len(text), "ts": time.time(), "remaining": text,
        }) + "\n", encoding="utf-8")
        monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))
        out = hook._compact_input({"hook_event_name": "UserPromptSubmit",
                                   "prompt": text})
        assert out["pawflow_injected_prompt"] is True
        assert "prompt" not in out


class TestEnvelopeAndDelivery:
    def test_event_envelope_is_unchanged(self, hook, monkeypatch, capsys):
        sent = []
        monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
        monkeypatch.setenv("PAWFLOW_CCI_EVENT_URL", "wss://events/x")
        monkeypatch.setenv("PAWFLOW_CCI_EVENT_TOKEN", "tok")
        monkeypatch.setenv("HOSTNAME", "container-1")
        monkeypatch.setattr(hook, "_deliver",
                            lambda url, token, session, event: sent.append(
                                (url, token, session, event)) or True)
        monkeypatch.setattr(hook.sys, "stdin", __import__("io").StringIO(
            json.dumps({"hook_event_name": "Stop",
                        "last_assistant_message": "done"})))
        assert hook.main() == 0
        url, token, session, event = sent[0]
        assert (url, token, session) == ("wss://events/x", "tok", "sess")
        assert set(event) == {"type", "hook_event_name", "input",
                              "container_id", "timestamp"}
        assert event["type"] == "hook"
        assert event["hook_event_name"] == "Stop"
        assert event["container_id"] == "container-1"
        assert event["input"]["last_assistant_message"] == "done"
        # Consumer epochs and turn receipts are server-owned: the hook mints
        # neither and echoes none.
        assert not {"consumer_epoch", "turn_receipt", "event_id"} & set(event)
        assert capsys.readouterr().out == ""

    def test_delivery_retries_once_then_gives_up(self, hook, monkeypatch):
        attempts = []

        def _connect(url, token, session):
            attempts.append(1)
            raise ConnectionError("refused")
        monkeypatch.setattr(hook, "_connect", _connect)
        monkeypatch.setattr(hook.time, "sleep", lambda s: None)
        assert hook._deliver("wss://e", "t", "s", {"type": "hook"}) is False
        assert len(attempts) == hook._DELIVERY_RETRIES + 1

    def test_delivery_succeeds_on_retry(self, hook, monkeypatch):
        calls = []

        class _Sock:
            def sendall(self, data):
                calls.append(data)

            def close(self):
                pass

        state = {"n": 0}

        def _connect(url, token, session):
            state["n"] += 1
            if state["n"] == 1:
                raise ConnectionError("refused")
            return _Sock()
        monkeypatch.setattr(hook, "_connect", _connect)
        monkeypatch.setattr(hook.time, "sleep", lambda s: None)
        assert hook._deliver("wss://e", "t", "s", {"type": "hook"}) is True
        assert len(calls) == 1

    def test_missing_env_is_a_silent_noop(self, hook, monkeypatch, capsys):
        for name in ("PAWFLOW_CCI_SESSION_TOKEN", "PAWFLOW_CCI_EVENT_URL",
                     "PAWFLOW_CCI_EVENT_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        assert hook.main() == 0
        assert capsys.readouterr().out == ""


class TestAntigravityClient:
    def test_agy_payload_is_normalized_to_claude_field_names(self, hook, tmp_path):
        transcript = tmp_path / "t.jsonl"
        _claude_transcript(transcript, [
            {"role": "user", "content": "typed in agy"},
            {"role": "model", "content": [{"type": "text", "text": "agy said"}]},
        ])
        raw = hook._normalize_client_input({
            "hookEventName": "PreInvocation",
            "transcriptPath": str(transcript),
            "sessionId": "s1",
        }, "agy")
        assert raw["hook_event_name"] == "UserPromptSubmit"
        assert raw["transcript_path"] == str(transcript)
        assert raw["session_id"] == "s1"
        assert raw["prompt"] == "typed in agy"
        stop = hook._compact_input(hook._normalize_client_input({
            "hookEventName": "Stop",
            "transcriptPath": str(transcript),
            "finalModelOutput": "native agy final",
            "terminationReason": "STOP",
        }, "agy"))
        assert stop["last_assistant_message"] == "native agy final"
        assert stop["final_source"] == "hook_field"
        assert stop["reason"] == "STOP"

    def test_non_agy_clients_are_untouched(self, hook):
        raw = {"hook_event_name": "Stop", "hookEventName": "ignored"}
        assert hook._normalize_client_input(raw, "cc") is raw
        assert hook._normalize_client_input(raw, "codex") is raw

    def test_agy_client_prints_empty_json_object(self, hook, monkeypatch, capsys):
        monkeypatch.setenv("PAWFLOW_CCI_HOOK_CLIENT", "agy")
        for name in ("PAWFLOW_CCI_SESSION_TOKEN", "PAWFLOW_CCI_EVENT_URL",
                     "PAWFLOW_CCI_EVENT_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        assert hook.main() == 0
        assert capsys.readouterr().out.strip() == "{}"
