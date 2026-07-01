# Slice Review: m3-5-reshape-live-crew task=reshape-crew-scaffold

**Task:** `reshape-crew-scaffold` (index 2) — owns AT 4, 10, 11, 12, 13, 14, 15, 16 (D1 `reshape_crew` tool: base/verb/role/apply/gate/lineage stages; live-mutation dispatch left stubbed).
**Cycle:** 0. **Verdict:** PASS.

## Summary

The `reshape_crew` MCP tool (server.py, +250 lines) implements stages 1–5 and 7 of the spec verbatim, with stage 6 (per-verb live effects) left as a **clearly-marked STUB** for the `reshape-crew-verbs` task. Independently verified the critical scope boundary: no live mutation is prematurely implemented. A new 23-test file covers all eight owned ATs. All test commands exit 0 in re-run.

## Slice adherence (AT 4, 10–16)

Each owned AT maps to a dedicated test class (verified by reading the tests):

| AT | Test class / count | Verified assertion |
|----|-----|-----|
| 4 (tool registered, deletion-detector) | `TestReshapeCrewToolRegistered` (1) | `"reshape_crew"` present in `list_tools()` |
| 10 (gate decline/timeout, no mutation) | `TestGateDeclineOrTimeout` (4) | decline & timeout → `stage:"gate"`; crew untouched |
| 11 (approve + lineage) | `TestGateApproveLineage` (3) | `ok:True`, `status:"instantiated"`; shape_id reaches `stage:"gate"` on 2nd call; diff carried |
| 12 (unknown base) | `TestUnknownBase` (2) | `stage:"base"`; no proposal registered |
| 13 (non-instantiated base) | `TestNonInstantiatedBase` (4) | pending/approved/declined/timed_out → `stage:"base"` |
| 14 (unknown verb) | `TestUnknownVerb` (2) | `stage:"verb"`; no proposal registered |
| 15 (unresolvable role) | `TestUnresolvableRole` (4) | swap+augment unknown role → `stage:"adapt"` + `unresolved_roles`; absent known_roles skips → gate |
| 16 (illegal mutation) | `TestIllegalMutation` (3) | duplicate slot `ShapeValidationError` → `stage:"adapt"`; no proposal; no mutation |

Stages 1–5 + 7 verified against spec §Specification (base resolution rejects None/non-instantiated; verb guard `_KNOWN_VERBS`; role resolution reuses `known_roles`/`resolve_role` seam; verb apply mirrors adapt_shape; M1.5 gate REUSED via register_proposal + await_proposal, not reimplemented; lineage mark_instantiated + latest_topology).

## CRITICAL scope check — live-mutation dispatch is a genuine STUB

Verified by grepping the entire function body (1310–1461): **zero** real calls to `spawn_teammate`/`kill_teammate`/`set_edge_override`/`record_topology`/`remove_edge_overrides`/`broker.send`. Every occurrence is inside the stage-6 block comment (the handoff contract for the verbs task). Stage 6's executable body is only `_actions = {"spawned":[], ...}` + `# STUB` marker. Real broker calls: `get_proposal`, `register_proposal`, `await_proposal` (gate), `mark_instantiated` (lineage), `latest_topology` (read-only) — all scaffold-sanctioned. `record_topology` correctly lives in the stub. No premature mutation.

## Scope (Invariant 1)

Diff: `claude_crew/server.py` (declared), new `tests/test_reshape_crew_gate.py` (declared). No out-of-scope files.

## Non-regression

- `uv run pytest tests/test_reshape_crew_gate.py` → **23 passed** (exit 0).
- `uv run pytest tests/test_shape_adapt_tool.py` → **14 passed** (exit 0).

## Findings

### Critical / High / Medium / Low
_None identified._

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — approve path calls `mark_instantiated` + returns `status:"instantiated"` while applying no crew mutation (stage 6 stubbed). By design; resolved when `reshape-crew-verbs` fills dispatch. Feature-reviewer confirms composed result.
- [INFO-02] `slice.review-process.cross-slice-observation` — AT 5–9 (per-verb live effects) and AT 17–18 (LIVE regression) owned by `reshape-crew-verbs`; the STUB block comment is the handoff contract.

## Code-quality smoke

`server.py`: docstring enumerates every failure envelope + success shape; error mapping is intentional stage-classification (structured envelopes, not swallowed). No secrets/dead code; verb construction consistent with adapt_shape. Stage-6 block comment is load-bearing handoff doc. `tests/test_reshape_crew_gate.py`: clean AT-mapped classes; concurrent-task poll correctly exercises the blocking gate; negatives assert stage envelope AND no-proposal/no-mutation postcondition.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| INFO-01 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | Scaffold marks instantiated without live effect by design; feature-reviewer verifies composed result post-verbs. |
| INFO-02 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | AT 5–9/17–18 owned by reshape-crew-verbs; STUB comment is handoff contract. |

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
