# claude-crew

Local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

See [`doc/PRODUCT-VISION.md`](doc/PRODUCT-VISION.md) for the full vision.

## Status

Pre-alpha. MVP in progress — see `doc/features/`.

## Known Limitations

**MCP servers must be in user-level config to reach teammates.** SDK teammates load `~/.claude.json` (user-level global MCP config) but not project-level MCP servers. Any MCP server you want available inside a teammate session must be registered in `~/.claude.json` — project-level MCP servers are only reachable by the lead session. HTTP MCPs registered in user config load reliably on cold start. See `doc/research/mcp-cold-start-behavior.md`.

**Global shell hook env vars not injected in SDK mode.** Shell-command hooks configured in `~/.claude/settings.json` (`PreToolUse`, `PostToolUse`, etc.) _do_ fire when SDK teammates run, and `matcher`-based filtering (e.g. `matcher: "Bash"`) works correctly. However, the Claude CLI does not inject the hook-specific environment variables (`CLAUDE_TOOL_NAME`, `CLAUDE_HOOK_EVENT`, etc.) that it provides in interactive mode. Hook scripts that filter by tool name via env vars (e.g. `if [ "$CLAUDE_TOOL_NAME" = "Bash" ]`) will not work — the variable will always be empty. Use `matcher` in the hook config instead of env-var checks. See `doc/research/hooks-sdk-behavior.md`.

## Development

```bash
uv sync
uv run pytest
```
