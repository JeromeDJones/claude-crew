## Task

Implement task `d0-unconditional-send-to` (index 0) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `d0-unconditional-send-to`
Task index: `0`
Description: Remove the spawn-time `_has_out_edges` conditional in
`claude_crew/sdk_teammate.py:_run` (lines ~1542-1560) so the in-process
`send_to` MCP server + `_SEND_TO_TOOL_ID` allowed-tools/catalog entry are
wired for EVERY SdkTeammate regardless of out-edges. Flip the documented
contract in-code and update any existing test that asserts a no-out-edge
teammate lacks `send_to`. Foundational: reshape's respawn-free edge
addition depends on it.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): (see breakout in /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md)

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_None — read only the spec section assigned to your task slice._

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_d0_send_to_unconditional.py tests/test_scoped_send.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-task-d0-unconditional-send-to-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr-worktrees/d0-unconditional-send-to`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr-worktrees/d0-unconditional-send-to"` before any file operation. Treat this path as binding.

## Instructions

Follow this seven-step workflow:

1. Read the spec and the breakout entry for your task in full. Identify your
   slice of the acceptance tests by index.
2. Run the spec's test command. On cycle 0 expect failures (especially in
   your slice's tests). On cycle ≥ 1, focus first on the failing tests
   listed above before re-running the full suite.
3. Implement the change for your task's slice using available tools. Do not
   touch concerns claimed by other tasks unless your slice genuinely cannot
   reach green without it — in that case, prefer the smallest cross-slice
   edit possible and note it in the build report's scope-creep section.
4. Run the test command again. Iterate until your slice's tests pass and the
   suite as a whole stays green.
5. Capture remaining failing tests (if any) and the final exit code.
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-task-d0-unconditional-send-to-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-task-d0-unconditional-send-to-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
