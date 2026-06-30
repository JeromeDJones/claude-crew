# Slice Review: shape-graphic-redesign task=unified-node-language-proposal

**Cycle:** 0 (build cycle 1 — task was reworked) · **Verdict:** PASS

## Check 1 — Slice adherence (owned ATs: 11,12,13,14,15)

All five owned acceptance tests are implemented in the new `tests/test_unified_proposal_language.py` (10 test cases) and pass in my re-run. Verified against the spec:

- **AT 11** — asserts the live `direct` edge stroke equals `var(--edge-direct)` AND the proposal-modal `gated` edge stroke equals `var(--edge-gated)`. Real cross-surface token-vocabulary assertion, not a structural stand-in. ✓
- **AT 12** — `#proposal-modal .nodecard.proposed` count > 0 AND computed `border-style` includes `dashed`. ✓
- **AT 13** — structural detectors for `shapeToMermaidUnified` and `.nodecard.proposed`. ✓
- **AT 14** — `maxWidth: 420` literal absent from `dashboard.html`; thumbnail present; modal not visible pre-click; click opens `#proposal-modal .modal` with `.zoom-surface`. ✓
- **AT 15** — `securityLevel: 'strict'` literal present; the browser XSS suite (`test_dashboard_artifact_xss.py`) runs in the command and passes. ✓

**Deferred-test deliverable check:** none deferred. Every named deliverable (`shapeToMermaidUnified`, `openProposalModal`, `.nodecard.proposed`, the thumbnail rewrite) is exercised by an owned AT. The `openProposalModal` path was verified to introduce **no new per-instance `fetch`** — it builds `edgeStats` locally from the structured shape already on `/api/state` and reuses `renderInto`, preserving the multi-instance LEADER invariant (CLAUDE.md). [`Info`]

## Check 2 — Non-regression

- Slice command `uv run pytest -m dashboard tests/test_unified_proposal_language.py tests/dashboard/test_dashboard_artifact_xss.py` → **10 passed** (exit 0). [`nonregression.pass`]
- **Full dashboard suite** `uv run pytest -m dashboard` (run per coordinator instruction — the scoped-command gap is exactly what masked the cycle-0 regression) → **72 passed, 1613 deselected, 0 failed** in 208s. [`nonregression.pass`]

The cross-task command `uv run pytest tests/test_shape_proposal_payload.py` is owned by the sibling `proposal-payload-structured-shape` slice (already merged at `d137f37`); not re-run here as it is out of this slice's footprint. [`Info` / cross-slice]

## Check 3 — Code-quality smoke (changed files)

I scrutinized all five collateral test-file edits the coordinator flagged. **Every edit is intent-preserving locator/scope narrowing or assertion-strengthening — no weakening found:**

- **`test_edge_dashboard.py` ~657** (`svgs.count() == 1`) — narrowed `.rail-topology svg` → `#topo-host svg`. `#topo-host` is the topology canvas containing only the mermaid flowchart SVG; the expand-button SVG lives in the sibling `.topo-head`. So `count() == 1` is *more correct* than the now-2-match `.rail-topology svg`, not weaker. The "exactly one unified graph" intent is preserved. ✓
- **~662 legacy `radialGradient#mcCenter` absence** — same `#topo-host` scope; the legacy radial would have been inside the topology SVG, so the count==0 deletion-detector remains meaningful. ✓
- **`test_dashboard_render.py` ~1040 path query** — `#topo-host svg path`; the `/edge-log` crewId capture assertion downstream is unchanged. ✓
- **gated-bridge two-amber-segments** (`test_edge_dashboard.py` ~838) — `#topo-host path.flowchart-link`; the per-path color/segment assertions are untouched and still meaningfully test the bridge. ✓
- **slot-to-teammate (AT-8)** — `wait_for` locator only; assertion body unchanged. ✓
- **`test_shape_resurface.py` / `test_shape_render.py`** — adapted to the new thumbnail→modal design (SVG now renders in `#proposal-modal`, not inline). `test_shape_resurface` is actually **stronger** (old accepted SVG-or-code fallback; new requires SVG in the modal). `test_shape_render` faithfully clicks the thumbnail, asserts SVG + slot labels (`implementor`/`reviewer`) visible in the modal, and keeps Approve/Decline in the gate panel. ✓
- **`test_roster_spotlight.py`** — pure `#topo-host svg` narrowing + count==1. ✓

`dashboard.html`: `maxWidth: 420` is a clean deletion; new JS (`shapeToMermaidUnified`, `openProposalModal`) is consistent with the existing render pipeline.

No Critical/High/Medium findings.

## Findings

| Severity | Tag | Note |
|----------|-----|------|
| Info | nonregression.cross-slice | `test_shape_proposal_payload.py` owned by sibling slice (already merged); not in this footprint. |
| Info | invariant.preserved | `openProposalModal` adds no new per-instance fetch — multi-instance LEADER invariant intact. |

Verdict rule: no Critical or High → **PASS**.

_Coordinator note: cycle-0 surfaced a real (test-locator) regression mis-labeled "pre-existing"; the coordinator caught it by verifying against master, and cycle 1 fixed all 8 remaining `.rail-topology svg` collisions. Process lesson for retro: per-task test commands on `dashboard.html`-touching tasks should run the full `-m dashboard` suite, and implementors must baseline "pre-existing" against master, not git-stash._
