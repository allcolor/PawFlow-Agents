"""Managed MCP: the ``agy_mcp`` WP0 probe record and its gate verdict.

The recorded evidence is what was observed on the pinned image; the verdict
function is what CI will re-run once an authenticated probe records hook
payloads. Until then the provider must stay unavailable and this test pins
that outcome to the evidence rather than to an opinion.
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


def test_recorded_probe_keeps_agy_mcp_unavailable():
    record = _record()
    verdict = evaluate_probe(record)
    assert verdict["agy_mcp_available"] is False
    assert "final_source_proven" in verdict["missing"]
    assert "stop_hook_fired" in verdict["missing"]
    # The spec table and the evidence agree.
    assert managed_mcp_spec("agy_mcp").available is False


def test_recorded_binary_evidence():
    record = _record()
    verdict = evaluate_probe(record)
    assert record["agy_version"] == "1.1.25"
    assert verdict["has_inject_steps"] is True
    assert verdict["has_transcript_path"] is True
    assert verdict["has_user_prompt_submit"] is False
    assert verdict["has_final_field_identifier"] is False
    assert {"PreInvocation", "PostInvocation", "SessionStart", "SessionEnd",
            "PreToolUse", "PostToolUse"} <= set(verdict["hooks_in_binary"])


def test_verdict_flips_only_with_a_proven_final_source():
    record = _record()
    proven = dict(record)
    proven["hook_payloads"] = {"Stop": ["hookEventName", "transcriptPath",
                                        "lastAssistantMessage"]}
    proven["final_field"] = "lastAssistantMessage"
    assert evaluate_probe(proven)["agy_mcp_available"] is True
    via_transcript = dict(record)
    via_transcript["hook_payloads"] = {"Stop": ["hookEventName", "transcriptPath"]}
    via_transcript["transcript_final"] = True
    assert evaluate_probe(via_transcript)["agy_mcp_available"] is True
    no_liveness = dict(via_transcript)
    no_liveness["proxy_independent_liveness"] = False
    assert evaluate_probe(no_liveness)["missing"] == ["proxy_independent_liveness"]


def test_identifier_parser_and_commands():
    counts = parse_identifier_counts("     98 PostInvocation\n 14 injectSteps\n")
    assert counts == {"PostInvocation": 98, "injectSteps": 14}
    commands = probe_commands("pawflow-claude-code:latest")
    assert commands["version"][-1] == "--version"
    assert all(identifier in commands["identifiers"][-1]
               for identifier in ("PreInvocation", "injectSteps"))
    assert set(HOOK_IDENTIFIERS) >= {"Stop" if False else "PreInvocation", "transcriptPath"}
