# Plan Review: agent-pack-refresh (cycle 1)

**Spec:** .rr/specs/agent-pack-refresh.md
**Cycle:** 1
**Reviewed:** 2026-05-24
**Verdict:** REQUEST-CHANGES

## Summary

The planner adopted the proposed 3-task split and resolved every cycle-0 finding's surface symptom — AT-10 / AT-5 are now in task 3, all per-task gates are `uv run pytest`, and Design Decisions explicitly pin the warning serialisation shape and diff-key namespacing. However, the same dependency-violation **pattern** that drove HIGH-01 last cycle has migrated to task 1: AT-3 and AT-6 are assigned to `pack-state-holder` but both ATs are worded to require `refresh_pack()` to be called, which is only implemented in task 2 (`refresh-method-and-diff`). Task 1 ships pure indirection with no AT it can satisfy as written. No architecture doc exists — noted, not failed.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `spec.task-breakout.at3-at6-require-refresh` — Task Breakout: AT-3 ("Resolver reads holder live") is worded "called *before* refresh and again *after* refresh adds `role-x`", and AT-6 ("Refresh uses captured roots, not cwd") is worded "when `Path.cwd()` is monkeypatched … and `refresh_pack()` is called". Both ATs explicitly invoke `refresh_pack()`, which is built in task 2, not task 1. Assigned to task 1 (`pack-state-holder`), they cannot pass at the task-1 boundary as written — this is the same cross-task dependency violation that drove cycle-0 HIGH-01 (just relocated to a different task pair). Either (a) reassign AT-3 and AT-6 to task 2 — task 1 then becomes a pure structural refactor with no claimed AT, which is acceptable given the holder is exercised by every AT in tasks 2/3 — or (b) reword AT-3 and AT-6 to drive the holder via direct mutation (`holder.pack[...] = ...`) so they validate the live-read semantics without requiring `refresh_pack()`. Category: clarify-assumption / remove-contradiction.

### Medium

_None identified._

### Low

- [LOW-01] `spec.task-breakout.task1-no-claimed-at` — Observational: if HIGH-01 is resolved by route (a), task 1 has no claimed AT but the spec's contract requires each AT to be claimed by exactly one task — that contract still holds (all 11 ATs claimed exactly once across tasks 2 + 3). Just note explicitly in task 1's description that it is a structural refactor whose correctness is asserted by downstream tasks' ATs (via the holder reads), so a future reviewer doesn't trip on the empty `acceptanceTests: []`. Category: clarify-assumption.

## Persistent Findings

- `spec.task-breakout.dependency-violation` — `.rr/reports/agent-pack-refresh-review-0.md` (cycle 0, HIGH-01) — restated as HIGH-01 above. Cycle-0 instance was AT-10 placed in task 1 while the MCP tool lived in task 2; cycle-1 instance is AT-3/AT-6 placed in task 1 while `refresh_pack()` lives in task 2. Same root cause: an AT references an artifact built downstream of the task that claims it.

## Spec Quality

- **Acceptance tests:** Clear
- **Test command:** Present and runnable
- **Scope boundary:** Clear

## Verdict Rule Applied

REQUEST-CHANGES: 1 High finding (AT-3 and AT-6 require `refresh_pack()`, which task 1 does not ship — recurrence of cycle-0 dependency-violation pattern).
