## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/specs/graceful-termination-memory-flush.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/reports/graceful-termination-memory-flush-feature-review-0.md`

Follow the review-feature skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/specs/graceful-termination-memory-flush.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/specs/graceful-termination-memory-flush.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
Integrated source diff vs master (file-level; read full files in the worktree at claude_crew/ and tests/):

 claude_crew/broker.py                   |  88 ++++++-
 claude_crew/sdk_teammate.py             | 180 ++++++++++++--
 claude_crew/server.py                   |  18 +-
 claude_crew/teammate.py                 |  36 ++-
 doc/sdk-teammate-wiring.md              |   2 +-
 tests/test_graceful_flush_sdk.py        | 421 ++++++++++++++++++++++++++++++++
 tests/test_graceful_kill_broker.py      | 325 ++++++++++++++++++++++++
 tests/test_graceful_shutdown_all.py     | 268 ++++++++++++++++++++
 tests/test_graceful_termination_base.py |  72 ++++++
 tests/test_live_graceful_flush.py       |  99 ++++++++
 tests/test_server_graceful.py           | 194 +++++++++++++++
 11 files changed, 1682 insertions(+), 21 deletions(-)

Key files: claude_crew/teammate.py (ABC hook + StubTeammate), claude_crew/sdk_teammate.py (real flush: begin_graceful_termination/_run_flush_turn/sentinel), claude_crew/broker.py (kill_teammate graceful + _terminating + shutdown_all parallel), claude_crew/server.py (MCP arg threading), doc/sdk-teammate-wiring.md (doc fix).
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

`/home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush`

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
