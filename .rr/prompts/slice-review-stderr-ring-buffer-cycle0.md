## Task

Review task `stderr-ring-buffer` (index 0) of the breakout for slice
adherence, non-regression, and code-quality smoke. Your final-turn text is
the report. The coordinator persists it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr/reports/teammate-death-diagnostics-task-stderr-ring-buffer-slice-review-0.md`

Use the `review-slice` skill for the verification checklist, severity tiers,
tag vocabulary, and verdict rule.

## Task Under Review

Task name: `stderr-ring-buffer`
Task index: `0`

Spec: `specs/teammate-death-diagnostics.md`
Breakout: `specs/teammate-death-diagnostics.md`
Build report: `/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr/reports/teammate-death-diagnostics-task-stderr-ring-buffer-build-0.md`

Find the breakout entry whose `name` is `stderr-ring-buffer`. Its `acceptanceTests`
list (1-based indices into the spec's `## Acceptance Tests`) names which
acceptance tests this task owns — evaluate adherence against those, not the
whole spec.

## Inputs

This task's slice-level test command (run as the primary non-regression check;
fall back to the suite-level `## Test Command` with a note when empty):

uv run pytest tests/test_sdk_teammate.py

Other tasks' test commands (newline-separated, run for non-regression check):



## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior slice-review report first. Surface any findings
that recurred — unresolved issues carry forward and count against PASS.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr-worktrees/stderr-ring-buffer`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- Three checks, no more: slice adherence (this task's acceptance tests),
  non-regression (spec test command exits 0 in your re-run), code-quality
  smoke (changed files only).
- Cross-slice observations are Info tier; they do not affect the verdict.
- On cycle ≥ 1: compare findings against the prior report; note recurrences.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <slice-review-report-path>`
