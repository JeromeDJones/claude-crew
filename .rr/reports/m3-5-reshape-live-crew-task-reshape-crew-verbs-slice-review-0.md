# Slice Review: m3-5-reshape-live-crew task=reshape-crew-verbs

**Task:** `reshape-crew-verbs` (index 3) — owns AT 5, 6, 7, 8, 9 (live-mutation core: per-verb dispatch filling the scaffold's stage-6 stub).
**Cycle:** 0. **Verdict:** PASS.

## Summary

The scaffold's stage-6 STUB is replaced with a per-verb live dispatch (server.py, +226/−2). Reviewed the two load-bearing invariants hard and confirmed both correctly implemented AND genuinely pinned by non-hollow tests: swap ordering (spawn+record before kill) and D6 stale-override cleanup. All test commands exit 0 (7 slice + 42 non-regression). Gate stages 1–5 and lineage (7) untouched.

## Slice adherence (AT 5–9) — hard review

### AT 9 — Swap ordering (load-bearing) — VERIFIED, test NOT hollow
Impl: `spawn_teammate` → `record_topology(slot→new_id)` → THEN `kill_teammate(old_id, graceful=True)`, with `_old_id != _new_id` guard. `test_swap_ordering_record_topology_before_kill` monkeypatches BOTH `record_topology` and `kill_teammate` into a `call_order` list and asserts `record_idx < kill_idx` (targeting old id) — reversing the impl to kill-then-record fails this. Also asserts the recorded topology maps `reviewer → new_id` and the old teammate is tombstoned. Real ordering pin. ✓

### AT 8 — Drop + D6 stale-override cleanup — VERIFIED
Impl: record minus-topology before kill; sweep `_edge_overrides` for keys where `_pair[0] == _drop_slot or _pair[1] == _drop_slot` (both directions); `remove_edge_overrides`; inform survivors; graceful-kill. `test_drop_removes_node_and_stale_override_and_informs` plants `("a","b")`, drops `b`, asserts swept + recorded in `edge_overrides_removed`. Removing the sweep fails the test. ✓ (see LOW-01 re: from-direction branch)

### AT 5 — set_gate — VERIFIED
Only `set_edge_override` + `actions["edge_overrides_set"]`. No spawn/kill/inform/topology. Test asserts all other action keys empty, topology unchanged, no `crew_reshape` envelope. ✓

### AT 6 / AT 7 — add_node / augment inform — VERIFIED
Spawns new node with `_neighbors_for(slot)` (mirrors instantiate_shape); reshaped map = base ∪ {new_slot: new_id}; record_topology; informs already-running sources ONLY (freshly-spawned skipped via `if _source_slot == _new_node.slot: continue`; sources from `_base_slot_to_teammate`, deduped). Tests assert impl keeps original id (no respawn), one `neighbor_added` msg, `actions.informed == [impl_id]`, reviewer gets zero reshape msgs. ✓

### Best-effort swallowing — reviewed, does not hide bugs
`_inform` + `kill_teammate` swallow `TeammateAlreadyDeadError`/`UnknownTeammateError`. Ids originate from the recorded base topology, so a swallow means the teammate genuinely died mid-flush (spec best-effort semantics). No path swallows an error indicating a wrong-id bug for a must-exist node. ✓

### actions record — VERIFIED per branch
Each branch populates correct keys; every test asserts exact contents including empty keys for untouched branches. ✓

## Scope (Invariant 1)

Diff: `claude_crew/server.py` (declared), new `tests/test_reshape_crew_verbs.py` (declared). All dispatch symbols imported at module top. No violation.

## Non-regression

- `uv run pytest tests/test_reshape_crew_verbs.py` → **7 passed** (exit 0).
- `uv run pytest tests/test_reshape_crew_gate.py tests/test_reshape_broker_helpers.py` → **42 passed** (exit 0).

## Findings

### Critical / High / Medium
_None identified._

### Low
- [LOW-01] `slice.test.weak-coverage` — the D6 override-sweep test only plants `("a","b")` (dropped slot `b` as **to-**endpoint), exercising `_pair[1] == _drop_slot`. The symmetric `_pair[0] == _drop_slot` branch (dropped slot as **from-**endpoint) is correct in impl but not independently pinned. Not hollow (sweep is genuinely exercised); Low. Fix: add a from-direction override to the D6 test.

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — Drop's `neighbor_removed` inform fires for survivors linked via stale override (not live edge), because `Drop.apply` rejects nodes with live edges. Spec-consistent; documented in build report.
- [INFO-02] `slice.review-process.cross-slice-observation` — the neighbor list passed to the freshly-spawned node is not directly asserted (only "reviewer received no inform" negative). Covered transitively + by LIVE AT 17 (out of slice scope).

## Code-quality smoke

`server.py` dispatch: clean single-dispatch structure; helpers (`_neighbors_for`, `_build_topology`, `_inform`) cohesive + documented; narrow except clauses (no bare/broad swallow); no secrets/dead code. Same-package private access to `broker._edge_overrides`/`broker._info` acceptable. Tests: module-top imports, strong per-AT + negative-space assertions. Only nit is LOW-01.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| LOW-01 | Low | slice.test.weak-coverage | deferred-with-rationale | From-direction sweep correct but unpinned; core D6 genuinely tested. Add from-direction case at retro. |
| INFO-01 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | Drop survivor-inform override-keyed by spec design; documented. |
| INFO-02 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | New-node neighbor list verified transitively + LIVE AT 17, outside slice. |

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
