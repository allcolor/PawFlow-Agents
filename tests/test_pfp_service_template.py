"""PFP service templates: installable presets for the existing service form."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import pfp_package
from core.flowfile import FlowFile


def _write_template_package(root: Path, keypair, *, template_data=None):
    pkg = root / "examples.service-templates.pfpdir"
    template_dir = pkg / "content" / "service-templates"
    template_dir.mkdir(parents=True)
    data = template_data if template_data is not None else {
        "format": "pawflow.service-template.v1",
        "title": "Synthetic service template fixture",
        "description": "Test-only preset values.",
        "category": "Test",
        "tags": ["fixture"],
        "service_type": "llmConnection",
        "service_description": "Synthetic test service",
        "config": {
            "provider": "__test_provider__",
        },
    }
    (template_dir / "fixture-preset.json").write_text(
        json.dumps(data), encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": "examples.service-templates",
        "version": "1.0.0",
        "description": "Service template fixture",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [{
            "id": "service_template:fixture-preset",
            "type": "service_template",
            "name": "fixture-preset",
            "path": "content/service-templates/fixture-preset.json",
        }],
    }
    (pkg / "pfp.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


@pytest.fixture(autouse=True)
def _reset_stores(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore
    from core.service_registry import ServiceRegistry

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    ResourceStore.reset()
    ServiceRegistry.reset()


@pytest.fixture(autouse=True)
def _mock_llm_review(monkeypatch):
    import core.package_review as package_review

    class _ReviewLLM:
        def complete(self, **kwargs):
            class _Response:
                content = json.dumps({
                    "risk": "low",
                    "allowed": True,
                    "requires_human_review": False,
                    "findings": [],
                    "sanitized_summary": "ok",
                    "recommended_changes": [],
                })
            return _Response()

    monkeypatch.setattr(
        package_review, "_resolve_review_llm",
        lambda user_id, conversation_id: (_ReviewLLM(), None, "review_llm"))


@pytest.fixture
def keypair():
    return pfp_package.create_signing_key()


def _build(root: Path, keypair, *, template_data=None):
    pkg = _write_template_package(root, keypair, template_data=template_data)
    return pfp_package.build_pfp(
        str(pkg), private_key=keypair["private_key"])


def test_service_template_is_an_installable_resource_type():
    assert pfp_package._RESOURCE_TYPES["service_template"] == "service_template"
    assert "service_template" in pfp_package._INSTALLABLE_TYPES


@pytest.mark.parametrize("template_data,reason", [
    ({"format": "pawflow.service-template.v1", "config": {}}, "service_type"),
    ({
        "format": "pawflow.service-template.v1",
        "service_type": "llmConnection",
        "config": [],
    }, "config"),
    ({
        "format": "wrong",
        "service_type": "llmConnection",
        "config": {},
    }, "format"),
])
def test_invalid_service_template_is_blocked(tmp_path, keypair,
                                             template_data, reason):
    built = _build(tmp_path, keypair, template_data=template_data)
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = plan["objects"][0]

    assert row["status"] == "blocked"
    assert reason in row["reason"]


def test_install_and_uninstall_template_never_creates_a_service(tmp_path, keypair):
    built = _build(tmp_path, keypair)
    result = pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["service_template:fixture-preset"])

    assert result["ok"] is True
    from core.resource_store import ResourceStore
    from core.service_registry import ServiceRegistry
    stored = ResourceStore.instance().get(
        "service_template", "fixture-preset", "alice")
    assert stored["service_type"] == "llmConnection"
    assert stored["config"]["provider"] == "__test_provider__"
    assert stored["installed_from"]["package"] == "examples.service-templates"
    assert ServiceRegistry.get_instance().get_all("user", "alice") == {}

    removed = pfp_package.uninstall_pfp(
        "examples.service-templates", user_id="alice", scope="user")
    assert removed["ok"] is True
    assert ResourceStore.instance().get(
        "service_template", "fixture-preset", "alice") is None


def test_list_service_templates_returns_installed_scoped_catalog():
    from core.resource_store import ResourceStore
    from tasks.ai.actions.service_flow import _handle_service_flow

    ResourceStore.instance().create("service_template", "fixture-preset", "alice", {
        "format": "pawflow.service-template.v1",
        "title": "Synthetic service template fixture",
        "category": "Test",
        "tags": ["fixture"],
        "service_type": "llmConnection",
        "config": {"provider": "__test_provider__"},
    })
    ff = FlowFile(content=b"")
    result = _handle_service_flow(
        None, "list_service_templates", {}, None, "alice", ff)
    payload = json.loads(result[0].get_content())

    assert payload["service_templates"][0]["name"] == "fixture-preset"
    assert payload["service_templates"][0]["_scope"] == "user"
    assert payload["service_templates"][0]["config"] == {
        "provider": "__test_provider__",
    }


def test_chat_ui_selects_template_then_prefills_existing_service_form():
    js = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "tasks/io/chat_ui/resources_service_templates.js",
            "tasks/io/chat_ui/resources_service_login.js",
        )
    )

    assert "function showServiceTemplatePicker" in js
    assert "list_service_templates" in js
    assert "async function showServiceInstallForm(template)" in js
    assert "_applyServiceTemplateValues" in js
    assert "action$('service_install', _instPayload)" in js
    assert "service_install_from_template" not in js
