"""tools/gauge_probe.py -- the diagnostic that settles gauge-vs-compaction gaps.

The probe exists because reading the two counters and reasoning about them is
not enough: they disagree for a legitimate reason (the cold-CLI bootstrap
boundary) and for illegitimate ones, and only measurement separates the two.
Its one load-bearing number is UNEXPLAINED, which must be 0.
"""
import json
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "tools" / "gauge_probe.py"


_BOOTSTRAP = "/cc_sessions/c/a/.pawflow_cci/initial_context.md"


def _rows(with_boundary):
    """A conversation whose native read either is, or is not, a bootstrap read.

    Laid out the way a turn produces it: the marker rides on the assistant
    call, the body flag on the linked result.
    """
    rows = [
        {"role": "user", "msg_id": "m1",
         "content": "a user message long enough to weigh something"},
        {"role": "assistant", "msg_id": "m2", "content": "",
         "tool_calls": [{"id": "t1", "name": "read",
                         "arguments": {"path": "/x/y.py"},
                         "tool_origin": "native"}]},
        {"role": "tool", "msg_id": "m3", "tool_call_id": "t1",
         "content": "the bootstrap file, read back into the provider window"},
        {"role": "assistant", "msg_id": "m4",
         "content": "an answer produced after the bootstrap"},
    ]
    if with_boundary:
        rows[1]["tool_calls"][0]["arguments"] = {"path": _BOOTSTRAP}
        rows[1]["source"] = {"context_usage_boundary": "cli_bootstrap_read"}
        rows[2]["source"] = {"context_usage_bootstrap_body": True}
    return rows


def _write_conversation(root, rows, agent="claude"):
    seg = root / agent / "context"
    seg.mkdir(parents=True)
    seg.joinpath("000000.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    seg.joinpath("index.json").write_text(
        json.dumps({"total_rows": len(rows)}), encoding="utf-8")
    return root


def _run(*args):
    proc = subprocess.run(
        [sys.executable, str(PROBE)] + [str(a) for a in args],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _field(out, label):
    for line in out.splitlines():
        if line.startswith(label):
            return line.split(":", 1)[1].strip()
    raise AssertionError("%r not in probe output:\n%s" % (label, out))


def test_bootstrap_body_difference_is_fully_explained(tmp_path):
    out = _run(_write_conversation(tmp_path, _rows(True)), "claude")

    assert _field(out, "UNEXPLAINED").startswith("0")
    assert _field(out, "bootstrap body messages").startswith("[2]")
    # The gauge drops the read body, so it must read lower.
    gauge = int(_field(out, "gauge   (no bootstrap bodies)"))
    compact = int(_field(out, "compact (all content)"))
    assert 0 < gauge < compact


def test_without_a_bootstrap_read_the_two_counters_agree_exactly(tmp_path):
    out = _run(_write_conversation(tmp_path, _rows(False)), "claude")

    assert _field(out, "UNEXPLAINED").startswith("0")
    assert _field(out, "bootstrap body messages").startswith("none")
    assert _field(out, "all structural markers").startswith("none")
    assert (_field(out, "gauge   (no bootstrap bodies)")
            == _field(out, "compact (all content)"))


def test_bodies_are_found_structurally_not_by_text_match(tmp_path):
    # A message that merely quotes the marker string is not a bootstrap body.
    # The text match is what a grep gives, and it is wrong here: reading this
    # repository's own source into a conversation makes every such line look
    # like one and hides what the gauge really drops.
    rows = _rows(True)
    rows.append({
        "role": "tool", "msg_id": "m5", "tool_call_id": "t2",
        "content": 'src: context_usage_bootstrap_body == True',
    })
    out = _run(_write_conversation(tmp_path, rows), "claude")

    assert _field(out, "all structural markers").startswith("[1]")
    assert _field(out, "bootstrap body messages").startswith("[2]")
    assert _field(out, "UNEXPLAINED").startswith("0")


def test_a_bare_jsonl_path_is_accepted(tmp_path):
    # How a version recovered from the conversation's git history is inspected.
    root = _write_conversation(tmp_path, _rows(True))
    out = _run(root / "claude" / "context" / "000000.jsonl")

    assert _field(out, "messages").startswith("4")
    assert _field(out, "UNEXPLAINED").startswith("0")
