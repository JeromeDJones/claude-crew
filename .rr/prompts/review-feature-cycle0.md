## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/fidelity-audit-followups-feature-review-0.md`

Use the `review-feature` skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
A	.rr/breakouts/fidelity-audit-followups.md
A	.rr/idea.txt
A	.rr/prompts/breakout-feature-cycle0.md
A	.rr/prompts/breakout-feature-cycle0.vars
A	.rr/prompts/breakout-feature-cycle1.md
A	.rr/prompts/breakout-feature-cycle1.vars
A	.rr/prompts/implementor-cycle0.md
A	.rr/prompts/implementor-cycle0.vars
A	.rr/prompts/plan-reviewer-cycle0.md
A	.rr/prompts/plan-reviewer-cycle0.vars
A	.rr/prompts/planner-cycle0.md
A	.rr/prompts/planner-cycle0.vars
A	.rr/prompts/review-breakout-cycle0.md
A	.rr/prompts/review-breakout-cycle0.vars
A	.rr/prompts/review-breakout-cycle1.md
A	.rr/prompts/review-breakout-cycle1.vars
A	.rr/prompts/review-breakout-cycle2.md
A	.rr/prompts/review-breakout-cycle2.vars
A	.rr/prompts/review-slice-cycle0.md
A	.rr/prompts/review-slice-cycle0.vars
A	.rr/reports/fidelity-audit-followups-breakout-review-0.md
A	.rr/reports/fidelity-audit-followups-breakout-review-1.md
A	.rr/reports/fidelity-audit-followups-breakout-review-2.md
A	.rr/reports/fidelity-audit-followups-plan-review-0.md
A	.rr/skew-warning.md
A	.rr/specs/fidelity-audit-followups.md
M	claude_crew/subagents/_loader.py
M	claude_crew/subagents/_user_loader.py
M	tests/test_fidelity_audit.py
M	tests/test_user_loader.py
```

This is the synthesis surface — every file the assembled feature touches.

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior feature-review report first. Surface findings
that recurred — unresolved issues carry forward and count against PASS.

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your feature-review with the
architecture it describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- Three checks only: integration coherence, holistic spec satisfaction,
  cracks. Per-slice quality issues belong to slice-review (already done).
- Run the spec's test command at least once via `Bash` as the feature-level
  non-regression check.
- On cycle ≥ 1: compare findings against the prior report.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <feature-review-report-path>`
