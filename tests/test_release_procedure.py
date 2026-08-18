"""Release-procedure invariants that protect published tag names."""

from pathlib import Path


def test_release_tag_explicitly_forbids_the_v_prefix():
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "A leading `v` is forbidden" in agents
    assert "never create or push `v1.0.0-beta.N`" in agents
    assert "The tag **must not** have a leading `v`" in contributing
    assert "`v1.0.0-beta.<N>` is forbidden" in contributing
    assert "git tag 1.0.0-beta.N" in contributing
    assert "git push origin 1.0.0-beta.N" in contributing
    assert "git tag v1.0.0-beta" not in contributing
    assert "git push origin v1.0.0-beta" not in contributing


def test_release_workflow_marks_semver_suffixes_as_prereleases():
    workflow = Path(".github/workflows/release-assets.yml").read_text(
        encoding="utf-8"
    )

    assert "prerelease: ${{ contains(env.RELEASE_TAG, '-') }}" in workflow
