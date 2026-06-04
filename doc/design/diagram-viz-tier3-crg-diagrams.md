# Deferred Idea — Tier 3: CRG-derived diagrams (impact / call-graph / architecture)

**Status:** deferred (write-up for a later slice; do NOT build in the mermaid-artifact-viewer v1)
**Depends on:** Tier 1 (mermaid render pass in the Mission Control artifact viewer) shipped.
**Home repo:** could live in claude-crew (the dashboard) or as a CRG-side exporter; decide at scoping.
**Date:** 2026-06-04

## The idea

CRG (the code-review-graph MCP server) already holds the structural graph of every
registered repo — callers/callees, imports, tests, impact radius, architecture/coupling.
Tier 3 turns those structured query results into **mermaid diagrams** rendered in the
artifact viewer, so "what does this change touch?" becomes a picture instead of a list.

Three concrete diagram types, each from an existing CRG tool:

1. **Impact radius** — from `get_impact_radius_tool(symbol)`: a flowchart of the symbol +
   everything structurally affected by changing it. The pre-change "blast radius" picture.
2. **Call graph / neighborhood** — from `query_graph_tool` (callers_of / callees_of) or
   `traverse_graph_tool`: the local call neighborhood of a function as a directed graph.
3. **Architecture / coupling overview** — from `get_architecture_overview_tool`: module /
   community structure + coupling edges, as a high-level map of the repo.

## Why it's valuable

- The single best "help me map this change" aid: before touching a function, *see* its
  consumers; before a refactor, *see* the coupling.
- CRG already computes all of it; today it returns `file:line` lists and JSON. The only
  gap is a structured-result -> mermaid translation + the (Tier-1) render surface.
- Generalizes the "Know What You're Touching" coding-standard from a query into a glance.

## Sketch of the mechanism

- A translator (CRG-side exporter, or a small claude-crew helper) maps a CRG tool's
  structured result -> a mermaid `flowchart`/`graph` block. Node = symbol (label =
  name + file), edge = relationship (calls / imports / impacts).
- Surface via `surface_document` (or a dedicated `surface_diagram`) to Mission Control,
  which renders it via Tier 1.
- Bound the graph size (CRG results can be large) — cap nodes, summarize beyond a depth,
  and `log`/note what was truncated (no silent cap).

## Open questions (resolve when scoped)

- CRG-side exporter (a new `*_mermaid_tool` returning a diagram) vs. claude-crew-side
  translator consuming existing tool output? The former is reusable by any CRG client;
  the latter keeps CRG unchanged.
- Graph-size bounding strategy (cap N nodes? collapse by community? depth limit?).
- Layout quality for large graphs — mermaid auto-layout degrades past ~30-40 nodes; may
  need clustering / subgraphs.

## Out of scope for Tier 3 itself

- Rendering (Tier 1).
- The repo-react DAG diagram (Tier 2) — different source (breakout YAML, not CRG).
