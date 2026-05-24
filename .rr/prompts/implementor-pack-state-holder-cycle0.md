## Task

Implement task `pack-state-holder` (index 0) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder/.rr/specs/agent-pack-refresh.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `pack-state-holder`
Task index: `0`
Description: Introduce `_PackState` dataclass in `claude_crew/factories.py` carrying `pack`, `role_ss`, `bodies`, `home_dir`, `project_root`, and a `threading.Lock`. Refactor `default_factory()` so the three closure- locals are replaced by a single holder instance; rewrite `factory()`, `_resolve_role`, and `_resolve_agent_def` to read fields LIVE off the holder at every call rather than capturing them. No `refresh()` yet and no new MCP tool — just the indirection. The startup initial-load path must remain behaviorally identical (same diagnostics flow, same `merged_pack`/`role_ss`/`bodies` semantics for the first build). AT-3 exercises the live-read indirection via DIRECT holder mutation (no `refresh_pack()` involved — that arrives in task 2). The refactor's behavior-preservation guarantee is additionally gated by the full existing test suite, which this task's `testCommand` runs.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): AT-3 (see spec ## Acceptance Tests)

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder/.rr/specs/agent-pack-refresh.md` has the full DAG. Read your
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
uv run pytest
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder/.rr/specs/agent-pack-refresh.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder/.rr/specs/agent-pack-refresh.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder/.rr/specs/agent-pack-refresh.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-pack-state-holder-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr-worktrees/pack-state-holder"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-pack-state-holder-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-pack-state-holder-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
