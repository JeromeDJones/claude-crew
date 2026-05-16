## Task

Review task `yaml-loader-extension` (index 0) of the breakout for slice
adherence, non-regression, and code-quality smoke. Your final-turn text is
the report. The coordinator persists it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-task-yaml-loader-extension-slice-review-0.md`

Use the `review-slice` skill for the verification checklist, severity tiers,
tag vocabulary, and verdict rule.

## Task Under Review

Task name: `yaml-loader-extension`
Task index: `0`

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/yaml-loader-extension/.rr/specs/fidelity-audit-followups.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/yaml-loader-extension/.rr/breakouts/fidelity-audit-followups.md`
Build report: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-task-yaml-loader-extension-build-0.md`

Find the breakout entry whose `name` is `yaml-loader-extension`. Its `acceptanceTests`
list (1-based indices into the spec's `## Acceptance Tests`) names which
acceptance tests this task owns — evaluate adherence against those, not the
whole spec.

## Inputs

Other tasks' test commands (newline-separated, run for non-regression check):



## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior slice-review report first. Surface any findings
that recurred — unresolved issues carry forward and count against PASS.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr-worktrees/yaml-loader-extension`

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
