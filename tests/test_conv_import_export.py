"""Tests for conversation import/export functionality.

Covers:
- Export to PawFlow .zip (valid archive with transcript.jsonl)
- Export to Claude Code .jsonl (correct format conversion)
- Import analysis — PawFlow and Claude Code formats
- Import execute — PawFlow and Claude Code formats with agent mapping
"""

import io
import json
import time
import uuid
import zipfile

import pytest

from core.conversation_store import ConversationStore
from core.segmented_jsonl import SegmentedJsonl


def _msg(role="user", content="hello", **kw):
    m = {
        "role": role, "content": content,
        "msg_id": uuid.uuid4().hex[:12], "ts": time.time(), "seq": 1,
    }
    m.update(kw)
    return m


@pytest.fixture(autouse=True)
def reset_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def conv(store):
    cid = store.generate_id()
    store.save(cid, [_msg("user", "Hello"), _msg("assistant", "Hi there")], user_id="testuser")
    return store, cid


# ── Export PawFlow zip ──

def _logical_rows(path):
    return list(SegmentedJsonl(path).iter_rows())


def _export_pawflow_zip(store, cid):
    from core import FlowFile
    from core.file_store import FileStore
    from tasks.ai.actions.conversation import _handle_conversation

    class _Self:
        pass

    ff = FlowFile(content=b"")
    out = _handle_conversation(
        _Self(), "conv_export_pawflow", {"conversation_id": cid},
        store, "testuser", ff,
    )
    res = json.loads(out[0].get_content().decode())
    assert res.get("ok"), res
    fid = res["url"].split("/files/", 1)[1].split("/", 1)[0]
    stored = FileStore.instance().get(fid, user_id="testuser")
    assert stored is not None
    _filename, raw, _content_type = stored
    return raw


def test_export_pawflow_creates_valid_zip(conv):
    store, cid = conv
    raw = _export_pawflow_zip(store, cid)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        assert "transcript.jsonl" in names
        transcript = [json.loads(l) for l in zf.read("transcript.jsonl").decode().splitlines() if l.strip()]
        assert [m.get("content") for m in transcript if m.get("role") in ("user", "assistant")] == ["Hello", "Hi there"]


def test_export_pawflow_excludes_git(conv):
    store, cid = conv
    raw = _export_pawflow_zip(store, cid)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            assert '.git' not in name


# ── Export Claude Code JSONL ──

def test_export_claude_code_format(conv):
    store, cid = conv
    msgs = store.load(cid, user_id="testuser")
    lines = []
    for m in msgs:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            lines.append(json.dumps({"type": "human", "message": {"role": "user", "content": content}}))
        elif role == "assistant":
            lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}}))
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["type"] == "human"
    assert first["message"]["content"] == "Hello"
    second = json.loads(lines[1])
    assert second["type"] == "assistant"
    assert second["message"]["content"] == "Hi there"


def test_export_claude_code_tool_role():
    """Tool messages map to tool_result type."""
    m = {"role": "tool", "content": "result data"}
    if m["role"] == "tool":
        entry = {"type": "tool_result", "message": {"role": "user", "content": m["content"]}}
    assert entry["type"] == "tool_result"
    assert entry["message"]["content"] == "result data"


# ── Import analyze — PawFlow ──

def _make_pawflow_zip(messages, conv_agents=None):
    """Build a PawFlow archive in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        transcript = "\n".join(
            json.dumps({"role": m["role"], "content": m["content"],
                        "msg_id": uuid.uuid4().hex[:12], "ts": time.time()})
            for m in messages
        ) + "\n"
        zf.writestr("transcript.jsonl", transcript)
        extras = {"conversation_id": "old_cid", "user_id": "old_user"}
        if conv_agents:
            extras["conv_agents"] = conv_agents
        zf.writestr("extras.json", json.dumps(extras))
    return buf.getvalue()


def test_analyze_pawflow_counts_messages():
    raw = _make_pawflow_zip([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ])
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        count = sum(1 for line in zf.read("transcript.jsonl").decode().splitlines() if line.strip())
    assert count == 3


def test_analyze_pawflow_extracts_agents():
    agents = {
        "coder": {"definition": "coding-agent", "params": {"name": "coder"}},
        "reviewer": {"definition": "review-agent", "params": {"name": "reviewer"}},
    }
    raw = _make_pawflow_zip([{"role": "user", "content": "hi"}], conv_agents=agents)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        extras = json.loads(zf.read("extras.json"))
        found = []
        for name, cfg in extras.get("conv_agents", {}).items():
            found.append({"name": name, "definition": cfg.get("definition", name)})
    assert len(found) == 2
    names = {a["name"] for a in found}
    assert "coder" in names
    assert "reviewer" in names


def test_analyze_pawflow_rejects_invalid_zip():
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(b"not a zip file"))


def test_analyze_pawflow_rejects_missing_transcript():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr("extras.json", '{}')
    raw = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert "transcript.jsonl" not in zf.namelist()


# ── Import analyze — Claude Code ──

def _make_cc_jsonl(messages):
    """Build a Claude Code JSONL string."""
    lines = []
    for m in messages:
        role = m["role"]
        content = m["content"]
        if role == "user":
            lines.append(json.dumps({"type": "human", "message": {"role": "user", "content": content}}))
        elif role == "assistant":
            lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}}))
        elif role == "tool":
            lines.append(json.dumps({"type": "tool_result", "message": {"role": "user", "content": content}}))
    return "\n".join(lines) + "\n"


def test_analyze_claude_code_counts_messages():
    text = _make_cc_jsonl([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    count = sum(1 for line in text.splitlines() if line.strip())
    assert count == 2


# ── Import execute — Claude Code to PawFlow transcript ──

def test_cc_to_pawflow_transcript_conversion():
    """Claude Code JSONL correctly converts to PawFlow transcript format."""
    cc_text = _make_cc_jsonl([
        {"role": "user", "content": "Write a function"},
        {"role": "assistant", "content": "Here it is"},
        {"role": "tool", "content": "file written"},
    ])
    transcript_lines = []
    for line in cc_text.splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        msg_type = entry.get("type", "")
        message = entry.get("message", {})
        content = message.get("content", "")
        mid = uuid.uuid4().hex[:12]
        ts = time.time()
        if msg_type == "human":
            transcript_lines.append(json.dumps({"role": "user", "content": content, "msg_id": mid, "ts": ts}))
        elif msg_type == "assistant":
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                content = "\n".join(text_parts)
            transcript_lines.append(json.dumps({"role": "assistant", "content": content, "msg_id": mid, "ts": ts}))
        elif msg_type == "tool_result":
            transcript_lines.append(json.dumps({"role": "tool", "content": str(content), "msg_id": mid, "ts": ts}))

    assert len(transcript_lines) == 3
    parsed = [json.loads(l) for l in transcript_lines]
    assert parsed[0]["role"] == "user"
    assert parsed[0]["content"] == "Write a function"
    assert parsed[1]["role"] == "assistant"
    assert parsed[1]["content"] == "Here it is"
    assert parsed[2]["role"] == "tool"
    assert parsed[2]["content"] == "file written"


def test_cc_structured_content_flattened():
    """Claude Code assistant messages with array content are flattened to text."""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "tool_use", "name": "bash"},
                {"type": "text", "text": "Part 2"},
            ]
        }
    }
    content = entry["message"]["content"]
    if isinstance(content, list):
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        content = "\n".join(text_parts)
    assert content == "Part 1\nPart 2"


# ── Import execute — PawFlow roundtrip ──

def test_pawflow_import_roundtrip(store):
    """A PawFlow archive can be extracted and creates a valid conversation."""
    messages = [
        {"role": "user", "content": "Hello from import"},
        {"role": "assistant", "content": "Imported reply"},
    ]
    agents = {"bot": {"definition": "my-bot", "params": {"name": "bot"}}}
    raw = _make_pawflow_zip(messages, conv_agents=agents)

    cid = uuid.uuid4().hex
    conv_dir = store._store_dir / store._safe_name("testuser") / store._safe_name(cid)
    conv_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(conv_dir)

    transcript = (conv_dir / "transcript.jsonl").read_text(encoding="utf-8")
    lines = [l for l in transcript.splitlines() if l.strip()]
    assert len(lines) == 2

    extras = json.loads((conv_dir / "extras.json").read_text(encoding="utf-8"))
    assert "bot" in extras["conv_agents"]
    assert extras["conv_agents"]["bot"]["definition"] == "my-bot"


# ── CC import execute ─ real CC format (user/assistant + tool_use/tool_result) ──

def _cc_execute(store, cc_text, agent_mapping=None):
    """Directly invoke conv_import_execute for a CC JSONL payload."""
    from tasks.ai.actions.conversation import _handle_conversation
    from core import FlowFile
    from core.file_store import FileStore

    fs = FileStore.instance()
    fid = fs.store("import.jsonl", cc_text.encode("utf-8"), "application/jsonl",
                   user_id="testuser", conversation_id="_upload")

    # Mock self object — _handle_conversation only uses `store` on self for
    # this action path.
    class _Self: pass
    ff = FlowFile(content=b"")
    # Analyze first to obtain a temp_id
    body = {"file_id": fid, "format": "claude_code"}
    out = _handle_conversation(_Self(), "conv_import_analyze", body, store, "testuser", ff)
    info = json.loads(out[0].get_content().decode())
    assert info.get("ok"), info
    temp_id = info["temp_id"]

    ff2 = FlowFile(content=b"")
    body2 = {
        "temp_id": temp_id, "format": "claude_code",
        "title": "Imported CC",
        "agent_mapping": agent_mapping or {"claude": {"definition": "claude", "params": {"name": "claude"}, "llm_service": ""}},
    }
    out2 = _handle_conversation(_Self(), "conv_import_execute", body2, store, "testuser", ff2)
    res = json.loads(out2[0].get_content().decode())
    assert res.get("ok"), res
    return res["conversation_id"]


def test_cc_import_real_format_user_assistant(store):
    """Real CC session: type=user/assistant with structured content blocks."""
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Sure."},
            {"type": "tool_use", "id": "tu_1", "name": "grep", "input": {"pattern": "foo"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "match"},
        ]}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Done."},
        ]}}),
    ]
    cid = _cc_execute(store, "\n".join(lines) + "\n")
    conv_dir = store._store_dir / store._safe_name("testuser") / store._safe_name(cid)
    transcript = _logical_rows(conv_dir / "transcript.jsonl")
    msgs = [m for m in transcript if m.get("role")]
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool_call", "tool", "assistant"]
    assert msgs[0]["content"] == "Hello"
    assert msgs[1]["content"] == "Sure."
    assert msgs[2]["tool_name"] == "grep"
    assert msgs[2]["tool_call_id"] == "tu_1"
    assert msgs[2]["parent_message_id"] == msgs[1]["msg_id"]
    assert msgs[3]["tool_call_id"] == "tu_1"
    assert msgs[3]["parent_message_id"] == msgs[2]["msg_id"]
    assert msgs[3]["content"] == "match"
    assert msgs[4]["content"] == "Done."


def test_cc_import_drops_empty_assistant_stub(store):
    """Assistant entries with no text AND no tool_use produce zero messages."""
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "real"}}),
    ]
    cid = _cc_execute(store, "\n".join(lines) + "\n")
    conv_dir = store._store_dir / store._safe_name("testuser") / store._safe_name(cid)
    transcript = _logical_rows(conv_dir / "transcript.jsonl")
    msgs = [m for m in transcript if m.get("role")]
    assert len(msgs) == 2
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["content"] == "real"


def test_cc_import_creates_shared_context(store):
    """shared.jsonl must match what ConversationStore.filter_for_shared
    produces for a native conv: no tool rows, no context injections,
    tool/detail rows skipped, source/badges preserved.
    Regression: the UI "Shared" view reported "divergé / aucun contexte"
    on every imported conv because only transcript.jsonl was written.
    """
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Hi there."},
            {"type": "tool_use", "id": "tu_x", "name": "grep", "input": {"pattern": "foo"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_x", "content": "bar"},
        ]}}),
    ]
    cid = _cc_execute(store, "\n".join(lines) + "\n")
    conv_dir = store._store_dir / store._safe_name("testuser") / store._safe_name(cid)
    shared_path = conv_dir / "shared.jsonl"
    assert SegmentedJsonl(shared_path).exists(), "shared context not created on CC import"
    shared = _logical_rows(shared_path)
    # Shared is agent-neutral: both entries become role=user after
    # _transform_for_shared. The second entry is the ex-assistant
    # (prefixed with [Agent claude]) — tool_result is dropped, and
    # the tool_use block was stored only as a private tool_call row.
    assert len(shared) == 2
    assert all(m["role"] == "user" for m in shared)
    assert all(m["role"] not in ("tool", "tool_call", "thinking") for m in shared)
    # Agent turn keeps its source badge and text (prefixed)
    agent_turn = shared[1]
    assert "Hi there." in agent_turn["content"]
    assert agent_turn["content"].startswith("[Agent claude")
    assert agent_turn["source"]["type"] == "agent"
    assert agent_turn["source"]["name"] == "claude"
    # Every entry has msg_id + ts + seq (required by _deserialize_messages)
    for m in shared:
        assert m.get("msg_id") and m.get("ts") and m.get("seq")


def test_cc_import_registers_in_list_conversations(store):
    """After import, list_conversations must include the new conv."""
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})]
    cid = _cc_execute(store, "\n".join(lines) + "\n")
    convs = store.list_conversations(user_id="testuser")
    assert any(c["conversation_id"] == cid for c in convs)


def test_cc_import_preserves_timestamp_in_list(store):
    """Imported conv must have a non-zero updated_at (no 01/01/1970).

    Regression: import used to emit 'timestamp' but _scan_cache reads 'ts',
    so updated_at stayed at 0 and the sidebar date was the epoch.
    """
    ts = 1_700_000_000  # 2023-11-14
    lines = [
        json.dumps({"type": "user", "timestamp": ts,
                    "message": {"role": "user", "content": "hi"}}),
        json.dumps({"type": "assistant", "timestamp": ts + 10,
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}),
    ]
    cid = _cc_execute(store, "\n".join(lines) + "\n")
    convs = store.list_conversations(user_id="testuser")
    me = next(c for c in convs if c["conversation_id"] == cid)
    assert me["updated_at"] >= ts, f"updated_at={me['updated_at']} (01/01/1970 bug)"


def test_pawflow_import_remaps_agents(store):
    """Agent mapping updates extras.json with new definitions."""
    agents = {"old_agent": {"definition": "old-def", "params": {"name": "old_agent"}}}
    raw = _make_pawflow_zip([{"role": "user", "content": "hi"}], conv_agents=agents)

    cid = uuid.uuid4().hex
    conv_dir = store._store_dir / store._safe_name("testuser") / store._safe_name(cid)
    conv_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(conv_dir)

    extras = json.loads((conv_dir / "extras.json").read_text(encoding="utf-8"))
    agent_mapping = {"old_agent": {"definition": "new-def", "params": {"name": "old_agent"}, "llm_service": "openai"}}
    new_conv_agents = {}
    for imp_name, mapping in agent_mapping.items():
        new_conv_agents[imp_name] = {
            "definition": mapping.get("definition", imp_name),
            "params": mapping.get("params", {"name": imp_name}),
            "llm_service": mapping.get("llm_service", ""),
        }
    extras["conv_agents"] = new_conv_agents
    extras["conversation_id"] = cid
    extras["user_id"] = "testuser"
    (conv_dir / "extras.json").write_text(json.dumps(extras), encoding="utf-8")

    reloaded = json.loads((conv_dir / "extras.json").read_text(encoding="utf-8"))
    assert reloaded["conv_agents"]["old_agent"]["definition"] == "new-def"
    assert reloaded["conv_agents"]["old_agent"]["llm_service"] == "openai"
    assert reloaded["conversation_id"] == cid
    assert reloaded["user_id"] == "testuser"
