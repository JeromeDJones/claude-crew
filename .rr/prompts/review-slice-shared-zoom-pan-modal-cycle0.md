## Task

Review task `shared-zoom-pan-modal` (index 1) of the breakout for slice
adherence, non-regression, and code-quality smoke. Your final-turn text is
the report. The coordinator persists it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-shared-zoom-pan-modal-slice-review-0.md`

Use the `review-slice` skill for the verification checklist, severity tiers,
tag vocabulary, and verdict rule.

## Task Under Review

Task name: `shared-zoom-pan-modal`
Task index: `1`

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Build report: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-shared-zoom-pan-modal-build-0.md`

The build report path above is an absolute path; read it at that exact absolute path — do not
resolve it relative to the working directory. If you cannot find the file at that exact absolute
path, report it missing rather than looking in a fallback location.

Find the breakout entry whose `name` is `shared-zoom-pan-modal`. Its `acceptanceTests`
list (1-based indices into the spec's `## Acceptance Tests`) names which
acceptance tests this task owns — evaluate adherence against those, not the
whole spec.

## Inputs

This task's slice-level test command (run as the primary non-regression check;
fall back to the suite-level `## Test Command` with a note when empty):

uv run pytest -m dashboard tests/test_topology_zoom_modal.py tests/test_unified_topology_keyed_lookup.py

Other tasks' test commands (newline-separated, run for non-regression check):

uv run pytest tests/test_shape_proposal_payload.py

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior slice-review report first. Surface any findings
that recurred — unresolved issues carry forward and count against PASS. **The
implementation under review has CHANGED since your last cycle** — the implementor
reworked it to address your findings. Re-read the current diff and changed files
from disk NOW; do not rely on your cached prior-cycle assessment or re-emit your
prior verdict. Evaluate the current code on disk, not the one you remember.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/shared-zoom-pan-modal`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- Three checks, no more: slice adherence (this task's acceptance tests),
  non-regression (spec test command exits 0 in your re-run), code-quality
  smoke (changed files only).
- Cross-slice observations are Info tier; they do not affect the verdict.
- On cycle ≥ 1: compare findings against the prior report; note recurrences.
- **Deferred-test deliverable presence check (mandatory, not discretionary):**
  When a task names a deferred-test deliverable — a deliverable whose primary
  test is deferred out of the owned acceptance tests — grep for the actual
  symbol/wiring (production caller, mounted component, registered tool/route)
  in the changed files. Treat its absence as `slice.incomplete` (High) even
  when every owned AT is green.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <slice-review-report-path>`
