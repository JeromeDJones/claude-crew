## Task

You are `rr-documenter`. Drive both retrospecting sub-steps for slice
`m3-5-reshape-live-crew`, producing two artifacts in order before emitting your final
verdict:

1. **backlog-routing** — synthesize all findings from `.rr/reports/*`
   (including build reports and deferred-fixes) into the backlog-candidates
   artifact; route workflow-elicitation suggestions when enabled.
2. **doc-sync** — auto-apply accepted doc edits and author/create
   `doc/ARCHITECTURE.md` per the harvest contract; emit the doc-sync checklist.

## Inputs

- Slug: `m3-5-reshape-live-crew`
- Spec under retrospect: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
- Reports directory: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports`
- Workflow-retro enabled: `false`
- Workflow-retro user input block (populated by coordinator when enabled):



- Backlog-candidates output path: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-backlog-candidates.md`
- Doc-sync report output path: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-doc-sync.md`
- Prior report (empty on cycle 0): ``
- Already-routed retro findings — skip these in backlog-routing: ``

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew`

Change to this directory before all file operations.

## Cycle

Cycle: 0

On cycle ≥ 1, read `` first and address every reviewer
concern by name across both sub-steps.

## Instructions

**Sub-step 1 — backlog-routing**

Synthesize backlog candidates and write them to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-backlog-candidates.md`.

Read the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md` and every `*.md` under `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports`
before writing — including **build reports** (these capture the implementor's
voice, which has no other path into BACKLOG on clean PASS-first-try features).
Also harvest deferred-fix candidates from the **feature-review report** and
the **spec** (`## Deferred Fixes` sections).

**Workflow-elicitation intake:** Dispatch on `WORKFLOW_RETRO_ENABLED`.

- **`WORKFLOW_RETRO_ENABLED=false` (default).** Write the canonical skip-stub
  text as a note in the candidates artifact and proceed immediately to
  synthesis without requesting any broadcast:

  ```
  _workflow-retro: skipped (workflowRetroEnabled=false; enable per-slice via /repo-react with-workflow-retro <slug> at slice-start, or globally via RR_WORKFLOW_RETRO=1 env)._
  ```

- **`WORKFLOW_RETRO_ENABLED=true`.** The coordinator has already collected
  user input from the broadcast+user-input loop and passed it as
  `WORKFLOW_RETRO_USER_BLOCK` above (see Inputs). Route suggestions from
  that block into backlog candidates → `doc/BACKLOG.md`.

From all collected evidence (reports, build reports, deferred-fixes, workflow-
elicitation block when enabled), perform in sequence:

a. **Collect all findings** across roles and reports.
b. **Deduplicate** on `(surface, target-file-or-skill, fix-shape)`. Merge
   duplicates into a single candidate with combined `Sources:`.
c. **Surface-tag** each candidate: one of `skill | prompt | tooling | coordinator | craft`.
d. **Drop craft entries** — collect them into the `## Dropped (craft → agent-memory)`
   section. Do not include them in BACKLOG candidates.
e. **Rank survivors** by source-count descending, then severity (`High > Medium > Low`).
f. **Draft BACKLOG-shaped per-candidate blocks** — one `## Candidate <id>` block per
   survivor with fields: `Severity`, `Surface`, `Title`, `Sources`, `Recommended
   fix-shape`, `Batching hint`, and `Body`.
g. **Warn on malformed reports.** Record under `## Parse warnings`; skip — do not fail.
h. **Write the candidates artifact** to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-backlog-candidates.md`.

If `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports` is empty, write the artifact with `## Outcome: no candidates`
and a note explaining the empty evidence base.

**Sub-step 2 — doc-sync**

Auto-apply accepted doc edits and emit the doc-sync checklist at
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-doc-sync.md`.

Read the spec and every report in `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports` before applying changes.
Render the checklist using `doc/templates/doc-sync-checklist-template.md`.

The five target files, in canonical order:

1. `doc/PRODUCT-VISION.md`
2. `doc/ROADMAP.md`
3. `doc/features/FEATURE-m3-5-reshape-live-crew.md`
4. `doc/BACKLOG.md`
5. `doc/ARCHITECTURE.md` — harvest durable knowledge per the harvest contract
   at `doc/templates/harvest-contract-template.md`. If `doc/ARCHITECTURE.md`
   does not exist, create it from the harvest-contract-template skeleton.
   If it exists, append/edit the relevant subsystem section. Harvest only
   durable knowledge (subsystem responsibilities, cross-module contracts,
   invariants, the *why* behind structural decisions) — not ephemeral
   information (cycle counts, per-task narrative, dated review notes).

**Auto-apply rule:** For each row where you propose a change, apply it via
Write/Edit directly — do not wait for coordinator/user row-by-row approval.
The human bookend is at the signoff gate.

- Any finding whose ID appears in `` is already routed.
  Do not re-propose it.
- Write the doc-sync checklist to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-doc-sync.md`.
- The checklist must include all five rows in canonical order.
- If `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports` is empty, write the checklist with
  `_No prior reports — no doc-sync candidates._` and `## Outcome: all candidates declined`.

Emit a final line `RR-VERDICT: PASS m3-5-reshape-live-crew 0 /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-doc-sync.md`
on success, or `RR-VERDICT: REQUEST-CHANGES m3-5-reshape-live-crew 0 /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-doc-sync.md`
if you cannot produce a coherent checklist.
