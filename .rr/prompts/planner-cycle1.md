## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

M3 — Adaptation Algebra for Workflow Shapes.

The authoritative, design-LOCKED brief is committed in this repo at
`doc/design/m3-adaptation-algebra.md`. READ IT FIRST and in full — it is the
source of truth. Also read `doc/ROADMAP.md` § M3 for arc context. The decisions
below are confirmed with Jerome (2026-06-17) and are NOT open for re-litigation;
they are restated here only so the spec cannot drift from them.

What to build
-------------
Make shape adaptation COMPUTABLE: five typed verbs that each transform a frozen
`Shape` into a new frozen `Shape` plus a structured `AdaptationDiff`, with every
adaptation presented at the EXISTING M1.5 human gate before it takes effect.
claude-crew owns the algebra (mechanism); policy consumers (repo-react
right-sizing) own which adaptation to apply. This is pre-instantiation only.

Locked decisions (do not re-open)
---------------------------------
1. Interaction model: iterative, gated, ONE approval per adjustment. The new
   shape is registered as a NEW pending proposal carrying its `AdaptationDiff`,
   reusing the M1.5 async gate verbatim. `register_proposal` / `propose_shape`
   ALREADY carry an `adaptation_diff` parameter (broker.py, server.py) — M3 FILLS
   that channel; it does NOT invent a second approval path and does NOT change the
   gate signature. The gate shows the DIFF (what changed), not a from-scratch
   re-review. A second adjustment repeats the cycle, gate-approved again.
2. Implementation = command/transform, NOT a Shape-wrapping decorator. `Shape`
   stays a pure frozen dataclass, unchanged. Three pieces, all in
   `claude_crew/shapes.py` (pure-data module, no broker/SDK dep):
   (a) a tiny abstract base `ShapeAdaptation` with uniform
       `apply(shape) -> tuple[Shape, AdaptationDiff]`, one concrete class per verb;
       each encapsulates its own validation + diff construction; `apply` is pure
       (no mutation, returns a new frozen `Shape`);
   (b) `AdaptationDiff` — structured frozen dataclass: `verb`, `target` (slot or
       edge key), `before`/`after` summary (changed fields only), and a
       human-readable `render() -> str`. The structured form powers golden tests +
       provenance; `render()` produces the string fed into the gate's existing
       `adaptation_diff: str` channel. Do NOT widen the gate channel to carry the
       structured object — that is an explicit non-goal / future option.
   (c) an adaptation chain (provenance) — ordered record of applied adaptations,
       modeling describe -> adapt -> approve -> adapt -> approve. Exact carrier is
       an OPEN QUESTION for you (see below).
3. ALL FIVE verbs in-slice, NO deferred work. Each verb: typed params, pure
   `apply`, happy-path correctness AND illegal-mutation rejection (loud
   `ShapeValidationError`, never a silent no-op or partial shape).

The five verbs (full table incl. sad paths is in the brief §4)
--------------------------------------------------------------
- add_node: ShapeNode + optional incident edges. Reject duplicate slot; edge
  to/from non-existent slot; self-loop.
- swap: slot, new role (+ optional model/tools/skills). Replace a node's role;
  slot identity + incident edges unchanged. Reject missing slot; new role
  UNRESOLVABLE against factory.known_roles / resolve_role.
- augment: ShapeNode + edge(s) wiring it to an existing slot (e.g. a 2nd
  reviewer). Reject missing target slot; augmenting role unresolvable; resulting
  duplicate slot/edge.
- set_gate: edge (from,to), new mode (+ optional reverse_mode). Reject missing
  edge; mode not in {gated,tee,direct}.
- drop: slot. Reject missing slot; node has LIVE in/out edges (must rewire or
  drop edges first — no dangling-edge shapes).

`swap`/`augment` role-resolution is IN-SLICE: resolve through the SAME seam
`instantiate_shape` uses — `factory.known_roles()` + `factory.resolve_role()` —
unresolvable role -> loud rejection at adapt time, BEFORE the gate, mirroring
instantiate's all-or-nothing pre-flight. When the factory exposes no
`known_roles` (stub-mode tests), skip resolution exactly as instantiate does.

Invariant to test hard: every verb's output `Shape` must re-satisfy
`parse_shape`'s rules (unique slots, edges reference existing slots, no
self-loops, no duplicate edges, valid modes) — a verb can NEVER produce a shape
`parse_shape` would reject. Round-trip each adapted shape through validation.

Server / broker surface
------------------------
- server.py: new lead MCP tool(s) to apply an adaptation. On success ->
  `register_proposal(new_shape, adaptation_diff=diff.render())` (reuse M1.5 gate)
  -> return {ok, shape_id, status:"pending", diff}. On illegal mutation ->
  {ok:False, stage:"adapt", error}; NO proposal registered.
- broker.py: NO new gate machinery (`register_proposal` already takes
  `adaptation_diff`). Only addition if you choose to persist the adaptation chain
  for provenance (open question).
- NO dashboard.html work for M3 — the diff surfaces via the existing
  `adaptation_diff` string channel.

Open questions for YOU to resolve in the spec (brief §7)
--------------------------------------------------------
1. Tool surface: one `adapt_shape(base, verb, params)` tool with a verb
   discriminator, vs five per-verb tools. Lean is ONE tool (smaller, uniform) but
   weigh discoverability + arg-validation clarity and decide.
2. Adaptation-chain carrier: plain returned tuples vs a lightweight
   `AdaptationChain` dataclass vs persisted on broker state. Lean: a `shapes.py`
   dataclass returned/threaded; broker-persistence only if observability needs it.
3. `augment` edge semantics: exact required params for wiring the augmented node
   (one edge? mode default?).
Base shape is PRE-INSTANTIATION ONLY (RESOLVED): a pending proposal, an
approved-but-not-instantiated shape, or an inline shape — NEVER a live/
instantiated topology. Reshaping a running crew is carved out as M3.5; do not
touch the live teammate registry or routing.

Explicitly OUT OF SCOPE (brief §6)
----------------------------------
M5 autonomy (un-gated adaptation); M4 (RR-as-shape); blessed-shape library +
classifier (now repo-react policy, not claude-crew); widening the gate channel to
carry the structured object + bespoke dashboard diff rendering; cross-session
chain persistence/replay; reshaping a LIVE crew (= M3.5).

Test surface (deletion-detecting where feasible — brief §8)
-----------------------------------------------------------
New `tests/test_shape_adaptation.py`: per-verb happy-path (golden assertions on
resulting frozen shape); per-verb sad-path (each illegal-mutation row raises
loudly — no silent no-op, no partial shape); round-trip invariant (every adapted
shape passes `parse_shape`); swap/augment role-resolution (resolvable succeeds,
unresolvable rejected, via a `known_roles`-injected stub factory mirroring the
instantiate pre-flight tests); `AdaptationDiff.render()` golden tests; gate
integration (an adapt call registers a pending proposal carrying the diff;
`resolve_shape` approves it; a SECOND adapt on the approved shape registers again
— the iterative re-gate loop). This is a widely-consumed substrate change — the
validation gate MUST run the FULL `uv run pytest`, not a keyword-filtered subset.

Touch-points (grounded in current code — brief §9)
--------------------------------------------------
- claude_crew/shapes.py — ShapeAdaptation base + 5 verb classes + AdaptationDiff
  (+ chain carrier). Pure data; no new deps.
- claude_crew/server.py — adapt tool(s); reuse parse_shape,
  register_proposal(..., adaptation_diff=), and the known_roles/resolve_role
  resolution block (the one instantiate_shape uses) for swap/augment.
- claude_crew/broker.py — register_proposal already supports the diff; optional
  chain persistence only if you pick that carrier.
- tests/test_shape_adaptation.py — new; gate-integration assertions alongside the
  existing shape-gate tests.

Read the project CLAUDE.md test conventions (imports at module top;
get_running_loop; bound async drains; full-suite validation when changing
widely-consumed behavior) and the existing shape/gate tests before writing.

## Cycle

Cycle: 1
Prior review report (empty on cycle 0): /home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-review-0.md

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
1: m3-adaptation-algebra

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra`

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
