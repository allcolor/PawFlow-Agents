# Ruff remediation plan

## Objective

Reduce the declared Ruff policy to zero diagnostics across every Python file
tracked by Git, then make that exact policy a blocking CI and release gate.
The work is intentionally separate from feature and release commits.

This plan does not redefine findings as harmless, hide them behind a broad
ignore, or run unsafe fixes across the repository. It distinguishes mechanical
rewrites from findings that require a behavioural decision, tests each batch,
and turns every completed rule family into a permanent CI gate.

## Reproducible baseline

Baseline captured on 2026-08-19 from
`b46ba83a3a421c62e7ec2db6f9d8bc197d303f41` with the pinned Ruff 0.16.2
default selection, `target-version = "py310"`, and the exceptions declared
in `pyproject.toml`.
The audit passed every path returned by `git ls-files '*.py'` to Ruff:

| Metric | Count |
| --- | ---: |
| Tracked Python files | 1,349 |
| Files with findings | 1,148 |
| Total diagnostics | 11,494 |
| Safe fixes advertised by Ruff | 7,813 |
| Unsafe fixes advertised by Ruff | 396 |
| Display-only suggested fixes | 197 |
| Findings with no automatic fix | 3,088 |

The largest rule families are:

| Rule | Count | Safe | Other | Treatment |
| --- | ---: | ---: | ---: | --- |
| `UP006` | 4,539 | 4,539 | 0 | Mechanical type modernization |
| `I001` | 1,594 | 1,594 | 0 | Reviewed import ordering |
| `BLE001` | 1,516 | 0 | 1,516 | Exception-boundary redesign |
| `UP035` | 962 | 61 | 901 | Typing import migration after `UP006` |
| `UP045` | 856 | 856 | 0 | Mechanical Python 3.10 unions |
| `RUF100` | 266 | 266 | 0 | Review each obsolete suppression |
| `PLW1510` | 197 | 0 | 197 | Explicit subprocess policy |
| `RUF012` | 167 | 0 | 167 | Class state versus `ClassVar` |
| `RUF059` | 112 | 0 | 112 | Reviewed unpacking cleanup |
| `RUF013` | 104 | 0 | 104 | Explicit optional annotations |
| `PLR0402` | 103 | 103 | 0 | Import modernization |
| `G201` | 89 | 0 | 89 | Logging semantics |
| `TRY004` | 59 | 0 | 59 | Correct exception type |
| `UP037` | 58 | 58 | 0 | Quoted annotation cleanup |
| `FURB167` | 54 | 54 | 0 | Regex flag modernization |
| `B023` | 46 | 0 | 46 | Loop-closure correctness |

Findings by major root are `core` 5,577, `tasks` 2,159,
`services` 1,335, `tests` 1,133, `engine` 375,
`pawflow_relay` 339, `tools` 229, `pawflow_cli` 149, and
`scripts` 123. Counts are evidence for planning, not an allowed budget.
Every phase recalculates them from the current commit.

## Invariants

1. Keep Ruff and Bandit pinned in `requirements-lint.txt`.
2. Keep Ruff pinned while this baseline is being reduced; do not let an
   unreviewed Ruff upgrade change the enabled default rules mid-remediation.
3. Do not add repository-wide ignores to make the count fall.
4. A targeted `noqa` is allowed only where the behaviour is intentional, the
   reason is adjacent, and a test exercises the boundary.
5. Never run `--unsafe-fixes` repository-wide. Review unsafe edits one at a
   time or in a homogeneous, bounded batch.
6. Preserve Python 3.10 support and public import paths.
7. Keep lint-only commits separate from functional changes. Record before and
   after counts in each commit or pull-request description.
8. After a rule reaches zero globally, add it to the blocking CI selection in
   the same series so it cannot return.
9. A lower total is not sufficient: no blocking rule may regress and all tests
   relevant to changed code must pass.

The existing `E402` policy and `F401` package-`__init__` exception remain
because their load-bearing import semantics are already documented. They are
not part of the 11,494-diagnostic baseline.

## Validation ladder for every batch

1. Run Ruff for only the rules and paths being edited.
2. Run `python -m compileall` on every changed Python package.
3. Run focused tests for affected modules and import/export contracts.
4. Run `git diff --check` and inspect every non-mechanical hunk.
5. At the end of each rule family, run the complete test suite.
6. Push only after local validation, then require the Python 3.10–3.13 CI
   matrix, package install smoke tests, Ruff gate, and Bandit to pass.

If a batch changes runtime behaviour unexpectedly, revert that batch rather
than adding a suppression to preserve it.

## Phase 0 — policy and tooling

Deliverables:

- pin Ruff 0.16.2 and Bandit 1.9.4;
- make the release procedure truthfully match the current blocking CI command;
- record this baseline and the remediation sequence;
- keep the existing correctness gate `E9,F63,F7,F82` green.

Exit criteria:

- local and CI use the same pinned Ruff version;
- a fresh audit reproduces the baseline, apart from concurrent feature changes;
- no additional ignore has been introduced.

## Phase 1 — safe typing modernization

Order:

1. `UP006` across the tree.
2. `UP045` across the tree.
3. Re-audit `UP035`; remove typing imports made obsolete by the first two
   steps, then review the remaining runtime-sensitive cases.
4. `UP037`, `UP012`, and `UP041`.

Split each rule into reviewable roots: tests and scripts first, then
`pawflow_cli` and `pawflow_relay`, then `engine`, then `tasks` and
`services`, and finally `core`. Use only Ruff fixes marked safe. Verify
runtime annotations, Pydantic models, dataclasses, public aliases, and
serialization tests before closing the family.

Exit criteria: every listed rule is zero and is added to the CI gate.

## Phase 2 — imports, exports, and directives

Rules include `I001`, `PLR0402`, `F401`, `RUF100`, `RUF022`, and
`RUF023`.

`I001` is not treated as blindly mechanical: import order is load-bearing in
registration modules, plugin discovery, package re-exports, and circular-import
breakers. Process those modules in small groups and run import-contract and
registry tests after each group. For `RUF100`, preserve intentional disabled
rule markers through explicit configuration or a reasoned local directive;
remove only genuinely obsolete markers.

Exit criteria: zero diagnostics for the family, unchanged public imports and
registration order, and the family added to CI.

## Phase 3 — state, closures, and process semantics

Rules include `RUF012`, `RUF013`, `RUF059`, `B020`, `B023`,
`SIM115`, and `PLW1510`.

Required decisions:

- distinguish intentional shared class constants from accidental mutable class
  state;
- bind loop variables explicitly in callbacks and closures;
- preserve resource lifetime when replacing raw `open` calls;
- choose `check=True` when failure must propagate and `check=False` only
  where the return code is deliberately handled.

Add regression tests around callback identity, subprocess failure, resource
cleanup, and class-instance isolation.

Exit criteria: zero diagnostics for the family and all choices observable in
tests or adjacent rationale.

## Phase 4 — exception and logging boundaries

Rules include `BLE001`, `S110`, `S112`, `G201`, `TRY002`, and
`TRY004`.

Classify each catch site as one of:

- domain logic: catch the narrow exception and preserve the failure contract;
- protocol or task boundary: translate a known exception to a typed error;
- process/service containment boundary: a broad catch may remain only with
  structured logging, cancellation and fatal-exception handling, an adjacent
  reason, and a targeted directive;
- cleanup: use a narrow exception or `contextlib.suppress` only when loss is
  explicitly safe.

Do not replace `Exception` mechanically with another broad tuple. Test error
codes, retry behaviour, cancellation, cleanup, and log records.

Exit criteria: zero diagnostics for the family, including justified local
directives counted as policy-compliant by Ruff.

## Phase 5 — time, simplification, performance, and remaining rules

Handle timezone rules such as `DTZ005` and `DTZ006` with an explicit UTC or
local-time contract. Then process `SIM`, `PIE`, `PERF`, `FURB`, and the
remaining low-volume rules. Safe fixes may be grouped by rule; unsafe
simplifications require equivalence tests, especially for generators,
short-circuiting, mutation, and exception timing.

Executable-bit findings such as `EXE001` are resolved through Git modes or by
removing an unnecessary shebang, not suppressed.

Exit criteria: a fresh audit reports zero under the full declared policy.

## Phase 6 — make zero permanently blocking

After the full audit reaches zero:

- replace the incremental CI selection with `ruff check . --no-fix`;
- use the identical command in the release procedure;
- cover all tracked Python roots, including tests and scripts;
- retain the pinned toolchain and make upgrades explicit, reviewed changes;
- run the full Python 3.10–3.13 CI matrix before merging the gate.

The final acceptance command is:

```bash
python -m pip install -r requirements-lint.txt
ruff check . --no-fix
```

Success means zero diagnostics, no unreviewed suppressions, a green complete
test suite, and a branch/release gate that fails on the first regression.
