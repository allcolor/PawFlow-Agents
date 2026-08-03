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
