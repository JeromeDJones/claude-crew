## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-feature-review-0.md`

Follow the review-feature skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
Branch diff (master...HEAD), EXCLUDING .rr/ audit artifacts. Full code diff available via: git -C /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew diff master...HEAD -- . ':(exclude).rr/'

== name-status (code + docs) ==
M	CLAUDE.md
M	claude_crew/broker.py
M	claude_crew/sdk_teammate.py
M	claude_crew/server.py
M	doc/ARCHITECTURE.md
A	tests/test_d0_send_to_unconditional.py
M	tests/test_e2e_pack_parity.py
M	tests/test_e2e_pack_tool_allowlist.py
A	tests/test_live_reshape.py
A	tests/test_reshape_broker_helpers.py
A	tests/test_reshape_crew_gate.py
A	tests/test_reshape_crew_verbs.py
A	tests/test_reshape_docs_staleness.py
M	tests/test_sdk_teammate.py

== stat ==
 CLAUDE.md                              |   6 +-
 claude_crew/broker.py                  |  34 +-
 claude_crew/sdk_teammate.py            |  36 +-
 claude_crew/server.py                  | 474 +++++++++++++++++++
 doc/ARCHITECTURE.md                    |  15 +-
 tests/test_d0_send_to_unconditional.py | 321 +++++++++++++
 tests/test_e2e_pack_parity.py          |  10 +-
 tests/test_e2e_pack_tool_allowlist.py  | 136 ++++--
 tests/test_live_reshape.py             | 500 ++++++++++++++++++++
 tests/test_reshape_broker_helpers.py   | 197 ++++++++
 tests/test_reshape_crew_gate.py        | 830 +++++++++++++++++++++++++++++++++
 tests/test_reshape_crew_verbs.py       | 660 ++++++++++++++++++++++++++
 tests/test_reshape_docs_staleness.py   |  47 ++
 tests/test_sdk_teammate.py             |  42 +-
 14 files changed, 3230 insertions(+), 78 deletions(-)
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

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew`

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
