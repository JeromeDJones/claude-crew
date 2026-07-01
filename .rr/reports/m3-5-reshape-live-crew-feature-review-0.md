# Feature Review: m3-5-reshape-live-crew

**Verdict: PASS.** Cross-slice synthesis of M3.5 `reshape_crew` — 6 implementation slices + 1 reconciliation slice, all merged. **Feature-level non-regression: `uv run pytest` → 1707 passed, 41 skipped (incl. live AT-17/18), 1 xfailed, 0 failed (247s, exit 0).** No Critical/High.

## Cross-slice integration coherence

**Scaffold+verbs composition.** Scaffold's stage-6 STUB (`server.py:1383–1425`) filled by verbs' inlined `_apply_live_reshape` (`1426–1658`):
- `record_topology` called exactly once on the approve path per mutating verb — add_node/augment (1534), drop (1574), swap (1649); set_gate none. `mark_instantiated(_sid)` (1663) is a separate lineage transition, not a second record — scaffold's INFO-01 double-record concern does not materialize.
- No dead stub code; `_actions` dict initialized once (1426), uniformly populated across all four branches.
- Swap ordering (D5/AT-9): spawn + record_topology (1630–1649) strictly precede kill_teammate(old) (1653–1655).

**D0 blast radius.** Unconditional send_to wiring coherent end-to-end: security boundary is `broker.authorize_send` (unchanged); 11 pack/allowlist consumer tests re-baselined to expect `mcp__crew-send__send_to` as always-wired framework infra; both docs describe it identically incl. the `tools:[]`→`['mcp__crew-send__send_to']` addendum.

**Mode-validation seam.** Broker helpers accept any mode by design; caller backstops: `SetGate.apply` validates `mode ∈ _VALID_MODES` (shapes.py:569) + edge-existence before the gate → invalid mode returns `stage:"adapt"`, never reaches `broker.set_edge_override`. No gap.

## Holistic spec satisfaction

All 20 ATs covered: AT-01/03 (test_d0_send_to_unconditional), AT-02 deletion-detector (`_has_out_edges` absent from sdk_teammate.py + ARCHITECTURE.md), AT-04 (`reshape_crew` registered server.py:1212), AT-05–16 (test_reshape_crew_gate + test_reshape_crew_verbs), AT-19 (test_reshape_broker_helpers), AT-20 (test_reshape_docs_staleness), AT-17/18 LIVE (test_live_reshape, skipped in stub — coordinator owns live gate). M1.5 gate reused not duplicated (broker.py +33/-1 additive). D6 override cleanup present (drop sweeps _edge_overrides on dropped slot; swap preserves).

## Cracks-fell-through

- Informing-message payload uniform across verbs (`{type:"crew_reshape", event:"neighbor_added"|"neighbor_removed", ...}` via single best-effort `_inform`, swallows dead/unknown errors, records only successes).
- Error-envelope shapes consistent (`stage:"base"|"verb"|"adapt"|"gate"`); verb dispatch raises no new shapes.
- Drop's `neighbor_removed` fires only for stale-override-linked survivors (Drop.apply rejects live-edge nodes) — spec-consistent.

## Findings

### Critical / High / Medium / Low
_None identified._

### Info (retro inventory)
- Carried-forward slice deferrals: d0 LOW-01 (module-top import hoist), verbs LOW-01 (from-direction D6-sweep test case), docs INFO-01 / plan-review LOW-01 (AT-20 staleness covers ARCHITECTURE.md only — matches its pinned scope).
- `doc/ideas/m3.5-reshape-live-crew.md` still references removed `_has_out_edges` (historical planning artifact, outside AT-02 scope; harmless).

## Verdict

No Critical/High. Full suite green at feature level. Cross-slice composition, D0 coherence, holistic AT coverage all satisfied.

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
