"""Unit tests for deterministic Media Studio capability routing."""

import pytest

from core.media_studio import (
    CreativeBrief,
    MediaCapability,
    MediaCapabilityCatalog,
    MediaIntent,
    MediaProductionProposal,
    MediaReference,
    MediaSelectionPreferences,
    MediaSelectionRequest,
)


def capability(capability_id, *, engine="comfyui", local=True,
               operations=("image_to_video",), roles=("source_image",),
               cost=0.0, tags=(), available=True, unavailable_reason="",
               max_duration=10, model="", preset=""):
    locality = ("local",) if local else ("remote",)
    return MediaCapability(
        capability_id=capability_id,
        engine=engine,
        service_id=f"service-{capability_id}",
        service_revision="revision-1",
        scope="user",
        media_kinds=("video",),
        operations=operations,
        accepted_reference_roles=roles,
        output_content_types=("video/mp4",),
        tags=locality + tuple(tags),
        preset_id=preset,
        model=model,
        estimated_cost_usd=cost,
        max_duration_seconds=max_duration,
        max_width=1920,
        max_height=1080,
        available=available,
        unavailable_reason=unavailable_reason,
    )


def request(**changes):
    values = {
        "media_kind": "video",
        "operation": "image_to_video",
        "required_reference_roles": ("source_image",),
        "output_content_type": "video/mp4",
        "duration_seconds": 6,
        "width": 1280,
        "height": 720,
    }
    values.update(changes)
    return MediaSelectionRequest(**values)


def test_capability_contract_rejects_incomplete_and_inconsistent_entries():
    with pytest.raises(ValueError, match="service_id is required"):
        MediaCapability(
            capability_id="broken",
            engine="comfyui",
            service_id="",
            service_revision="revision-1",
            scope="user",
            media_kinds=("video",),
            operations=("generate",),
            output_content_types=("video/mp4",),
        )

    with pytest.raises(ValueError, match="unavailable_reason"):
        capability("broken", available=False)


def test_catalog_filters_hard_constraints_and_returns_reason_codes():
    catalog = MediaCapabilityCatalog([
        capability("short", max_duration=4),
        capability("remote", local=False, cost=0.4),
        capability("local", model="wan-2.2"),
    ])

    result = catalog.select(
        request(),
        MediaSelectionPreferences(
            question_mode="automatic",
            local_preference="local",
            max_cost_usd=0.2,
        ),
    )

    assert result.outcome == "selected"
    assert result.selected.capability_id == "local"
    assert "local_preferred" in result.reason_codes
    assert {
        item.capability_id: item.reason_code for item in result.rejected
    } == {
        "remote": "cost_limit_exceeded",
        "short": "duration_limit_exceeded",
    }


def test_exact_model_is_a_hard_requirement():
    catalog = MediaCapabilityCatalog([
        capability("wan", model="wan-2.2"),
        capability("kling", engine="partner", local=False, model="kling-3"),
    ])

    result = catalog.select(
        request(),
        MediaSelectionPreferences(
            question_mode="automatic",
            model="kling-3",
        ),
    )

    assert result.selected.capability_id == "kling"
    assert "requested_model" in result.reason_codes
    assert result.rejected[0].reason_code == "model_mismatch"


def test_meaningful_local_quality_tradeoff_requests_user_choice():
    catalog = MediaCapabilityCatalog([
        capability("local-fast", tags=("fast",)),
        capability(
            "remote-quality",
            engine="partner",
            local=False,
            cost=0.35,
            tags=("high_quality",),
        ),
    ])

    result = catalog.select(request())

    assert result.outcome == "user_choice"
    assert result.selected is None
    assert result.requires_user_choice is True
    assert {item.capability_id for item in result.alternatives} == {
        "local-fast", "remote-quality"
    }
    assert result.reason_codes == ("material_tradeoff",)


def test_explicit_preference_resolves_tradeoff_without_question():
    catalog = MediaCapabilityCatalog([
        capability("local-fast", tags=("fast",)),
        capability(
            "remote-quality",
            engine="partner",
            local=False,
            cost=0.35,
            tags=("high_quality",),
        ),
    ])

    result = catalog.select(
        request(),
        MediaSelectionPreferences(local_preference="local"),
    )

    assert result.outcome == "selected"
    assert result.selected.capability_id == "local-fast"
    assert result.requires_user_choice is False


def test_unavailable_result_explains_every_rejection():
    catalog = MediaCapabilityCatalog([
        capability("offline", available=False, unavailable_reason="not ready"),
        capability("wrong-operation", operations=("generate",)),
        capability("wrong-reference", roles=("style_reference",)),
    ])

    result = catalog.select(request())

    assert result.outcome == "unavailable"
    assert result.reason_codes == ("no_compatible_capability",)
    assert {
        item.capability_id: item.reason_code for item in result.rejected
    } == {
        "offline": "unavailable",
        "wrong-operation": "operation_mismatch",
        "wrong-reference": "reference_role_mismatch",
    }


def test_catalog_snapshot_requires_unique_capability_ids():
    duplicate = capability("same")
    with pytest.raises(ValueError, match="capability_id must be unique"):
        MediaCapabilityCatalog([duplicate, duplicate])


def test_versioned_contracts_create_uuid_timestamp_and_preserve_prompts():
    intent = MediaIntent.create(
        kind="video",
        operation="image_to_video",
        confidence=0.95,
        explanation="The request asks to animate an image.",
        requires_references=True,
        requires_scenario=False,
    )
    reference = MediaReference.create(
        role="source_image",
        file_id="file-1",
        filename="source.png",
        content_type="image/png",
        source_message_id="message-1",
    )
    brief = CreativeBrief.create(
        media_kind="video",
        operation="image_to_video",
        objective="Animate the source portrait.",
        prompt_original="Make this portrait blink.",
        prompt_refined="Portrait subject blinks naturally, locked camera.",
        references=(reference,),
        assumptions=("Preserve subject identity.",),
    )

    assert intent.intent_id.startswith("intent_")
    assert "+00:00" in intent.created_at
    assert brief.prompt_original == "Make this portrait blink."
    assert brief.to_dict()["references"][0]["role"] == "source_image"


def test_exact_prompt_brief_cannot_silently_refine_user_text():
    with pytest.raises(ValueError, match="exact_prompt"):
        CreativeBrief.create(
            media_kind="image",
            operation="generate",
            objective="Create an image.",
            prompt_original="exact text",
            prompt_refined="changed text",
            exact_prompt=True,
        )


def test_production_proposal_digest_detects_stale_or_modified_content():
    proposal = MediaProductionProposal.create(
        project_id="media-project-1",
        parent_revision_id="",
        title="Short teaser",
        creative_direction="One continuous atmospheric shot.",
        shots=({"id": "shot-1", "duration_seconds": 5},),
        approvals=("produce",),
    )

    assert len(proposal.digest) == 64
    with pytest.raises(ValueError, match="digest"):
        MediaProductionProposal(
            **{**proposal.__dict__, "digest": "0" * 64}
        )
