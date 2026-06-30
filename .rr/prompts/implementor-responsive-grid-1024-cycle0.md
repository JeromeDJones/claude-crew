## Task

Implement task `responsive-grid-1024` (index 3) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

- [Info] process.full-suite: prior task (unified-node-language) had a regression masked by a SCOPED test command — a `.rail-topology svg` 2-SVG collision broke tests in test_edge_dashboard.py / test_dashboard_render.py that the scoped slice command never ran. LESSON FOR YOU: after your responsive-grid changes, run the FULL dashboard suite `uv run pytest -m dashboard` (not just your scoped slice command) and baseline any "pre-existing" failure against MASTER (commit 184a7f7), never git-stash. The new topology canvas SVG is `#topo-host svg` (not `.rail-topology svg`) — if you add any responsive test touching the topology, use `#topo-host`.

## Task Slice

Task name: `responsive-grid-1024`
Task index: `3`
Description: In claude_crew/ui/dashboard.html, replace the MissionControlLayout hardcoded inline
gridTemplateColumns "320px minmax(0, 1fr)" with a .dash-grid className whose columns
become clamp(220px, 22vw, 260px) minmax(0, 1fr) under a @media (max-width:1024px)
rule (also tightening .nodecard min-width to ~72 and roster padding under the
breakpoint). Two-pane stays; no drawer, no single-column. Serialized after
unified-node-language-proposal because it edits the same dashboard.html. Author
Playwright tests (marked @pytest.mark.dashboard) for AT 16,17 and the structural
swap deletion-detector AT 18.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [16,17,18]

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_Read the spec section + the committed mockup doc/design/mockups/shape-graphic-redesign-mock.html (the .dash-grid + @media(max-width:1024px) reference). The slice branch already has tasks 0,1,2 merged — build on them._

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest -m dashboard tests/test_responsive_grid.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-responsive-grid-1024-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/responsive-grid-1024`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/responsive-grid-1024"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-responsive-grid-1024-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-responsive-grid-1024-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
