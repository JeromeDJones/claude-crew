# Idea: Diagram Visualization Layer (Mermaid in the Artifact Viewer)

**Status:** draft for repo-react slice
**Date:** 2026-06-04
**Author:** Jerome + Kael

## The job to be done

When Jerome and Kael (or the crew) shape a plan or an idea, Jerome wants to **see
the shape of the change** — not read it as prose. He struggles to mentally map
multi-part changes from text alone. The fix is a visualization he can *look at and
discuss*: "here's the change, drawn." Concretely: mermaid diagrams generated as
part of planning, rendered somewhere he can see them, and iterated on in
conversation ("this node is wrong → change it → re-render").

## The decision: enhance what we already have — don't build a new thing

The Mission Control dashboard (claude-crew `ui_server.py` + `claude_crew/ui/dashboard.html`)
already provides a **document/artifact viewer** (`surface_document` MCP tool →
artifact registry → right-side ArtifactDrawer). Two facts make "enhance" the clear call:

1. **It's already running and always-on.** The claude-crew MCP server is user-scoped,
   so the dashboard binds `127.0.0.1:7821` whenever any Claude session is alive —
   *even with no crew spawned*. And `surface_document` is a **lead tool**, so the main
   Kael session can push artifacts to it directly, no teammates required.
2. **It renders markdown but not diagrams.** The artifact pipeline is
   `marked.parse (MD→HTML) → DOMPurify.sanitize(_DOMCFG) → link-hardening`. A
   ```mermaid code block renders as *raw code*, not a diagram. **This is why the
   viewer is underused** — there's no visual payoff over reading the text in the terminal.

So the gap is narrow and specific: **add a mermaid render pass to the artifact
viewer.** The hard infrastructure (registry, always-on dashboard, multi-instance
leader/proxy, XSS sanitization, Playwright test harness) is all shipped.

## v1 scope (this slice)

Add safe mermaid rendering to the dashboard artifact viewer:

- Load mermaid via **pinned unpkg + SRI integrity hash**, matching the existing
  `marked` / `DOMPurify` delivery pattern (`claude_crew/ui/dashboard.html` ~lines 467–468;
  the file documents the `curl … | openssl dgst -sha384 -binary | openssl base64 -A` recipe).
- After the existing `marked → DOMPurify` sanitize step inserts HTML into the DOM,
  run mermaid on the ```mermaid blocks so they render to inline SVG.
- **Initialize mermaid with `securityLevel: 'strict'`** (sanitizes diagram labels,
  blocks `click`/script directives) and a dark theme to match the dashboard.
- The shipped XSS regression test (`tests/dashboard/test_dashboard_artifact_xss.py`)
  must stay green, and be **extended** with a malicious-mermaid payload class.

### The security crux (frontier-reviewed)

The one genuinely hard decision: **mermaid renders AFTER DOMPurify**, so the
mermaid-produced SVG bypasses the existing sanitizer. The design must close that hole:
- `securityLevel: 'strict'` is the primary guard (mermaid sanitizes its own output,
  no script/click).
- Decision for the planner: **also re-run DOMPurify on mermaid's SVG output** before
  it's displayed, or rely on strict mode alone? Spell out the chosen ordering exactly
  (this is what the local implementor will follow verbatim).
- Render must be **scoped to artifact content only** (the existing `language-mermaid`
  blocks inside the sanitized artifact node), never to arbitrary page DOM.

## Validation (two layers + manual)

- **Static / unit:** assert `dashboard.html` includes the mermaid `<script>` with an
  SRI hash, calls the mermaid render with `securityLevel: 'strict'`, and scopes the
  render to artifact nodes.
- **Integration (Playwright/chromium — harness already exists):** (a) a surfaced
  artifact containing a valid ```mermaid block renders an `<svg>` in the DOM;
  (b) a malicious mermaid payload (script/click/`<img onerror>` in labels) does **not**
  execute JS — extends `test_dashboard_artifact_xss.py`.
- **Manual:** Jerome surfaces a real plan-with-diagram via `surface_document` and
  confirms it renders + is legible in the browser.
- Full suite stays green (`uv run pytest`); the Playwright tests need
  `uv run playwright install chromium` as a prerequisite (already the case for the
  existing XSS test).

## The habit (adopted alongside — NOT code, NOT in this slice)

Once mermaid renders, Kael starts **emitting a mermaid diagram as part of plans/ideas**
and `surface_document`-ing it, so "see it and discuss it" becomes the default. This is
a behavioral change to how Kael plans, not a code change — it ships the moment the
render pass lands.

## Out of scope (v1) — the layer's later tiers

The word "layer" is deliberate: once mermaid renders, the same surface is fed by
generators we add later. Explicitly deferred:

- **Tier 2 — auto-render the repo-react task DAG.** The breakout YAML *is* a dependency
  graph; render it as mermaid automatically so the slice's structure + critical path
  are visible. (Lives in the repo-reactor plugin, not claude-crew.)
- **Tier 3 — CRG-derived diagrams.** Impact radius / call graph / architecture as
  mermaid, so "what does this change touch?" is a picture.
- Terminal-native rendering (terminals render SVG badly — browser tab on
  `localhost:7821` is the surface).
- Any new/standalone viewer — we reuse Mission Control.
- A "living diagram that updates in place" (stable artifact id that re-renders); v1
  re-surfaces a fresh artifact each iteration. Revisit if the registry cap (50) chafes.

## Open questions

1. Mermaid version to pin (latest stable major; planner picks + computes SRI).
2. Re-sanitize mermaid SVG output post-render, or trust `securityLevel: 'strict'`? (security crux above)
3. Dark theme tuning to match the dashboard palette (cosmetic; can default to mermaid `dark`).
