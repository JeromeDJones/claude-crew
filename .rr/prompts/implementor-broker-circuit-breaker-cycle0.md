## Task

Implement task `broker-circuit-breaker` (index 1) of the breakout against
the spec at `specs/m2-edge-routing.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `broker-circuit-breaker`
Task index: `1`
Description: In claude_crew/broker.py, add the per-edge circuit breaker on tee/direct edges:
_edge_exchanges counters with CIRCUIT_BREAKER_MAX_EXCHANGES budget, _edge_pending
2-node deadlock detection, force-insert-the-lead behavior (override edge to gated +
emit a {type:"circuit_breaker", edge, reason} control envelope to LEAD), and the
EdgeStat dataclass surfaced on BrokerSnapshot.topology_edge_stats. Consumes the
routing resolver and _edge_overrides from broker-edge-routing. Author
tests/test_circuit_breaker.py covering ATs 6–7.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [6,7]

The breakout artifact at `specs/m2-edge-routing.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_None — read only the spec section assigned to your task slice. (Note: claude_crew/broker.py already contains task broker-edge-routing's routing resolver, _edge_overrides, _edge_mode, send_scoped, authorize_send, promote_edge — build the circuit breaker on top of that committed code.)_

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_circuit_breaker.py
```

## Artifacts

Spec: `specs/m2-edge-routing.md`
Acceptance tests: `specs/m2-edge-routing.md`
Breakout: `specs/m2-edge-routing.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-broker-circuit-breaker-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/broker-circuit-breaker`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/broker-circuit-breaker"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-broker-circuit-breaker-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-broker-circuit-breaker-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
