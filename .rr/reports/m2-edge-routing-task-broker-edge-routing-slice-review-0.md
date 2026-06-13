# Slice Review: m2-edge-routing task=broker-edge-routing

## Verdict summary
**PASS.** All five owned acceptance tests (AT#1–#5) are implemented, meaningfully tested, and green on my independent re-run. No regression in the touched `tests/test_broker.py`. One Info-tier scope observation (under-declared `taskTouches`) — does not affect the verdict.

## Check 1 — Slice adherence (AT#1–#5)

Evaluated against the breakout entry `broker-edge-routing` (`acceptanceTests: [1,2,3,4,5]`).

| AT | Requirement | Implementation | Test | Status |
|----|-------------|----------------|------|--------|
| #1 gated | lead-bound wrapper `{gated_for,from,payload}`, recipient inbox empty, original not logged | `_send_routed` else-branch builds wrapper to LEAD, marks original `_seen_ids`, never logs/delivers original | `TestGatedRouting` (3 tests: wrapper fields, empty recipient log, seq assigned) | ✓ |
| #2 tee | original → recipient inbox + derived cc `{cc_of,...}` → LEAD; both in `_log` | tee-branch delivers stamped original AND fresh-id cc to LEAD, both appended to `_log` | `TestTeeRouting` (cc_of=original id, both in log, `len(_log)==2`) | ✓ |
| #3 direct | recipient inbox + log, NOT lead-bound | direct-branch delivers + logs, no LEAD notify | `TestDirectRouting` (lead empty, recipient log present) | ✓ |
| #4 no-edge fallback | gated wrapper, recipient empty; reproduces lead-routed behavior | `_resolve_routing_mode` returns `"gated"` for no-topology AND no-forward-edge | `TestNoEdgeFallback` (both topology-present and no-topology cases, + dedup of retried original) | ✓ |
| #5 scoped auth rejects | `send_scoped(a,c)` non-declared → `UnauthorizedEdgeError`, nothing enqueued; `authorize_send(a,LEAD)` no-op | `authorize_send` raises on missing forward edge / topology; `send_scoped` authorizes before building envelope | `TestScopedAuthorization` (5+ tests incl. LEAD no-op, slot resolution, happy-path delivery) | ✓ |

Routing-resolver placement is correct: the M2 gate (`recipient != LEAD_ID and sender in _teammates`) sits **after** the dedup/tombstone/terminating/unknown checks, so a duplicate original returns `None` before any wrapper is minted (satisfies the spec's "deduped original produces no wrapper" design note). Lead-origin and lead-bound sends short-circuit to the untouched legacy path, preserving the coordinator-moat invariant.

## Check 2 — Non-regression

- Slice command `uv run pytest tests/test_edge_routing.py` → **19 passed** (my re-run, combined below).
- Touched-file `tests/test_broker.py` → **105 passed**.
- Combined re-run `tests/test_edge_routing.py tests/test_broker.py` → **124 passed in 1.44s** (independent).
- Matches coordinator ground-truth (full suite 1481 passed / 0 failed; the build report's "2 signal-startup failures" are the known flaky `test_shutdown_signals` timing tests, green on re-run).

## Check 3 — Code-quality smoke (changed files only)

- `_send_routed` repeats envelope-construction + seq-assignment across its three branches — minor duplication, but each branch is legible and the divergent post-steps justify it. **Low, non-blocking.**
- `send_scoped(..., id=...)` shadows the builtin `id`, but this matches the spec's mandated signature verbatim — accepted.
- Scope correctly partitioned: `_edge_overrides` + `promote_edge` + `_edge_mode` override-check are present (assigned to this task); circuit-breaker counters (`_edge_exchanges`/`_edge_pending`) are correctly **absent** (task `broker-circuit-breaker`). No premature reach into the next slice.
- New `UnauthorizedEdgeError` and helpers are well-documented; failure paths raise loudly (aligns with fail-loud standard).

## Invariant-1 adjudication (`tests/test_broker.py` outside declared globs)

**Ruling: `breakout.scope.under-declared` (Info-tier, legitimate necessary edit — NOT creep).**

The implementor added `Topology` to imports and adapted SC-5 `test_teammate_send_does_not_wake_lead_poll` to record a `direct` edge. This is **caused by this task's own behavior change**: M2's gated-fallback means a no-topology teammate→teammate send now correctly wakes the lead poll, invalidating the test's old premise. The edit is minimal (+15/−3), preserves the original invariant's intent (direct-mode peer sends don't notify LEAD), and re-asserts it under the new model. Any task that alters `broker.send`'s teammate→teammate default necessarily touches this assertion — the `taskTouches` globs should have listed `tests/test_broker.py`. This is an annotation gap, not out-of-scope work, and the full suite is green.

## Findings

| Sev | Tag | Finding |
|-----|-----|---------|
| Info | `breakout.scope.under-declared` | `tests/test_broker.py` edited outside declared `taskTouches`; legitimate, necessary adaptation of SC-5 to M2's gated-fallback behavior. |
| Low | `code.duplication` | `_send_routed` repeats envelope/seq construction across three branches; acceptable for clarity, no action required. |

No Critical or High findings.
