## Task

Implement task `ui-route` (index 3) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/specs/click-to-view-tool-output.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `ui-route`
Task index: `3`
Description: Register Starlette route GET /tool-output/{teammate_id}/{tool_use_id} in UiServer._make_app. Validate path params against `^[A-Za-z0-9_\-]+$` (400 on miss). Delegate to Broker.get_tool_output; return 200 {body, truncated, redaction_version} on hit, 404 {error:"not_found"} on miss, 500 {error:"internal_error"} on unexpected exception (mirrors /wait-messages handler). Also extend _build_state's tool-event merge to include tool_use_id on the kind:"tool" record. httpx-based async tests cover AT-5, AT-6, AT-9.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): AT-5, AT-6, AT-9 (see spec ## Acceptance Tests)

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/specs/click-to-view-tool-output.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

claude_crew/ui_server.py (the /wait-messages handler is the reference pattern for the new /tool-output route — mirror its localhost posture, path-param validation, 404/400/500 handling)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_ui_server_tool_output.py -v
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/specs/click-to-view-tool-output.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/specs/click-to-view-tool-output.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/specs/click-to-view-tool-output.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/reports/click-to-view-tool-output-task-ui-route-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/reports/click-to-view-tool-output-task-ui-route-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/click-to-view-tool-output/.rr-worktrees/ui-route/.rr/reports/click-to-view-tool-output-task-ui-route-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
