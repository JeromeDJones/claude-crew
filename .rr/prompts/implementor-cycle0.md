## Task

Implement task `full-validation-baseline` (index 2) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline/.rr/specs/fidelity-audit-followups.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Task Slice

Task name: `full-validation-baseline`
Task index: `2`
Description: (see breakout)

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [2, 8]

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline/.rr/breakouts/fidelity-audit-followups.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline/.rr/specs/fidelity-audit-followups.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline/.rr/specs/fidelity-audit-followups.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline/.rr/breakouts/fidelity-audit-followups.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-task-full-validation-baseline-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/full-validation-baseline"` before any file operation. Treat this path as binding.

## Instructions

Follow this seven-step workflow:

1. Read the spec and the breakout entry for your task in full. Identify your
   slice of the acceptance tests by index.
2. Run the spec's test command. On cycle 0 expect failures (especially in
   your slice's tests). On cycle ≥ 1, focus first on the failing tests
   listed above before re-running the full suite.
3. Implement the change for your task's slice using available tools. Do not
   touch concerns claimed by other tasks unless your slice genuinely cannot
   reach green without it — in that case, prefer the smallest cross-slice
   edit possible and note it in the build report's scope-creep section.
4. Run the test command again. Iterate until your slice's tests pass and the
   suite as a whole stays green.
5. Capture remaining failing tests (if any) and the final exit code.
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-task-full-validation-baseline-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-task-full-validation-baseline-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
