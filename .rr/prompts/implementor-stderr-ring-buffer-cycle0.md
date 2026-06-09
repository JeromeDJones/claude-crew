## Task

Implement task `stderr-ring-buffer` (index 0) of the breakout against
the spec at `specs/teammate-death-diagnostics.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `stderr-ring-buffer`
Task index: `0`
Description: Add the bounded, byte-capped stderr ring buffer to SdkTeammate in
claude_crew/sdk_teammate.py: module constants _STDERR_RING_MAXLEN=50 and
_STDERR_RING_BYTE_CAP=65536; __init__ fields _stderr_ring (deque) and
_stderr_ring_bytes; the never-raising _on_stderr_line callback; the
_stderr_tail_redacted() helper (joins ring, runs redact_output, returns
None when empty); register opts_kwargs["stderr"] = self._on_stderr_line in
_run; and add snap["stderr_tail"] + snap["in_flight_tools"] to
status_snapshot(). Authors the ring-buffer unit tests in
tests/test_sdk_teammate.py (ATs 1-4).

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [1,2,3,4] (see breakout in teammate-death-diagnostics.md)

The breakout artifact at `specs/teammate-death-diagnostics.md` has the full DAG. Read your
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
uv run pytest tests/test_sdk_teammate.py
```

## Artifacts

Spec: `specs/teammate-death-diagnostics.md`
Acceptance tests: `specs/teammate-death-diagnostics.md`
Breakout: `specs/teammate-death-diagnostics.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr/reports/teammate-death-diagnostics-task-stderr-ring-buffer-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr-worktrees/stderr-ring-buffer`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr-worktrees/stderr-ring-buffer"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr/reports/teammate-death-diagnostics-task-stderr-ring-buffer-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/teammate-death-diagnostics/.rr/reports/teammate-death-diagnostics-task-stderr-ring-buffer-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
