## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

Build the Unified Topology View for the Mission Control dashboard: collapse the two
currently-stacked left-rail graphs into ONE graph.

Today the dashboard left rail (claude_crew/ui/dashboard.html) shows two separate graphs:
the pre-M2 lead-centric roster hub (`MiniGraph`, hand-drawn SVG: lead at center,
teammates as spokes with activity dots/pulses) and the M2 edge-routing panel
(`TopologyEdgePanel`, mermaid graph TD of the recorded shape topology: slot nodes,
peer edges colored by mode, exchange/⚡ badge, click->/edge-log, promote->/edge-promote).
They are visually redundant and structurally incompatible (the hub can't show peer
edges). Unify them into a single `TopologyGraph` whose nodes are the crew and which
carries BOTH the activity layer AND the routing layer at once, degrading to a roster
view when no shape is instantiated.

AUTHORITATIVE INPUT — read these fully before planning:
- doc/design/unified-topology-view-uxspec.md  ← the UX-hardened spec. It already
  resolves the 6 design questions, defines 7 acceptance criteria (AC-1..AC-7), the
  BC-03 keyed edge-mapping fix, the data contract consumed, and a grep-confirmed
  sibling-test footprint (its section 8). Treat this spec as the source of truth;
  your job is to turn it into a buildable task DAG with concrete acceptance tests.
- doc/design/unified-topology-view.md  ← the originating brief (context).
- doc/design/mockups/unified-topology-view-mock.html  ← the approved mockup (three
  states + a live BC-03 bug-simulation toggle). The shipped UI must match it.
- claude_crew/ui/dashboard.html  ← the two components being replaced (MiniGraph,
  TopologyEdgePanel) and the foreignObject/mermaid render pipeline already in use.
- claude_crew/ui_server.py around the topology_edge_stats serialization (~L461) and
  the /edge-log + /edge-promote handlers (the crew_id leader->follower proxy).
- claude_crew/broker.py Topology dataclass (edges + slot_to_teammate) + EdgeStat.

SCOPE: presentation + exactly ONE additive data-surface field. No routing/broker
behavior change.
- Replace MiniGraph + TopologyEdgePanel with a single TopologyGraph (mermaid graph TD,
  lead as a first-class node; gated edges visibly route through lead).
- Roster fallback (empty topology_edge_stats): graph LR star, lead -> teammates,
  neutral edges, no badges/click. Same component, the only branch is edge generation.
- Slot-labeled foreignObject node cards (slot label + 8-char teammate-id), activity
  on the node BORDER (color + pulse), routing on the EDGES (stroke color/width + badge).
- The ONE additive field: surface slot_to_teammate on /api/state (mirror the existing
  topology_edge_stats serialization shape). REQUIRED so per-node activity (teammate-id
  keyed) can attach to slot nodes (slot keyed) — the `agent.role===slot` guess is not
  reliable. This is additive serialization of an existing broker field; NOT a behavior
  change.

BC-03 (the bug this slice fixes): edge decoration must key by (from_slot, to_slot),
NOT positional links[i]->edgeStats[i]. Reciprocal pairs (a,b)+(b,a) must each carry
their own stats and never swap. This MUST have a GREEN-SUITE deletion-detecting test:
a pytest-playwright test (the dashboard is already tested this way — see
tests/test_edge_dashboard.py) that injects a reciprocal pair with distinct
mode/tripped/exchange values, renders, and asserts each rendered edge carries its
correct stats — failing if anyone reverts to positional indexing. Browser pixel/
legibility checks stay out of the green suite, but mapping CORRECTNESS is in it.

MULTI-INSTANCE (do not regress): every per-edge fetch keeps crew_id in the path
(/edge-log/<crew_id>/<from>/<to>, /edge-promote/...); the M2 leader->follower proxy
contract (the CLAUDE.md trap) and its AT must still pass.

SIBLING-TEST FOOTPRINT (from spec section 8 — declare these in taskTouches): definite
edits tests/dashboard/test_roster_spotlight.py, tests/test_edge_dashboard.py,
tests/test_circuit_breaker.py; probable tests/dashboard/test_dashboard_mermaid.py,
tests/test_dashboard_render.py; NEW tests/test_unified_topology_keyed_lookup.py.
Grep the test suite for MiniGraph / TopologyEdgePanel / flowchart-link references and
declare every file that needs updating.

OUT OF SCOPE: no new routing/breaker behavior; no changes to EdgeStat / edge-log /
edge-promote / promote_edge semantics; no pixel/legibility checks in the green suite;
multi-topology slot collision uses last-write-wins (a slot_to_teammate_by_topology list
is explicitly deferred). Lead-spoke motion pulses are intentionally removed (activity
moves to node border) — this is a deliberate change, not a regression.

TEST COMMAND: uv run pytest (FULL suite — this is a widely-consumed dashboard change;
a scoped subset would mask cross-cutting regressions). pytest-playwright is already a
project dependency.

## Cycle

Cycle: 1
Prior review report (empty on cycle 0): /home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/reports/unified-topology-view-plan-review-0.md

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.12.3/doc/templates/spec-template.md`

Existing specs in this worktree:
1: unified-topology-view

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
