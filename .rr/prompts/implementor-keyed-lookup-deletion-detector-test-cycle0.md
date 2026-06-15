## Task

Implement task `keyed-lookup-deletion-detector-test` (index 3) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_Prior slice-reviews: MED-01 (gated-through-lead bridge) resolved in task 2; LOW-01 (synthetic-key collision when two gated edges share a lead-adjacent endpoint) deferred to backlog. Both concern GATED edges; your AT-5/AT-6 tests target the DIRECT reciprocal pair + the window.mapEdgeStatsToPaths branches — unaffected by those. The window.mapEdgeStatsToPaths(svgRoot, edgeStats) helper already EXISTS (built in task 2): id-parse primary (/^L[-_](.+?)[-_](.+?)[-_]\\d+$/), LS-/LE- class fallback, positional last-resort + console.warn. Test it as-is; do not modify it._

## Task Slice

Task name: `keyed-lookup-deletion-detector-test`
Task index: `3`
Description: Author the NEW green-suite Playwright file
tests/test_unified_topology_keyed_lookup.py. The file defines its OWN
stubbed-/api/state fixture inline (a reciprocal-pair topology_edge_stats
payload plus the slot_to_teammate field) — it does NOT import
five_agent_url (module-local to test_roster_spotlight.py, not importable);
it reuses only the module-scoped Playwright `page` fixture from
tests/conftest.py. (1) DOM-level deletion-detector (AT-5): render with a
reciprocal pair (a,b,direct,healthy,8x)+(b,a,direct,tripped,1x), query
path.flowchart-link, assert each rendered edge carries its OWN stroke
color (green vs red), thickness (not-3px vs 3px), and badge (`direct 8`
vs `direct ⚡`), keyed by parsing path id / LS-/LE- classes. (2)
Unit-level (AT-6): call window.mapEdgeStatsToPaths via page.evaluate()
across all five branches (source-order, mermaid-reordered, reciprocal,
malformed-id->class-fallback, both-fail->positional+warn) in the same
Playwright session. Claims AT-5, AT-6.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [5,6]

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

claude_crew/ui/dashboard.html (window.mapEdgeStatsToPaths + displayEdges); doc/design/unified-topology-view-uxspec.md (section 2 keyed-lookup contract); tests/test_edge_dashboard.py (existing Playwright fixture patterns + the gated-bridge tests at L821/L886 for style); tests/conftest.py (module-scoped page fixture)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_unified_topology_keyed_lookup.py -q
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/specs/unified-topology-view.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/reports/unified-topology-view-task-keyed-lookup-deletion-detector-test-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr-worktrees/keyed-lookup-deletion-detector-test`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr-worktrees/keyed-lookup-deletion-detector-test"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/reports/unified-topology-view-task-keyed-lookup-deletion-detector-test-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/unified-topology-view/.rr/reports/unified-topology-view-task-keyed-lookup-deletion-detector-test-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
