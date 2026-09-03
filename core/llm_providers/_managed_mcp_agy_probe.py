"""WP0 probe for ``agy_mcp``: does the official agy build expose a final hook?

The managed MCP plan enables ``agy_mcp`` only after the supported Antigravity
CLI proves, on a real build, (1) which lifecycle hooks it fires, (2) their
payload fields, (3) a transcript path, and (4) a reliable final-answer source.
This module records that probe as data plus a deterministic verdict, so CI
can re-run it against the pinned image and the provider flips to available
only when the evidence says so -- never from the UI.

Run the commands manually or in a development gate::

    python -m core.llm_providers._managed_mcp_agy_probe --image pawflow-claude-code:latest

The verdict is computed from ``evaluate_probe`` on the collected evidence; the
last recorded run lives in ``tests/fixtures/agy_managed_hook_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 - the probe runs the pinned CLI image on purpose.
import sys
from collections.abc import Iterable

#: Identifiers whose presence in the agy binary is evidence of hook support.
HOOK_IDENTIFIERS = (
    "PreInvocation", "PostInvocation", "UserPromptSubmit", "SessionStart",
    "SessionEnd", "PreToolUse", "PostToolUse", "injectSteps",
    "transcriptPath", "hookEventName", "hookSpecificOutput",
    "lastAssistantMessage", "last_assistant_message", "additionalContext",
    "stopReason", "hooks.json",
)

#: What ``agy_mcp`` needs before it can claim a native final answer.
REQUIRED_FINAL_EVIDENCE = (
    # A hook that fires when the turn is over, with a payload we have seen.
    "stop_hook_fired",
    # Either a final-text field in that payload or a transcript path whose
    # last assistant message is the answer.
    "final_source_proven",
    # Liveness that does not depend on the observer proxy log.
    "proxy_independent_liveness",
)


def probe_commands(image: str) -> dict[str, list]:
    """The exact commands the probe runs, for the record and for CI."""
    grep_pattern = "|".join(re.escape(item) for item in HOOK_IDENTIFIERS)
    return {
        "version": ["docker", "run", "--rm", "--entrypoint", "agy", image,
                    "--version"],
        "help": ["docker", "run", "--rm", "--entrypoint", "agy", image,
                 "--help"],
        "identifiers": [
            "docker", "run", "--rm", "--entrypoint", "bash", image, "-lc",
            "grep -a -o -E '" + grep_pattern + "' \"$(command -v agy)\" "
            "| sort | uniq -c | sort -rn"],
        "changelog_hooks": [
            "docker", "run", "--rm", "--entrypoint", "bash", image, "-lc",
            "agy changelog 2>/dev/null | grep -n -i 'hook'"],
        # Requires a logged-in agy home mounted at /tmp/agyhome to fire any
        # hook: print mode authenticates before it evaluates hooks.
        "hooks_listing": [
            "docker", "run", "--rm", "-e", "HOME=/tmp/agyhome",
            "--entrypoint", "agy", image, "-p", "/hooks",
            "--output-format", "json"],
    }


def parse_identifier_counts(text: str) -> dict[str, int]:
    """Parse ``uniq -c`` output into ``{identifier: count}``."""
    counts: dict[str, int] = {}
    for line in (text or "").splitlines():
        match = re.match(r"\s*(\d+)\s+(\S+)\s*$", line)
        if not match:
            continue
        counts[match.group(2)] = int(match.group(1))
    return counts


def evaluate_probe(evidence: dict) -> dict:
    """Turn collected evidence into the gate verdict.

    ``evidence`` carries ``identifier_counts`` (from the binary), the observed
    hook payloads (``hook_payloads``: event name -> list of field names), an
    optional ``final_field`` observed on the Stop payload, ``transcript_final``
    (whether the transcript's last assistant message matched the visible
    answer) and ``proxy_independent_liveness``.
    """
    counts = dict(evidence.get("identifier_counts") or {})
    payloads = dict(evidence.get("hook_payloads") or {})
    hooks_in_binary = sorted(
        name for name in HOOK_IDENTIFIERS
        if counts.get(name, 0) > 0 and name[0].isupper() and "." not in name)
    stop_fields = list(payloads.get("Stop") or [])
    stop_hook_fired = bool(stop_fields)
    final_field = str(evidence.get("final_field") or "")
    transcript_final = bool(evidence.get("transcript_final"))
    final_source_proven = bool(
        stop_hook_fired and (final_field in stop_fields or transcript_final))
    liveness = bool(evidence.get("proxy_independent_liveness"))
    checks = {
        "stop_hook_fired": stop_hook_fired,
        "final_source_proven": final_source_proven,
        "proxy_independent_liveness": liveness,
    }
    missing = [name for name in REQUIRED_FINAL_EVIDENCE if not checks[name]]
    return {
        "hooks_in_binary": hooks_in_binary,
        "has_user_prompt_submit": counts.get("UserPromptSubmit", 0) > 0,
        "has_inject_steps": counts.get("injectSteps", 0) > 0,
        "has_transcript_path": counts.get("transcriptPath", 0) > 0,
        "has_final_field_identifier": any(
            counts.get(name, 0) > 0 for name in (
                "lastAssistantMessage", "last_assistant_message")),
        "checks": checks,
        "missing": missing,
        "agy_mcp_available": not missing,
    }


def run_probe(image: str, commands: Iterable[str] = ()) -> dict:
    """Execute the read-only probe commands and collect their output."""
    wanted = set(commands) or None
    outputs: dict[str, dict] = {}
    for name, argv in probe_commands(image).items():
        if wanted is not None and name not in wanted:
            continue
        try:
            result = subprocess.run(  # nosec B603
                argv, capture_output=True, text=True, timeout=180,
                check=False)
            outputs[name] = {
                "argv": argv, "returncode": result.returncode,
                "stdout": result.stdout[-8000:],
                "stderr": result.stderr[-4000:],
            }
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            outputs[name] = {"argv": argv, "error": str(exc)}
    identifiers = parse_identifier_counts(
        (outputs.get("identifiers") or {}).get("stdout", ""))
    version = ((outputs.get("version") or {}).get("stdout", "") or "").strip()
    return {
        "image": image,
        "agy_version": version,
        "commands": outputs,
        "identifier_counts": identifiers,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="pawflow-claude-code:latest")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    evidence = run_probe(args.image)
    evidence["verdict"] = evaluate_probe(evidence)
    text = json.dumps(evidence, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0 if evidence["verdict"]["agy_mcp_available"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
