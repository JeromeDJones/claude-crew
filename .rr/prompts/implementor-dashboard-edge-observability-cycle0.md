## Task

Implement task `dashboard-edge-observability` (index 3) of the breakout against
the spec at `specs/m2-edge-routing.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `dashboard-edge-observability`
Task index: `3`
Description: Deliver edge observability as an ON-GRAPH OVERLAY, not a side edge-list table. In
claude_crew/ui_server.py: emit topology_edge_stats (carrying crew_id) on the /api/state
instance payload; add GET /edge-log/{crew_id}/{from}/{to} and POST
/edge-promote/{crew_id}/{from}/{to} as per-instance endpoints with _PATH_PARAM_RE
guards and leader→follower proxies (_proxy_edge_log/_proxy_edge_promote mirroring
_proxy_shape_approval). In claude_crew/ui/dashboard.html: paint live edge state
directly on the rendered mermaid `graph TD` topology SVG (mermaid@11.4.1, the same
renderer the shape-gate proposal card uses) via a POST-RENDER SVG-WALK DECORATION LAYER
— the same pattern as the existing foreignObject legibility fix. After mermaid.render,
walk the SVG's .flowchart-link / edge-path elements (mermaid v11's edge class names,
already used for edge-thickening), key each to its (from,to) slots, and apply: per-mode
coloring (gated/tee/direct), an animation pulse when the exchange count increments, a
distinct tripped/gated style, click hit-testing that opens the GET /edge-log result,
and a promote-to-gated control (POST /edge-promote) reachable from the selected edge.
Author tests/test_edge_dashboard.py covering AT 11 (single-instance edge stats), AT 12
(edge-log + promote JSON), and AT 13 (multi-instance aggregation + proxy, mirroring
test_e2e_multi_instance.py) — the GREEN-SUITE data + endpoint contract, tested via
in-process UIServer + httpx. The on-graph SVG rendering itself (coloring, pulse, click
hit-testing) is verified MANUALLY / via a Playwright probe and is NOT in the default
green suite — do not add Playwright to the green suite or the spec Test Command.
Depends on broker-circuit-breaker for the EdgeStat snapshot surface and promote_edge;
touches only ui_server.py + dashboard.html so it runs parallel to scoped-send-teammate.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [11,12,13]

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
uv run pytest tests/test_edge_dashboard.py
```

## Artifacts

Spec: `specs/m2-edge-routing.md`
Acceptance tests: `specs/m2-edge-routing.md`
Breakout: `specs/m2-edge-routing.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-dashboard-edge-observability-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/dashboard-edge-observability`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr-worktrees/dashboard-edge-observability"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-dashboard-edge-observability-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-dashboard-edge-observability-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
