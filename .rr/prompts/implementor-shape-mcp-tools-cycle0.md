## Task

Implement task `shape-mcp-tools` (index 2) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `shape-mcp-tools`
Task index: `2`
Description: Add factory.known_roles to the sdk factory in claude_crew/factories.py
(lambda: tuple(holder.pack.keys()), read live; stub factory leaves it unset),
mirroring the factory.startup_diagnostics accessor idiom. Register
propose_shape(shape, adaptation_diff?, timeout_seconds=600) and
instantiate_shape(shape_id) as new @mcp.tool() closures in claude_crew/server.py.
propose_shape parses the shape (parse error -> {ok:False, stage:"parse"}),
registers a proposal, blocks on await_proposal. instantiate_shape refuses any
non-approved shape_id (no spawn); when the factory exposes known_roles it
pre-flight-resolves EVERY node role (exact or unique ":role" suffix promotion)
and, if any is unresolvable, returns {ok:False, unresolved_roles:[...]} spawning
NOTHING (all-or-nothing); otherwise spawns one teammate per node via
broker.spawn_teammate(role, name=slot, extra_tools=list(...)...), records a
Topology, marks the proposal instantiated (single-use).

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): 8,9,10,14 (see spec ## Acceptance Tests)

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

claude_crew/server.py (add the two @mcp.tool closures); claude_crew/factories.py (add factory.known_roles accessor, mirror startup_diagnostics idiom); claude_crew/broker.py (register_proposal/await_proposal/resolve_proposal/record_topology — built by task 1); claude_crew/shapes.py (parse_shape — task 0)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_shape_gate.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/specs/workflow-shape-composition-m0.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/workflow-shape-composition-m0-task-shape-mcp-tools-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr-worktrees/shape-mcp-tools`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr-worktrees/shape-mcp-tools"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/workflow-shape-composition-m0-task-shape-mcp-tools-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/workflow-shape-composition-m0/.rr/reports/workflow-shape-composition-m0-task-shape-mcp-tools-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
