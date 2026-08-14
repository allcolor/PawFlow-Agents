# Agent Instructions

This file complements `CLAUDE.md` (development conventions). Read both.

## Releases

Before preparing or executing any release, read the project wiki page
`complete-release-procedure` (`project_wiki(action='page', slug='complete-release-procedure')`).
It is the authoritative release circuit. Two rules from it are non-negotiable:

- A `Release 1.0.0-beta.N` commit contains **only** release metadata
  (`pyproject.toml`, `CHANGELOG.md`, `PROJECT_SUMMARY.md`, version-string
  references). All feature/fix/test/doc content lands in its own dedicated
  commits pushed before the release commit — never bundled into the release
  commit.
- Tag only a SHA whose full branch CI matrix is green; never push tag and
  release commit together.
