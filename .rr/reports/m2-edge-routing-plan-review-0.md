# Plan Review: m2-edge-routing (cycle 0)

**Spec:** .rr/specs/m2-edge-routing.md
**Cycle:** 0
**Reviewed:** 2026-06-13
**Verdict:** REQUEST-CHANGES

## Summary

A strong, unusually thorough combined artifact: 13 concrete stub-mode acceptance tests, clean four-seam architecture, every design decision carries a rationale and a "carried into" trace, and the four-task breakout covers all 13 ATs exactly once with a sound dependency chain. The verdict is driven solely by a `taskTouches` format defect: two tasks declare their test files in inline-array YAML (`[...]`) form, which the deterministic `taskTouches` parser cannot read — so those test files register as unclaimed, and the same parser backs downstream merge-back file-conflict tooling.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `spec.test-command.unrunnable` — Task Breakout (`broker-edge-routing`, line 445): `tests/test_edge_routing.py` is declared in `taskTouches` but in inline-array form (`taskTouches: ["claude_crew/broker.py", "tests/test_edge_routing.py"]`), which the deterministic `taskTouches` parser (block-list only: `taskTouches:` newline then `- "..."`) does not read. The dry-run gate therefore reports the file as unclaimed/unrunnable, and the identical parser feeds merge-back file-footprint/lock computation — so this task's broker.py + test-file footprint is invisible to conflict detection, not merely a cosmetic nit. Convert the inline array to block-list form. make-test-command-runnable.
- [HIGH-02] `spec.test-command.unrunnable` — Task Breakout (`broker-circuit-breaker`, line 460): `tests/test_circuit_breaker.py` is declared only in inline-array `taskTouches` form, with the same consequence as HIGH-01 — the file is invisible to the block-list `taskTouches` parser and to the file-footprint tooling that shares it. Convert the inline array to block-list form (matching `scoped-send-teammate` and `dashboard-edge-observability`, which already use block form). make-test-command-runnable.

### Medium

- [MEDIUM-01] `breakout.task.touches-format-inconsistency` — Task Breakout: two tasks use inline-array `taskTouches` (lines 445, 460) and two use block-list form (lines 478-483, 513-516) within the same DAG. Beyond the gate failures above, mixed form is a latent trap — any tooling or future reviewer that parses one form silently misses the other. Normalize all four tasks to block-list form. clarify-scope.

### Low

- [LOW-01] `breakout.architecture.uncovered-conjunct` — Architecture Overview: `architecture-conjunct-check.sh` flagged nine conjuncts (`routing`, `authorization`, `circuit breaker…`, `teammate_prompt.py`, `scoped send_to`, `neighbor injection`, `wiring…instantiate_shape…`, `ui/dashboard.html`, `observability…/api/state…`). On inspection every one is covered in substance by a task description — `routing`/`authorization` by `broker-edge-routing`, `circuit breaker` by `broker-circuit-breaker`, `teammate_prompt.py`/`scoped send_to`/`neighbor injection`/`instantiate_shape` by `scoped-send-teammate`, `ui/dashboard.html`/`/api/state observability` by `dashboard-edge-observability`. The flags are split-on-`;`/`+`/`**`-markup paraphrase artifacts, not real coverage gaps. No fix required (advisory).
- [LOW-02] `breakout.deps.serialization-edge` — Task Breakout (`scoped-send-teammate` → `broker-circuit-breaker`): the edge is a same-file serialization edge on `claude_crew/broker.py` rather than a pure contract dependency (the contract need is on `broker-edge-routing`'s `send_scoped`/`authorize_send`). This is the blessed per-repo pattern (workflow.md "serialize same-file work") and the spec discloses the rationale ("to serialize broker.py edits"), so it is correct — noting only that it is a file-serialization, not a data-contract, edge. No fix required (advisory).

## Persistent Findings

_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear — 13 numbered scenarios, each with observable assertions, stub-mode harness named, sad paths covered (AT#5/#9 rejection, AT#6/#7 breaker trips).
- **Test command:** Present and runnable — `uv run pytest` with prerequisites named; full-suite gate justified per the repo's cross-cutting-regression standard. The two HIGH findings concern `taskTouches` declaration form, not the spec-level `## Test Command` block.
- **Scope boundary:** Clear — `## Out of Scope` enumerates non-trivial exclusions (reverse_mode, M3 adaptation verbs, M1/M4/M5 work, token budgets, >2-node deadlocks, live-SDK send_to, green-suite SVG render) that a reasonable implementor might otherwise have pulled in.

## Verdict Rule Applied

REQUEST-CHANGES: 2 High findings (inline-array `taskTouches` unparseable by the deterministic gate and shared file-footprint tooling). Substantively the spec and decomposition are otherwise PASS-quality; the fix is a mechanical reformat of two `taskTouches` lines to block-list form.
