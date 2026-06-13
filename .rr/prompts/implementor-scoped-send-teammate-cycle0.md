## Task

Implement task `scoped-send-teammate` (index 2) of the breakout against
the spec at `specs/m2-edge-routing.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None at Medium+._ Note: the circuit breaker is BUDGET-ONLY (no deadlock detector) — reciprocal direct exchanges below CIRCUIT_BREAKER_MAX_EXCHANGES do NOT trip, so your AT#10 (direct ping-pong a→b→a→b stays off the lead) is supported by the breaker as long as the test stays below budget.

## Task Slice

Task name: `scoped-send-teammate`
Task index: `2`
Description: Wire the teammate-facing scoped send + neighbor injection. Add a neighbors= param to
build_teammate_prompt (claude_crew/teammate_prompt.py) emitting a SENTINEL_NEIGHBORS
section; thread neighbors through broker.spawn_teammate (claude_crew/broker.py) and
SdkTeammate (claude_crew/sdk_teammate.py, including an in-process SDK MCP send_to tool
whose handler calls broker.send_scoped); have server.instantiate_shape
(claude_crew/server.py) compute per-node out/in adjacency from shape.edges and pass it
at spawn. Author tests/test_scoped_send.py covering AT 8 (prompt injection via the
captured system_prompt_override), AT 9 (broker.send_scoped authorize/reject), and AT 10
(direct ping-pong stays off the lead inbox). Depends on broker-circuit-breaker to
serialize broker.py edits.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [8,9,10]

The breakout artifact at `specs/m2-edge-routing.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_Read the spec section for your task slice. broker.py already has (from merged tasks): _send_routed routing, send_scoped, authorize_send, _edge_overrides, promote_edge, the budget-only circuit breaker (_apply_circuit_breaker, _edge_exchanges, EdgeStat, BrokerSnapshot.topology_edge_stats). Build on that committed code._

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_scoped_send.py
```

## Artifacts

Spec: `specs/m2-edge-routing.md`
Acceptance tests: `specs/m2-edge-routing.md`
Breakout: `specs/m2-edge-routing.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-scoped-send-teammate-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/scoped-send-teammate`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/scoped-send-teammate"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-scoped-send-teammate-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-scoped-send-teammate-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
