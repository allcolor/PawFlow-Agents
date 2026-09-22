"""Submission ACKs must prove a tmux Enter without stealing stream events."""

import hashlib

from services.cc_interactive_event_service import CCInteractiveEventService


PROMPT = "PawFlow cold-session bootstrap.\nRead the whole context first.\n"


def _service(provider="codex-interactive"):
    service = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    service.register_session("sess", provider=provider)
    service.remember_injected_prompt("sess", PROMPT)
    return service


def _hook(prompt, *, injected):
    return {
        "type": "hook",
        "hook_event_name": "UserPromptSubmit",
        "input": {
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_len": len(prompt),
            "pawflow_injected_prompt": injected,
            **({} if injected else {"prompt": prompt}),
        },
    }


def test_exact_user_prompt_submit_is_a_non_destructive_ack():
    service = _service()
    marker = service.submission_marker("sess")
    event = _hook(PROMPT.rstrip("\r\n"), injected=True)
    service.publish_event("sess", event)

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "hook"
    assert service.wait_event("sess", timeout=0) == event


def test_compact_injected_hook_without_hash_acks_latest_prompt():
    service = _service()
    marker = service.submission_marker("sess")
    event = {
        "type": "hook",
        "hook_event_name": "UserPromptSubmit",
        "input": {
            "hook_event_name": "UserPromptSubmit",
            "prompt_len": len(PROMPT),
            "pawflow_injected_prompt": True,
        },
    }

    service.publish_event("sess", event)

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "hook"


def test_a_stale_identical_hook_does_not_ack_the_next_prompt():
    service = _service()
    service.publish_event("sess", _hook(PROMPT, injected=True))
    marker = service.submission_marker("sess")

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == ""


def test_fragment_is_reported_instead_of_acknowledging_the_full_prompt():
    service = _service()
    marker = service.submission_marker("sess")
    fragment = "PawFlow cold-session bootstrap."
    service.publish_event("sess", _hook(fragment, injected=False))

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "fragment"


def test_mitm_responses_request_proves_submission_without_consuming_it():
    service = _service()
    marker = service.submission_marker("sess")
    event = {
        "type": "request_start", "request_id": "r1",
        "path": "/backend-api/codex/responses?conversation=1",
    }
    service.publish_event("sess", event)

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "request"
    assert service.wait_event("sess", timeout=0) == event


def test_a_different_submit_is_reported_instead_of_called_no_ack():
    service = _service()
    marker = service.submission_marker("sess")
    service.publish_event("sess", _hook("an older queued prompt", injected=True))

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "other"


def _ws_submit(*texts):
    return {
        "type": "ws_prompt_submit", "request_id": "ws1",
        "prompt_sha256s": [
            hashlib.sha256(t.rstrip("\r\n").encode("utf-8")).hexdigest()
            for t in texts],
    }


def test_codex_websocket_turn_proves_submission_without_the_hook():
    """2026-09-22: the hook connection broke; the proxy had seen the turn."""
    service = _service()
    marker = service.submission_marker("sess")
    service.publish_event("sess", _ws_submit("<environment_context/>", PROMPT))

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "hook"
    # Side-channel evidence: the turn coordinator never sees it.
    assert service.wait_event("sess", timeout=0) == {}


def test_websocket_digest_of_a_foreign_prompt_is_not_a_receipt():
    service = _service()
    marker = service.submission_marker("sess")
    service.publish_event("sess", _ws_submit("something a human typed"))

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == ""


def test_tool_continuations_do_not_repeat_the_receipt():
    """Each continuation may resend the user message; one receipt only."""
    service = _service()
    service.publish_event("sess", _ws_submit(PROMPT))
    marker = service.submission_marker("sess")
    service.publish_event("sess", _ws_submit(PROMPT))

    assert service.submission_marker("sess") == marker
