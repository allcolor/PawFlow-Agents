"""Service-registry normalization for immutable Media Studio snapshots."""

from core import ServiceFactory
from core._service_defs import ServiceDef
from core.media_capability_discovery import snapshot_media_capabilities
from core.media_studio import (
    MediaCapabilityCatalog,
    MediaSelectionPreferences,
    MediaSelectionRequest,
)
from tasks import _register_all_services


class Registry:
    def __init__(self, definitions):
        self.definitions = definitions
        self.calls = []

    def resolve_all(self, **arguments):
        self.calls.append(arguments)
        return {item.service_id: item for item in self.definitions}


def test_snapshot_normalizes_comfy_presets_without_exposing_workflow_bodies():
    _register_all_services()
    comfy = ServiceDef(
        service_id="comfy-images",
        service_type="comfyUIImageGeneration",
        scope="user",
        scope_id="alice",
        created_at=100,
        config={
            "base_url": "relay://gpu/localhost:8188",
            "api_key": "must-not-leak",
            "workflows": {
                "generate": {
                    "workflow": {
                        "1": {
                            "class_type": "KSampler",
                            "inputs": {"seed": 1},
                        }
                    },
                    "bindings": {"seed": {"node": "1", "input": "seed"}},
                    "output": {
                        "node": "2",
                        "key": "images",
                        "index": 0,
                        "content_types": ["image/png"],
                    },
                    "metadata": {
                        "preset_id": "flux-dev-generate",
                        "revision": "1.0.0",
                        "created_at": "2026-08-25T00:00:00+00:00",
                        "media_kind": "image",
                        "provenance": {
                            "source": "reviewed workflow",
                            "license": "Apache-2.0",
                        },
                        "model": "flux-dev",
                        "capabilities": ["high_quality"],
                        "limits": {"max_width": 2048, "max_height": 2048},
                        "required_inventory": {
                            "nodes": ["KSampler"],
                            "models": ["flux-dev"],
                            "loras": [],
                            "custom_nodes": [],
                        },
                    },
                },
                "edit_image": {
                    "workflow": {"3": {"class_type": "LoadImage", "inputs": {}}},
                    "bindings": {},
                    "output": {"node": "4", "key": "images", "index": 0},
                    "metadata": {
                        "preset_id": "flux-dev-edit",
                        "revision": "1.0.0",
                        "created_at": "2026-08-25T00:00:00+00:00",
                        "media_kind": "image",
                        "provenance": {
                            "source": "reviewed workflow",
                            "license": "Apache-2.0",
                        },
                        "capabilities": [],
                        "limits": {},
                        "required_inventory": {
                            "nodes": ["LoadImage"],
                            "models": [],
                            "loras": [],
                            "custom_nodes": [],
                        },
                    },
                },
            },
        },
    )
    registry = Registry([comfy])

    snapshot = snapshot_media_capabilities(
        "alice", "conv-1", registry=registry, service_factory=ServiceFactory)

    assert registry.calls == [{
        "user_id": "alice", "conv_id": "conv-1", "enabled_only": True
    }]
    assert len(snapshot.capabilities) == 2
    generate = next(
        item for item in snapshot.capabilities
        if item.operations == ("generate",)
    )
    assert generate.engine == "comfyui"
    assert generate.model == "flux-dev"
    assert generate.tags == ("local", "private", "high_quality")
    assert generate.max_width == 2048
    assert generate.output_content_types == ("image/png",)
    edit = next(
        item for item in snapshot.capabilities
        if item.operations == ("edit_image",)
    )
    assert "source_image" in edit.accepted_reference_roles
    serialized = snapshot.to_dict()
    assert serialized["digest"] == snapshot.digest
    assert "workflow" not in str(serialized)
    assert "must-not-leak" not in str(serialized)


def test_snapshot_normalizes_comfyui_audio_preset():
    _register_all_services()
    workflow = {
        "1": {"class_type": "TextEncode", "inputs": {"text": "prompt"}},
        "2": {"class_type": "SaveAudio", "inputs": {}},
    }
    definition = ServiceDef(
        service_id="comfy-audio",
        service_type="comfyUIAudioGeneration",
        scope="user",
        scope_id="alice",
        created_at=100,
        config={
            "workflows": {
                "generate_audio": {
                    "workflow": workflow,
                    "bindings": {"prompt": {"node": "1", "input": "text"}},
                    "output": {
                        "node": "2",
                        "key": "audio",
                        "index": 0,
                        "content_types": ["audio/wav"],
                    },
                    "metadata": {
                        "preset_id": "ace-step-music",
                        "revision": "2.0.0",
                        "created_at": "2026-08-25T00:00:00+00:00",
                        "media_kind": "audio",
                        "provenance": {
                            "source": "reviewed workflow",
                            "license": "Apache-2.0",
                        },
                        "model": "ace-step",
                        "capabilities": ["music"],
                        "limits": {"max_duration_seconds": 240},
                        "required_inventory": {
                            "nodes": ["SaveAudio"],
                            "models": ["ace-step"],
                            "loras": [],
                            "custom_nodes": [],
                        },
                    },
                },
            },
        },
    )

    snapshot = snapshot_media_capabilities(
        "alice", "conv-1", registry=Registry([definition]),
        service_factory=ServiceFactory)

    assert len(snapshot.capabilities) == 1
    capability = snapshot.capabilities[0]
    assert capability.engine == "comfyui"
    assert capability.media_kinds == ("audio",)
    assert capability.operations == ("generate_audio",)
    assert capability.preset_id == "ace-step-music"
    assert capability.model == "ace-step"
    assert capability.tags == ("local", "private", "music")
    assert capability.max_duration_seconds == 240
    assert capability.output_content_types == ("audio/wav",)


def test_snapshot_normalizes_builtin_audio_tts_and_voice_services():
    _register_all_services()
    definitions = [
        ServiceDef(
            service_id="music",
            service_type="sunoAudioGeneration",
            scope="global",
            scope_id="__global__",
            created_at=101,
            config={},
        ),
        ServiceDef(
            service_id="speech",
            service_type="pocketTTS",
            scope="user",
            scope_id="alice",
            created_at=102,
            config={},
        ),
        ServiceDef(
            service_id="voice",
            service_type="luxTTS",
            scope="user",
            scope_id="alice",
            created_at=103,
            config={},
        ),
    ]

    snapshot = snapshot_media_capabilities(
        "alice",
        "conv-1",
        registry=Registry(definitions),
        service_factory=ServiceFactory,
    )
    shapes = {
        (item.service_id, item.media_kinds[0], item.operations[0])
        for item in snapshot.capabilities
    }

    assert ("music", "audio", "generate_audio") in shapes
    assert ("speech", "speech", "speak") in shapes
    assert ("voice", "voice_clone", "clone_voice") in shapes
    assert ("voice", "speech", "speak") in shapes


def test_unknown_service_types_are_ignored_without_live_connection():
    class Factory:
        @staticmethod
        def get(_service_type):
            raise KeyError("not registered")

    snapshot = snapshot_media_capabilities(
        "alice",
        "conv-1",
        registry=Registry([
            ServiceDef(
                service_id="unknown",
                service_type="futureProvider",
                scope="conv",
                scope_id="conv-1",
                created_at=104,
                config={},
            )
        ]),
        service_factory=Factory,
    )

    assert snapshot.capabilities == ()


def test_unknown_cost_fails_closed_when_user_sets_a_budget():
    _register_all_services()
    snapshot = snapshot_media_capabilities(
        "alice",
        "conv-1",
        registry=Registry([
            ServiceDef(
                service_id="music",
                service_type="sunoAudioGeneration",
                scope="global",
                scope_id="__global__",
                created_at=105,
                config={},
            )
        ]),
        service_factory=ServiceFactory,
    )
    result = MediaCapabilityCatalog(snapshot.capabilities).select(
        MediaSelectionRequest(
            media_kind="audio",
            operation="generate_audio",
            output_content_type="audio/mpeg",
        ),
        MediaSelectionPreferences(max_cost_usd=1),
    )

    assert result.outcome == "unavailable"
    assert result.rejected[0].reason_code == "cost_unknown"


def test_service_definition_revision_changes_snapshot_capability_identity():
    _register_all_services()
    first = ServiceDef(
        service_id="music",
        service_type="sunoAudioGeneration",
        scope="global",
        scope_id="__global__",
        created_at=106,
        config={"model": "v1"},
    )
    second = ServiceDef(
        service_id="music",
        service_type="sunoAudioGeneration",
        scope="global",
        scope_id="__global__",
        created_at=106,
        config={"model": "v2"},
    )

    first_snapshot = snapshot_media_capabilities(
        "alice", "conv-1", registry=Registry([first]),
        service_factory=ServiceFactory)
    second_snapshot = snapshot_media_capabilities(
        "alice", "conv-1", registry=Registry([second]),
        service_factory=ServiceFactory)

    assert (
        first_snapshot.capabilities[0].service_revision
        != second_snapshot.capabilities[0].service_revision
    )
