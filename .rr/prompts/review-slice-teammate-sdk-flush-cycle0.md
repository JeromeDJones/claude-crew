## Task

Review task `teammate-sdk-flush` (index 1) of the breakout for slice
adherence, non-regression, and code-quality smoke. Your final-turn text is
the report. The coordinator persists it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/reports/graceful-termination-memory-flush-task-teammate-sdk-flush-slice-review-0.md`

Use the `review-slice` skill for the verification checklist, severity tiers,
tag vocabulary, and verdict rule.

## Task Under Review

Task name: `teammate-sdk-flush`
Task index: `1`

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/specs/graceful-termination-memory-flush.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/specs/graceful-termination-memory-flush.md`
Build report: `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/reports/graceful-termination-memory-flush-task-teammate-sdk-flush-build-0.md`

Find the breakout entry whose `name` is `teammate-sdk-flush`. Its `acceptanceTests`
list (1-based indices into the spec's `## Acceptance Tests`) names which
acceptance tests this task owns — evaluate adherence against those, not the
whole spec.

## Inputs

This task's slice-level test command (run as the primary non-regression check;
fall back to the suite-level `## Test Command` with a note when empty):

uv run pytest tests/test_graceful_flush_sdk.py

Other tasks' test commands (newline-separated, run for non-regression check):

uv run pytest tests/test_graceful_termination_base.py
uv run pytest tests/test_graceful_kill_broker.py

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior slice-review report first. Surface any findings
that recurred — unresolved issues carry forward and count against PASS.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr-worktrees/teammate-sdk-flush`

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
