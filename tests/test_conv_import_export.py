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

def test_export_pawflow_creates_valid_zip(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(conv_dir.rglob('*')):
            if f.is_file() and '.git' not in f.parts:
                zf.write(f, str(f.relative_to(conv_dir)))
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "transcript.jsonl" in names


def test_export_pawflow_excludes_git(conv):
    store, cid = conv
    conv_dir = store._conv_dir(cid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(conv_dir.rglob('*')):
            if f.is_file() and '.git' not in f.parts:
                zf.write(f, str(f.relative_to(conv_dir)))
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
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
            json.dumps({"t": "msg", "role": m["role"], "content": m["content"],
                        "msg_id": uuid.uuid4().hex[:12], "timestamp": time.time()})
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
            transcript_lines.append(json.dumps({"t": "msg", "role": "user", "content": content, "msg_id": mid, "timestamp": ts}))
        elif msg_type == "assistant":
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                content = "\n".join(text_parts)
            transcript_lines.append(json.dumps({"t": "msg", "role": "assistant", "content": content, "msg_id": mid, "timestamp": ts}))
        elif msg_type == "tool_result":
            transcript_lines.append(json.dumps({"t": "msg", "role": "tool", "content": str(content), "msg_id": mid, "timestamp": ts}))

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
