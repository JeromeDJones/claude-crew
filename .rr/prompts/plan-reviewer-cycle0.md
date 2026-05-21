## Task

Review the combined spec+breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/specs/click-to-view-tool-output.md` for clarity, completeness, scope
discipline, testability (spec sections), and decomposition quality (## Task Breakout section).
Write a review report using the plan-review-report template (in the plugin install at
`doc/templates/plan-review-report-template.md`). One artifact, one gate, one verdict covering both halves.

## Spec Under Review

`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/specs/click-to-view-tool-output.md`

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

`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- PASS only if:
  - The spec sections are clear, complete, well-scoped, and have a testable `## Test Command`.
  - The `## Task Breakout` section covers every numbered acceptance test (exactly one task per AT),
    has no spurious dependency edges, no mega-tasks, and task descriptions are concrete enough an
    implementor can build without re-reading the full spec.
- REQUEST-CHANGES if any required section is missing or ambiguous, the test command is not
  runnable as written, any acceptance test is unclaimed or duplicated, or any task is a mega-task.
- On cycle ≥ 1: compare findings against the prior report; note anything that recurred.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <plan-review-report-path>`
