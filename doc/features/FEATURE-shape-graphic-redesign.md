# Feature: shape-graphic-redesign

**Status:** Shipped (2026-06-29)  
**Spec:** `.rr/specs/shape-graphic-redesign.md`  
**Validation:** PASS — 1648 passed, 39 skipped, 1 xfailed (246.80s)  
**Advances:** Success Criterion 4 (live observability — roomier, more legible shape graphic)

---

## Problem

The Mission Control shape graphic was too cramped to be legible. Two surfaces rendered crew topology / workflow shapes:

- The live in-rail `TopologyGraph` — jammed into a static ~240px host.
- The `ShapeProposalCard` proposal gate — capped at `maxWidth: 420px`, different visual idiom.

Neither gave the operator room to comprehend the whole shape at non-trivial crew sizes. At narrower desktop widths (~1024px) the fixed 320px left rail dominated the layout.

---

## What Was Built

### Shared zoom/pan modal substrate (`dashboard.html`)

One shared implementation feeding two thin openers. Globally-scoped vanilla JS:

| Symbol | Responsibility |
|--------|---------------|
| `fitToHost(hostEl)` | Measures SVG at `scale(1)`; computes largest zoom fitting host (`w-24`, `h-24`); floor **0.05** (not the old 0.3); writes `dataset.zoom/tx/ty`; calls `applyTransform`. |
| `applyTransform(hostEl)` | Applies `translate(tx,ty) scale(z)` on `.pan-layer`; updates zoom %-label to `Math.round(z*100)+'%'` (actual zoom, not hardcoded). |
| `bindPanZoom(hostEl)` | Wheel=zoom (clamp `[0.25, 4]`), mousedown/move/up=pan, `no-anim` during gesture. `panZoomBoundRef` guard prevents double-bind in React strict mode. |
| `renderInto(hostEl, src, edgeStats)` | Renders mermaid → wraps SVG in `.pan-layer` → DOMPurify sanitizes → decorates edges via `mapEdgeStatsToPaths` → double-rAF auto-fit. |
| `openTopologyModal()` | Reads `window._topoModalData` (live `mermaidSrc` + `edgeStats`); injects `.modal-backdrop > .modal`; binds pan/zoom; calls `renderInto`. |
| `openProposalModal(proposal)` | Reads `proposal.shape` from `/api/state`; calls `shapeToMermaidUnified(proposal.shape, {proposed:true})`; same modal substrate; no new per-instance fetch. |

**Invariant:** `.modal-body` uses class `zoom-surface` (flex:1 fills the modal body), NEVER `.topology-host` (the in-rail height cap that was causing the 340px-stuck-in-754px-modal layout bug).

### `shapeToMermaidUnified(shape, {proposed})` client helper

Produces a `graph TD` mermaid source using the same `.nodecard` foreignObject labels as the live `TopologyGraph`. Gated edges are expanded through `lead` (sender→lead→receiver) matching `displayEdges`. `proposed:true` adds `.nodecard.proposed` (dashed-accent, `border-style: dashed`, lower dot opacity). Renders through the existing `securityLevel:'strict'` + DOMPurify pipeline — no new renderer, no relaxed sanitize config.

### `/api/state` additive `shape` key (one-line change in `ui_server.py`)

`shape_to_dict(p.shape)` serialized alongside the existing `mermaid` key (retained for back-compat). `list_pending_shapes` tool reads `mermaid` and is unaffected. `openProposalModal` reads the new `shape` key to feed `shapeToMermaidUnified`.

### In-rail topology host changes

- `.topo-head` control row (title + subtitle + −/fit/+/expand buttons) replaces old static header divs.
- Host height: `clamp(220, 240 + 18·max(0, agents−3), 340)px`. 3-agent crew → 240px (unchanged); 6-agent crew → 294px; 8+ agents → 340px cap.
- `window._topoModalData` updated after each render for the modal opener.

### Responsive `.dash-grid` class

Replaces hardcoded `gridTemplateColumns: "320px minmax(0, 1fr)"` JSX inline style in `MissionControlLayout`. Default track: `320px minmax(0, 1fr)`. `@media (max-width:1024px)` → `clamp(220px, 22vw, 260px) minmax(0,1fr)`. Two-pane layout preserved at all supported widths.

---

## Acceptance Tests

| # | Description | Kind |
|---|-------------|------|
| AT 1 | Roomier in-rail host + `.topo-head` controls | Playwright |
| AT 2 | `.topo-head` / `openTopologyModal` structural | grep |
| AT 3 | Modal fits whole graph (8-agent crew) | Playwright |
| AT 4 | `.modal-body` uses `.zoom-surface`, footer pinned | Playwright |
| AT 5 | Zoom label reflects actual zoom percentage | Playwright |
| AT 6 | Roster/empty topology degrades gracefully in modal | Playwright |
| AT 7 | `zoom-surface` + double-rAF structural | grep |
| AT 8 | `mapEdgeStatsToPaths` + `_source` + `/edge-log/` + `promote-to-gated` non-regression | grep |
| AT 9 | `/api/state` proposal carries both `mermaid` and `shape` (gated edge mode) | pytest (non-browser) |
| AT 10 | `shape_to_dict` wired in `ui_server.py` serialization | grep |
| AT 11 | Unified `--edge-*` stroke tokens: live direct + proposal gated | Playwright |
| AT 12 | Proposal nodes carry `.nodecard.proposed` + `border-style: dashed` | Playwright |
| AT 13 | `shapeToMermaidUnified` + `.nodecard.proposed` structural | grep |
| AT 14 | Proposal card is thumbnail → opens modal; `maxWidth: 420` absent | Playwright |
| AT 15 | `securityLevel: 'strict'` preserved + XSS suite green | grep + Playwright |
| AT 16 | Responsive ≤1024px: rail ≤260px, two-pane preserved | Playwright |
| AT 17 | Responsive ≥1440px: rail >260px (base 320px track) | Playwright |
| AT 18 | `dash-grid` + clamp present, hardcoded inline JSX grid absent | grep |

**Test command:**
```bash
uv run pytest -m dashboard && uv run pytest tests/test_shape_proposal_payload.py
```

**Full suite result:** 1648 passed, 39 skipped, 1 xfailed — exit 0.

---

## Key Invariants Preserved

- **XSS hardening**: `shapeToMermaidUnified` feeds `renderInto` which uses the existing `securityLevel:'strict'` + DOMPurify config unchanged.
- **Multi-instance LEADER**: `openProposalModal` reads `proposal.shape` from the already-aggregated `/api/state` response; no new per-instance endpoint introduced.
- **Back-compat `mermaid` key**: retained on wire; `list_pending_shapes` (`server.py`) unaffected.
- **`window.mapEdgeStatsToPaths` keyed lookup**: BC-03 deletion-detector unchanged; existing `test_unified_topology_keyed_lookup.py` AT-5/AT-6 stay green.
- **`displayEdges` gated-bridge `_source` expansion**: preserved byte-compatibly.
- **`/edge-log/` fetch + promote-to-gated control**: preserved; AT-8 structural guard covers both literals.

---

## Task Breakout

| Task | Files | ATs |
|------|-------|-----|
| `proposal-payload-structured-shape` | `claude_crew/ui_server.py`, `tests/test_shape_proposal_payload.py` | AT 9, 10 |
| `shared-zoom-pan-modal` | `claude_crew/ui/dashboard.html`, `tests/**` | AT 1–8 |
| `unified-node-language-proposal` | `claude_crew/ui/dashboard.html`, `tests/**` | AT 11–15 |
| `responsive-grid-1024` | `claude_crew/ui/dashboard.html`, `tests/**` | AT 16–18 |

Tasks 1 and 2 are parallelizable (disjoint file footprints). Tasks 2→3→4 are serialized (all edit `dashboard.html`).

---

## Process Lessons (retro)

- **`dashboard.html`-touching task `testCommand` must include the full `-m dashboard` suite.** A scoped subset passes while sibling test files that exercise the same UI surface remain undetected. Observed: task 3 cycle-0 mis-labeled 4 regressions (from task 2's expand-button SVG) as "pre-existing" using `git stash` baseline; cycle-1 corrected. See backlog candidate C1.
- **"Pre-existing" classification requires a master baseline, not `git stash`.** In a serial dependency chain, `git stash` pops to the post-prior-tasks state which already contains earlier-task regressions.
