from types import SimpleNamespace


def test_dynamic_skill_template_renders_pawflow_snapshot(monkeypatch):
    from core import skill_resolver

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            assert rtype == "skill"
            assert name == "dynamic-image"
            return {
                "description": "Dynamic image skill",
                "template_engine": "jinja",
                "prompt": (
                    "agent={{ pawflow.current_agent.name }}\n"
                    "relay={{ pawflow.default_relay.id }}\n"
                    "image={{ pawflow.media_services('image')[0].id }}\n"
                    "tool={{ pawflow.tool_schema('generate_image').name }}"
                ),
            }

    monkeypatch.setattr(
        skill_resolver._PawFlowTemplateContext,
        "current_agent",
        property(lambda self: {"name": "assistant"}),
    )
    monkeypatch.setattr(
        skill_resolver._PawFlowTemplateContext,
        "default_relay",
        property(lambda self: {"id": "fs_main"}),
    )
    monkeypatch.setattr(
        skill_resolver._PawFlowTemplateContext,
        "media_services",
        lambda self, kind="": [{"id": "codex_image_service"}],
    )
    monkeypatch.setattr(
        skill_resolver._PawFlowTemplateContext,
        "tool_schema",
        lambda self, name: {"name": name, "parameters": {"properties": {}}},
    )

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    blocks = skill_resolver.resolve_skill_prompts(
        ["dynamic-image"], "alice",
        conversation_id="conv1", agent_name="assistant")

    rendered = "\n".join(blocks)
    assert "agent=assistant" in rendered
    assert "relay=fs_main" in rendered
    assert "image=codex_image_service" in rendered
    assert "tool=generate_image" in rendered


def test_static_skill_prompt_still_uses_param_substitution(monkeypatch):
    from core import skill_resolver

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            return {
                "description": "Static skill",
                "prompt": "Use ${tool_name} carefully.",
                "parameters": {"tool_name": {"default": "read"}},
            }

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    blocks = skill_resolver.resolve_skill_prompts(
        [{"name": "static", "params": {"tool_name": "generate_image"}}],
        "alice")

    assert "Use generate_image carefully." in blocks[0]


def test_skill_manifest_advertises_without_prompt_body(monkeypatch):
    from core import skill_resolver

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            return {
                "description": "Static skill",
                "prompt": "SECRET FULL PROMPT BODY",
            }

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    lines = skill_resolver.resolve_skill_manifests(["static"], "alice")

    assert "Static skill" in lines[0]
    assert "load_skill" in lines[0]
    assert "SECRET FULL PROMPT BODY" not in lines[0]


def test_skill_manifest_resolves_conversation_scoped_skill(monkeypatch):
    from core import skill_resolver

    calls = []

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            calls.append((rtype, name, user_id, conversation_id))
            if conversation_id == "conv1":
                return {"description": "Conversation skill", "prompt": "secret"}
            return None

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    lines = skill_resolver.resolve_skill_manifests(
        ["conv-skill"], "alice", conversation_id="conv1")

    assert "Conversation skill" in lines[0]
    assert calls == [("skill", "conv-skill", "alice", "conv1")]


def test_load_skill_only_returns_assigned_skill(monkeypatch):
    from core.handlers.skills import LoadSkillHandler

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            if rtype == "agent":
                return {
                    "assigned_skills": [
                        {"name": "static", "params": {"tool_name": "bash"}},
                    ],
                }
            if rtype == "skill" and name == "static":
                return {
                    "description": "Static skill",
                    "prompt": "Use ${tool_name} carefully.",
                    "parameters": {"tool_name": {"default": "read"}},
                }
            return None

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    handler = LoadSkillHandler()
    handler.set_user_id("alice")
    handler.set_agent_name("assistant")

    loaded = handler.execute({"name": "static"})
    denied = handler.execute({"name": "other"})

    assert "## Skill: static" in loaded
    assert "Use bash carefully." in loaded
    assert "not assigned" in denied


def test_default_media_service_uses_agent_preference(monkeypatch):
    from core import skill_resolver

    ctx = skill_resolver._PawFlowTemplateContext(
        "alice", "conv1", "artist")
    monkeypatch.setattr(
        ctx,
        "media_services",
        lambda kind="": [
            {"id": "pixazo_image_service"},
            {"id": "codex_image_service"},
        ],
    )

    class Store:
        def get_extra(self, cid, key):
            assert cid == "conv1"
            assert key == "image_services"
            return {"artist": "codex_image_service"}

    from core.conversation_store import ConversationStore
    monkeypatch.setattr(ConversationStore, "instance", staticmethod(lambda: Store()))

    assert ctx.default_media_service("image")["id"] == "codex_image_service"


def test_builtin_image_generation_specialist_declares_jinja_template():
    from core.resource_store import ResourceStore

    skill = ResourceStore.instance().get_any(
        "skill", "image-generation-specialist", "__global__")

    assert skill
    assert skill.get("template_engine") == "jinja"
    assert "pawflow.media_services('image')" in skill.get("prompt", "")
    assert "service" in skill.get("prompt", "")
