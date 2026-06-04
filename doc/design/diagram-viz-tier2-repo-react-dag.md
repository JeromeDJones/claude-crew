# Deferred Idea — Tier 2: Auto-render the repo-react task DAG as a diagram

**Status:** deferred (write-up for a later slice; do NOT build in the mermaid-artifact-viewer v1)
**Depends on:** Tier 1 (mermaid render pass in the Mission Control artifact viewer) shipped.
**Home repo:** `repo-reactor` (`~/dev/FDE`) — it owns the breakout; claude-crew only renders.
**Date:** 2026-06-04

## The idea

A repo-react slice's `## Task Breakout` YAML *is* a dependency graph: each task has
`name`, `dependsOn`, `acceptanceTests`, `taskTouches`, `implementationKind`. Today the
coordinator and Jerome read it as a YAML block. Tier 2 turns it into a **mermaid
flowchart automatically** — so the slice's task structure, dependency edges, parallel
ready-sets, and critical path are *visible* instead of parsed in the head.

## Why it's valuable

- The breakout is the single artifact whose shape is hardest to hold mentally — it's a DAG.
- It's generated every slice, for free, in a structured format already.
- A picture makes "what runs in parallel / what's blocked on what / where's the long pole"
  obvious — exactly the mental-mapping problem that motivated the whole visualization layer.

## Sketch of the mechanism

- A small generator (in `bin/`, e.g. `breakout-to-mermaid.sh` or folded into `spec-tasks.sh`)
  reads the breakout YAML and emits a mermaid `flowchart` block: one node per task
  (label = name + implementationKind + AT count), one edge per `dependsOn`.
  Optionally color nodes by status (pending / implementing / done) and highlight the
  critical path.
- The coordinator `surface_document`s the rendered mermaid to Mission Control at slice-start
  (and re-surfaces on each `transition-to implementing` so status colors update live).
- Reuses Tier 1's render pass entirely — no new dashboard work; this is purely a *generator*
  feeding the surface that already renders mermaid.

## Open questions (resolve when scoped)

- Live status coloring needs re-surfacing on state transitions — acceptable churn, or a
  stable-artifact-id update-in-place (the same "living diagram" question Tier 1 deferred)?
- Critical-path computation: worth it in v1 of Tier 2, or just the raw DAG first?
- Does this belong in the SKILL coordinator flow (auto-surface) or as an opt-in `/repo-react`
  sub-command (`/repo-react diagram <slug>`)?

## Out of scope for Tier 2 itself

- Rendering (that's Tier 1, already shipped by the time this is built).
- CRG-derived diagrams (that's Tier 3).
