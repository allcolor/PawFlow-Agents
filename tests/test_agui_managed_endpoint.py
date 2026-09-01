"""Managed AG-UI action endpoints (plan B1-X/B6, step P1-F).

The batch machine itself is covered by test_agui_managed_batch.py; here
we exercise the HTTP surface: ?action= dispatch, the X-PawFlow-Exec-Token
header, the JSON shapes and the error→status mapping, all against a real
batch built through the P1-D store primitives.
"""

import json

import pytest

from core.a2a_store import A2AStore
from services import agui_server_endpoint as endpoint
from services._agui_actions import handle_managed_action


class _Request:
    def __init__(self, *, body=None, headers=None, query_string="",
                 publication_id="a2ap_test"):
        self.body = json.dumps(body).encode("utf-8") if body is not None else b""
        self.path_params = {"publication_id": publication_id}
        self.headers = headers or {"Host": "pawflow.example"}
        self.query_string = query_string
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


def _decoded(request):
    status, _headers, body = request.completed
    return status, (json.loads(body.decode("utf-8")) if body else None)


def _patch_auth(monkeypatch, owner="owner", agent="helper"):
    """Satisfy _publication's owner + agent-config gate for a tmp store."""
    from core.conversation_store import ConversationStore
    import core.conv_agent_config as cac
    monkeypatch.setattr(ConversationStore, "resolve_owner",
                        lambda self, cid: owner, raising=False)
    monkeypatch.setattr(cac, "get_all_agent_configs",
                        lambda cid: {agent: {}}, raising=False)


@pytest.fixture()
def managed(tmp_path, monkeypatch):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    _patch_auth(monkeypatch)
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated",
        managed_mode=True)
    raw, key = store.create_key(publication["publication_id"], "client")
    pub_id = publication["publication_id"]
    # Build a real frozen batch through the P1-D flow.
    admission = store.acquire_agui_turn(publication, key["key_id"], "t-1",
                                        0, "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a", "page_tool",
                           catalogue_id="host:page_tool",
                           catalogue_version="cv-1")
    store.finish_agui_turn(context_id, "run-1", "w1", "success",
                           batch_deadline_seconds=600.0)
    batch_token = store.batch_token_for(context_id, "run-1")
    return {"store": store, "publication": publication, "pub_id": pub_id,
            "bearer": raw, "context_id": context_id,
            "batch_token": batch_token}


def _headers(managed, exec_token=None):
    headers = {"Host": "pawflow.example",
               "Authorization": f"Bearer {managed['bearer']}"}
    if exec_token is not None:
        headers["X-PawFlow-Exec-Token"] = exec_token
    return headers


def _post(managed, action, exec_token=None, body=None):
    req = _Request(body=body if body is not None else {},
                   headers=_headers(managed, exec_token),
                   query_string=f"action={action}",
                   publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    return _decoded(req)


# ── descriptor ───────────────────────────────────────────────────────

def test_descriptor_announces_managed_mode_and_actions(managed):
    req = _Request(headers=_headers(managed),
                   publication_id=managed["pub_id"])
    endpoint.handle_describe(req)
    status, payload = _decoded(req)
    assert status == 200
    assert payload["executionMode"] == "managed"
    assert payload["capabilities"]["managedBatch"] is True
    assert payload["actions"] == ["attach", "claim_batch", "begin",
                                  "deposit", "renew"]
    assert payload["cancel"] == {"method": "DELETE",
                                 "header": "X-PawFlow-Cancel-Token"}


def test_descriptor_bootstraps_the_raw_managed_thread_id(managed,
                                                         monkeypatch):
    store = managed["store"]
    seen = []
    real_ensure = store.ensure_agui_thread

    def ensure(publication, key_id, thread_id):
        seen.append(thread_id)
        return real_ensure(publication, key_id, thread_id)

    monkeypatch.setattr(store, "ensure_agui_thread", ensure)
    req = _Request(headers=_headers(managed),
                   query_string="thread_id=client-thread",
                   publication_id=managed["pub_id"])
    endpoint.handle_describe(req)
    status, payload = _decoded(req)
    assert status == 200
    assert seen == ["client-thread"]
    assert payload["thread"] == {
        "threadId": "client-thread", "generation": 0}


def test_descriptor_is_classic_without_managed_mode(tmp_path, monkeypatch):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    _patch_auth(monkeypatch)
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated")
    raw, _key = store.create_key(publication["publication_id"], "client")
    req = _Request(headers={"Host": "pawflow.example",
                            "Authorization": f"Bearer {raw}"},
                   publication_id=publication["publication_id"])
    endpoint.handle_describe(req)
    _status, payload = _decoded(req)
    assert payload["executionMode"] == "classic"
    assert payload["actions"] == []
    assert payload["cancel"] is None


# ── claim / begin / deposit / renew round trip ───────────────────────

def test_full_managed_round_trip_over_http(managed):
    status, claim = _post(managed, "claim_batch", managed["batch_token"],
                          {"batchClaimId": "cl-1"})
    assert status == 200
    assert claim["state"] == "reserved_pre_effect"
    assert claim["claimGeneration"] == 1
    owner_token = claim["ownerToken"]
    receipt = claim["receipts"][0]["receipt"]
    assert claim["receipts"][0]["toolCallId"] == "call-a"

    # Idempotent re-claim returns the same credentials.
    _s, replay = _post(managed, "claim_batch", managed["batch_token"],
                       {"batchClaimId": "cl-1"})
    assert replay["ownerToken"] == owner_token

    # renew is an idempotent no-op success.
    status, renew = _post(managed, "renew", owner_token)
    assert status == 200 and renew["renewed"] is True

    # begin needs the matching catalogue identity.
    status, begun = _post(managed, "begin", receipt,
                          {"catalogueId": "host:page_tool",
                           "catalogueVersion": "cv-1"})
    assert status == 200 and begun["begun"] is True

    # deposit the effect outcome — the single call completes the batch.
    status, deposit = _post(managed, "deposit", receipt,
                            {"kind": "result", "payload": {"ok": 1}})
    assert status == 200
    assert deposit["batchState"] == "complete"
    assert deposit["replay"] is False


def test_second_claim_id_conflicts_with_409(managed):
    _post(managed, "claim_batch", managed["batch_token"],
          {"batchClaimId": "cl-1"})
    status, payload = _post(managed, "claim_batch", managed["batch_token"],
                            {"batchClaimId": "cl-2"})
    assert status == 409
    assert payload["error"] == "batch_already_claimed"


def test_bad_token_is_401(managed):
    status, payload = _post(managed, "claim_batch", "garbage",
                            {"batchClaimId": "cl-1"})
    assert status == 401
    assert payload["error"] == "token_invalid"


def test_begin_with_changed_catalogue_is_a_terminal_outcome(managed):
    claim = _post(managed, "claim_batch", managed["batch_token"],
                  {"batchClaimId": "cl-1"})[1]
    receipt = claim["receipts"][0]["receipt"]
    status, payload = _post(managed, "begin", receipt,
                            {"catalogueId": "host:page_tool",
                             "catalogueVersion": "cv-9"})
    # Not an error — the call terminalized without executing.
    assert status == 200
    assert payload["terminal"] is True
    assert payload["outcome"] == "catalogue_changed"


def test_deposit_after_complete_replays_over_http(managed):
    # The single call completes the batch on first deposit; a later
    # divergent deposit on the consumed batch replays the terminal
    # outcome (200), never mutates it — the HTTP surface of the P1-D
    # replay rule.
    claim = _post(managed, "claim_batch", managed["batch_token"],
                  {"batchClaimId": "cl-1"})[1]
    receipt = claim["receipts"][0]["receipt"]
    _post(managed, "begin", receipt,
          {"catalogueId": "host:page_tool", "catalogueVersion": "cv-1"})
    _post(managed, "deposit", receipt,
          {"kind": "result", "payload": {"ok": 1}})
    status, payload = _post(managed, "deposit", receipt,
                            {"kind": "error", "payload": {"boom": 1}})
    assert status == 200
    assert payload["replay"] is True
    assert payload["kind"] == "result"


def test_claim_missing_batch_claim_id_is_400(managed):
    status, payload = _post(managed, "claim_batch", managed["batch_token"],
                            {})
    assert status == 400


def test_unknown_action_is_400_after_auth(managed):
    req = _Request(body={}, headers=_headers(managed),
                   query_string="action=frobnicate",
                   publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 400
    assert payload["error"] == "unknown_action"


# ── review v1 probes (P1-F/1 v2) ─────────────────────────────────────

def test_unknown_action_without_credentials_is_authenticated_first(managed):
    # An unauthenticated caller must NOT learn an action is unknown — the
    # publication + key gate runs before the action lookup.
    req = _Request(body={}, headers={"Host": "pawflow.example"},
                   query_string="action=frobnicate",
                   publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 401
    assert payload["error"] != "unknown_action"


def test_unknown_action_on_missing_publication_is_404(managed):
    req = _Request(body={}, headers={"Host": "pawflow.example"},
                   query_string="action=frobnicate",
                   publication_id="a2ap_nope")
    endpoint.handle_run(req)
    status, _payload = _decoded(req)
    assert status == 404


def test_a_batch_token_cannot_cross_to_another_publication(managed,
                                                           monkeypatch):
    # Publication B, authenticated with B's own key, must NOT be able to
    # claim publication A's batch by presenting A's batch_token.
    store = managed["store"]
    # A DISTINCT publication (different conversation) with its own key.
    other = store.configure_publication(
        "owner", "conv-2", "helper", context_policy="isolated",
        managed_mode=True, label="other")
    assert other["publication_id"] != managed["pub_id"]
    raw_b, _key_b = store.create_key(other["publication_id"], "client-b")
    req = _Request(
        body={"batchClaimId": "cl-x"},
        headers={"Host": "pawflow.example",
                 "Authorization": f"Bearer {raw_b}",
                 "X-PawFlow-Exec-Token": managed["batch_token"]},
        query_string="action=claim_batch",
        publication_id=other["publication_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    # Uniform token failure — B learns nothing about A's batch.
    assert status == 401
    assert payload["error"] == "token_invalid"
    # A's batch is untouched: A can still claim it.
    status, claim = _post(managed, "claim_batch", managed["batch_token"],
                          {"batchClaimId": "cl-a"})
    assert status == 200 and claim["state"] == "reserved_pre_effect"


def test_internal_error_is_500_not_a_400_leak(managed, monkeypatch):
    from core.a2a_store import A2AStore

    def _boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(A2AStore, "claim_agui_batch", _boom)
    status, payload = _post(managed, "claim_batch", managed["batch_token"],
                            {"batchClaimId": "cl-1"})
    assert status == 500
    assert payload["error"] == "internal_error"
    assert "secret internal detail" not in json.dumps(payload)


def test_managed_action_refused_on_classic_publication(tmp_path,
                                                       monkeypatch):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    _patch_auth(monkeypatch)
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated")
    raw, _key = store.create_key(publication["publication_id"], "client")
    req = _Request(body={"batchClaimId": "cl-1"},
                   headers={"Host": "pawflow.example",
                            "Authorization": f"Bearer {raw}",
                            "X-PawFlow-Exec-Token": "whatever"},
                   query_string="action=claim_batch",
                   publication_id=publication["publication_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 409
    assert payload["error"] == "not_managed"


def test_handle_managed_action_returns_false_for_plain_run():
    # A run POST (no ?action=) is not a managed action — the dispatcher
    # declines so the caller falls through to the SSE run path.
    req = _Request(body={})
    assert handle_managed_action(req, "") is False


# ── plain managed POST: pilot before stream, 409 before SSE ──────────

class _StreamRequest(_Request):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.streamed = None

    def complete_stream(self, status, headers, stream):
        self.streamed = (status, headers, stream)


def test_managed_post_starts_the_pilot_before_the_stream(managed,
                                                         monkeypatch):
    import core._agui_managed_runtime as managed_runtime
    calls = []
    prepared = {
        "spec": {"run_id": "run-1"},
        "context": {"context_id": managed["context_id"]},
    }
    monkeypatch.setattr(managed_runtime, "acquire_managed_turn",
                        lambda publication, key, body: prepared)
    monkeypatch.setattr(managed_runtime, "ensure_managed_pilot",
                        lambda p: calls.append(("pilot", p)))
    req = _StreamRequest(body={"threadId": "t", "runId": "run-9",
                               "messages": []},
                         headers=_headers(managed),
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    # The durable pilot was started before the SSE stream was handed to
    # the HTTP layer — the generator is only a journal tail.
    assert calls == [("pilot", prepared)]
    assert req.streamed is not None and req.streamed[0] == 200
    assert req.streamed[1]["X-PawFlow-Subscriber-Epoch"] == "1"
    assert managed["store"].get_agui_run(
        managed["context_id"], "run-1")["subscriber_epoch"] == 1
    assert req.completed is None


def _frames_to_events(stream, limit=50):
    events = []
    for frame in stream:
        text = frame.decode("utf-8")
        if text.startswith(":"):
            continue
        events.append(json.loads(text[len("data: "):]))
        if len(events) >= limit:
            break
    return events


def test_attach_tails_the_journal_by_token(managed):
    # The fixture's run is terminal with a frozen batch: attach must
    # replay the committed journal after the caller's watermark and end.
    store = managed["store"]
    tokens = store.agui_run_tokens_for(managed["context_id"], "run-1")
    req = _StreamRequest(body={"afterSeq": 0},
                         headers=_headers(managed,
                                          tokens["attach_token"]),
                         query_string="action=attach",
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    assert req.completed is None
    status, headers, stream = req.streamed
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert headers["X-PawFlow-Subscriber-Epoch"] == "1"
    events = _frames_to_events(stream)
    # The terminal RUN_FINISHED (journaled by finish) closes the tail
    # and carries the batch token in wire shape.
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["batchToken"] == managed["batch_token"]


def test_second_attach_takes_over_gaplessly_without_touching_pilot(managed):
    import core._agui_managed_runtime as managed_runtime

    store = managed["store"]
    key_id = store.list_keys(managed["pub_id"])[0]["key_id"]
    admission = store.acquire_agui_turn(
        managed["publication"], key_id, "t-2", 0, "run-2", "hash-2")
    context_id = admission["context_id"]
    first_seq = store.append_agui_event(
        context_id, "run-2",
        '{"type":"TEXT_MESSAGE_CONTENT","messageId":"m","delta":"one"}')
    tokens = store.agui_run_tokens_for(context_id, "run-2")
    pilots_before = dict(managed_runtime._PILOTS)

    first_req = _StreamRequest(
        body={"afterSeq": 0},
        headers=_headers(managed, tokens["attach_token"]),
        query_string="action=attach",
        publication_id=managed["pub_id"])
    endpoint.handle_run(first_req)
    first_headers, first_stream = first_req.streamed[1:]
    assert first_headers["X-PawFlow-Subscriber-Epoch"] == "1"
    first_event = json.loads(
        next(first_stream).decode("utf-8")[len("data: "):])
    assert first_event["delta"] == "one"

    second_req = _StreamRequest(
        body={"afterSeq": first_seq},
        headers=_headers(managed, tokens["attach_token"]),
        query_string="action=attach",
        publication_id=managed["pub_id"])
    endpoint.handle_run(second_req)
    second_headers, second_stream = second_req.streamed[1:]
    assert second_headers["X-PawFlow-Subscriber-Epoch"] == "2"

    second_seq = store.append_agui_event(
        context_id, "run-2",
        '{"type":"TEXT_MESSAGE_CONTENT","messageId":"m","delta":"two"}')
    assert second_seq == first_seq + 1
    with pytest.raises(StopIteration):
        next(first_stream)
    second_event = json.loads(
        next(second_stream).decode("utf-8")[len("data: "):])
    assert second_event["delta"] == "two"

    # Subscriber takeover neither admits nor starts/stops the durable pilot.
    assert store.get_agui_admission(context_id, "run-2")["state"] == "reserved"
    assert managed_runtime._PILOTS == pilots_before
    second_stream.close()


def test_attach_replays_after_the_watermark_only(managed):
    store = managed["store"]
    committed = store.get_agui_run(managed["context_id"],
                                   "run-1")["committed_sequence"]
    tokens = store.agui_run_tokens_for(managed["context_id"], "run-1")
    req = _StreamRequest(body={"afterSeq": committed},
                         headers=_headers(managed,
                                          tokens["attach_token"]),
                         query_string="action=attach",
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    assert _frames_to_events(req.streamed[2]) == []  # nothing after it
    # Beyond the committed sequence → a 400, never a silent empty tail.
    req = _StreamRequest(body={"afterSeq": committed + 10},
                         headers=_headers(managed,
                                          tokens["attach_token"]),
                         query_string="action=attach",
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 400 and payload["error"] == "invalid_after_seq"


def test_attach_with_a_bad_token_is_uniformly_401(managed):
    req = _StreamRequest(body={}, headers=_headers(managed, "garbage"),
                         query_string="action=attach",
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 401 and payload["error"] == "token_invalid"
    assert req.streamed is None


# ── DELETE cancel ────────────────────────────────────────────────────

def _delete(managed, cancel_token):
    req = _Request(headers={**_headers(managed),
                            "X-PawFlow-Cancel-Token": cancel_token},
                   publication_id=managed["pub_id"])
    endpoint.handle_cancel(req)
    return _decoded(req)


def test_delete_cancels_a_running_run_idempotently(managed):
    store = managed["store"]
    publication = managed["publication"]
    key_id = store.list_keys(managed["pub_id"])[0]["key_id"]
    admission = store.acquire_agui_turn(publication, key_id, "t-2", 0,
                                        "run-2", "hash-2")
    context_id = admission["context_id"]
    store.adopt_agui_run(context_id, "run-2", "w2")
    tokens = store.agui_run_tokens_for(context_id, "run-2")
    status, payload = _delete(managed, tokens["cancel_token"])
    assert status == 200
    assert payload == {"outcome": "cancelled", "already": False}
    status, payload = _delete(managed, tokens["cancel_token"])
    assert status == 200
    assert payload == {"outcome": "cancelled", "already": True}
    run = store.get_agui_run(context_id, "run-2")
    assert (run["state"], run["outcome"]) == ("terminal", "cancelled")


def test_delete_with_a_bad_token_is_uniformly_401(managed):
    status, payload = _delete(managed, "garbage")
    assert status == 401 and payload["error"] == "token_invalid"


def test_delete_requires_managed_mode(tmp_path, monkeypatch):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    _patch_auth(monkeypatch)
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated")
    raw, _key = store.create_key(publication["publication_id"], "client")
    req = _Request(headers={"Host": "pawflow.example",
                            "Authorization": f"Bearer {raw}",
                            "X-PawFlow-Cancel-Token": "whatever"},
                   publication_id=publication["publication_id"])
    endpoint.handle_cancel(req)
    status, payload = _decoded(req)
    assert status == 409 and payload["error"] == "not_managed"


def test_managed_post_acquire_refusal_is_a_real_409(managed, monkeypatch):
    import core._agui_managed_runtime as managed_runtime
    from core._agui_managed_runtime import ManagedAcquireError

    def _refuse(publication, key, body):
        raise ManagedAcquireError(409, "thread_busy", "busy")

    monkeypatch.setattr(managed_runtime, "acquire_managed_turn", _refuse)
    monkeypatch.setattr(
        managed_runtime, "ensure_managed_pilot",
        lambda p: pytest.fail("no pilot may start on a refused acquire"))
    req = _StreamRequest(body={"threadId": "t", "runId": "run-9",
                               "messages": []},
                         headers=_headers(managed),
                         publication_id=managed["pub_id"])
    endpoint.handle_run(req)
    status, payload = _decoded(req)
    assert status == 409
    assert payload["error"] == "thread_busy"
    assert req.streamed is None
