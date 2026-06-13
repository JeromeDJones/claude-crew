# Slice Review: m2-edge-routing task=broker-circuit-breaker

## Verdict summary
**PASS.** Both owned acceptance tests (AT#6 budget, AT#7 deadlock) are implemented and green on my independent re-run; no regression in the sibling `broker-edge-routing` slice or the full suite. The breaker forces edges to `gated` (not drop), emits the control envelope to LEAD, and is idempotent. One **non-blocking soundness finding** (deadlock false-positive) is surfaced — its breaking impact lands in a cross-slice AT (Info tier per charter) and does not affect this verdict.

## Check 1 — Slice adherence (AT#6, AT#7)

Evaluated against breakout entry `broker-circuit-breaker` (`acceptanceTests: [6,7]`).

| AT | Requirement | Implementation | Test | Status |
|----|-------------|----------------|------|--------|
| #6 budget | edge `direct`, `MAX=N`; (N+1)-th routes to LEAD; `{type:circuit_breaker, edge:[a,b], reason:budget_exceeded}` to LEAD; effective mode now `gated` | `_apply_circuit_breaker` increments `_edge_exchanges`, trips on `count > max`, sets `_edge_overrides[(f,t)]="gated"`, emits ctrl envelope, returns `"gated"` so `_send_routed` re-routes the trigger as a gated wrapper | `TestBreakerBudget` (8 tests) | ✓ |
| #7 deadlock | reciprocal `a→b`+`b→a` both in-flight unanswered → trip; `{reason:deadlock}` to LEAD; involved edge forced `gated` | sets `_edge_pending[forward]=True`, trips when `_edge_pending[reverse]` already True; same trip/override/ctrl path | `TestBreakerDeadlock` (6 tests) | ✓ |

Plus `TestEdgeStatSnapshot` (3 tests) verifying `BrokerSnapshot.topology_edge_stats`. The `EdgeStat` dataclass + `topology_edge_stats` snapshot surface (consumed downstream by `dashboard-edge-observability`) is correctly built.

**Interaction with `broker-edge-routing`:** the breaker is layered cleanly ahead of the tee/direct delivery in `_send_routed`; a trip mutates `routing_mode` to `"gated"` so the trigger flows through the existing gated branch. Post-trip sends are short-circuited by `_resolve_routing_mode` before the breaker is re-entered — idempotency verified. **Forces `gated`, never `drop`** ✓.

## Check 2 — Non-regression

- Slice command `uv run pytest tests/test_circuit_breaker.py` → **17 passed**.
- Combined with sibling slice `tests/test_edge_routing.py` → **36 passed** (independent re-run).
- Matches coordinator ground-truth (full suite **1498 passed / 0 failed**; `test_shutdown_signals` is the known timing flake).

## Check 3 — Code-quality smoke (changed files only)

Strong overall: clear docstrings, `tripped` set separates auto-trips from operator `promote_edge`, the synthetic `sender="broker"` control envelope is lead-bound correctly.

**One genuine soundness gap (non-blocking — see findings):** `_edge_pending` is only ever set `True` (broker.py:934) and read in the deadlock check — **never cleared** (confirmed by full-file grep). The spec's Edge Cases require the opposite ("`_edge_pending` cleared on delivery of the reply, so no trip"). With no clearing path, **any** reciprocal pair `a→b` then `b→a` trips a `"deadlock"`, including normal back-and-forth. This satisfies owned AT#7 but contradicts the false-positive contract and breaks cross-slice **AT#10** (`direct ping-pong a→b→a→b stays off the lead`, owned by `scoped-send-teammate`). The spec carries an internal tension (AT#7 vs. the false-positive Edge Case vs. AT#10 are not reconcilable at the broker wire level without reply-correlation data) → flagged for coordinator/feature-reviewer adjudication, not a silent fix.

## Findings

| Sev | Tag | Finding |
|-----|-----|---------|
| Info | `slice.cross-cutting` | `_edge_pending` never cleared → deadlock detector over-trips on legitimate reciprocal replies. Owned ATs 6–7 pass; breaking impact is in cross-slice **AT#10**. Flagged for adjudication; fix structurally belongs in this slice's `_apply_circuit_breaker`. |

No Critical or High findings.

---

## COORDINATOR ADJUDICATION (post-review, Kael + Jerome, 2026-06-13)

The reviewer's finding is upheld and the spec contradiction is resolved by **dropping 2-node deadlock detection entirely**. Rationale: "A waits B waits A" is not well-defined at a message-bus level (the broker can't distinguish a blocked peer from one that simply hasn't sent yet); reply-clearing makes the both-pending state unreachable (dead code); and any non-clearing implementation contradicts the reciprocal-peer-conversation contract (AT#10). The **per-edge exchange budget is the sole runaway guard** — it already force-inserts the lead on a runaway loop. Spec amended: `_edge_pending`/deadlock removed; AT#7 redefined to assert reciprocal-below-budget does NOT trip + budget trips on runaway. Task `broker-circuit-breaker` re-dispatched for rework against the amended spec.
