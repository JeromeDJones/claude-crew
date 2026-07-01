# Plan Review: m3-5-reshape-live-crew (cycle 0)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md
**Cycle:** 0
**Reviewed:** 2026-06-30

**Verdict:** PASS

## Summary

Reviewed the combined spec + task-breakout for live in-place crew reshaping
(`reshape_crew` MCP tool + D0 unconditional `send_to` wiring + three additive
broker helpers). The spec is unusually strong: every provenance claim it makes
about existing code was independently verified against the tree and holds, the
inverse-coverage check passes (every named deliverable has a failing-if-absent
AT, including both live tests and the D0 flip), and the 20 ATs map one-to-one
onto six well-scoped tasks with sound dependency edges. Two Medium clarity gaps
(drop override-cleanup algorithm; `record_topology` ownership across the
scaffold/verbs task split) and two Lows remain — none Critical or High, so the
verdict is PASS.

## Findings

### Critical

_None identified._

### High

_None identified._

### Medium

- [MEDIUM-01] `spec.specification.ambiguous` — Specification §6 (drop) + AT 8:
  the drop path calls `broker.remove_edge_overrides([...removed (from,to)
  pairs...])`, but never defines how "removed pairs" are computed. `Drop.apply`
  (verified, shapes.py:646-657) *rejects* nodes with live edges, so by the time
  a drop is legal the diff between base and new shape contains **no** edge
  removal — a diff-derived pair set is empty. Yet AT 8(c) requires the stale
  `_edge_overrides[("a","b")]` (an override that lingers because its edge was
  removed by a *prior* verb, not this one) to be deleted. A literal reading of
  "removed (from,to) pairs" yields an empty set and AT 8(c) fails. The intended
  algorithm — scan `_edge_overrides` for every key whose `from_slot` **or**
  `to_slot` equals the dropped slot — is only hinted in the Edge Cases section
  ("plus any dangling override keys"). An autonomous implementor could build the
  wrong thing and only discover it when AT 8 goes red. Spell the algorithm out in
  the Specification. Category: clarify-assumption.

- [MEDIUM-02] `spec.task-breakout.ambiguous` — Tasks `reshape-crew-scaffold` and
  `reshape-crew-verbs` split `record_topology` ownership ambiguously. The
  scaffold description assigns "the approve path's lineage (mark_instantiated +
  record_topology of the reshaped topology)" to itself, while "Leaves the
  per-verb live-effect dispatch as a stub." But for add_node/augment/swap the
  reshaped `Topology.slot_to_teammate` requires the **spawned teammate_id**,
  which is produced only inside the verbs task's `_apply_live_reshape`. So the
  scaffold cannot build (nor record) the reshaped topology for any mutating verb,
  yet AT 11 (lineage: "its topology is the latest recorded") is assigned to the
  scaffold task alone. Either AT 11's scaffold test must restrict itself to a
  non-topology-appending path, or `record_topology` belongs entirely inside
  `_apply_live_reshape` (verbs task) with the scaffold owning only gate +
  `mark_instantiated`. Clarify the boundary so the implementor of the scaffold
  task knows whether it calls `record_topology` at all. Category:
  clarify-assumption.

### Low

- [LOW-01] `spec.acceptance-test.coverage-gap` — AT 20's NAMED-LITERAL staleness
  guard greps only `doc/ARCHITECTURE.md` for `_has_out_edges`. `CLAUDE.md`'s
  "Known limitations" / SDK-wiring prose is required to be *updated* by AT 20 and
  by the `reshape-docs-sync` task, but no deletion-detector guards it against
  surviving stale "`send_to` only for out-edge teammates" prose. Extend the grep
  guard to `CLAUDE.md` (or `doc/sdk-teammate-wiring.md`). Category:
  add-acceptance-test.

- [LOW-02] `spec.task-breakout.spurious-edge` — `reshape-crew-scaffold`
  `dependsOn: [broker-live-reshape-helpers]`, but the scaffold's own ATs
  (4, 10-16) exercise gate/negative branches that use only pre-existing broker
  methods (`register_proposal`, `await_proposal`, `mark_instantiated`); the three
  NEW helpers (`latest_topology`, `set_edge_override`, `remove_edge_overrides`)
  are consumed in the verbs task, not the scaffold. The two tasks touch disjoint
  files (`broker.py` vs `server.py`) and could run in parallel. The edge is
  defensible-conservative (and entangled with MEDIUM-02's `record_topology`
  question), so this is informational, not a required change. Category:
  remove-contradiction.

## Persistent Findings

_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear
- **Test command:** Present and runnable
- **Scope boundary:** Clear

## Verdict Rule Applied

PASS: no Critical or High findings (2 Medium, 2 Low — advisory only).
