## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

Outcome: Make claude-crew's agent-memory feature unopinionated about WHERE memory
lives. Today only `memory: user` works (writes to ~/.claude/agent-memory/<role>/);
`memory: project` and `memory: local` already parse in the frontmatter enum but
only emit a WARN and do nothing (sdk_teammate.py:501-506). Finish the deferred v1
cut: make all three scopes functional.

Three required behaviors:

1. Multi-scope path resolution — teammate_memory.py:memory_dir() must branch on
   scope:
     - user    -> ~/.claude/agent-memory/<role>/            (unchanged)
     - project -> <root>/.claude/agent-memory/<role>/       (committed, team-shared)
     - local   -> <root>/.claude/agent-memory.local/<role>/ (gitignored, machine-local)
   Project root resolves from the teammate's spawn-time self._cwd.

2. Per-scope write guidance — the memory-writing instruction text (module-level
   constants in teammate_memory.py, currently user-scope-specific: "keep learnings
   general since they apply across all projects") must vary by scope. Project scope:
   emphasize project-specific, committed/shared, no secrets or machine-specific
   detail. Local scope: machine-local, not shared, fine for experimental/
   checkout-specific notes. build_memory_section() takes a `scope` param.

3. Auto-attach Write tool — when `memory:` is set on an agent and `Write` is not
   already in its `tools`, inject `Write` (via dataclasses.replace, never mutating
   the shared pack) so the agent can actually persist. Respect the tools=()
   safe-by-default invariant — this injection is justified because memory is an
   explicit opt-in.

Constraints:
- Don't break the `user` scope path or existing tests.
- The lead write-guard (is_lead_project_memory_path, guards
  ~/.claude/projects/*/memory/) must not erroneously block the new project/local
  paths — verify no collision.
- Tests at two layers per validate-before-change: unit (path resolution per scope,
  guidance text per scope, auto-Write injection) + integration (through
  sdk_teammate.__init__ / build_teammate_prompt that the right scope guidance +
  Write land in the spawned agent's config).
- For `local` scope: document the recommended .gitignore entry in guidance text;
  do NOT auto-edit .gitignore.

Read for context: claude_crew/teammate_memory.py, claude_crew/_loader.py
(frontmatter parsing + build_memory_section), claude_crew/sdk_teammate.py:~500-531
(injection point), claude_crew/factories.py:267-308 (tool-merge seam),
doc/features/FEATURE-teammate-memory-persistence.md (the spec that deferred this),
tests/test_teammate_memory.py + memory tests in tests/test_sdk_teammate.py.

Out of scope: Changing the `user` scope convention; auto-gitignore editing; the MCP
tool surface; broker semantics; the write-guard's own path logic (only verify
non-collision).

## Cycle

Cycle: 0
Prior review report (empty on cycle 0): 

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.5.3/doc/templates/spec-template.md`

Existing specs in this worktree:
1 specs

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
