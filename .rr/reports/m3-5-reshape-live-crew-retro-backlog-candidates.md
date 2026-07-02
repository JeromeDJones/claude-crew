# Backlog Candidates — m3-5-reshape-live-crew (cycle 0)

**Synthesized from:** 7 slice reviews, 7 build reports, 1 plan review, 1 feature review, 1 validation report, spec.

---

## Workflow Retrospective

`WORKFLOW_RETRO_ENABLED=false` — workflow-retro suggestions are collected but not routed to BACKLOG this cycle. Stub record preserved for audit trail.

---

## Deferred Fixes (from slice reviews + feature review)

### BC-1 · Hoist inline imports in `test_sdk_teammate.py` to module top

| Field | Value |
|-------|-------|
| Source | d0-unconditional-send-to slice review — LOW-01 |
| Severity | Low |
| Tag | `quality.style` |
| Effort | Trivial (< 5 min) |
| Status | BACKLOG |

**Description:** The 3 flipped tests in `TestSdkTeammateMcpServersWiring` (in `tests/test_sdk_teammate.py`) add a function-body inline import `from claude_crew.sdk_teammate import _SEND_TO_MCP_SERVER_NAME`, violating the project's CLAUDE.md "Imports at module top" convention. The new `test_d0_send_to_unconditional.py` does it correctly. The three existing tests were coordinator-sanctioned amendments to the D0 slice; the style nit was acknowledged and deferred.

**Fix:** Hoist `from claude_crew.sdk_teammate import _SEND_TO_MCP_SERVER_NAME` to the module-level import block in `tests/test_sdk_teammate.py`. One-line change.

---

### BC-2 · Add from-direction case to D6 override-sweep test

| Field | Value |
|-------|-------|
| Source | reshape-crew-verbs slice review — LOW-01 |
| Severity | Low |
| Tag | `test.weak-coverage` |
| Effort | < 15 min |
| Status | BACKLOG |

**Description:** `test_drop_removes_node_and_stale_override_and_informs` in `tests/test_reshape_crew_verbs.py` plants only `("a","b")` as a stale override (dropped slot `b` as **to-**endpoint), exercising `_pair[1] == drop_slot`. The symmetric branch `_pair[0] == drop_slot` (dropped slot as **from-**endpoint) is correct in impl but independently unpinned. Core D6 is genuinely tested; the from-direction branch is not hollow — it is just uncovered by an independent test assertion.

**Fix:** In `test_drop_removes_node_and_stale_override_and_informs`, plant a second override keyed `("b","c")` (dropped slot `b` as from-endpoint) before the drop, and assert it also appears in `actions["edge_overrides_removed"]` and has been removed from `broker._edge_overrides`.

---

### BC-3 · Extend AT-20 staleness guard to cover `CLAUDE.md`

| Field | Value |
|-------|-------|
| Source | plan-review LOW-01; docs-sync slice review INFO-01 |
| Severity | Low (optional) |
| Tag | `test.scope-gap` |
| Effort | < 10 min |
| Status | BACKLOG |

**Description:** `tests/test_reshape_docs_staleness.py` greps only `doc/ARCHITECTURE.md` for `_has_out_edges`. `CLAUDE.md` also contained the stale conditional-wiring prose (now fixed), but no deletion-detector guards it against future re-introduction. The docs-sync slice reviewer acknowledged this and noted it matches AT-20's spec-pinned scope; it is not a defect, but extending coverage would close the gap.

**Fix:** Add `test_has_out_edges_not_in_claude_md()` to `tests/test_reshape_docs_staleness.py`, mirroring the existing `test_has_out_edges_not_in_architecture_md()` but opening `CLAUDE.md`.

---

### BC-4 · Clean up stale `_has_out_edges` reference in `doc/ideas/m3.5-reshape-live-crew.md`

| Field | Value |
|-------|-------|
| Source | feature-review Info; plan-review out-of-scope note |
| Severity | Informational |
| Tag | `docs.stale-planning-artifact` |
| Effort | Trivial |
| Status | BACKLOG (optional) |

**Description:** The planning idea file `doc/ideas/m3.5-reshape-live-crew.md` still references the removed `_has_out_edges` gate (the spawn-time conditional that D0 deleted). This is a historical planning artifact — it correctly describes the pre-D0 behavior — and is outside any AT scope. No runtime impact.

**Fix:** Annotate the stale paragraph with `<!-- superseded by D0, m3-5-reshape-live-crew -->` or delete the `_has_out_edges` description and note that it was removed. Alternatively, leave it as-is for historical fidelity (the idea file documents intent before implementation).

---

## Process / Institutional Knowledge

### BC-5 · Document the live peer-delivery edge-mode lesson in CLAUDE.md

| Field | Value |
|-------|-------|
| Source | Validation report root-cause note (initially both live ATs timed out) |
| Severity | High-signal process lesson |
| Tag | `process.test-convention` |
| Effort | 1 paragraph |
| Status | AUTO-APPLIED in doc-sync (see doc-sync checklist) |

**Description:** AT-17 and AT-18 in `tests/test_live_reshape.py` initially timed out in the live run. Root cause: the tests used the `ShapeEdge` default mode (`gated`), which per M2's `_send_routed` routes the message to the **lead's inbox**, not the recipient's inbox. The fix was direct-mode edges + `general` acting roles + 180s poll headroom. No prior live test had ever driven a real teammate `send_to` (M2 only tested the broker side at stub level).

**Lesson:** Live tests asserting teammate→teammate **peer delivery** (recipient inbox contains the message) must declare `direct`-mode edges. Gated edges route to the coordinator — a peer-delivery assertion against a gated edge will time out, not produce a meaningful failure.

**Action:** This is auto-applied as a CLAUDE.md and ARCHITECTURE.md test conventions bullet in the doc-sync phase. No separate backlog item needed after doc-sync completes.

---

## Summary Table

| ID | Description | Severity | Effort | Status |
|----|-------------|----------|--------|--------|
| BC-1 | Hoist inline imports in test_sdk_teammate.py | Low | Trivial | BACKLOG |
| BC-2 | Add from-direction case to D6 override-sweep test | Low | < 15 min | BACKLOG |
| BC-3 | Extend AT-20 staleness guard to CLAUDE.md | Low (optional) | < 10 min | BACKLOG |
| BC-4 | Clean up stale _has_out_edges in doc/ideas/ | Info | Trivial | BACKLOG (optional) |
| BC-5 | Live peer-delivery edge-mode lesson | Process | 1 paragraph | Auto-applied (doc-sync) |

No Critical or High findings carried into backlog from this feature. Feature shipped clean at the feature-review level; all Critical/High/Medium findings during the cycle were resolved before merge.
