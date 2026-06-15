# Plan Review: unified-topology-view (cycle 0)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md
**Cycle:** 0
**Reviewed:** 2026-06-14
**Verdict:** REQUEST-CHANGES

## Summary

A presentation-rewrite spec collapsing two left-rail graphs into one `TopologyGraph` plus one additive serialization field, with a well-structured 4-task linear breakout. Spec sections are clear, all 9 acceptance tests are claimed exactly once, dependency edges are sound, and dead-code citations check out against the live `dashboard.html`/`broker.py`. The verdict is driven by one High finding: the deterministic `test-command-dry-run.sh` gate fails because three tasks use inline-array `taskTouches` YAML the parser cannot read, leaving the planned-creation test file unrecognized.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `breakout.task.unclaimed-touch-style` — Task Breakout: The dry-run gate (`test-command-dry-run.sh`) emits `spec.test-command.unrunnable` for `tests/test_unified_topology_keyed_lookup.py` — referenced by task `keyed-lookup-deletion-detector-test`'s `testCommand` but reported as not in any task's `taskTouches`. Root cause: tasks `broker-snapshot-slot-mapping`, `serialize-slot-to-teammate`, and `keyed-lookup-deletion-detector-test` write `taskTouches` as an inline YAML array (`taskTouches: ["..."]`), but the gate's `extract_task_touches` parser only recognizes block-list style (`taskTouches:` followed by `  - "..."` lines, as task `unify-topology-graph-component` correctly uses). The three inline-array tasks contribute zero entries to the gate's touch set; the planned-creation file (which does not yet exist on disk) therefore reads as unclaimed and the deterministic pipeline gate exits non-zero, blocking the workflow regardless of the semantic correctness of the claim. Fix: convert all inline-array `taskTouches` to block-list style. make-test-command-runnable.

### Medium

- [MEDIUM-01] `spec.assumptions.prior-art-location-mismatch` — Assumptions: The fixture-mechanism Assumption cites "`five_agent_url` and friends in `tests/conftest.py`," but `five_agent_url` is defined module-locally in `tests/dashboard/test_roster_spotlight.py:149` — not in `tests/conftest.py` (conftest holds only the module-scoped Playwright browser/context fixtures). The fixture exists and does support the claim in substance, but the misattributed location compounds with its module-local scope: the new `tests/test_unified_topology_keyed_lookup.py` (task4) cannot import `five_agent_url` without first promoting it to a shared conftest. An implementor relying on the stated location will look in conftest, not find it, and must decide whether to refactor — a judgment the spec should pre-resolve. clarify-scope.

### Low

- [LOW-01] `breakout.architecture.paraphrase-mismatch` — Architecture Overview: `architecture-conjunct-check.sh` flags 4 conjuncts as verbatim-uncovered (the `BrokerSnapshot` field, the `_build_local_instance` payload key, the "untouched `/edge-log`/`/edge-promote` handlers" constraint, and the `MiniGraph`/`TopologyEdgePanel`→`TopologyGraph` replacement). Each is covered in substance by the corresponding task description (task1, task2, the Out-of-Scope constraint, and task3 respectively); the mismatch is purely the long backtick-and-line-ref bullet prose failing substring match. No action required. add-coverage (advisory only).
- [LOW-02] `breakout.task.size-borderline` — Task Breakout: Task `unify-topology-graph-component` claims 4 ATs and touches 5 files (one prod + four retargeted test files), spanning component replacement, three render branches, the keyed lookup with three fallback levels, and lead-spoke pulse removal. It is large but cohesive and irreducible (all prod work is in `dashboard.html`, which cannot be split across parallel writers), and the description is concrete enough to build to PASS in 1–3 cycles. Not a mega-task; noted for transparency. clarify-scope (advisory only).

## Persistent Findings

_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear
- **Test command:** Present and runnable (prerequisite `playwright install chromium` named; `uv run pytest` exercises all new/retargeted tests)
- **Scope boundary:** Clear

## Verdict Rule Applied

REQUEST-CHANGES: 1 High finding.
