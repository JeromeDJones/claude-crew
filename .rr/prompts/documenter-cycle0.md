## Task

You are `rr-documenter`. Drive all three retrospecting sub-steps for slice
`multi-scope-agent-memory`, producing three artifacts in order before emitting your final
verdict:

1. **feature-retro** — author the feature retrospective report
2. **workflow-retro** — author the workflow-retro report (or write the
   canonical skip stub when disabled)
3. **doc-sync** — compute candidate doc-sync edits and emit the checklist

## Inputs

- Slug: `multi-scope-agent-memory`
- Spec under retrospect: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md`
- Reports directory: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports`
- Feature-retro report output path: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-feature.md`
- Workflow-retro report output path: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-workflow.md`
- Workflow-retro enabled: `false`
- Workflow-retro user input block (populated by coordinator when enabled):



- Doc-sync report output path: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-doc-sync.md`
- Prior report (empty on cycle 0): ``
- Already-routed retro findings — skip these in doc-sync: `_None — no findings routed yet (cycle 0)._`

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory`

Change to this directory before all file operations.

## Cycle

Cycle: 0

On cycle ≥ 1, read `` first and address every reviewer
concern by name across all three sub-steps.

## Instructions

**Sub-step 1 — feature-retro**

Author the feature retrospective report at `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-feature.md`.

Read the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md` and every `*.md` under `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports`
before writing. Conform to `doc/templates/feature-retro-template.md` (read it
once before writing — it lives in the plugin install).

- Write findings only. Omit `## Applied Fixes` and `## Deferred Fixes`
  entirely. No destination metadata, no direct file edits.
- Cite report filenames in your `## What Went Well` and `## What Didn't`
  bullets.
- If no prior reports exist under `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports`, record
  `_No prior reports — retro skipped on grounds of empty evidence base._`
  under both `## What Went Well` and `## What Didn't`.
- Do not run the validation test command. Do not modify code that would
  invalidate the validation PASS already on record.

**Sub-step 2 — workflow-retro**

Dispatch on `WORKFLOW_RETRO_ENABLED` (see Inputs above).

- **`WORKFLOW_RETRO_ENABLED=false` (default).** Write the canonical skip stub
  to `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-workflow.md`:

  ```
  _workflow-retro: skipped (workflowRetroEnabled=false; enable per-slice via /repo-react with-workflow-retro <slug> at slice-start, or globally via RR_WORKFLOW_RETRO=1 env)._
  ```

  Do not invoke any broadcast. Proceed immediately to sub-step 3.

- **`WORKFLOW_RETRO_ENABLED=true`.** The coordinator has already collected
  user input from the broadcast+user-input loop and passed it as
  `WORKFLOW_RETRO_USER_BLOCK` above. Author the workflow-retro report using
  the user block under `## User Input`. Write the full report to
  `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-workflow.md`.

**Sub-step 3 — doc-sync**

Compute candidate doc-sync edits for slice `multi-scope-agent-memory` against the five target
files under the worktree's `doc/` tree, and emit a `doc-sync-checklist`
artifact at:

`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-doc-sync.md`

Read the spec under retrospect and every report in `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports` before
proposing changes. Render the checklist using
`doc/templates/doc-sync-checklist-template.md` (read it once before writing —
it lives in the plugin install).

The five target files, in canonical order:

1. `doc/PRODUCT-VISION.md`
2. `doc/ROADMAP.md`
3. `doc/features/FEATURE-multi-scope-agent-memory.md`
4. `doc/BACKLOG.md`
5. `doc/ARCHITECTURE.md` — skip cleanly with note `file absent` if it does
   not exist; never propose creating it.

If `doc/ARCHITECTURE.md` does not exist, skip with note `file absent`.

- Any finding whose ID appears in the already-routed block (see Inputs) is
  already routed. Do not re-propose it under any doc target.
- Write the doc-sync checklist file at `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-doc-sync.md`. Do not
  stage, commit, or modify any other files in this slice — the coordinator
  owns staging and the conditional `docs:` commit.
- The checklist must include all five rows in canonical order.
- If `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports` is empty, write the checklist with
  `_No prior reports — no doc-sync candidates._` under each row's `Notes`
  and `## Outcome: all candidates declined`.

Emit a final line `RR-VERDICT: PASS multi-scope-agent-memory 0 /home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-doc-sync.md`
on success, or `RR-VERDICT: REQUEST-CHANGES multi-scope-agent-memory 0 /home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-retro-doc-sync.md`
if you cannot produce a coherent checklist.
