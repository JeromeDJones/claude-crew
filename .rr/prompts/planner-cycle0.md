## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/m1-5-async-shape-gate/.rr/specs/m1-5-async-shape-gate.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

M1.5 — Async, resurfaceable, dual-channel shape-gate.

PRIMARY SOURCE: read these two docs in the repo first — they ARE the spec.
  - doc/design/M1.5-async-shape-gate-BRIEF.md   (the HOW: scope, contracts, touch-points, acceptance tests)
  - doc/design/shape-gate-async-surface.md       (the WHY: rationale)
Everything below is a faithful condensation; defer to the brief on any conflict.

== ONE-PARAGRAPH SUMMARY ==
M0 shipped a shape-gate where propose_shape BLOCKS the lead's turn until a human approves via the
dashboard ONLY, and the gate UI is an EPHEMERAL modal that vanishes on navigation. M1.5 keeps the gate
invariant (no crew spawns without explicit human approval) but DECOUPLES approval from blocking and ADDS
a chat channel: propose_shape returns immediately after surfacing the shape; the operator approves EITHER
on the dashboard OR by telling the coordinator in chat; the lead is NOTIFIED on resolution via its inbox
(no polling, no freezing); and the gate is RESURFACEABLE like a surfaced artifact (notification + tray/badge
+ re-open, survives navigation). The M0 modal is reused as the VISUAL; what changes is WHEN it blocks, HOW
it's approved, and HOW it's recalled.

== WHAT TO BUILD ==
1. propose_shape -> NON-BLOCKING (server.py): parse, register_proposal, surface, return immediately with
   {ok, shape_id, status:"pending", shape}. Do NOT block. Keep an opt-in propose_shape(..., wait=True)
   blocking path as a non-default flag (broker.await_proposal stays available).
2. New lead tool resolve_shape(shape_id, decision) (server.py + broker.py) -> calls
   broker.resolve_proposal(shape_id, "approve"|"decline"). This is the CHAT-channel approval. Tool count 14->15.
   Optional companion read list_pending_shapes() -> pending proposals (shape_id, name, crew_id, mermaid/summary)
   so the coordinator can describe the shape over text before approval (recommended; makes phone/text ergonomic).
   broker.resolve_proposal already has the pending-only guard from M0 hardening — reuse as-is.
3. Surface + notify-on-resolve (broker.py + ui_server.py) — REUSE the artifact-surface pattern, not a transient
   modal. Mirror surface_document -> broker artifact registry; ui_server _build_local_instance already emits
   shape_proposals carrying crew_id. On resolve (either channel), broker drops a {type:"shape_resolved",
   shape_id, status} message into the LEAD's inbox (reuse _lead_message_condition / wait_for_lead_message /
   get_messages) so the lead learns WITHOUT blocking; its normal get_messages loop picks it up, then it calls
   instantiate_shape. No polling.
4. Dashboard resurfaceable gate (dashboard.html): keep the M0 modal as the visual (OverlayPanel mode="modal",
   cross-instance flatMap, crew_id labels — already shipped); make it RECALLABLE: a pending gate shows a
   badge/tray entry (mirror the artifact unread pill in MCTopBar), modal opens from there, switching instances
   does NOT lose it, closing leaves the badge. Clears only when resolved (approve/decline) via either channel.
   Approve/Decline still POST /shape-approval/{crew_id}/{shape_id} (UI channel, leader->follower proxy unchanged).

== INVARIANTS THAT MUST HOLD ==
  - The shape is still the gate: instantiate_shape still refuses any non-approved proposal (zero spawns without
    human approval). Unchanged.
  - Observable by construction: resolution flows through the broker; the notify message is on the broker channel.
  - Coordinator-in-the-loop, strengthened: the lead stays free to supervise/chat; it is notified, not blocked.

== ACCEPTANCE TESTS (two layers, happy + sad) — see brief section 6 for the full list ==
  1. Non-blocking propose: propose_shape returns {status:"pending", shape_id} WITHOUT waiting (proposal still
     pending in broker when the call returns).
  2. Chat approval: resolve_shape(id,"approve") -> status approved -> instantiate_shape spawns. decline ->
     instantiate refuses. Sad: resolve_shape on unknown / already-resolved id -> error (reuse hardening guard).
  3. Notify-on-resolve: after resolve (either channel) a shape_resolved message appears in the lead's
     get_messages stream for that shape_id + status. Sad: no message before resolution.
  4. UI channel still works: POST /shape-approval/{crew_id}/{shape_id} single + multi-instance proxy
     (carry over M0 AT11/AT12).
  5. Resurfaceable (Playwright): pending gate shows unread badge/tray entry; opening shows modal DAG;
     switching instances does NOT lose the pending gate; resolving clears it. Cross-instance: follower's
     pending gate surfaces on the leader.
  6. list_pending_shapes (if built): returns pending proposals with enough to describe over text; empty when none.

== MIGRATION / REGRESSIONS TO EXPECT ==
  - M0 tests assume BLOCKING propose_shape (tests/test_shape_gate.py: approve via broker.resolve_proposal THEN
    read the blocked return). These MUST be updated for the non-blocking contract (propose returns pending;
    resolve; then assert). Budget for it.
  - Full suite must stay green (the 2 test_shutdown_signals flakes are pre-existing baseline).

== OUT OF SCOPE (do NOT build here) ==
  - Edge routing / scoped teammate send_to / neighbor injection / circuit breaker — that's M2.
  - Blessed shape library + right-sizing router — that's M1 (deferred).
  - Adaptation algebra (M3), RR re-author (M4), autonomy (M5).

== OPEN QUESTIONS (decide during planning) — see brief section 9 ==
  - Timeout semantics in the non-blocking model: lean NO auto-timeout by default (it's resurfaceable; operator
    can decline); keep await_proposal's timeout only for the opt-in blocking path.
  - resolve_shape the only chat path, or also auto-surface a one-line shape description when proposing? Lean:
    coordinator describes the shape in its own message when it calls propose_shape.
  - Notify payload shape for the lead inbox on resolve: keep minimal (shape_id + status).

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

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.12.3/doc/templates/spec-template.md`

Existing specs in this worktree:
_None._

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m1-5-async-shape-gate`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
