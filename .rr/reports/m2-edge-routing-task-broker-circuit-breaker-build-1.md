# Build Report — broker-circuit-breaker — cycle 1

**Slug:** m2-edge-routing  
**Task:** broker-circuit-breaker  
**Cycle:** 1  
**Date:** 2026-06-13  
**Verdict:** PASS

---

## Spec Amendment Applied

2-node deadlock detection was **dropped entirely** from the spec (operator decision, 2026-06-13). Rationale: a "A waits B waits A" detector is not well-defined at the message-bus level; the non-clearing `_edge_pending` implementation over-trips on legitimate reciprocal `a→b→a→b` traffic that AT#10 requires to stay off the lead. The per-edge exchange budget is now the **sole** trip condition.

---

## Test Command

```
uv run pytest tests/test_circuit_breaker.py
```

## Slice Test Results

**17 passed, 0 failed** in 0.09s

```
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_routes_to_lead PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_edge_becomes_gated PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_gated_wrapper_in_lead PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_breaker_idempotent_no_duplicate_control_envelope PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_on_tee_edge PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_module_constant_default_value PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_broker_default_budget_is_module_constant PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_exchange_counter_in_edge_stats PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_reciprocal_below_budget_stays_off_lead PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_reciprocal_below_budget_no_breaker_trip PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_reciprocal_ab_edge_trips_when_budget_exceeded PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_reciprocal_ba_edge_trips_independently PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_reciprocal_no_pending_state_left_on_broker PASSED
tests/test_circuit_breaker.py::TestBreakerReciprocalExchanges::test_edge_stats_exchanges_counted_per_direction PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_empty_without_topology PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_edge_stats_populated_for_recorded_topology PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_edge_stats_reflect_override_mode PASSED
```

## Full Suite Results

**1467 passed, 0 failed, 34 skipped, 1 xfailed** in ~83s (excluding pre-existing `test_shutdown_signals.py` flakes)

### Non-slice failures (pre-existing infrastructure flakes — unrelated to this change)

```
FAILED tests/test_shutdown_signals.py — process-startup timing (pre-existing, confirmed by stash test in cycle 0)
```

---

## Changes Summary (Cycle 1 — Spec Amendment)

### `claude_crew/broker.py`

**Removed `_edge_pending`** — the `dict[tuple[str, str], bool]` deadlock-detection field was removed from `Broker.__init__`. No other code referenced it besides `_apply_circuit_breaker`.

**Simplified `_apply_circuit_breaker`** — removed all deadlock-detection logic:
- Removed `reverse_key` computation
- Removed `self._edge_pending[forward_key] = True` assignment
- Removed `if self._edge_pending.get(reverse_key, False): trip_reason = "deadlock"` check
- Removed `trip_reason` variable; `reason` in the control-envelope payload is now the hardcoded string `"budget_exceeded"`
- The sole remaining trip condition: `count > self._circuit_breaker_max_exchanges`

The method now has a single clean flow: resolve slots → increment counter → idempotency guard → budget check → trip (or return unchanged).

**Updated docstring** — reflects "budget is the sole trip condition (no deadlock detector)".

Everything else from cycle 0 is unchanged: `CIRCUIT_BREAKER_MAX_EXCHANGES`, `EdgeStat`, `topology_edge_stats` on `BrokerSnapshot`, `_edge_exchanges`, `_edge_tripped`, `_circuit_breaker_max_exchanges`, `promote_edge`, the gated-wrapper routing, and `snapshot()` population.

### `tests/test_circuit_breaker.py`

**Dropped `TestBreakerDeadlock`** (6 tests) — tested the removed deadlock-detection behavior.

**Added `TestBreakerReciprocalExchanges`** (6 tests) — new AT#7 coverage:

| Test | What it verifies |
|------|-----------------|
| `test_reciprocal_below_budget_stays_off_lead` | `a→b→a→b` sequence below budget: all 4 messages delivered directly, `get_messages(LEAD)` empty |
| `test_reciprocal_below_budget_no_breaker_trip` | 3 hops each direction at exactly budget=3: no control envelope, no edge overrides |
| `test_reciprocal_ab_edge_trips_when_budget_exceeded` | `(N+1)`-th `a→b` trips `a→b` with `reason:"budget_exceeded"`; `b→a` untouched |
| `test_reciprocal_ba_edge_trips_independently` | `b→a` edge has its own independent counter and trips separately |
| `test_reciprocal_no_pending_state_left_on_broker` | `_edge_pending` attribute does NOT exist on broker (regression guard) |
| `test_edge_stats_exchanges_counted_per_direction` | `topology_edge_stats` shows per-direction independent exchange counts |

**Retained `TestBreakerBudget`** (8 tests) and **`TestEdgeStatSnapshot`** (3 tests) unchanged from cycle 0.

---

## Scope-Creep Note

None — only `claude_crew/broker.py` and `tests/test_circuit_breaker.py` were touched. The spec file `.rr/specs/m2-edge-routing.md` was updated by the operator; no code changes were made to it.

---

## Files Changed

```
M  claude_crew/broker.py
A  tests/test_circuit_breaker.py  (new, untracked)
```

(`git diff --name-status HEAD` shows `M .rr/specs/m2-edge-routing.md` and `M claude_crew/broker.py`; `tests/test_circuit_breaker.py` is untracked/new.)
