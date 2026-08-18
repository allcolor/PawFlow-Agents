# Complete release procedure

This is the blocking release circuit for PawFlow. A release is complete only
after local validation, branch CI, tag-triggered publication workflows, and the
published release have all been verified.

This document is the version-controlled source of truth. The project wiki page
`complete-release-procedure` must mirror it and cite this file.

## Non-negotiable invariants

- Work on `main`; never release from an unmerged branch or a dirty, unexplained
  worktree.
- Feature, fix, test, and documentation changes belong in dedicated commits.
  Push them and obtain green branch CI before creating release metadata.
- A commit named `Release 1.0.0-beta.N` contains only release metadata:
  `pyproject.toml`, `CHANGELOG.md`, `PROJECT_SUMMARY.md`, and any current
  version-string references that genuinely require an update.
- Push the release commit by itself. Tag it only after the complete branch CI
  matrix is green for that exact SHA.
- Never push a release commit and its tag together.
- Use a lightweight tag named exactly `1.0.0-beta.N`. A leading `v` is
  forbidden.
- `1.0.0bN` is the PEP 440 package version; `1.0.0-beta.N` is the Git tag
  and GitHub release name.
- Beta GitHub releases are normal releases, not GitHub prereleases.
- Never amend, force-push, bypass hooks, or move/reuse a published release tag.

## 1. Establish the release candidate

Fetch the remote and inspect the complete repository state:

```bash
git fetch origin --tags
git switch main
git status --short --branch
git log --oneline --decorate -10
git diff
git diff --check
```

Confirm all of the following before proceeding:

- local `main` is based on the expected `origin/main`;
- every modified and untracked file is understood;
- no generated artifact, secret, or unrelated user change would enter a
  commit;
- the implementation, tests, documentation, and changelog notes are complete;
- the previous release tag and the next beta number are unambiguous;
- the proposed tag does not already exist locally, remotely, or as a GitHub
  release.

Useful checks, after replacing `N`:

```bash
git tag --list '1.0.0-beta.*' --sort=-version:refname
git ls-remote --tags origin 'refs/tags/1.0.0-beta.N'
gh release view 1.0.0-beta.N
```

The last two commands should report no existing tag or release. Stop on any
ambiguity instead of guessing or overwriting existing release state.

## 2. Validate and publish the functional changes

Run validation that matches the branch workflow before committing. At minimum:

```bash
python -m pytest tests/ -x -q --tb=short
python -m compileall -q core tasks services pawflow_cli
ruff check .
bandit -q -r core tasks services pawflow_cli
python -m build
```

Also run any focused suites required by the change. Verify the built wheel in a
clean virtual environment, including the `pawflow --version` and
`pawcode --version` smoke checks. For release assets, run the repository's
bundled-package and installer checks used by
`.github/workflows/release-assets.yml`.

Commit the functional change with explicit paths; do not include release
metadata merely to save a commit. Review the staged diff and commit contents:

```bash
git add <explicit paths>
git diff --cached
git commit -m '<descriptive change message>'
git show --stat --oneline HEAD
git status --short --branch
```

Push `main`, then wait for the entire branch CI run for the exact commit SHA.
The required workflow includes the Python 3.10, 3.11, 3.12, and 3.13 test
matrix, package build/install smoke checks, compilation, Ruff error checks, and
Bandit.

```bash
candidate_sha=$(git rev-parse HEAD)
git push origin main
gh run list --branch main --commit "$candidate_sha"
```

Do not prepare the release commit until every required job for
`candidate_sha` is green.

## 3. Prepare the metadata-only release commit

Replace `N` with the selected beta number and update only current release
metadata:

1. Set `project.version = "1.0.0bN"` in `pyproject.toml`.
2. Convert the current `Unreleased` notes in `CHANGELOG.md` into a dated
   `## [1.0.0-beta.N] — YYYY-MM-DD` section, grouped appropriately, and leave
   a fresh empty `Unreleased` section above it.
3. Update the current version, date, and beta label in
   `PROJECT_SUMMARY.md`.
4. Update other current version-string references only when they are genuine
   release metadata. Do not rewrite historical changelog entries.

`core.__version__` is derived from `pyproject.toml` in source checkouts and
from installed package metadata in built distributions; do not hardcode it in
`core/__init__.py`.

Review all metadata changes and verify consistency:

```bash
git diff -- pyproject.toml CHANGELOG.md PROJECT_SUMMARY.md
python cli.py --version
python -m pytest tests/test_docs_version_consistency.py -q
python -m build
```

Smoke-test the newly built wheel in a clean virtual environment. Before
committing, ensure the staged file list contains only permitted release
metadata:

```bash
git add pyproject.toml CHANGELOG.md PROJECT_SUMMARY.md
git diff --cached --name-only
git diff --cached
git commit -m 'Release 1.0.0-beta.N'
git show --name-only --format=fuller HEAD
```

If any feature, fix, test, or ordinary documentation file appears in this
commit, stop and separate it before pushing.

## 4. Push the release commit and wait for branch CI

Push `main` without creating or pushing the tag:

```bash
release_sha=$(git rev-parse HEAD)
git push origin main
gh run list --branch main --commit "$release_sha"
```

Wait for every required job in the full branch CI matrix to succeed for
`release_sha`. A green run for an earlier SHA, a partial matrix, or only local
tests is insufficient.

Reconfirm that `origin/main` points to the intended release SHA:

```bash
git fetch origin
test "$(git rev-parse origin/main)" = "$release_sha"
```

## 5. Create and push the tag separately

Only after the exact release SHA has full green branch CI, create a lightweight
tag and verify its target:

```bash
git tag 1.0.0-beta.N "$release_sha"
test "$(git rev-list -n 1 1.0.0-beta.N)" = "$release_sha"
git push origin 1.0.0-beta.N
```

Push only this tag in the final command. Never use `git push --tags`.

## 6. Verify publication

The tag starts two publication workflows:

- **Release Assets** builds and attaches the bundled packages, installer,
  PawCode, Relay CLI, Relay Desktop, VS Code extension, Android APK, and source
  archives expected by the workflow.
- **Docker Images** publishes the configured PawFlow and relay images to GHCR.

Monitor both workflows for the tag and verify that every required job
succeeds. Then verify the final public state:

- the remote tag targets `release_sha`;
- the GitHub release exists and is not marked as a prerelease;
- all expected assets are present and downloadable;
- the expected GHCR images and tags are present;
- the release page and source archives resolve correctly.

Useful commands:

```bash
gh run list --branch 1.0.0-beta.N
gh release view 1.0.0-beta.N --json tagName,targetCommitish,isPrerelease,assets,url
git ls-remote origin refs/tags/1.0.0-beta.N
```

Record the release SHA, CI run, publication runs, and release URL in the work
item. A successful tag push alone is not a completed release.

## 7. Failure recovery

Before the tag exists:

- fix functional problems in a dedicated commit;
- rerun local validation, push `main`, and wait for green branch CI;
- create another metadata-only release commit only if release metadata must
  change;
- never amend or force-push an already published commit.

After the tag exists:

- do not move, delete, or recreate the tag as routine recovery;
- rerun a failed publication workflow only when the tagged code and metadata
  are correct and the failure is transient infrastructure;
- if code or metadata is wrong, make a dedicated fix and publish the next beta
  number through the complete procedure;
- never overwrite an existing GitHub release or reuse its version.

## 8. Close the release

The release work item can be completed only when:

- local validation passed;
- functional and metadata commits have the required scope;
- branch CI is green for the exact tagged SHA;
- the lightweight no-`v` tag points to that SHA;
- Release Assets and Docker Images are green;
- the normal GitHub release, assets, and container images are verified;
- `git status --short --branch` shows no unexplained state.
