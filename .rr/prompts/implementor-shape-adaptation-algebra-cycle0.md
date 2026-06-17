## Task

Implement task `shape-adaptation-algebra` (index 0) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

_None._

## Task Slice

Task name: `shape-adaptation-algebra`
Task index: `0`
Description: Add the pure-data adaptation algebra to claude_crew/shapes.py: the abstract
ShapeAdaptation base; the five frozen verb classes (AddNode, Swap, Augment,
SetGate, Drop) each with a pure apply(shape) -> tuple[Shape, AdaptationDiff]
that validates its enumerated sad paths and never mutates/partially-returns
(Swap replaces supplied optional model/extra_tools/extra_skills and retains
omitted ones); the structured frozen AdaptationDiff with render() (incl. the
per-optional "; {field} {old} -> {new}" clause); the AdaptationChain +
AdaptationStep provenance carriers; and the shape_to_dict(shape) serializer
(inverse of parse_shape, preserving phases/cwd/model/extra_tools/extra_skills/
reverse_mode) used by the round-trip invariant. Shape/ShapeNode/ShapeEdge stay
unchanged. Authors the new tests/test_shape_adaptation.py with the per-verb
happy-paths, the optional-field swap golden (AT 35), all structural sad-paths,
the rich-field round-trip invariant (AT 6), the render() golden tests, and the
chain-provenance test.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,34,35

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md` has the full DAG. Read your
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
uv run pytest tests/test_shape_adaptation.py
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-shape-adaptation-algebra-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr-worktrees/shape-adaptation-algebra`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr-worktrees/shape-adaptation-algebra"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-shape-adaptation-algebra-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-task-shape-adaptation-algebra-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
