## Task

Implement task `adapt-shape-tool` (index 1) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `adapt-shape-tool`
Task index: `1`
Description: Add the adapt_shape MCP tool to claude_crew/server.py: resolve the base shape
(exactly one of base_shape_id/base_shape; base_shape_id must resolve to a
pending or approved proposal — reject instantiated/declined/timed_out at
stage:"base"); for verb in {swap, augment}, resolve the new/augmenting role
through the same factory.known_roles()/resolve_role() seam instantiate_shape
uses (skip when known_roles absent); construct and apply the verb command;
on success call broker.register_proposal(new_shape, adaptation_diff=diff.render())
and return {ok, shape_id, status:"pending", diff, shape=shape_to_dict(new_shape)};
map every failure to the documented {ok:False, stage:...} envelope with NO
proposal registered. broker.py is NOT modified. Authors the new
tests/test_shape_adapt_tool.py with gate integration, the iterative re-gate loop,
role-resolution (resolvable / unresolvable / no-known_roles-skip), and the
base/verb/illegal-mutation guards.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): 23,24,25,26,27,28,29,30,31,32,33

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

- claude_crew/shapes.py (the ShapeAdaptation base + AddNode/Swap/Augment/SetGate/Drop verbs + AdaptationDiff + AdaptationChain + shape_to_dict added by the shape-adaptation-algebra task; your tool constructs and applies these)
- claude_crew/server.py (the instantiate_shape pre-flight role-resolution seam factory.known_roles()/resolve_role() at ~919-936; broker.register_proposal usage; existing @mcp.tool() patterns like propose_shape/instantiate_shape)
- tests/test_shape_gate.py (test conventions: create_connected_server_and_client_session, make_server(broker=, factory=), stub-factory known_roles injection, _content_json unwrap)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_shape_adapt_tool.py tests/test_shape_gate.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-adapt-shape-tool-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr-worktrees/adapt-shape-tool`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr-worktrees/adapt-shape-tool"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-adapt-shape-tool-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-adapt-shape-tool-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
