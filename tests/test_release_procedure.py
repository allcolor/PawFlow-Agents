"""Release-procedure invariants that protect published tag names."""

from pathlib import Path


def test_release_tag_explicitly_forbids_the_v_prefix():
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")
    procedure = Path("docs/RELEASE_PROCEDURE.md").read_text(encoding="utf-8")

    assert "A leading `v` is forbidden" in agents
    assert "never create or push `v1.0.0-beta.N`" in agents
    assert "without a leading `v`" in contributing
    assert "A leading `v` is" in procedure
    assert "forbidden" in procedure
    assert "git tag 1.0.0-beta.N" in procedure
    assert "git push origin 1.0.0-beta.N" in procedure
    assert "git tag v1.0.0-beta" not in procedure
    assert "git push origin v1.0.0-beta" not in procedure
