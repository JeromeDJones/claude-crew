## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/specs/agent-pack-refresh.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

Feature: add an MCP tool to claude-crew that refreshes agent definitions on demand.

PROBLEM / MOTIVATION
--------------------
claude-crew freezes its merged agent pack once at MCP-server startup. In
`default_factory()` (claude_crew/factories.py), `build_merged_pack()` is called
exactly once and its result is captured as closure-locals (`merged_pack`,
`merged_bodies`, `role_ss`) inside the returned `factory` closure;
`agent_def_resolver` closes over the same `merged_pack`. Consequence: if an
operator edits or adds a `.claude/agents/*.md` file in the repo where the
claude-crew server is running, the change is invisible to new teammate spawns
until the whole server is restarted. We want an on-demand refresh so the running
server re-reads agent definitions without a restart.

Scope confirmed with Jerome: "just using the agents in the current repo is fine"
— we are NOT solving per-cwd / per-target-repo drop-in. One long-lived server,
launched in the target repo, edit an agent file, call a tool, picked up.

LOCKED DESIGN DECISIONS (agreed before planning)
------------------------------------------------
1. Restructure the frozen closure-locals into a small MUTABLE HOLDER (e.g. a
   `_PackState` with `.pack`, `.role_ss`, `.bodies`). Both `factory()` and
   `agent_def_resolver()` read the holder LIVE on each call instead of capturing
   immutable locals. A `refresh()` recomputes `build_merged_pack(home, project)`
   and ATOMICALLY swaps the holder's fields.
2. Refresh recomputes against the SAME home_dir / project_root captured at
   startup — NOT re-resolved from current cwd. Per-cwd re-resolution is the
   deliberately-rejected footgun (see load_project_agents docstring). Capture the
   roots explicitly so refresh is deterministic rather than relying on Path.cwd()
   drift.
3. New MCP tool `refresh_agents` exposed on the server alongside the existing 8
   tools. Calls refresh, returns a structured result.
4. SEMANTICS = FUTURE SPAWNS ONLY. Already-running teammates keep the
   AgentDefinition snapshot they spawned on; you cannot retro-mutate a live SDK
   subprocess's agent set. The tool description AND the return value must state
   this explicitly.
5. FULL RE-MERGE of all four layers (default + plugin + user + project), not just
   project — editing a user-level agent should refresh too.
6. RETURN VALUE: per-source counts + a diff vs the prior pack
   (added / removed / changed role keys) + any load warnings surfaced this pass
   (the bad-file / oversize / shadow-drop WARNs that build_merged_pack already
   emits via the claude_crew.subagents.loader logger).

CONSTRAINTS / NON-NEGOTIABLES
-----------------------------
- Two-layer validation (engineering standard):
  * Implementation layer: holder swap + refresh logic. Happy = a newly-added
    agent file appears in the pack after refresh. Sad = a newly-BROKEN agent file
    warns and is skipped, and the PRIOR pack stays intact (refresh must not blow
    away a working pack because one file failed to parse).
  * Integration layer: through `make_server`. Happy = the `refresh_agents` tool
    returns the diff. Sad = refresh with a malformed agent file reports the
    warning in its result without crashing the server.
  Each layer covers happy AND sad paths.
- Validation GATE runs the FULL suite (`uv run pytest`), NOT a keyword-filtered
  subset. The factory is widely consumed; a scoped run can pass while a
  cross-cutting regression merges undetected (known repo lesson).
- The existing frozen-at-startup behavior for the INITIAL load must not change;
  refresh is purely additive.
- Atomic swap: a refresh that fails partway (e.g. build_merged_pack raises) must
  leave the live pack unchanged. No torn reads.

OPEN SCOPE CALL FOR THE PLANNER
-------------------------------
`startup_diagnostics` is captured once in default_factory and threaded into the
Broker at construction (Broker(startup_diagnostics=...)), surfaced on the
dashboard's Startup Notices panel. v1 intent: surface refreshed warnings in the
TOOL RESPONSE. Whether to ALSO update broker.startup_diagnostics / the dashboard
panel on refresh is a deliberate in/out-of-scope decision — lean toward
deferring it to keep blast radius small, but the planner should make the call
explicitly and justify it, not leave it ambiguous.

POINTERS TO READ
----------------
- claude_crew/factories.py — `default_factory()`: the closure, `factory()`,
  `agent_def_resolver` / `_resolve_agent_def`, and the `startup_diagnostics`
  attach. THIS is the primary refactor surface.
- claude_crew/subagents/_user_loader.py — `build_merged_pack(home_dir,
  project_root)`: the recompute entry point, already parameterized on the roots.
- claude_crew/server.py — `make_server()`: factory build (~line 88), the
  `_project_root` / `_home_dir` capture (~lines 76-79), and the existing
  `@mcp.tool` registrations to mirror for tool signature + return conventions.
- claude_crew/broker.py — how `startup_diagnostics` flows in (only if the planner
  opts to refresh dashboard diagnostics).
- tests/ — existing make_server tests, loader tests, and factory tests.
  conftest.py forces stub mode by default; refresh tests will need sdk-mode
  factory construction or direct build_merged_pack injection (the suite already
  monkeypatches Path.home/Path.cwd and build_merged_pack — follow those patterns).

OUT OF SCOPE (do not spend cycles here)
---------------------------------------
- Per-cwd / per-teammate pack resolution (the upstream "true drop-in"
  capability). Explicitly NOT this feature.
- Hot-reloading the agent set of teammates that are already running.
- Any change to the four-layer precedence or merge semantics.
- File-watching / automatic refresh. This is on-demand via the tool only.

## Cycle

Cycle: 1
Prior review report (empty on cycle 0): /home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-review-0.md

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.7.4/doc/templates/spec-template.md`

Existing specs in this worktree:
1 spec: agent-pack-refresh (this slice — revise it in place)

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
