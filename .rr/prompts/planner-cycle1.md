## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

#### Workflow Shape Composition — M0: Shape as data + the shape-gate

#### Context (READ THESE FIRST)

Two committed design docs in this repo carry the full vision, the decisions, and the
milestone arc. Read both before planning:
- `doc/design/workflow-shape-composition.md` — the WHY (thesis, moat argument, invariants, resolved forks).
- `doc/design/workflow-shape-composition-feature-draft.md` — the HOW (M0→M5 milestones, shape schema draft,
  adaptation algebra, codebase touch-points). **This slice implements M0 only** (feature-draft §3 "M0").

Both pre-build forks are RESOLVED (do not re-litigate):
- Substrate: build NATIVE — our own DAG runner over the existing SdkTeammate/broker substrate. Do NOT build
  on Anthropic's first-party `Workflow` tool.
- Default edge mode: `gated`-first. New edges default to `gated` (lead-inbox-first = today's behavior).

#### What M0 must deliver (the user-visible outcome)

M0 is the keystone: it makes a workflow "shape" a first-class, declarative, legible data structure, and makes
**human approval of the proposed shape the primary gate** before any teammate spawns. It delivers right-sizing
value with ZERO new inter-teammate communication risk — no teammate `send_to`, no peer edges enforced yet.

Concretely, three capabilities:

1. **Shape-template schema + parser** (feature-draft §4). A shape is: a set of nodes (each a logical `slot` name,
   a `role` resolving to an agent-pack definition, a `model` tier/id, and optional tools/extra_tools/skills/
   cwd-policy), plus directed `edges` each carrying a `mode` (`gated`|`tee`|`direct` — but in M0 every edge is
   `gated`; the field is *declared and recorded*, not yet enforced as routing), plus optional phase/lifecycle
   metadata. Parse + validate shapes from data; reject malformed shapes loudly at parse time.

2. **New lead tool `propose_shape(shape, adaptation_diff?)`** — renders the proposed graph to Mission Control
   (lean on the already-shipped mermaid/diagram-viz layer in `ui_server.py` + dashboard) and **blocks on human
   approve/tweak**. This is the gate (invariant 2): no teammate spawns until a human has approved the shape.

3. **New lead tool `instantiate_shape(shape_id)`** — on approval, spawns the declared teammates and records the
   topology in the broker as a new queryable `Topology` structure (edges + per-edge policy). Edges are RECORDED
   but enforcement is trivial in M0 (all `gated` = today's routing behavior, unchanged). No teammate-held
   `send_to` tool yet.

#### Constraints & non-negotiables

- **Native only.** Build on `broker.py` / `sdk_teammate.py` / `server.py` / `ui_server.py` as they exist. Do not
  introduce an external orchestration runtime. (Invariant 5.)
- **Observable by construction.** Anything that will eventually route messages must route through the broker and
  be surfaced to Mission Control. M0 doesn't add message routing, but the `Topology` it records must be
  queryable/observable, laying the rail.
- **The shape is the gate.** `instantiate_shape` MUST refuse to spawn anything that wasn't approved via
  `propose_shape`. The approval is the authorization.
- **Dashboard multi-instance rule** (see project CLAUDE.md "Dashboard is a multi-instance LEADER"): any new
  per-instance dashboard endpoint must carry `crew_id` and proxy leader→follower. If the shape-gate render adds
  an endpoint, honor this — and add a multi-instance test, not just a single-instance one.
- **Shape file format is still OPEN** (YAML vs md+frontmatter to match the agent-pack convention). Pick the one
  that best matches the existing pack-loading convention in `factories.py`/`subagents/` and justify it in the
  spec; this is a reasonable in-slice decision, not a blocker.
- Two layers of tests, happy + sad (per the repo's validate-before-change standard): schema-parse unit tests
  (valid/invalid shapes); integration test — propose → approve (stubbed) → instantiate → assert the right crew
  spawned + topology recorded; sad — malformed shape rejected at parse, approval-declined aborts spawn.

#### Pointers the planner should read

- `claude_crew/broker.py` — where `Topology` state (edges + policy) is added; today routing is by `recipient`.
- `claude_crew/server.py` — the 12-tool MCP surface to extend with `propose_shape` / `instantiate_shape`.
- `claude_crew/sdk_teammate.py` — teammate spawn path (M0 only spawns from the approved shape; no scoped
  `send_to` yet — that's M2).
- `claude_crew/ui_server.py` + dashboard — shape-gate render + approve flow; reuse the shipped diagram-viz layer.
- `claude_crew/factories.py` + `subagents/` — the agent-pack convention a shape's `role` resolves against, and
  the format precedent for storing blessed shapes.
- The project CLAUDE.md sections on SDK-teammate wiring and the multi-instance dashboard leader/follower rule.

#### Explicitly OUT OF SCOPE for M0 (do not build these — later milestones)

- Blessed shape library + lead router (M1).
- Per-edge routing enforcement (`tee`/`direct`), scoped teammate `send_to`, neighbor injection, circuit breaker
  (M2). In M0 edges are recorded as data only; routing is unchanged from today.
- Adaptation algebra verbs `add_node`/`swap`/`augment`/`set_gate`/`drop` (M3).
- Re-authoring RepoReactor as the `heavy-feature` shape (M4).
- Memory-informed / lead-autonomous adaptation (M5).

The goal of M0 is the smallest shippable keystone that leaves master green: shape-as-data + the human shape-gate +
topology recording. Everything after it builds on this.

## Cycle

Cycle: 1
Prior review report (empty on cycle 0): /home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/workflow-shape-composition-m0-plan-review-0.md

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
1 spec (workflow-shape-composition-m0)

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
