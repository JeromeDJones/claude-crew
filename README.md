# claude-crew

Local multi-agent orchestrator for Claude Code. A lead session drives a persistent crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface.

**Why it exists:** Claude Code is limited to one concurrent agent conversation. claude-crew breaks that ceiling — the lead can spawn multiple role-specialized teammates, each holding full context across many exchanges, with teammates recursively spawning their own subagents for focused work.

## Quick Start

### 1. Install

```bash
git clone <repo>
cd claude-crew
uv sync
```

Requires Python 3.12+ and `uv`.

### 2. Authenticate

claude-crew uses the same credentials as Claude Code. It checks in order:

1. `ANTHROPIC_API_KEY` environment variable
2. `CLAUDE_CODE_OAUTH_TOKEN` environment variable
3. `~/.claude/.credentials.json` (set automatically when you log in to Claude Code)

If you're already logged in to Claude Code, step 3 works automatically — nothing extra needed.

### 3. Register with Claude Code

```bash
claude mcp add claude-crew -- uv --directory /path/to/claude-crew run claude-crew
```

This registers the MCP server globally. Claude Code will start it automatically when you open a session.

### 4. Verify

In any Claude Code session, ask: *"List my crew."* You should get back an empty crew list with no errors. If you get a tool-not-found error, check `claude mcp list` and confirm `claude-crew` appears.

---

## Basic Usage

All interaction happens through MCP tools the lead calls in a Claude Code session.

### Spawn a teammate

```
spawn_teammate(role="planner")
spawn_teammate(role="explorer", name="alice")
spawn_teammate(role="general-purpose", model="claude-sonnet-4-6")
```

Returns a `teammate_id` (e.g. `tm_abc123`). Hold onto it.

### Send a message

```
send_to(teammate_id="tm_abc123", payload="Review this spec and identify gaps: ...")
```

The teammate works asynchronously. Messages are queued in the broker.

### Poll for replies

```
get_messages(since_seq=0)
```

Returns all messages to the lead since seq 0. Use `since_seq` from the last result to poll incrementally. Or use long-poll to block until a reply arrives:

```
get_messages(since_seq=42, wait_seconds=30)
```

### Check teammate status

```
get_teammate_status(teammate_id="tm_abc123")
```

Returns whether the teammate is idle, which tool it's currently running, and how long it's been active. Useful for knowing when to poll.

### Kill a teammate

```
kill_teammate(teammate_id="tm_abc123")
```

Terminates the subprocess. Subsequent sends return a `teammate_dead` error.

### Watch the transcript

```
get_transcript_path()
```

Returns the JSONL file path. Run `tail -f <path>` in a terminal for live observability of all bus traffic, tool calls, and subagent spawns.

---

## Built-in Roles

Three roles ship with claude-crew. Custom roles can be added (see below).

| Role | Model | Purpose | Tools |
|------|-------|---------|-------|
| `explorer` | Haiku | Read-only codebase investigation | Read, Grep, Glob |
| `planner` | Sonnet | Spec writing, design documents | Read, Grep, Glob, Write |
| `general-purpose` | Sonnet | Full-featured utility work | Read, Grep, Glob, Edit, Write, WebFetch, WebSearch |

None have `Bash` (shell breakout risk) or `Task` (subagents are leaves in v1).

---

## Custom Roles

Drop a `.md` file with YAML frontmatter into `~/.claude/agents/` (user-level) or `.claude/agents/` (project-level). Project-level takes precedence over user-level, which takes precedence over built-ins.

```markdown
---
name: reviewer
description: Reviews code changes for correctness and style
model: claude-sonnet-4-6
tools:
  - Read
  - Grep
  - Glob
---

You are a code reviewer. When given a diff or a set of files, you identify bugs,
style violations, and architectural concerns. Be direct and specific.
```

Roles are loaded once at MCP startup and frozen for the session.

---

## Development

```bash
uv sync
uv run pytest                          # all tests (stub mode, no API calls)
uv run pytest tests/test_broker.py    # single file
uv run pytest -k "test_spawn"         # pattern match

# Live SDK tests (requires ANTHROPIC_API_KEY)
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py
```

Tests run in stub mode by default — teammates echo back messages without making any API calls. Fast and free.

---

## Known Limitations

**MCP servers must be in user-level config to reach teammates.** SDK teammates load `~/.claude.json` (user-level global MCP config) but not project-level MCP configs. Any MCP server you want available inside a teammate session must be registered at the user level via `claude mcp add --scope user`. See `doc/research/mcp-cold-start-behavior.md`.

**Global shell hook env vars not injected in SDK mode.** Shell hooks (`PreToolUse`, `PostToolUse`) do fire when SDK teammates run, and `matcher`-based filtering works correctly. However, hook-specific env vars (`CLAUDE_TOOL_NAME`, `CLAUDE_HOOK_EVENT`, etc.) are always empty inside SDK mode. Use `matcher` in your hook config instead of env-var checks. See `doc/research/hooks-sdk-behavior.md`.

**Local machine only.** No network transport, no remote crews. All teammates run as subprocesses on the same machine as the lead.

**Two-level recursion ceiling in v1.** Lead → teammate → subagent. Deeper trees are architecturally possible but deferred.

---

## Architecture

```
Claude Code (lead session)
    │  MCP tools
    ▼
claude-crew server (FastMCP over stdio)
    │
    ▼
Broker — message bus, teammate registry, dedup, JSONL transcript
    │
    ├── Teammate (SDK) — subprocess via claude-agent-sdk, per-turn loop
    │       └── Subagents — Task tool, spawned by teammate as needed
    ├── Teammate (SDK)
    └── ...
```

See `doc/PRODUCT-VISION.md` for the full vision and feature pipeline.
