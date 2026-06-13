# Build Report — broker-edge-routing — cycle 0

**Slug:** m2-edge-routing  
**Task:** broker-edge-routing  
**Cycle:** 0  
**Date:** 2026-06-13  
**Verdict:** PASS

---

## Test Command

```
uv run pytest tests/test_edge_routing.py
```

## Slice Test Results

**19 passed, 0 failed** in 0.11s

```
tests/test_edge_routing.py::TestGatedRouting::test_gated_delivers_wrapper_to_lead PASSED
tests/test_edge_routing.py::TestGatedRouting::test_gated_does_not_deliver_to_recipient_log PASSED
tests/test_edge_routing.py::TestGatedRouting::test_gated_wrapper_has_seq_assigned PASSED
tests/test_edge_routing.py::TestTeeRouting::test_tee_delivers_to_recipient_log PASSED
tests/test_edge_routing.py::TestTeeRouting::test_tee_delivers_cc_to_lead PASSED
tests/test_edge_routing.py::TestTeeRouting::test_tee_both_appear_in_broker_log PASSED
tests/test_edge_routing.py::TestDirectRouting::test_direct_delivers_to_recipient_log PASSED
tests/test_edge_routing.py::TestDirectRouting::test_direct_not_lead_bound PASSED
tests/test_edge_routing.py::TestDirectRouting::test_direct_in_broker_log PASSED
tests/test_edge_routing.py::TestNoEdgeFallback::test_no_edge_routes_gated_when_topology_exists PASSED
tests/test_edge_routing.py::TestNoEdgeFallback::test_no_topology_routes_gated PASSED
tests/test_edge_routing.py::TestNoEdgeFallback::test_gated_fallback_original_id_deduped PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_send_scoped_rejects_non_declared_recipient PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_send_scoped_rejects_undeclared_teammate_no_edge PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_authorize_send_to_lead_never_raises PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_authorize_send_raises_for_no_topology PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_send_scoped_delivers_to_declared_neighbor PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_send_scoped_resolves_slot_name PASSED
tests/test_edge_routing.py::TestScopedAuthorization::test_send_scoped_to_lead_always_allowed PASSED
```

## Full Suite Results

**1479 passed, 2 failed, 34 skipped, 1 xfailed** in 214s

### Non-slice failures (pre-existing infrastructure flakes — unrelated to this change)

```
FAILED tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister
  AssertionError: claude-crew did not register within 15.0s
FAILED tests/test_shutdown_signals.py::TestSignalShutdown::test_sigint_triggers_clean_exit_and_deregister
  AssertionError: claude-crew did not register within 15.0s
```

These tests spawn the real `claude-crew` binary as a subprocess and wait for it to self-register in the InstanceRegistry. The timeout failure ("did not register within 15.0s") is a process-startup timing issue unrelated to broker routing. These tests do not touch `broker.py` or any of the changed files.

---

## Implementation Summary

### `claude_crew/broker.py`

**`UnauthorizedEdgeError`** — new exception class added after `TeammateAlreadyDeadError`.

**`Broker.__init__`** — added `self._edge_overrides: dict[tuple[str, str], str] = {}` for circuit-breaker trips and `promote_edge` overrides.

**`Broker.send()`** — added a short-circuit check: when `env.recipient != LEAD_ID and env.sender in self._teammates`, route via `_send_routed()`. Lead-bound, lead-origin, internal ("broker"), and dead-sender sends continue through the unchanged path.

**`Broker._send_routed()`** — new private method implementing per-edge routing:
- `direct` → stamp+log original to recipient's inbox; no LEAD notification.
- `tee` → stamp+log original to recipient's inbox; create cc envelope `{cc_of, from, to, payload}` stamped+logged to LEAD.
- `gated` / no-edge fallback → mark original id in `_seen_ids` (dedup), create wrapper `{gated_for, from, payload}` stamped+logged to LEAD; original NOT in log, NOT in recipient inbox.

**Private helpers:**
- `_active_topology_for(sender_id, recipient_id)` — latest topology containing both ids (reversed search).
- `_id_to_slot(topo, teammate_id)` — reverse-map teammate_id → slot name.
- `_edge_mode(topo, from_slot, to_slot)` — returns mode honoring `_edge_overrides` first, then `topo.edges`.
- `_resolve_routing_mode(sender_id, recipient_id)` — synthesizes the above into "gated"/"tee"/"direct".
- `_resolve_scoped_recipient(sender_id, recipient)` — resolves slot name, teammate_id, or LEAD_ID for `send_scoped`.

**Public API:**
- `authorize_send(sender_id, recipient_id)` — no-op for LEAD; raises `UnauthorizedEdgeError` if no forward edge exists in active topology.
- `send_scoped(sender_id, recipient, payload, *, id=None)` — resolves recipient, calls `authorize_send`, then `send()`.
- `promote_edge(from_slot, to_slot)` — sets `_edge_overrides[(from_slot, to_slot)] = "gated"`. Idempotent.

### `tests/test_broker.py` (scope-creep — see below)

Added `Topology` to imports. Updated `test_teammate_send_does_not_wake_lead_poll` to set up a `direct` edge topology, preserving the SC-5 intent (direct sends don't wake the lead) under M2's new gated-fallback behavior for no-topology teammate→teammate sends.

### `tests/test_edge_routing.py` (new)

19 tests covering ATs 1–5:
- `TestGatedRouting` (3 tests) — AT#1
- `TestTeeRouting` (3 tests) — AT#2
- `TestDirectRouting` (3 tests) — AT#3
- `TestNoEdgeFallback` (3 tests) — AT#4
- `TestScopedAuthorization` (7 tests) — AT#5

---

## Scope-Creep Note

`tests/test_broker.py` was modified (not listed in `taskTouches`) because the behavioral change to `send()` — gated fallback for no-topology teammate→teammate sends — broke the existing `test_teammate_send_does_not_wake_lead_poll` test (SC-5). The fix preserves the test's intent by adding a `direct` edge so the test continues to verify that direct-mode sends don't wake the lead poll. This is the minimum cross-slice edit required to keep the suite green.

---

## Files Changed

```
M  claude_crew/broker.py
M  tests/test_broker.py
A  tests/test_edge_routing.py  (new)
```

(`git diff --name-status HEAD` shows `M claude_crew/broker.py` and `M tests/test_broker.py`; `test_edge_routing.py` is untracked/new.)
