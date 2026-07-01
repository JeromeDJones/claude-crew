## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

M3.5 — Reshape a Live Crew (Workflow Shape Composition arc).

AUTHORITATIVE DESIGN DOC (read this first — it is committed on the branch): doc/ideas/m3.5-reshape-live-crew.md. It contains verified seam facts (file:line), the locked design decisions, per-verb live semantics, race notes, deliverables, and the exact live-test strategy. Also read doc/ROADMAP.md (the M3.5 row + "Next: M3.5" section) and doc/features/FEATURE-m3-adaptation-algebra.md (the predecessor). Treat the design doc as decided direction, not a menu — the decisions below were locked with the operator (Jerome) on 2026-06-30. Your job is to turn it into a rigorous, testable spec + task breakout, not to re-open the settled forks.

#### What M3.5 is

M3 shipped a pure-data adaptation algebra: five verb commands (AddNode / Swap / Augment / SetGate / Drop) in claude_crew/shapes.py, each apply(shape) -> (Shape, AdaptationDiff), exposed via the adapt_shape MCP tool. adapt_shape is PRE-INSTANTIATION ONLY — it produces a new proposed shape that goes through the human gate and then instantiate_shape spawns a FRESH crew.

M3.5 applies those same verbs to an ALREADY-RUNNING (instantiated) crew — mutate the live topology in place: Swap/Drop kill (and for swap, respawn) live teammates; AddNode/Augment add teammates/edges into the running topology WITHOUT disturbing running teammates; SetGate rewires routing modes mid-flight. claude-crew owns the mechanism; repo-react (policy) owns which reshape to apply.

#### Verified seam facts (do not re-derive; confirmed 2026-06-30)

1. Routing reads LIVE broker state at delivery time, not a frozen snapshot. _send_routed -> _resolve_routing_mode -> _active_topology_for scans reversed(broker._topologies) (a MUTABLE list, latest-appended wins); _edge_mode checks broker._edge_overrides[(from,to)] FIRST. So rewiring is a DATA UPDATE, not a crew rebuild: membership change -> append a new frozen Topology; mode change -> write _edge_overrides.
2. authorize_send (broker.py ~1560) is the security boundary, checked at delivery time inside send_scoped. No active topology or no forward edge -> UnauthorizedEdgeError. The send_to tool's PRESENCE is NOT the gate.
3. The send_to MCP tool is free-form: schema {recipient: str, payload: dict}, recipient is any slot name or teammate id (sdk_teammate.py ~1375-1407). No enumerated allowlist. So informing a running teammate about a new neighbor via a MESSAGE is sufficient for it to use the edge — provided the tool is wired in its subprocess.
4. send_to wiring is currently spawn-time AND conditional: _run() gates on _has_out_edges (sdk_teammate.py ~1543-1560); a teammate spawned with zero out-edges never gets the tool, and it cannot be injected into a live subprocess. This is why Deliverable 0 (below) exists.
5. The neighbor prompt section is spawn-time cosmetic (teammate_prompt.py ~91-186); stale after a live add, but the runtime informing-message supersedes it.
6. The M1.5 human gate is fully reusable and untouched: register_proposal / await_proposal / resolve_proposal, adaptation_diff:str channel already carries the reshape diff. Only the CONSUMER (instantiate_shape, hardwired to spawn fresh) needs a sibling that applies live mutations.
7. Topology is frozen; broker._topologies is a mutable list; record_topology appends; _active_topology_for uses reversed() so newest matching wins; BrokerSnapshot.topologies is a snapshot tuple (safe to append during operation).
8. Existing kill machinery suffices: kill_teammate(graceful, flush_timeout) -> begin_graceful_termination -> _tombstone_teammate (idempotent; alive=False, pops registries, bounces in-flight+queued envelopes). No new lifecycle state needed.

#### Locked design decisions (do not re-open)

D1. Live reshape is HUMAN-GATED — reuse the M1.5 gate verbatim (propose reshape -> human approves/declines -> apply). Coordinator-in-the-loop is the moat.
D2. DISTINCT reshape_crew MCP tool — shares the five verb command objects from shapes.py; applies LIVE mutations. adapt_shape stays pure-data. Do not overload adapt_shape.
D3. Wire send_to UNCONDITIONALLY at spawn (drop the _has_out_edges gate). Makes live edge-addition respawn-free. Safety unchanged — authorize_send is the real boundary. Cost is a bounded contract flip ("plain SdkTeammate has no send_to" -> now always present) plus its tests.
D4. ADDITIONS NEVER RESPAWN — authorize + inform. AddNode/Augment that add an out-edge to a running teammate -> append Topology (authorize) + send that teammate an informing message ("new neighbor X, reachable via send_to"). Preserves accumulated turn context (operator's explicit priority). Enabled by D3 + seam 3 + seam 5.
D5. Swap/Drop kill by nature. Swap: spawn replacement (new id, same slot) + record new Topology -> THEN graceful-drain-and-kill old (this ordering leaves no dead-slot window). Drop: new Topology minus node/edges + graceful-kill + clean stale _edge_overrides + inform affected neighbors.
D6. Stale _edge_overrides cleanup on edge removal (Drop/Swap that remove edges must delete their (from,to) override entries, else a stale override shadows the new topology's declared mode).

#### Per-verb live semantics

SetGate: write _edge_overrides[(from,to)] = mode. Instant, no respawn, no message.
AddNode: spawn new teammate (correct neighbors at its own launch) + append new Topology. If the wiring edge's SOURCE is an already-running node -> inform it via message. Source stays up.
Augment: authorize the new edge (append Topology) + inform the source running node via message. No respawn.
Drop: new Topology minus node + its edges; graceful-kill the teammate; clean stale _edge_overrides; inform affected neighbors their edge is gone.
Swap: spawn replacement (new id, same slot, correct neighbors) -> record new Topology (slot->new id) -> graceful-drain-and-kill old.

#### Deliverables (the spec must cover all of these)

D0 — Unconditional send_to wiring. Remove the _has_out_edges conditional in sdk_teammate.py _run; wire the in-process send_to MCP server + allowed-tools entry for every SdkTeammate. Flip the documented contract; update the tests that assert plain-teammate has no send_to. FOUNDATIONAL — everything else depends on it.
D1 — reshape_crew MCP tool in server.py: verb discriminator (reuse shapes.py verb objects) -> resolve base LIVE topology of a running crew -> gate (register_proposal / await) -> on approve, apply live mutations per the verb table -> record new Topology / write overrides -> send informing messages -> return structured result. Reuse the M1.5 gate; keep broker.py changes minimal and additive.
D2 — Broker live-reshape support (broker.py, additive): minimal helpers the tool needs to (a) resolve the active topology for a running crew, (b) append the reshaped topology, (c) clean stale overrides, (d) send an informing message to a running teammate. Prefer reusing record_topology / _edge_overrides / existing send / kill_teammate.
D3 — LIVE tests (regression guard, MUST run before merge; this feature only manifests live):
  - Headline (no-respawn add): instantiate a real crew where a node has send_to wired; live-reshape to add a reviewer + edge impl->reviewer; assert the SAME still-alive implementor (same teammate_id, NOT respawned) receives the informing message, calls send_to("reviewer", ...), and the reviewer receives it. Must assert a REAL teammate turn/response, not stub-satisfiable structure.
  - Swap: live-reshape swaps a slot's role; assert old teammate dead, new one in the slot, and a slot-name send_to resolves to the NEW teammate.
D4 — Stub tests: topology append semantics, _edge_overrides write + stale-cleanup on Drop, gate state-machine integration, per-verb decision logic (inform-vs-nothing branch, kill ordering), the D0 contract flip.

#### Constraints and non-negotiables

- Reuse the M1.5 gate; do NOT build a parallel gate.
- Keep broker.py changes minimal and additive; prefer reusing existing methods (record_topology, _edge_overrides, send, kill_teammate, _tombstone_teammate).
- No production hacks; two-layer coverage (mechanism + one layer above), happy + sad paths.
- The two live tests are the regression guard and run before merge (project policy — see CLAUDE.md "Run live tests before merging"). A green stub suite is NOT a merge signal on its own for this feature.
- Swap ordering is spawn-new + record-topology THEN kill-old (no dead-slot window). Encode this as an acceptance test.

#### Out of scope (do not spend cycles here)

- Live tool-set mutation of a running subprocess beyond what D0 pre-wires (SDK bakes allowed-tools at launch) — that is exactly why D3/D0 exist instead of live tool injection.
- Autonomous (ungated) reshape — that is M5. M3.5 stays gated.
- RepoReactor-as-shape (M4).
- Dashboard/UI changes for live reshape beyond what the existing adaptation_diff gate channel already surfaces (a reshape diff already flows to Mission Control). Do not build new UI.

#### Validation

Full uv run pytest (stub) green AND the D3 live tests (CLAUDE_CREW_LIVE_TESTS=1) pass before merge. The spec's ## Validation section should run the stub suite; the live tests are named deliverables the coordinator runs at the validation gate.

## Cycle

Cycle: 0
Prior review report (empty on cycle 0): 

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).
- **Before claiming any UI/frontend capability is absent, survey BOTH the backend modules
  AND the frontend assets explicitly.** A grep of server-side code alone is not sufficient to
  conclude a UI capability does not exist — rendering pipelines, loaded libraries, and view
  components typically live in frontend files (`*.html`, `*.js`, `*.jsx`, `*.ts`, `*.tsx`,
  `*.vue`, template dirs), not in the server modules. Search the frontend asset files directly
  before writing an "X does not exist" claim into the spec.

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.16.1/doc/templates/spec-template.md`

Existing specs in this worktree:
_None._

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
- **Deletion-detection mandate:** For every deliverable whose primary verification is deferred out of the green suite (live-SDK turn, browser/Playwright render, manual check), you MUST author at least one green-suite deletion-detecting acceptance test — a structural test (grep, file-existence check, or import-graph probe) that exits non-zero when the deliverable is absent. An `## Out of Scope` clause may defer a deliverable's behavioral test but may never defer all green-suite coverage of its existence/wiring.
- **Grep-guard anchors must be NAMED LITERALs:** When specifying the anchor string for a deletion-detector guard test, state the exact literal substring the guard greps for — not a description of it. Write `anchor: "deletion-detect"` (the NAMED LITERAL), never `anchor: "the deletion-detect phrase"` or `anchor: (description of what the clause says)`. The NAMED LITERAL appears verbatim in both the clause and the test.
- **AT/Out-of-Scope authoring rules:** For the canonical authoring-rules home on acceptance-test and out-of-scope authoring conventions, see the `plan-feature` SKILL (canonical authoring-rules home for AT/OoS rules).
- **Per-validator negative ATs:** When the spec includes discrete input-validation, schema-enforcement, or permission-gate checks, enumerate a negative AT for each validator check — one AT per check, testing its rejection path in isolation, so a removed or broken check produces a directly-attributable failure. A single combined negative AT that exercises several checks at once does not satisfy this; each gate gets its own AT.
