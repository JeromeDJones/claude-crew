## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/workflow-shape-composition-m0-feature-review-0.md`

Follow the review-feature skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
 claude_crew/broker.py         | 122 ++++++++++
 claude_crew/factories.py      |   4 +
 claude_crew/server.py         | 168 ++++++++++++++
 claude_crew/shapes.py         | 241 ++++++++++++++++++++
 claude_crew/ui/dashboard.html | 126 +++++++++++
 claude_crew/ui_server.py      | 117 ++++++++++
 tests/test_shape_broker.py    | 233 +++++++++++++++++++
 tests/test_shape_dashboard.py | 496 +++++++++++++++++++++++++++++++++++++++++
 tests/test_shape_gate.py      | 422 +++++++++++++++++++++++++++++++++++
 tests/test_shape_render.py    | 297 ++++++++++++++++++++++++
 tests/test_shapes.py          | 508 ++++++++++++++++++++++++++++++++++++++++++
 11 files changed, 2734 insertions(+)

(full per-file diffs available via git; detailed per-slice analysis in the 5 slice-review reports under .rr/reports/)
```

This is the synthesis surface — every file the assembled feature touches.

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior feature-review report first. Surface findings
that recurred — unresolved issues carry forward and count against PASS.

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your feature-review with the
architecture it describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0`

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
