# Slice Review: m2-edge-routing task=broker-circuit-breaker (cycle 1)

## Verdict summary
**PASS.** The rework cleanly resolves the cycle-0 finding: the unsound deadlock detector is removed entirely, the breaker is now exchange-budget-only, and the rewritten `TestBreakerReciprocalExchanges` guards both halves of the amended AT#7. Owned ATs 6–7 green on independent re-run; no regression.

## Cycle-0 finding resolution

Prior finding (`slice.cross-cutting`): `_edge_pending` was never cleared → the deadlock detector over-tripped on legitimate reciprocal replies, threatening cross-slice AT#10. **RESOLVED by construction:**
- `grep _edge_pending` → **0 matches** in both `broker.py` and the test file. The over-trip code path is physically gone, not merely guarded.
- `_apply_circuit_breaker` no longer computes `reverse_key` or checks a reverse-pending flag; the budget (`count > _circuit_breaker_max_exchanges`) is the **sole** trip condition, and the trip reason is hardcoded `"budget_exceeded"`.
- The spec was amended (AT#7 redefined) and the operator decision is reflected in code, tests, and docstring (`spec amendment, 2026-06-13`).
- A dedicated regression guard `test_reciprocal_no_pending_state_left_on_broker` asserts `not hasattr(broker, "_edge_pending")`, preventing silent reintroduction.

## Check 1 — Slice adherence (AT#6, AT#7 amended)

| AT | Requirement | Implementation | Test | Status |
|----|-------------|----------------|------|--------|
| #6 budget | `MAX=N`; (N+1)-th routes to LEAD; `{type:circuit_breaker, edge:[a,b], reason:budget_exceeded}` to LEAD; effective mode `gated` | `count > max` → set `_edge_overrides[(f,t)]="gated"`, `_edge_tripped`, emit ctrl envelope, return `"gated"` for re-route | `TestBreakerBudget` (8 tests) | ✓ |
| #7 reciprocal/budget | reciprocal `a→b→a→b` **below** budget → none trip, none reach LEAD; **above** budget → trips `budget_exceeded`, edge forced `gated` | per-direction independent counters; budget-only trip | `TestBreakerReciprocalExchanges` | ✓ |

AT#7 both halves genuinely asserted — below-budget-no-trip (`test_reciprocal_below_budget_stays_off_lead`, `test_reciprocal_below_budget_no_breaker_trip` with strict `>` boundary) and above-budget-trip (`test_reciprocal_ab_edge_trips_when_budget_exceeded`, `test_reciprocal_ba_edge_trips_independently`). Per-direction counters mean ping-pong below budget cannot trip — exactly what cycle-0's over-trip violated, and the precondition AT#10 needs.

## Check 2 — Non-regression
- `uv run pytest tests/test_circuit_breaker.py` (+ sibling `tests/test_edge_routing.py`) → **36 passed** (independent re-run).
- Corroborates coordinator ground-truth (breaker 17, edge-routing 19, full suite **1498 / 0 failed**).

## Check 3 — Code-quality smoke (changed files only)
Clean rework. No dead deadlock code left (`reverse_key` and the pending dict fully excised). Per-direction independent `_edge_exchanges` counters, the `_edge_overrides`-membership idempotency guard, and the auto-trip-vs-manual-`promote_edge` distinction all retained and tested. Docstring accurately describes the budget-only model and cites the amendment.

## Findings
None at Critical/High/Medium. Cycle-0's finding is resolved. No new findings.
