# Feature: Lead Extends Teammate Tools and Skills at Spawn Time

**Status**: 📋 Queued for next session (handoff to `/repo-react`)
**Created**: 2026-05-03
**Authoring context**: Drafted at the close of the `ui-agent-transparency` session — that feature surfaced agent configs in the dashboard, which made today's gap visible: operators can now *see* what a role can do, but the lead has no way to say "this role needs more for this project." This feature closes that loop.

---

## Phase 1: Research & Requirements

### Problem Statement

`mcp__claude-crew__spawn_teammate` accepts `model`, `effort`, `cwd`, `permission_mode` as overrides — but tools and skills are pack-frontmatter-only. The lead cannot, mid-session, say "scout would benefit from `mcp__knowledge-graph__repo_map` for this project" or "rr-planner needs `serena` since we just switched off gkg." The pack author's choices are absolute. To grant a tool, the operator must edit the pack file in `~/.claude/plugins/cache/<...>/agents/<role>.md` — overwritten on next plugin update — and restart the MCP.

This collides with two realities:

1. **Per-project tool environments vary.** A repo with `gkg` indexed wants planners using KG tools. A repo with `serena` configured wants serena. A repo with neither wants Grep/Glob fallback. The pack can't predict.
2. **The lead has more context than the pack author.** The lead knows what MCP servers are configured for the current project, what the current task is, and what the user just said. It can make smarter per-spawn decisions than a static pack frontmatter ever can.

The cost of the workaround (pack-edit + restart) is high enough that operators won't do it for one-off cases — meaning teammates routinely run with strictly less capability than the operator would want, and the operator doesn't notice because the dashboard didn't surface the gap before today.

The companion problem: for the lead to make smart additive choices, it needs **discoverability** — visibility into what tools/skills/MCP servers actually exist in the user's environment. Today the lead has no surface for this; it would have to read `~/.claude.json` directly, which (a) duplicates path/precedence logic already centralized in `_user_loader.py`, (b) misses plugin-cache packs and project-level agents, and (c) puts secret-bearing MCP env blocks in the lead's context window.

### Architectural Posture (load-bearing constraints)

These are not negotiable; the spec must respect them:

1. **Additive only, never replace.** Pack frontmatter defines the role's capability *contract*. The lead can extend; it cannot strip. This preserves "capability-as-contract" — operators reading the dashboard can trust that whatever the pack granted is at minimum present, and any extras are explicit lead-side additions (visually distinguishable in the chips).
2. **No system-prompt override.** System prompt is the role's identity. Lead-side rewrites would let the lead silently make a "scout" act like a "builder." Append-only system-prompt extension is a separate future conversation, not part of this feature.
3. **Snapshot reflects effective surface.** The `_snapshot_config` path that today populates the dashboard `config` block must reflect the *post-merge* (pack ∪ extras) tool/skill list, not the pack baseline. Otherwise the transparency we just shipped silently lies. This is the part easiest to overlook.
4. **No-recursion still holds.** Lead can grant `Read`, `Grep`, MCP tools, etc. Lead may NOT grant `Task` to a teammate — that would let the subagent recursively spawn, breaking the leaf-node invariant claude-crew is built on. The spawn-time merge logic must reject `Task` (and any future recursive-spawn tool) explicitly with a clear error.
5. **Discoverability single-source.** The lead-side discovery surface lives in claude-crew, not the lead's logic. claude-crew already centralizes pack/skill/MCP resolution in `_user_loader.py`; promoting it to a returnable surface is a refactor, not new architecture. Reading `~/.claude.json` raw from the lead is rejected — it surfaces secret-bearing env blocks into the lead's context.

### Success Criteria

- [ ] **SC-1**: `mcp__claude-crew__spawn_teammate` accepts two new optional kwargs: `extra_tools: list[str] | None = None` and `extra_skills: list[str] | None = None`. Both default to empty additive sets when omitted.
- [ ] **SC-2**: Spawn-time merge rule is *additive and deduped*: `effective_tools = list(dict.fromkeys(pack_tools + extra_tools))` (insertion-order preserving, no dupes). Same for skills.
- [ ] **SC-3**: Recursive-spawn tools (`Task` and any future equivalent the SDK exposes) are rejected at the boundary. If `extra_tools` contains `Task`, `spawn_teammate` raises `ToolError` with a clear message ("Task tool cannot be granted at spawn time — claude-crew teammates are leaf nodes by design"). Test: `extra_tools=["Task"]` raises before any subprocess starts.
- [ ] **SC-4**: `Broker._snapshot_config` reflects the *effective* (post-merge) tool/skill list. Test: spawn with `extra_tools=["mcp__knowledge-graph__repo_map"]` against a pack that lists `[Read, Grep]`; assert `config.tools == ["Read", "Grep", "mcp__knowledge-graph__repo_map"]` via `get_teammate_status`.
- [ ] **SC-5**: New MCP tool `mcp__claude-crew__list_available_tools` returns a structured discovery payload:
  ```python
  {
      "builtins": list[str],          # SDK built-ins (Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch, ... — hardcoded, NO Task)
      "mcp_servers": list[{"name": str, "running": bool | None}],
                                       # from ~/.claude.json keys; running flag null when not cheaply probable
      "skills": list[str],             # union of user (~/.claude/skills/) + project (.claude/skills/) skill names
      "plugins": list[{"key": str, "agents": list[str], "skills": list[str]}],
                                       # from plugin cache + installed_plugins.json scope filter
      "project_root": str,             # the cwd the MCP server was launched against (frozen at startup)
  }
  ```
  Returns server *names* only — never serializes MCP server `command`/`args`/`env` blocks (would surface API keys).
- [ ] **SC-6**: `list_available_tools` reuses `_user_loader._load_user_mcp_server_names`, `_user_loader._discover_skill_names`, `_user_loader._read_installed_plugins`. No duplicated discovery logic.
- [ ] **SC-7**: Tool ID convention is documented in `list_available_tools`'s docstring: MCP server tool IDs follow `mcp__<server>__<tool>` and the server must be running for the SDK to grant them. The discovery payload returns server names — **NOT individual tool IDs**, because MCP servers publish their tool lists dynamically when a client connects (claude-crew would have to spawn every server at startup and query it; rejected as too costly). The lead is the source of truth for tool IDs: it already sees the full `mcp__<server>__<tool>` names in its own tool surface (system-reminder enumeration). The discovery payload tells the lead "this server is grantable"; the lead matches against tools it already knows it has access to and constructs the full ID. This division of labor is intentional and load-bearing for the design — document it in both the docstring and the feature's user-facing notes.
- [ ] **SC-8**: Dashboard chips visually distinguish lead-extended tools/skills from pack-granted ones. Probably a small "+N" badge or a colour/border variant. Operators looking at a row can tell at a glance "the pack gave 4 tools and the lead added 3 more for this spawn."
- [ ] **SC-9**: Existing teammates (spawned before this feature) continue to work — `extra_tools=None` produces identical behavior to today's spawn path. Backward-compatible additive change.
- [ ] **SC-10**: Live SDK test (gated by `CLAUDE_CREW_LIVE_TESTS=1`): spawn an `rr-planner` teammate with `extra_tools=["mcp__knowledge-graph__repo_map"]`, send it a message that requires the tool, assert the response demonstrates the tool was used. Proves the merge actually reaches the SDK subprocess, not just the snapshot.

### Out of Scope

- **System-prompt override or append.** Future work; identity-affecting changes need their own discussion.
- **Removing pack-granted tools.** Replace-style overrides explicitly rejected per Architectural Posture.
- **Per-message tool grants.** This feature is spawn-time only. Mid-conversation tool extension would require SDK-level support that doesn't exist.
- **Probing whether MCP servers are *actually* running.** Initial implementation can mark `running: null` rather than spawning probes. If demand emerges, follow up.
- **Validating tool names against the SDK's accepted list.** SDK rejects unknowns at spawn time; that's already covered by existing teammate-death telemetry. Don't pre-validate (the SDK is the source of truth and validation drift is worse than spawn failure).
- **A UI for the operator to add tools from the dashboard.** Read-only transparency only — same posture as `ui-agent-transparency`.
- **Merging extras into the pack file on disk.** Extras are per-spawn, ephemeral. Restart the MCP and they're gone. If the operator wants permanence, they edit the pack themselves.

### Open Questions

- **OQ-1: Discovery payload shape — flat or grouped?** SC-5 proposes grouped (builtins, mcp_servers, skills, plugins). Alternative: a single flat list of fully-qualified tool/skill names with a `source` tag per entry. Grouped is more typed; flat is easier to search-and-filter from the lead. Planner should pick.
- **OQ-2: Should `extra_tools` accept skill-style references** (`skill://plan-feature`) or only tool names? If yes, the merge logic forks. Keeping symmetric (`extra_tools` for tools only, `extra_skills` for skills) is cleaner; planner should confirm.
- **OQ-3: Telemetry for lead-extended spawns.** Should the transcript JSONL record extras separately from the pack baseline? (Useful for "show me every spawn where the lead granted KG tools" forensic queries.) Probably yes — minor addition to the spawn lifecycle record.
- **OQ-4: Plugin-cache packs (rr-* roles).** Confirm `_user_loader` already discovers them via `load_plugin_agents` + `installed_plugins.json` — if not, that's a prerequisite fix.

### Validation

Manual end-to-end procedure:

1. Restart claude-crew MCP after the feature ships.
2. From a Claude Code lead session: `mcp__claude-crew__list_available_tools()` — verify the returned structure has builtins, gkg server name (since user has it configured), skills from `~/.claude/skills/`, and the plugin agents (rr-* roles).
3. Spawn `rr-planner` with `extra_tools=["mcp__knowledge-graph__repo_map", "mcp__knowledge-graph__search_codebase_definitions"]`.
4. Hard-refresh `http://127.0.0.1:7821`. The rr-planner row should show **6 tools** (not 4) — the pack's 4 plus the 2 extras. Click → detail panel lists all 6, with the 2 extras visually distinguished from the 4 baseline tools.
5. Send the planner a message that requires `repo_map`. Verify the planner uses the tool successfully (not "tool not available").
6. Try `extra_tools=["Task"]` — expect immediate `ToolError`, no spawn.

### References

- **Companion shipped feature**: `FEATURE-ui-agent-transparency.md` (today's session — surfaces what a role *currently* has; this feature lets the lead change it).
- **Discovery code to reuse**: `claude_crew/subagents/_user_loader.py` — `_load_user_mcp_server_names`, `_discover_skill_names`, `_read_installed_plugins`, `load_plugin_agents`, `load_user_agents`, `load_project_agents`.
- **Spawn path**: `claude_crew/server.py:88-134` (the existing `spawn_teammate` MCP tool), `claude_crew/broker.py:140-180` (`Broker.spawn_teammate`), `claude_crew/factories.py:78-96` (`default_factory` closure where merged_pack lives).
- **Snapshot path**: `claude_crew/broker.py:_snapshot_config` (must reflect post-merge surface, not pack baseline).
- **SDK invariants**: `CLAUDE.md` § "SDK behavior — verified invariants" — `tools=[]` is enforced as no-tools; `model=None` is wire-safe. The merge logic must respect these.
- **Second-opinion analysis** (in-session general-purpose agent, 2026-05-03): rationale for Approach A over reading `~/.claude.json` directly — secret-bearing env blocks, centralized discovery in `_user_loader.py`, plugin-scope and cwd-binding semantics that would be re-implemented incorrectly by a Bash-based lead.
