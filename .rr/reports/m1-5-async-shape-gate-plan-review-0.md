# Plan Review: m1-5-async-shape-gate (cycle 0)

**Spec:** specs/m1-5-async-shape-gate.md
**Cycle:** 0
**Reviewed:** 2026-06-12
**Verdict:** PASS

## Summary

Reviewed the combined spec + breakout for making `propose_shape` non-blocking with a chat-channel approval path, lead-inbox notify-on-resolve, and a resurfaceable dashboard gate. Every contract claim was verified against current `broker.py`/`server.py`/`ui_server.py`/`dashboard.html` and holds (14 tools, blocking `propose_shape`, `resolve_proposal(shape_id, decision)` with pending-only guard and no current lead-notify, `shape_proposals` lacking `name`/`summary`, artifact unread-pill pattern present to mirror). Sections are complete and testable; the breakout covers all six ATs exactly once with one justified dependency edge and no mega-task. Verdict driven by the rule: no Critical or High findings.

## Findings

### Critical
_None identified._

### High
_None identified._

### Medium
_None identified._

### Low

- [LOW-01] `spec.architecture.call-site-survey-inaccuracy` — Architecture Overview / Call-site survey (line 23): the UI-channel row shows `self._broker.resolve_proposal(crew_id, shape_id, decision)`, but the actual broker signature is `resolve_proposal(shape_id, decision)` — the `crew_id` is route-level instance routing (`/shape-approval/{crew_id}/{shape_id}`), not a broker argument. Harmless because this path is explicitly out-of-scope/unchanged, but the survey misstates a signature the implementor may trust. Category: remove-contradiction.
- [LOW-02] `spec.task-breakout.slice-validation-scope` — Task Breakout / `server-async-tools`: this task flips the widely-consumed `propose_shape` default from blocking to non-blocking, yet its `testCommand` runs only `tests/test_shape_gate.py`. Per this repo's verified CLAUDE.md lesson ("Validate the whole suite when changing widely-consumed behavior"), a scoped slice gate can mask a cross-cutting regression in other suites that call `propose_shape` expecting the blocking contract. Mitigated — the spec's `## Test Command` and `## Validation` both run full `uv run pytest` at the feature gate — so this is advisory: recommend the implementor grep `propose_shape` callers and run the full suite for this slice. Category: clarify-assumption.

## Persistent Findings
_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear — 6 numbered ATs, each with construction notes, happy + sad paths, and concrete broker/tool assertions.
- **Test command:** Present and runnable — `uv sync && uv run playwright install chromium && uv run pytest`; prerequisites and the pre-existing `test_shutdown_signals` baseline flake are disclosed.
- **Scope boundary:** Clear — explicit Out of Scope (M1/M2/M3 deferrals, `/shape-approval` route unchanged, ARCHITECTURE.md doc-sync deferred to RR documenter).

### Decomposition (## Task Breakout)

- **AT coverage:** AT1→`server-async-tools`, AT2→`server-async-tools`, AT3→`broker-notify-on-resolve`, AT4→`ui-server-shape-surface`, AT5→`dashboard-resurfaceable-gate`, AT6→`server-async-tools`. All six claimed exactly once; none unclaimed or duplicated.
- **Dependency edges:** One edge — `dashboard-resurfaceable-gate` dependsOn `ui-server-shape-surface` (consumes the new `name` field). Justified, not spurious. The other three tasks touch disjoint files and run in parallel.
- **File footprints:** Disjoint across tasks (broker.py / server.py / ui_server.py / dashboard.html and their respective test files); no same-file collision between parallelizable tasks.
- **Mega-task check:** `server-async-tools` bundles three ATs (non-blocking propose + `resolve_shape` + `list_pending_shapes`) but all live in `server.py`'s MCP-tool layer — correct same-surface batching, not a mega-task. Descriptions are concrete (return shapes, error contracts, test migrations named); an implementor can build without re-reading the full spec.

## Verdict Rule Applied

PASS: no Critical or High findings (2 Low, advisory only).
