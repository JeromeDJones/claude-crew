# Slice Review: workflow-shape-composition-m0 task=dashboard-shape-render

## Scope
Task index 4, owns acceptance test 13. Reviewed from coordinator ground-truth diff (`claude_crew/ui/dashboard.html`, +126) and `tests/test_shape_render.py` (3 Playwright tests). Coordinator-run: slice green (3 passed, exit 0), full suite only the 2 acknowledged pre-existing `test_shutdown_signals` baseline flakes. Read-tool-only review.

## Check 1 — Slice adherence (AT13)

**(a) Graphical DAG via REUSED mermaid pipeline — PASS.** `ShapeProposalCard` wraps `proposal.mermaid` in `<pre><code className="language-mermaid">{...}</code></pre>` and invokes `renderMermaidBlocks(el)` through a ref callback after mount. This deliberately drives the **shipped** `renderMermaidBlocks` DOM-walk renderer (which carries the `securityLevel:'strict'` + DOMPurify/foreignObject XSS hardening) rather than reinventing a renderer — exactly the required pattern. The mermaid source is consumed from `/api/state` via `cli.shape_proposals` (the field the sibling state-route slice surfaces).

**(b) Approve/Decline controls — PASS.** `decide(decision)` POSTs `{decision}` to `/shape-approval/${proposal.crew_id}/${proposal.shape_id}`. Critically it routes by `proposal.crew_id` (carried in the state payload), so it is **crew-aware by construction** — honoring the CLAUDE.md multi-instance trap (the leader's `_proxy_shape_approval` forwards to the owning follower). Buttons are disabled while `busy`; failure re-enables via `.catch(() => setBusy(false))`.

**CRITICAL Playwright assertions — SATISFIED, non-vacuous.**
- `test_shape_gate_renders_dag_with_visible_labels`: waits for `.shape-gate-panel`, asserts ≥1 `<svg>` inside it (mermaid.render ran), then asserts slot names `implementor`/`reviewer` are present **both** in `panel.inner_text()` **and** via a JS sweep of `foreignObject *` / `<text>` nodes — a genuine "labels visible, not black boxes" check that directly targets the prior foreignObject lesson. Also asserts Approve/Decline buttons present. Real DOM/SVG assertions, not "page loaded".
- `test_shape_gate_xss_guard`: embeds `<script>window.XSS_SHAPE_FIRED=true</script>` + `<img src=x onerror=...>` into a node's **role** text (so `shape_to_mermaid` folds it into label strings — same attack surface as the artifact-viewer XSS test), waits 8s, then asserts (1) `window.XSS_SHAPE_FIRED !== true`, (2) zero `<script>` in the panel, (3) zero `on*` event-handler attributes survive, and (4) the panel + Approve control still render. This genuinely proves neutralization *while the diagram still renders*.
- `test_shape_gate_hidden_when_no_pending_proposals`: panel absent from DOM when no pending proposals.

## Check 2 — Non-regression
Purely additive: 126 insertions = two new React components (`ShapeProposalCard`, `ShapeGatePanel`) plus a single `<ShapeGatePanel proposals={cli.shape_proposals || []} />` line inserted into `MissionControlLayout` between `InstanceStrip` and the main grid. `ShapeGatePanel` returns `null` when there are no `status==='pending'` proposals (verified by the hidden-panel test), so the existing dashboard is visually and behaviorally unchanged in the common case. The `cli.shape_proposals || []` guard tolerates the field's absence. No existing component is modified. Full suite clean minus documented baseline flakes.

## Check 3 — Code-quality smoke
Clean. XSS handling is correctly **delegated** to the shared `renderMermaidBlocks` pipeline rather than duplicated — the right call and the one the AT demands. The ref-callback-with-`[]`-deps pattern is sound here because cards are keyed by `shape_id` (a new proposal yields a fresh card/mount). Optimistic `setResolved(true)` hides the card after a decision; even without it the next `/api/state` poll drops the proposal from `pending`, so the local state is harmless belt-and-suspenders. No blocking smells.

## Observations (Info tier — do not affect verdict)
- **Cross-slice data dependency:** the panel reads `cli.shape_proposals[].mermaid/crew_id/status/adaptation_diff` produced by the state-route slice (index 3) and POSTs to its `/shape-approval` route. Field-name/shape coherence across those slices is the feature-reviewer's charter; names line up consistently here.
- Optimistic resolve does not surface a server-error message to the operator (silently re-enables buttons on failure) — minor UX gap, not a defect.
- The `<ShapeGatePanel>` is rendered from `cli` (active instance) only; whether proposals from non-active instances should also surface is a product/integration question for the feature reviewer, not a slice defect.

## Verdict
All three checks pass. No Critical or High findings. The slice renders proposals as graphical DAGs through the reused, XSS-hardened mermaid pipeline, wires crew-aware Approve/Decline controls, and is backed by genuine visible-label and XSS-neutralization Playwright assertions. Additive, non-regressing.

**Verdict:** PASS
