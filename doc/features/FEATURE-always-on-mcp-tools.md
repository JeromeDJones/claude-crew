# Feature: Always-On MCP Tools

## Problem

`extra_tools` in `spawn_teammate` is opt-in per spawn. A lead session that doesn't explicitly pass MCP tool IDs at spawn time produces teammates that lack those tools entirely. For read-only, ambient tools like `mcp__knowledge-graph__*` this is a silent capability gap — the teammate can't use the tool, no error surfaces, and the lead may not remember to grant it.

## Goal

Allow a persistent configuration declaring MCP tools that are automatically granted to every spawned teammate, without requiring the lead to pass `extra_tools` on every `spawn_teammate` call.

## Design

### Config source

A new optional key in `~/.claude.json` (the same file already loaded for MCP server definitions):

```json
{
  "mcpServers": { ... },
  "claudeCrew": {
    "alwaysOnTools": [
      "mcp__knowledge-graph__repo_map",
      "mcp__knowledge-graph__search_codebase_definitions",
      "mcp__knowledge-graph__get_definition"
    ]
  }
}
```

Alternatively, a dedicated `~/.claude-crew.json` to avoid coupling to Claude's config format. Decision deferred.

### Behavior

`default_factory` reads `alwaysOnTools` at startup (same time as the merged pack is built — once, frozen for the process lifetime). When spawning any teammate, the always-on tool list is merged into `extra_tools` before the existing pack-extension logic runs. Explicit `extra_tools` from the lead are additive on top — no conflict.

The same dedup and MCP server auto-wiring logic that applies to `extra_tools` today handles always-on tools automatically.

### Scope

- Read-only MCP tools are the primary target (`knowledge-graph`, Atlassian read ops).
- Write-capable tools can be listed but the operator accepts responsibility.
- `Task` remains explicitly disallowed regardless of config.
- Always-on tools appear in `list_available_tools` response under a new `always_on_tools` key so the lead can see what's ambient.

## Acceptance Criteria

- `always_on_tools` config key is read at `default_factory` startup.
- Every teammate spawned via `spawn_teammate` receives the always-on tools without the lead passing `extra_tools`.
- Explicit `extra_tools` from the lead are merged additively on top of always-on tools.
- MCP server auto-wiring applies to always-on MCP tool IDs.
- `list_available_tools` returns the current `always_on_tools` list.
- Tests: config absent → no change in behavior; config present → tools granted; lead extra_tools + always-on → both granted without duplication.
