## Task

Implement task `mcp-refresh-agents-tool` (index 2) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool/.rr/specs/agent-pack-refresh.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

- [Info] cross-slice: AT-5 requires "future-spawns-only" in BOTH the refresh_agents tool DOCSTRING and RefreshResult.note (the note is already returned by _PackState.refresh from task 2 — verify and reuse).
- [Info] cross-slice: RefreshResult.counts per-layer values (default/plugin/user/project) currently return 0; only total is populated. AT-9/10/11 assert shape only, not per-layer values, so this does not block your task — but do NOT paper over it; surface it honestly in your build report so feature-review can rule on it.

## Task Slice

Task name: `mcp-refresh-agents-tool`
Task index: `2`
Description: Register a `@mcp.tool` `refresh_agents` on the FastMCP server in `claude_crew/server.py`. The tool calls `getattr(factory, "refresh_pack", <no-op>)()` and returns the structured `RefreshResult` dict documented in Data/API Contracts. The tool docstring MUST contain the substring `"future-spawns-only"` and explain that already-running teammates keep the AgentDefinition snapshot they spawned on (refresh affects subsequent spawns only). Wire so stub-mode `make_server()` exposes the tool and returns `ok=True` with empty diff, and sdk-mode surfaces real refresh output including warnings for a malformed project agent file without crashing the server. This task's gate is the full suite — it is the final task, the cross-cutting whole-suite check per the repo's "validate the whole suite" standard.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): AT-5, AT-9, AT-10, AT-11 (see spec ## Acceptance Tests)

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool/.rr/specs/agent-pack-refresh.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

claude_crew/server.py (make_server, the existing @mcp.tool registrations to mirror, the factory build at ~line 88); claude_crew/factories.py (factory.refresh_pack attached in task 2, and stub_factory.refresh_pack no-op)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool/.rr/specs/agent-pack-refresh.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool/.rr/specs/agent-pack-refresh.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool/.rr/specs/agent-pack-refresh.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-mcp-refresh-agents-tool-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/mcp-refresh-agents-tool"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-mcp-refresh-agents-tool-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-mcp-refresh-agents-tool-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
