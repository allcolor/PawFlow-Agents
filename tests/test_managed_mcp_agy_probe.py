"""Managed MCP: the ``agy_mcp`` WP0 probe record and its gate verdict.

The gate flips to available from an observed Stop payload or from Google's
published hook contract (https://antigravity.google/docs/hooks): the event,
its camelCase stdin fields and the ``transcriptPath`` to the persistent
transcript are documented there. A protobuf identifier or a changelog line
alone never suffices. These tests pin the verdict to the recorded evidence.
"""
import json
from pathlib import Path

from core.llm_providers._managed_mcp_agy_probe import (
    HOOK_IDENTIFIERS,
    evaluate_probe,
    parse_identifier_counts,
    probe_commands,
)
from core.managed_mcp_spec import managed_mcp_spec

FIXTURE = Path(__file__).parent / "fixtures" / "agy_managed_hook_probe.json"


def _record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_recorded_contract_enables_agy_mcp_from_the_transcript():
    record = _record()
    verdict = evaluate_probe(record)
    assert record["hook_payloads"] == {}
    assert record["documentation"]["url"].startswith("https://antigravity.google/")
    assert verdict["evidence_kind"] == "documented"
    assert verdict["final_source"] == "transcript"
    assert verdict["agy_mcp_available"] is True
    assert verdict["missing"] == []
    # The spec table and the evidence agree.
    spec = managed_mcp_spec("agy_mcp")
    assert spec.available is True
    assert spec.unavailable_reason == ""


def test_documented_stop_payload_has_no_final_text_field():
    """finalModelOutput is a protobuf declaration, not a documented hook key."""
    record = _record()
    assert "finalModelOutput" not in record["documentation"]["stop_input_fields"]
    assert record["documentation"]["documented_final_text_field"] == ""
    assert record["final_field"] == ""
    assert {"executionNum", "terminationReason", "fullyIdle", "transcriptPath"} <= set(
        record["documentation"]["stop_input_fields"])
    assert evaluate_probe(record)["schema_declares_final_field"] is False


def test_recorded_binary_evidence():
    record = _record()
    verdict = evaluate_probe(record)
    assert record["agy_version"] == "1.1.26"
    assert verdict["has_inject_steps"] is True
    assert verdict["has_transcript_path"] is True
    assert verdict["has_user_prompt_submit"] is False
    assert verdict["has_final_field_identifier"] is True
    assert "finalModelOutput" in record["stop_hook_schema_fields"]
    assert {"PreInvocation", "PostInvocation", "SessionStart", "SessionEnd",
            "PreToolUse", "PostToolUse"} <= set(verdict["hooks_in_binary"])


def test_schema_and_changelog_alone_never_enable_the_provider():
    record = _record()
    schema_only = dict(record)
    schema_only["documentation"] = {}
    assert schema_only["stop_hook_runs"] is True
    assert schema_only["stop_hook_schema_fields"]
    verdict = evaluate_probe(schema_only)
    assert verdict["evidence_kind"] == "schema_only"
    assert verdict["checks"]["stop_hook_fired"] is False
    assert verdict["agy_mcp_available"] is False
    nothing = dict(schema_only)
    nothing["stop_hook_schema_fields"] = []
    nothing["stop_hook_runs"] = False
    assert evaluate_probe(nothing)["evidence_kind"] == "none"


def test_documentation_without_transcript_or_final_field_is_not_enough():
    record = _record()
    weak = dict(record)
    weak["documentation"] = dict(record["documentation"], stop_input_fields=[
        "executionNum", "terminationReason"])
    verdict = evaluate_probe(weak)
    assert verdict["evidence_kind"] == "documented"
    assert verdict["missing"] == ["final_source_proven"]


def test_observed_payload_upgrades_the_evidence():
    record = _record()
    observed = dict(record)
    observed["hook_payloads"] = {"Stop": [
        "executionNum", "terminationReason", "fullyIdle", "transcriptPath"]}
    verdict = evaluate_probe(observed)
    assert verdict["evidence_kind"] == "observed"
    assert verdict["final_source"] == "transcript"
    assert verdict["agy_mcp_available"] is True
    with_field = dict(observed)
    with_field["hook_payloads"] = {"Stop": ["executionNum", "finalModelOutput"]}
    with_field["final_field"] = "finalModelOutput"
    assert evaluate_probe(with_field)["final_source"] == "hook_field"
    no_liveness = dict(observed)
    no_liveness["proxy_independent_liveness"] = False
    assert evaluate_probe(no_liveness)["missing"] == ["proxy_independent_liveness"]


def test_identifier_parser_and_commands():
    counts = parse_identifier_counts("     98 PostInvocation\n 14 injectSteps\n")
    assert counts == {"PostInvocation": 98, "injectSteps": 14}
    commands = probe_commands("pawflow-claude-code:latest")
    assert commands["version"][-1] == "--version"
    assert all(identifier in commands["identifiers"][-1]
               for identifier in ("PreInvocation", "injectSteps",
                                   "finalModelOutput"))
    assert set(HOOK_IDENTIFIERS) >= {
        "PreInvocation", "transcriptPath", "finalModelOutput"}
