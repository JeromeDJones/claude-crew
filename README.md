# claude-crew

Local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

See [`doc/PRODUCT-VISION.md`](doc/PRODUCT-VISION.md) for the full vision.

## Status

Pre-alpha. MVP in progress — see `doc/features/`.

## Development

```bash
uv sync
uv run pytest
```
