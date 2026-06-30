## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-feature-review-0.md`

Follow the review-feature skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
Full feature diff vs master (2435 lines) is on disk at: /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/branch-diff.txt — READ IT for the integration review (too large to inline). Name-status overview:
M	claude_crew/ui/dashboard.html
M	claude_crew/ui_server.py
M	tests/dashboard/test_roster_spotlight.py
M	tests/dashboard/test_shape_resurface.py
M	tests/test_dashboard_render.py
M	tests/test_edge_dashboard.py
A	tests/test_responsive_grid.py
A	tests/test_shape_proposal_payload.py
M	tests/test_shape_render.py
A	tests/test_topology_zoom_modal.py
A	tests/test_unified_proposal_language.py
M	tests/test_unified_topology_keyed_lookup.py

KEY INTEGRATION SURFACES to assess: claude_crew/ui/dashboard.html holds all 3 UI tasks (shared zoom/pan modal + unified node language + responsive grid) plus the existing topology; claude_crew/ui_server.py adds the structured shape payload (task 0) that the proposal modal (task 2) consumes. Read the integrated dashboard.html directly + the 4 slice-review reports in /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/.
```

This is the synthesis surface — every file the assembled feature touches.

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior feature-review report first. Surface findings
that recurred — unresolved issues carry forward and count against PASS. **The
assembled feature has CHANGED since your last cycle** — it was re-decomposed and
rebuilt to address your findings. Re-read the current branch diff and changed
files from disk NOW; do not rely on your cached prior-cycle assessment or re-emit
your prior verdict. Evaluate the current synthesis on disk, not the one you remember.

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your feature-review with the
architecture it describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign`

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
