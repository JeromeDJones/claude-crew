## Task

Implement task `sdk-teammate-multi-scope-injection` (index 3) of the breakout against
the spec at `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/specs/multi-scope-agent-memory.md`. Iterate until the spec's declared test command
passes.

Do not commit, push, or stage any files — the user owns merge and signoff.

## Prior Slice-Review Findings

Deduplicated findings from prior tasks' slice-review reports (informational —
address Mediums+ before writing new code; Infos are cross-slice observations
for awareness only):

- [INFO carry-forward, ensure-write-tool slice] AgentDefinition.tools is list[str] | None (NOT tuple). When you patch role_def via dataclasses.replace and assign self._agents = {**self._agents, role: patched_def}, use the list-based ensure_write_tool helper that already exists — do not re-tuple. Verify the patched def lands in self._agents and the original shared pack object is untouched.
- [INFO carry-forward] scope is typed as plain str in memory_dir/build_memory_section (runtime else-branch rejects unknowns). Pass role_memory straight through as the scope arg; no Literal cast needed.
- [SPEC pointer] The Data/API Contracts section (sdk_teammate.py block) sketches the exact replacement for the WARN branch at ~lines 500-506: resolve project_root = Path(self._cwd) if self._cwd else Path.cwd(); call build_memory_section(role, tools, scope=role_memory, project_root=...); on ValueError (unsafe role) log+skip; then ensure_write_tool + reassign self._agents. AT10 (project scope: system prompt has project guidance + path + Write in tools) and AT11 (local scope: local guidance + agent-memory.local path).

## Task Slice

Task name: `sdk-teammate-multi-scope-injection`
Task index: `3`
Description: Replace the WARN-only branch in sdk_teammate.py (currently lines ~500-506) with full injection for all three scopes. Resolve project_root from self._cwd with a Path.cwd() fallback. Call build_memory_section with the scope and root; on ValueError from an unsafe role, log and skip. Then call ensure_write_tool() and, if it returned a replaced def, assign self._agents = {**self._agents, role: patched_def} so the shared pack stays untouched.

Acceptance tests this task owns (1-based indices into spec's `## Acceptance
Tests`): 10, 11

The breakout artifact at `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/specs/multi-scope-agent-memory.md` has the full DAG. Read your
task's entry to see the precise scope. The acceptance tests above are *your*
responsibility; other tasks own the rest. The spec's full test command runs
the entire suite — your task is done when the tests in your slice pass and
no other slice's tests regress.

## Read Before Editing

claude_crew/sdk_teammate.py (the ~500-506 WARN branch to replace), claude_crew/teammate_memory.py (build_memory_section + ensure_write_tool signatures)

## Slice Test Command

The per-task test command for this slice. Run this as your **only PASS gate**.
When empty, fall back to the spec's suite-level `## Test Command` and write a
one-line note in the build report:
`note: testCommand absent for task <name> — fell back to suite-level command`

```
uv run pytest tests/test_sdk_teammate.py -k "memory" -x
```

## Artifacts

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/specs/multi-scope-agent-memory.md`
Acceptance tests: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/specs/multi-scope-agent-memory.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/specs/multi-scope-agent-memory.md`
Build report (write here): `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/reports/multi-scope-agent-memory-task-sdk-teammate-multi-scope-injection-build-0.md`

Prior build report (empty on cycle 0): 

Failing tests from prior cycle (empty on cycle 0 — run the full suite):


## Cycle

0

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection`

Run `cd "/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection"` before any file operation. Treat this path as binding.

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
6. Write the build report to `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/reports/multi-scope-agent-memory-task-sdk-teammate-multi-scope-injection-build-0.md` using the build-report
   template (in the plugin install at `doc/templates/build-report-template.md`).
   Include `git diff --name-status HEAD` output as the files-changed list.
7. Emit this as the **final line** of your response — no trailing text after it:
   `RR-VERDICT: PASS|FAIL|BLOCKED <slug> <cycle> /home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr-worktrees/sdk-teammate-multi-scope-injection/.rr/reports/multi-scope-agent-memory-task-sdk-teammate-multi-scope-injection-build-0.md`
   On BLOCKED, append a one-line reason after the path.

**Hard constraints:**
- NO `git commit`, NO `git push`, NO `git stage` — forbidden without exception.
- Per-run wallclock cap: 600 seconds. On timeout return `BLOCKED` with reason `test-command-timeout`.
- Do not narrate files changed inline — the build report captures that.
- Do not implement other tasks' slices. Stay scoped.
