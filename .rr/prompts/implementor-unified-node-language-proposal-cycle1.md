## Task

Implement task `unified-node-language-proposal` (index 2) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `unified-node-language-proposal`
Task index: `2`
Description: In claude_crew/ui/dashboard.html, add the shapeToMermaidUnified(shape, {proposed})
client helper that emits the same .nodecard foreignObject labels and --edge-* edge
colors as the live topology, plus the .nodecard.proposed dashed-accent CSS variant.
Convert ShapeProposalCard from the static maxWidth:420 side-by-side diagram into a
preview thumbnail that opens the shared zoom/pan modal (openProposalModal) built by
shared-zoom-pan-modal, consuming the structured shape delivered by
proposal-payload-structured-shape. Render through the existing mermaid
securityLevel:'strict' + DOMPurify pipeline unchanged. Depends on both prior tasks
(needs the structured payload AND the shared modal; serialized after shared-zoom-pan-modal
because both edit dashboard.html). Author Playwright tests (marked @pytest.mark.dashboard)
for AT 11,12,14, structural deletion-detector AT 13, and the XSS non-regression guard
AT 15 (assert securityLevel:'strict' present + run the existing XSS suite).

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): [11,12,13,14,15]

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

_The full dashboard suite must pass. See FAILING_TESTS_BLOCK for the corrected ground truth._

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest -m dashboard tests/test_unified_proposal_language.py tests/dashboard/test_dashboard_artifact_xss.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-unified-node-language-proposal-build-1.md`

Prior build report (empty on cycle 0): /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-unified-node-language-proposal-build-0.md

Failing tests from prior cycle (empty on cycle 0 — run the full suite):
COORDINATOR CORRECTION — your cycle-0 "4 pre-existing failures" classification is WRONG. I verified all 4 PASS on master (true pre-feature baseline, commit 184a7f7). Your git-stash baseline was the POST-task-1 slice branch, so you read task-1-introduced breakage as "pre-existing". They are NOT pre-existing.

ROOT CAUSE (NOT a behavior regression): all 4 are the SAME strict-mode locator collision you already fixed in 3 other test files — task 1 added the expand-button SVG inside .rail-topology, so the selector ".rail-topology svg" now matches 2 elements. The dashboard topology BEHAVIOR (gated bridge two-amber-segments, single-graph-with-lead, slot-to-teammate activity join, /edge-log crewId path) is CORRECT and unchanged; only the test locators are ambiguous.

THE 4 FAILING TESTS:
- tests/test_dashboard_render.py::test_at7_unified_topology_preserves_crew_id_in_edge_log_path
- tests/test_edge_dashboard.py::test_at3_unified_topology_renders_single_graph_with_lead_node
- tests/test_edge_dashboard.py::test_at8_activity_joins_via_slot_to_teammate
- tests/test_edge_dashboard.py::test_gated_edge_bridges_through_lead_with_two_amber_segments

REMAINING ".rail-topology svg" OCCURRENCES TO FIX (apply the SAME #topo-host disambiguation you used elsewhere, but PRESERVE each test's intent — some COUNT svgs/paths, so scope the COUNT/query to #topo-host so it EXCLUDES the expand-button SVG; do NOT blindly narrow a count to 1 if the test means the flowchart):
- tests/test_edge_dashboard.py: lines 649, 657, 662, 747, 831, 896
- tests/test_dashboard_render.py: lines 1027, 1040

AFTER FIXING: grep tests/ for any other ".rail-topology svg" and fix ALL. Then run the FULL dashboard suite: `uv run pytest -m dashboard` — confirm 0 failures (these 4 were the only remaining ones; total should be ~1645 passed, 0 failed). Update your build report's failures table to read ZERO. These test-locator updates are within your taskTouches (tests/**). Re-verify "pre-existing" claims against MASTER (184a7f7), never git-stash.

## Cycle

1

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/unified-node-language-proposal`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr-worktrees/unified-node-language-proposal"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-unified-node-language-proposal-build-1.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/reports/shape-graphic-redesign-task-unified-node-language-proposal-build-1.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
