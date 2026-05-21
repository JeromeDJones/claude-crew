## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/specs/click-to-view-tool-output.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/reports/click-to-view-tool-output-feature-review-0.md`

Use the `review-feature` skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/specs/click-to-view-tool-output.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/specs/click-to-view-tool-output.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
M	claude_crew/broker.py
M	claude_crew/redaction.py
M	claude_crew/sdk_teammate.py
M	claude_crew/teammate.py
M	claude_crew/ui/dashboard.html
M	claude_crew/ui_server.py
A	tests/test_broker_tool_output.py
A	tests/test_dashboard_tool_output.py
A	tests/test_redaction_output.py
A	tests/test_tool_outputs.py
A	tests/test_ui_server_tool_output.py
---
 claude_crew/broker.py               |  25 ++-
 claude_crew/redaction.py            |  47 ++++++
 claude_crew/sdk_teammate.py         |  31 +++-
 claude_crew/teammate.py             |  29 +++-
 claude_crew/ui/dashboard.html       | 137 +++++++++++++++--
 claude_crew/ui_server.py            |  47 ++++++
 tests/test_broker_tool_output.py    | 137 +++++++++++++++++
 tests/test_dashboard_tool_output.py | 166 ++++++++++++++++++++
 tests/test_redaction_output.py      | 159 +++++++++++++++++++
 tests/test_tool_outputs.py          | 235 ++++++++++++++++++++++++++++
 tests/test_ui_server_tool_output.py | 297 ++++++++++++++++++++++++++++++++++++
 11 files changed, 1296 insertions(+), 14 deletions(-)
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

`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output`

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
