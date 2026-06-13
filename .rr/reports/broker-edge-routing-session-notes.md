# broker-edge-routing implementation notes (cycle 0 — PASS)

## What was done

Implemented task `broker-edge-routing` (index 0) of `m2-edge-routing` spec.
All 19 slice tests pass. Full suite: 1479 passed, 2 pre-existing flakes in
`test_shutdown_signals.py` (subprocess startup timing, unrelated).

## Key decisions

**Gated routing does NOT log the original envelope.** Only the wrapper
(recipient=LEAD, payload={gated_for, from, payload}) is stamped+logged.
The original id is added to `_seen_ids` for dedup. This makes
`get_messages(b_id)` return empty for gated sends, consistent with
AT#1's "inbox receives nothing" language.

**Routing guard: `env.sender in self._teammates`** (not just not-LEAD).
This ensures internal sends ("broker" sender, dead-teammate bounces) bypass
edge routing and continue with direct delivery. Only live-teammate→live-teammate
triggers `_send_routed`.

**test_broker.py SC-5 updated.** `test_teammate_send_does_not_wake_lead_poll`
was broken by the gated-fallback change (no-topology sends now wake LEAD).
Fixed by adding a `direct` edge topology. `Topology` added to imports.

## Files changed

- `claude_crew/broker.py` — UnauthorizedEdgeError, _edge_overrides, _send_routed,
  _active_topology_for, _id_to_slot, _edge_mode, _resolve_routing_mode,
  _resolve_scoped_recipient, authorize_send, send_scoped, promote_edge
- `tests/test_broker.py` — Topology import + SC-5 test fix
- `tests/test_edge_routing.py` — new, 19 tests covering AT#1-5

## Spec location

`/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/broker-edge-routing/.rr/specs/m2-edge-routing.md`

## Build report

`/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-broker-edge-routing-build-0.md`
