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

### 5. Open the dashboard

When claude-crew starts it binds an HTTP server and logs the URL to stderr:

```
[claude-crew] ui -> http://127.0.0.1:7821
```

Port selection is automatic — it tries 7821 first and increments until a free port is found, so multiple claude-crew instances can run concurrently without colliding. Open the logged URL in a browser to see the Mission Control dashboard.

To pin a specific port, set `CLAUDE_CREW_UI_PORT`:

```bash
CLAUDE_CREW_UI_PORT=8080 claude
```

To disable the dashboard entirely (e.g. in sandboxed or CI environments):

```bash
CLAUDE_CREW_UI_PORT=0 claude
```

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

### Mission Control dashboard

Open **http://127.0.0.1:7821** while claude-crew is running. The dashboard shows:

- All alive teammates with role, status (idle / thinking / tool-use), and uptime
- Mini topology graph with animated pulses for active agents
- Live message stream (last 200 envelopes, auto-scrolling)
- Startup Notices panel — pack-load WARNs/INFOs (shadow events, unknown skills, frontmatter typos, plugin issues) captured during MCP-server startup; hidden when empty, INFO rows behind a "Show INFO" toggle

Status and message data refresh every 1.5 seconds via WebSocket. A connection dot in the top bar turns amber if the WebSocket drops and reconnects automatically after 3 seconds.

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

### Skills

Roles can declare access to user/project skills (`~/.claude/skills/<name>/SKILL.md` and `<project>/.claude/skills/<name>/SKILL.md`) — the same skills the lead Claude Code session invokes via `/<skill-name>`. The bundled `general-purpose` role declares `skills: all` by default, so spawned `general-purpose` teammates have parity-of-invocation with the lead. Other bundled roles (`explorer`, `planner`) declare no skills to keep their context light.

To declare skills on a custom role:

```markdown
---
name: reviewer
description: Reviews code changes
model: claude-sonnet-4-6
tools: [Read, Grep, Glob]
skills: [security-review, docs-maintain]   # explicit allowlist
# OR
# skills: all                                # all discoverable skills
---

You are a code reviewer...
```

**`settingSources` interaction.** The Claude Agent SDK auto-injects `setting_sources=["user","project"]` for skill discovery when `settingSources` is omitted or set to `["user","project"]`. Setting `settingSources: []` (explicit empty list) blocks discovery — the loader rejects this configuration with `PackLoadError: ... settingSources=[] (explicit empty list) is contradictory ...`. Either omit `settingSources` (recommended) or set it explicitly to `["user","project"]`.

**Discovery and warnings.** At MCP-server startup the loader walks `~/.claude/skills/` and `<cwd>/.claude/skills/` to enumerate discoverable skills. If a role declares `skills: [foo]` and `foo` is not on disk, a WARN logs to stderr in this exact format:

```
WARNING claude_crew.subagents.loader: agent 'reviewer' declares unknown skills ['foo'] — not found in user or project skill dirs at startup; teammate will fail to invoke them at runtime
```

Grep stderr for `declares unknown skills` to find these, or open the dashboard — Mission Control's Startup Notices panel surfaces the same WARN under category `unknown_skill`. The loader does NOT raise — operators may add the SKILL.md after server startup, or skills may live in a search path the loader doesn't traverse. The teammate will surface the missing-skill error if/when it tries to invoke.

**Project-skill cwd trap.** Project skills resolve from the cwd at MCP-server startup. Launch claude-crew from your project root for `<project>/.claude/skills/` to be discovered.

**Dashboard surfacing.** Startup-time WARNs are not yet visible in the Mission Control dashboard — pack-load happens before any teammate exists. Tracked as vision row #25 (`doc/PRODUCT-VISION.md`).

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

Mission Control (HTTP/WS on :7821)
    ├── GET /             → dashboard.html
    ├── GET /api/state    → JSON snapshot of broker state
    └── WS  /ws           → push state every 1.5s
```

Both the MCP stdio server and the HTTP server run in the same asyncio event loop — no threads, no shared-state issues. They share the same `Broker` instance.

See `doc/PRODUCT-VISION.md` for the full vision and feature pipeline.
