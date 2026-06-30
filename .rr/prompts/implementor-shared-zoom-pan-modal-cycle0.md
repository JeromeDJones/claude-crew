## Task

Implement task `shared-zoom-pan-modal` (index 1) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `shared-zoom-pan-modal`
Task index: `1`
Description: In claude_crew/ui/dashboard.html, build the shared zoom/pan substrate faithful to
the mockup (fitToHost with floor 0.05, applyTransform with actual-% zoom label,
bindPanZoom wheel/drag, renderInto with double-requestAnimationFrame auto-fit, the
.modal/.zoom-surface DOM, esc-to-close) and the live-topology expand path
(openTopologyModal). Add the .topo-head control row (title + subtitle + −/fit/+ +
expand button) above the topology host and grow the host
clamp(220, 240 + 18*max(0, agents-3), 340)px. The .modal-body MUST use .zoom-surface,
NOT the .topology-host height cap. Preserve window.mapEdgeStatsToPaths, the
displayEdges gated-bridge (_source) expansion, the /edge-log fetch + selected-edge
panel + promote-to-gated control, and the edge-decoration useEffect byte-compatibly.
First editor of dashboard.html. Author Playwright tests (marked @pytest.mark.dashboard)
for AT 1,3,4,5,6 and structural deletion-detectors AT 2,7,8.

PLAN-REVIEW LOW ADVISORY (coordinator): AT 8 currently greps only window.mapEdgeStatsToPaths and the _source token, so it would NOT catch accidental removal of the /edge-log selected-edge panel or the promote-to-gated control during the modal rework. Add a structural deletion-detector (in AT 8 or a sibling) that greps dashboard.html for an /edge-log literal AND a promote-to-gated control literal so their removal fails the green suite. Non-blocking but do it — these are real interactive features the modal work touches.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [1,2,3,4,5,6,7,8]

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_None — read only the spec section assigned to your task slice, plus the committed mockup doc/design/mockups/shape-graphic-redesign-mock.html cited as source of truth._

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest -m dashboard tests/test_topology_zoom_modal.py tests/test_unified_topology_keyed_lookup.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-shared-zoom-pan-modal-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/shared-zoom-pan-modal`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/shared-zoom-pan-modal"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-shared-zoom-pan-modal-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-shared-zoom-pan-modal-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
