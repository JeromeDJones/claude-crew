## Task

Review the breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md` for decomposition quality,
parallelism opportunities, hidden dependencies, and scope leakage. Write a
breakout-review report using the `review-breakout` skill's report schema
(your final-turn text is the report).

## Inputs

- Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`
- Breakout under review: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md`

You may read both. Cross-check that every acceptance test in the spec is
claimed by exactly one task in the breakout.

## Cycle

Cycle: 2
Prior report: /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-breakout-review-1.md

On cycle ≥ 1, read the prior report before reviewing. Surface any findings
that recurred — unresolved issues carry forward and count against PASS.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- PASS only if the decomposition covers every acceptance test, has no
  spurious dependencies, no obvious hidden coupling, and no scope leakage
  beyond the spec.
- REQUEST-CHANGES if any acceptance test is unclaimed, any dependency edge
  looks artificial, any task description leaves the implementor needing to
  re-read the whole spec, or scope outside the spec has been introduced.
- On cycle ≥ 1: compare findings against the prior report; note anything
  that recurred.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <breakout-review-report-path>`
