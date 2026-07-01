## Task

Review the combined spec+breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md` for clarity, completeness, scope
discipline, testability (spec sections), and decomposition quality (## Task Breakout section).
Write a review report using the plan-review-report template (in the plugin install at
`doc/templates/plan-review-report-template.md`). One artifact, one gate, one verdict covering both halves.

## Spec Under Review

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior report before reviewing. Surface any findings that recurred —
unresolved issues carry forward and count against PASS. **The spec under review has CHANGED
since your last cycle** — the planner revised it to address your findings. Re-read the spec in
full from disk NOW (especially the sections tied to your prior Critical/High findings); do not
rely on your cached prior-cycle understanding or re-emit your prior verdict. Evaluate the
current artifact on disk, not the one you remember.

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your review with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew`

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
- **Inverse-coverage check (REQUEST-CHANGES trigger):** For each deliverable named in a task
  description, ask whether at least one acceptance test would fail if that deliverable were absent.
  If no AT provides that coverage, tag the gap `spec.deliverable.untested` (High) and issue
  REQUEST-CHANGES — a coordinator-/reviewer-spotted verification gap is a hard block, not an
  advisory note.
- **Provenance-verification check:** When the spec uses language like "established", "existing",
  "prior", "already", or "current" to describe a codebase state (e.g., "the existing API
  endpoint", "the established convention", "prior specs show"), verify the claim against the
  repository tree before accepting it. If the referenced artifact, convention, or prior work
  cannot be confirmed in the tree, tag the claim `spec.provenance.unverified` (High) and issue
  REQUEST-CHANGES — unverified provenance claims produce specs that implement against a fiction.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <plan-review-report-path>`
