# Breakout Review: fidelity-audit-followups (cycle 0)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md
**Breakout:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md
**Cycle:** 0
**Reviewed:** 2026-05-16
**Verdict:** REQUEST-CHANGES

## Summary

Four-task DAG reviewed against 8 spec acceptance tests. Coverage is complete and non-duplicated except for one ownership conflict: AT-1 (≥7 non-zero cost lines) is claimed by `cost-telemetry-wire-up`, but that task wires only 6 of the 7 required live classes — the 7th (`TestAgentFormatYamlPolymorphism`) is wired by `yaml-polymorphism-refactor`. A slice reviewer running AT-1 against `cost-telemetry-wire-up` alone will see 6 non-zero lines and incorrectly fail the task. One High finding drives REQUEST-CHANGES; two Low findings are advisory.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `breakout.coverage.unclaimed` — Task `cost-telemetry-wire-up` / Acceptance Tests: Task claims AT-1 ("≥7 non-zero cost lines") but wires only 6 of the 7 live classes; the 7th (`TestAgentFormatYamlPolymorphism`) is wired by `yaml-polymorphism-refactor`, which adds the `_record_sdk_cost` call that produces the 7th line. A slice reviewer running the full live suite after `cost-telemetry-wire-up` alone will observe 6 non-zero lines against a threshold of 7 and fail the review — incorrectly, because the implementation is correct in isolation. AT-1 ownership should move to the task that completes the 7th line (`yaml-polymorphism-refactor`) or to the terminal gate (`full-validation-baseline`, which already re-checks AT-1 in its description). add-coverage.

### Medium

_None identified._

### Low

- [LOW-01] `breakout.architecture.uncovered-conjunct` — Notes / Invariant 3: The coordinator's `architecture-conjunct-check.sh` failed because the Architecture Overview conjunct headers ("Cost telemetry", "YAML loader") do not appear as literal substrings in any task `description` field. Semantic coverage is intact — the mapping is obvious from task names — but the prescribed remedy (explicit "folded into / covered by task <name>" lines in `## Notes`) is absent. Reviewer considers Invariant 3 semantically met. The Notes section should be augmented with the literal mapping so the script gate passes on the next run without coordinator override. add-coverage.

- [LOW-02] `breakout.task.vague-description` — Task `yaml-polymorphism-refactor`: Description says "Verify under live mode that both sentinels still appear in the parent reply" without defining "sentinel." An implementor must read the existing `TestAgentFormatYamlPolymorphism` to discover the observable. This is an inherited spec vagueness (plan-review MEDIUM-02), flagged at Low since the spec already passed review and the implementor can resolve it via source inspection. clarify-scope.

## Persistent Findings

_None — first cycle or no recurrence._

## Coverage Map

| Spec AT | Claimed by | Count |
|---------|------------|-------|
| AT-1    | cost-telemetry-wire-up | 1 (but unverifiable in isolation — see HIGH-01) |
| AT-2    | full-validation-baseline | 1 |
| AT-3    | yaml-loader-extension | 1 |
| AT-4    | yaml-loader-extension | 1 |
| AT-5    | yaml-polymorphism-refactor | 1 |
| AT-6    | yaml-loader-extension | 1 |
| AT-7    | yaml-loader-extension | 1 |
| AT-8    | full-validation-baseline | 1 |

All 8 ATs covered, no duplicates. One ownership placement error (HIGH-01).

## Dependency Graph Assessment

- `yaml-loader-extension → []` and `cost-telemetry-wire-up → []` — parallel siblings with disjoint file touches. No glob overlap.
- `yaml-polymorphism-refactor → [yaml-loader-extension, cost-telemetry-wire-up]` — both edges are real: loader extension is required for `build_merged_pack` to discover the YAML file; wire-up is required for `_record_sdk_cost` to exist.
- `full-validation-baseline → [yaml-polymorphism-refactor, cost-telemetry-wire-up, yaml-loader-extension]` — transitive terminal gate. The direct edges to `cost-telemetry-wire-up` and `yaml-loader-extension` are redundant (already implied by `yaml-polymorphism-refactor`'s deps) but harmless.

No spurious edges, no missing edges, no cycles.

## Breakout Quality

- **Decomposition:** Sound — four tasks, two parallelizable leaf tasks, one join, one terminal gate.
- **Task descriptions:** Concrete and implementor-buildable in 1–3 cycles for three of four tasks; `full-validation-baseline` is a pure-validation gate and buildable as described.
- **Scope discipline:** No leakage; `full-validation-baseline` correctly limits `taskTouches` to `.rr/**`.
- **Risks section:** Thorough — cost-capture timing, snapshot field-name drift, body-key operator confusion, and live-spend variance all named.

## Verdict Rule Applied

REQUEST-CHANGES: 1 High finding.
