# Feature: Startup Diagnostics on the Mission Control Dashboard (#25)

**Shipped:** 2026-05-06
**Branch:** `repo-react/startup-diagnostics-dashboard`
**Workflow:** RepoReactor full chain (planner → plan-reviewer → breakout → 4 implementor + slice-reviewer cycles → feature-reviewer → validating → retrospecting)
**Spec:** `.rr/specs/startup-diagnostics-dashboard.md` (in worktree, not versioned)

## Problem

Server-startup-time WARNs and INFOs from pack-load and related startup paths went to stderr only — the Mission Control dashboard had no way to render them because pack-load happened before any teammate envelope existed. Operators had to tail the claude-crew server log to know if their config was broken (pack shadowing, unknown skills, frontmatter typos, plugin escape rejections, cross-plugin role-key collisions).

Surfaced 2026-05-01 during #23 Phase 2 — OQ-1: "where do skill-not-found WARNs reach an operator?" The honest answer was "they don't, unless you're tailing logs."

## Outcome

A new "Startup Notices" panel in the dashboard, hidden when empty, shows every WARN/INFO captured during `build_merged_pack()`. Each row carries level badge, source short-name, message, relative timestamp. INFO rows hidden behind a "Show INFO" toggle so the shadow trail doesn't dominate.

## Architecture

Three-stage pipeline matching the spec's "Validation Contracts at Handoff Boundaries" table:

1. **Domain** (`claude_crew/diagnostics.py`) — `StartupDiagnostic` frozen dataclass; `StartupDiagCollector` `logging.Handler` subclass with `handleError`-safe `emit`; `classify(record)` six-category table; `collect_startup_diagnostics()` context manager; 4096-char message cap. Pure domain logic, no broker/factory/UI dependency.

2. **Broker carrier** — `BrokerSnapshot.startup_diagnostics: tuple[StartupDiagnostic, ...] = ()` mirrors the reserved-field pattern from #18 (`tool_events`). `Broker.__init__` accepts kwarg, coerces to tuple, stores private; `Broker.snapshot()` re-emits by identity. No public mutator.

3. **Factory capture + UI surface** — `factories.default_factory()` wraps `build_merged_pack()` in `collect_startup_diagnostics()`, freezes on exit, attaches `factory.startup_diagnostics`. `server.py::make_server()` threads it into `Broker(startup_diagnostics=...)`. `ui_server._build_local_instance` duck-types diagnostics into `list[dict]` for `/api/state`. Dashboard `StartupNoticesSection` reuses `terminated-section` CSS idiom; in-memory state only (no `localStorage`).

## Key Design Decisions

- **Frozen at end of startup.** Diagnostics are a set-once tuple, not a growing log. Runtime emissions never reach the snapshot.
- **`propagate=True` preserved.** Handler is purely additive — operators tailing stderr keep working.
- **Capture in factory, not broker.** Capture window = lifetime of `build_merged_pack()`. Smallest scope, least surprising.
- **Six-category classifier** at capture time (shadow / unknown_skill / unknown_mcp_server / frontmatter / plugin / other). Heuristic regex on logger name + message; rendered side has nothing to infer.
- **WARN+ default visible; INFO toggle.** Shadow trail can be voluminous on real configs; operators staring at the dashboard don't want it dominating.
- **No `localStorage` dismiss.** Config issues are real signals. Dismissing them is a footgun.
- **Stub-mode skip.** `factory.startup_diagnostics` is only attached in SDK mode; stub mode falls through to `BrokerSnapshot.startup_diagnostics = ()` via the `getattr(..., ())` default in `server.py`.
- **OQ-1 propagation probe + direct-attach fallback.** If a source logger has `propagate=False` set somewhere upstream, the collector direct-attaches to that logger as well. Level-restore stash currently coupled to the handler instance — flagged as Medium for follow-up refactor.
- **Duck-typed UI serialization.** `_build_local_instance` reads `.level/.message/.source/.timestamp/.category` without importing the domain type. Loose coupling at the serialization boundary.

## Acceptance Tests

| AT | Coverage | Test file |
|----|----------|-----------|
| 1 | Empty case (clean config → empty tuple) | `tests/test_factory_startup_diagnostics.py` |
| 2 | Pack shadow INFO captured + categorized | `tests/test_factory_startup_diagnostics.py` |
| 3 | Unknown-skill WARN captured + categorized (unit-form) | `tests/test_diagnostics.py` |
| 4 | Frontmatter parse rejection captured | `tests/test_factory_startup_diagnostics.py` |
| 5 | stderr propagation preserved | `tests/test_diagnostics.py` |
| 6 | Snapshot field is frozen | `tests/test_broker_startup_diagnostics.py` |
| 7 | `/api/state` shape | `tests/test_ui_server_startup_diagnostics.py` |
| 8 | Dashboard renders panel with correct rows (E2E) | `tests/dashboard/test_startup_notices.py` |
| 9 | Dashboard panel hidden when empty (E2E) | `tests/dashboard/test_startup_notices.py` |
| 10 | Stub mode skips capture | `tests/test_factory_startup_diagnostics.py` |
| 11 | Existing snapshot consumers unbroken | `tests/test_broker_startup_diagnostics.py` |

44 new tests pass. 963-test full non-live suite green; 4-test Playwright suite green.

## Deviations

Two slice.scope.task-touches-violation entries, both adjudicated as breakout planning gaps:

- **`claude_crew/server.py` edit by `factory-capture-wire` slice.** The factory→broker wire literally lives in `make_server()`; the breakout's `taskTouches` for that slice missed it. Reviewer adjudicated necessary, correct, and minimal — Medium downgrade from Critical.
- **`tests/dashboard/__init__.py` by `ui-payload-and-panel` slice.** Zero-byte package marker required for pytest discovery of the new test directory. Adjudicated Info-tier.

Process improvement: the planner heuristic should include `claude_crew/server.py` in `taskTouches` whenever a slice introduces a factory→broker data flow. Filed in BACKLOG.

## Follow-ups (deferred to BACKLOG)

- ERROR-tier startup-diagnostic badge CSS (red WARN-style + fixture)
- Remove no-op nested `try/except` in `StartupDiagCollector.emit`
- Refactor `_direct_attach_fallbacks` to return restore pairs explicitly instead of stashing on the handler instance
- Planner heuristic: include `server.py` in `taskTouches` when factory→broker wiring is introduced
- Reconcile `unknown_skill` category — narrow scope or add a startup-time emit site (also covers plan-review MED-01)

## Reports

All implementation artifacts live in `.rr/reports/` on branch `repo-react/startup-diagnostics-dashboard`:

- 4 build reports (one per slice)
- 4 slice-review reports (all PASS)
- 1 plan-review report (PASS, 2 MED + 2 LOW advisory)
- 1 breakout-review report (PASS, 2 LOW advisory)
- 1 feature-review report (PASS, 1 LOW)
- 1 validation report (PASS)
- 1 feature-retro report
- 4 implementor debriefs
- 1 workflow-retro stub (skipped — `workflowRetroEnabled=false` default)
