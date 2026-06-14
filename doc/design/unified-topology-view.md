# Unified Topology View — deferred follow-up to M2 (edge-routing)

**Status:** deferred design brief, ready for a UX-hardening pass → `/repo-reactor:repo-react`.
**Author:** Kael, 2026-06-13. **Origin:** M2 signoff pixel-check. The M2 on-graph edge overlay shipped as a *second, separate* graph stacked below the existing roster — operator (Jerome) wants **one unified topology view**, not two. This brief specifies that unification. It also subsumes M2 backlog item **BC-03** (positional edge-mapping bug).

> **UX-hardening required before code.** Per the project's standing practice for UI features, this slice gets a design/mockup pass with a frontend-design teammate (Opus) and operator approval **before** implementation. This brief is the *input* to that pass, not a finished visual spec.

---

## 1. Problem — two stacked "topology" graphs

The dashboard left rail currently shows **two** graphs, one above the other, both inside `MiniGraph` (`ui/dashboard.html`):

1. **The roster hub** (existing, pre-M2) — `MiniGraph`'s hand-drawn SVG: `lead` at center, live teammates as spokes radiating to it, each with an activity status dot (thinking / tool-use / waiting / idle) and motion pulses. It is **lead-centric** — every drawn edge is teammate↔`lead`. It shows *who is alive and what they're doing*.
2. **The Edge Routing panel** (M2, `TopologyEdgePanel`) — a separate mermaid `graph TD` rendering the recorded **shape topology**: slot nodes (`planner→implementor→reviewer`) with **peer** edges colored by mode (gated=amber, tee=blue, direct=green, tripped=red), an exchange-count/⚡ badge, click→`/edge-log`, and a promote→`/edge-promote` control. It shows *how teammates are wired and how messages route*.

The two are **structurally incompatible today**: the roster hub is lead-centric and cannot represent teammate↔teammate **peer** edges (M2's whole point), so the implementor added a second graph rather than decorating the first. The result is visually redundant and confusing — two "topology"-labeled graphs showing different relationships.

## 2. Goal — one graph that carries both layers

A single topology visual where **nodes are the crew (teammates/slots)** and the graph simultaneously conveys:

- **Activity status** (from the roster hub): per-node thinking / tool-use / waiting / idle indicator + the in-flight motion pulses.
- **Routing topology** (from M2): the recorded `Topology`'s peer edges, colored by effective mode (gated/tee/direct), tripped indicator, exchange-count badge, click-edge→message-log, promote-edge-to-gated control, and the pulse-on-exchange-increment animation.
- **The lead's relationship** to the crew, without forcing a lead-centric hub that hides peer edges.

And a **graceful fallback**: when no shape/topology is instantiated (the common case for ad-hoc crews), the view degrades to the roster (nodes + activity, no peer edges) — i.e. today's hub behavior is the empty-topology state of the unified view, not a separate widget.

## 3. Design questions for the UX-hardening pass (resolve these in the mockup)

1. **Layout.** Lead-centric hub can't show peer edges; a DAG / `graph TD` (mermaid, as M2 uses) shows peer edges but has no natural "lead at center." Options to evaluate: (a) mermaid DAG of slots/teammates with `lead` as a first-class node that gated/tee edges route through; (b) a force/radial layout with peer edges as chords; (c) keep mermaid DAG and represent `lead`-routing implicitly (gated edges visibly "pass through" lead). The operator's mental model is "watch the crew talk" — favor whatever makes peer conversation + gated-through-lead legible at a glance.
2. **Node identity.** Nodes are shape **slots** (`planner`/`implementor`/`reviewer`) that map to live teammate ids via `slot_to_teammate`. Label by slot/role; fold the teammate's activity status + id onto the same node. Reconcile with the roster's id-based nodes.
3. **Activity + routing on one node.** How to show a node that is BOTH "tool-use, pulsing" AND the endpoint of a tripped red edge, without visual overload.
4. **No-topology fallback.** Exact degradation to roster-only when `topology_edge_stats` is empty.
5. **Multi-instance.** The unified view must still carry `crew_id` on every per-edge fetch (`/edge-log`, `/edge-promote`) and respect the leader→follower proxy — do not regress the M2 multi-instance contract (CLAUDE.md trap; AT#13).
6. **Renderer.** Mermaid (reuse `renderMermaidBlocks` + the foreignObject legibility fix + the SVG-walk decoration) vs the existing hand-drawn SVG vs a hybrid. M2's `TopologyEdgePanel` is the closer base.

## 4. Subsumes BC-03 — keyed edge mapping (do NOT carry the positional bug forward)

M2's `TopologyEdgePanel` maps decoration to edges **positionally** (`links[i] → edgeStats[i]`), assuming mermaid emits SVG edge paths in `edgeStats` order. The M2 pixel-check **disproved** that assumption: in a 3-node topology with a reciprocal (back-)edge, mermaid reorders the paths, so the healthy `tee` back-edge inherited the tripped `direct` edge's stats and rendered red "gated⚡" — an operator would misread a healthy edge as a runaway. Reciprocal edges are a **core** M2 use case (direct ping-pong needs both directions), so this is not a rare case.

The unified view MUST key edges by `(from_slot, to_slot)`, not by index: parse each mermaid edge path's identity (mermaid v11 flowchart edge ids/classes encode source+target, e.g. `id="L-planner-implementor-0"` / `.edgeLabel` text) and look up the matching `EdgeStat` by `(from,to)`. Author a **deletion-detecting** structural test for this mapping where feasible (note: browser-side render verification stays out of the green suite per M2's Out-of-Scope precedent; the keyed-lookup *logic* can be unit-tested in isolation).

## 5. Out of scope / non-goals

- Changing any M2 **data** contract — `topology_edge_stats` on `/api/state`, `/edge-log`, `/edge-promote`, the broker `EdgeStat`/`promote_edge` surface are correct and validated; this slice is **presentation only**.
- New routing/broker behavior.
- The other M2 deferred items (none block this).

## 6. Interim state (what M2 ships)

Until this lands, M2 ships the **two-graph** layout with the **positional-mapping limitation** documented (BC-03): the Edge Routing panel can mislabel reciprocal/reordered edges. The substrate (routing enforcement, scoped send, breaker, data/endpoint contract) is unaffected and correct. This brief is the planned replacement.
