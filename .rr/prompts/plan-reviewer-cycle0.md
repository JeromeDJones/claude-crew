## Task

Review the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md` for clarity, completeness, scope discipline, and testability.
Write a review report using the plan-review-report template (in the plugin install at
`doc/templates/plan-review-report-template.md`).

## Spec Under Review

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior report before reviewing. Surface any findings that recurred —
unresolved issues carry forward and count against PASS.

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your review with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- PASS only if the spec is clear, complete, well-scoped, and has a testable `## Test Command`.
- REQUEST-CHANGES if any required section is missing, ambiguous, or the test command is not
  runnable as written.
- On cycle ≥ 1: compare findings against the prior report; note anything that recurred.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <plan-review-report-path>`
