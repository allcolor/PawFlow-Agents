from core.agent_hooks import AgentHookRunner


class _ConversationStore:
    def __init__(self, bindings):
        self.bindings = bindings

    def get_extra(self, conversation_id, key, user_id=""):
        assert conversation_id == "conv1"
        assert key == "conversation_hooks"
        return self.bindings


class _ResourceStore:
    def __init__(self, hooks):
        self.hooks = hooks

    def get_any(self, resource_type, name, user_id, conversation_id=""):
        assert resource_type == "agent_hook"
        assert user_id == "user1"
        assert conversation_id == "conv1"
        return self.hooks.get(name)


def _patch_stores(monkeypatch, bindings, hooks):
    conv_store = _ConversationStore(bindings)
    resource_store = _ResourceStore(hooks)
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        staticmethod(lambda: conv_store),
    )
    monkeypatch.setattr(
        "core.resource_store.ResourceStore.instance",
        staticmethod(lambda: resource_store),
    )


def _runner():
    return AgentHookRunner(
        user_id="user1",
        conversation_id="conv1",
        agent_name="assistant",
        provider="openai",
        model="gpt-5.5",
    )


def test_agent_hooks_apply_replacements_in_priority_order(monkeypatch):
    _patch_stores(
        monkeypatch,
        [
            {"name": "second", "priority": 20},
            {"name": "first", "priority": 10},
        ],
        {
            "first": {"name": "first", "events": ["pre_user_message"]},
            "second": {"name": "second", "events": ["pre_user_message"]},
        },
    )
    calls = []

    def _invoke(self, hook, envelope):
        calls.append((hook["name"], envelope["payload"]["content"]))
        return {
            "decision": "replace",
            "payload": {"content": envelope["payload"]["content"] + hook["name"]},
        }

    monkeypatch.setattr(AgentHookRunner, "_invoke", _invoke)

    result = _runner().run("pre_user_message", {"content": "start-"})

    assert calls == [("first", "start-"), ("second", "start-first")]
    assert result["decision"] == "replace"
    assert result["payload"]["content"] == "start-firstsecond"


def test_agent_hook_block_short_circuits(monkeypatch):
    _patch_stores(
        monkeypatch,
        [{"name": "blocker"}, {"name": "after"}],
        {
            "blocker": {"name": "blocker"},
            "after": {"name": "after"},
        },
    )
    calls = []

    def _invoke(self, hook, envelope):
        calls.append(hook["name"])
        if hook["name"] == "blocker":
            return {"decision": "block", "reason": "no", "payload": envelope["payload"]}
        return {"decision": "replace", "payload": {"content": "bad"}}

    monkeypatch.setattr(AgentHookRunner, "_invoke", _invoke)

    result = _runner().run("pre_user_message", {"content": "hello"})

    assert calls == ["blocker"]
    assert result["decision"] == "block"
    assert result["reason"] == "no"


def test_agent_hooks_filter_by_event_agent_and_tool(monkeypatch):
    _patch_stores(
        monkeypatch,
        [
            {"name": "wrong_event", "events": ["post_tool_call"]},
            {"name": "wrong_agent", "agents": ["other"]},
            {"name": "wrong_tool", "tools": ["write"]},
            {"name": "match", "events": ["pre_tool_call"], "agents": ["assistant"], "tools": ["read"]},
        ],
        {
            "wrong_event": {"name": "wrong_event"},
            "wrong_agent": {"name": "wrong_agent"},
            "wrong_tool": {"name": "wrong_tool"},
            "match": {"name": "match", "events": ["pre_tool_call"]},
        },
    )
    calls = []

    def _invoke(self, hook, envelope):
        calls.append(hook["name"])
        return {"decision": "allow", "payload": envelope["payload"]}

    monkeypatch.setattr(AgentHookRunner, "_invoke", _invoke)

    result = _runner().run("pre_tool_call", {"tool_name": "read", "arguments": {}})

    assert calls == ["match"]
    assert result["decision"] == "allow"


def test_agent_hook_fail_closed_blocks_on_exception(monkeypatch):
    _patch_stores(
        monkeypatch,
        [{"name": "strict", "fail_policy": "closed"}],
        {"strict": {"name": "strict"}},
    )

    def _invoke(self, hook, envelope):
        raise RuntimeError("boom")

    monkeypatch.setattr(AgentHookRunner, "_invoke", _invoke)

    result = _runner().run("pre_tool_call", {"tool_name": "read"})

    assert result["decision"] == "block"
    assert "boom" in result["reason"]
