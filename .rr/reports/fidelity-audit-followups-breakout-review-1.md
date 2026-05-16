# Breakout Review: fidelity-audit-followups (cycle 1)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md
**Breakout:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md
**Prior report:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-breakout-review-0.md
**Cycle:** 1
**Reviewed:** 2026-05-16
**Verdict:** REQUEST-CHANGES

## Summary

Cycle-0 HIGH-01 (AT-1 ownership) and LOW-01 (architecture conjunct mapping) are both resolved. The fix that moved AT-1 to `yaml-polymorphism-refactor` is correct and the Notes section now carries the explicit conjunct-to-task mapping. However, removing AT-1 from `cost-telemetry-wire-up` left that task with `acceptanceTests: []` while retaining `implementationKind: behavior-change` — a direct Invariant 2 violation. A slice reviewer for that task has no claimed integration test to run against a task that writes behavioral code (helper + 6 class wirings). One new High finding; verdict remains REQUEST-CHANGES.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `breakout.kind.behavior-needs-integration-test` — Task `cost-telemetry-wire-up`: Task declares `implementationKind: behavior-change` but `acceptanceTests: []`. The cycle-0 fix correctly vacated AT-1 (which this task cannot satisfy in isolation), but left the task with no runnable verification claim. A slice reviewer has no AT to execute — they cannot confirm the helper is correctly wired into all 6 classes and produces non-zero cost data without relying on a downstream task's results. Invariant 2 requires at least one integration-test claim for a behavior-change task. add-coverage.

### Medium

_None identified._

### Low

- [LOW-01] `breakout.task.vague-description` — Task `yaml-polymorphism-refactor` (persistent from cycle-0 LOW-02): Description still asserts "both sentinels still appear in the parent reply" without defining "sentinel." An implementor must read the existing test to discover the observable. Inherited from spec MEDIUM-02 (already accepted at plan-review PASS); flagged at Low because source inspection resolves it. clarify-scope.

## Persistent Findings

- `breakout.task.vague-description` (cycle-0 LOW-02) — `yaml-polymorphism-refactor` description / "sentinels" undefined — persists unchanged.

## Cycle-0 finding resolution

| Cycle-0 finding | Tag | Status |
|-----------------|-----|--------|
| HIGH-01 | `breakout.coverage.unclaimed` | **Resolved** — `cost-telemetry-wire-up.acceptanceTests` set to `[]`; AT-1 moved to `yaml-polymorphism-refactor`. |
| LOW-01 | `breakout.architecture.uncovered-conjunct` | **Resolved** — Notes now has explicit `### Architecture conjunct → task mapping` section naming both conjuncts and their owning tasks. Coordinator confirms Invariant 3 spirit satisfied; script over-match on prose fragments is not a real gap. |
| LOW-02 | `breakout.task.vague-description` | **Persists** — "sentinels" still undefined in `yaml-polymorphism-refactor` description. |

## Coverage Map

| Spec AT | Claimed by | Count |
|---------|------------|-------|
| AT-1    | yaml-polymorphism-refactor | 1 ✓ (task wires the 7th class, completing ≥7 threshold) |
| AT-2    | full-validation-baseline | 1 ✓ |
| AT-3    | yaml-loader-extension | 1 ✓ |
| AT-4    | yaml-loader-extension | 1 ✓ |
| AT-5    | yaml-polymorphism-refactor | 1 ✓ |
| AT-6    | yaml-loader-extension | 1 ✓ |
| AT-7    | yaml-loader-extension | 1 ✓ |
| AT-8    | full-validation-baseline | 1 ✓ |

All 8 ATs covered exactly once. `cost-telemetry-wire-up` claims none — Invariant 2 violation noted in HIGH-01.

## Dependency Graph Assessment

Unchanged from cycle 0; edges remain sound:
- `yaml-loader-extension → []` and `cost-telemetry-wire-up → []` — parallel, disjoint file touches.
- `yaml-polymorphism-refactor → [yaml-loader-extension, cost-telemetry-wire-up]` — both edges required.
- `full-validation-baseline → [yaml-polymorphism-refactor, cost-telemetry-wire-up, yaml-loader-extension]` — terminal gate; redundant direct edges harmless.

## Verdict Rule Applied

REQUEST-CHANGES: 1 High finding.
