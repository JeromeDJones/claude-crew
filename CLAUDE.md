# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync          # install dependencies
uv run pytest    # run the full test suite (stub mode, no SDK calls)
uv run pytest tests/test_broker.py          # run a single test file
uv run pytest -k "test_spawn"               # run tests matching a pattern
```

Live SDK tests are gated and skipped by default:

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py
```

Run the MCP server directly:

```bash
uv run claude-crew   # starts the FastMCP server (requires auth)
```

## Architecture

claude-crew is a local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

### Core components

**`server.py`** — FastMCP server. Exposes 8 tools to the lead: `spawn_teammate`, `send_to`, `broadcast`, `get_messages` (long-poll via `wait_seconds`), `list_crew`, `kill_teammate`, `get_teammate_status`, `get_transcript_path`. This is the only surface the lead touches.

**`broker.py`** — Single source of truth for team state. Owns the teammate registry, append-only message log, per-inbox queues, monotonic sequence counter, and dedup set. Tombstones dead teammates (marks dead, preserves in registry for status queries). Writes lifecycle and envelope records to the transcript sink.

**`teammate.py`** — Abstract base class. Defines the inbox-consumption loop, activity tracking (`_begin_turn` / `_end_turn` / `_stamp_activity`), and tool tracking (`_tool_uses` in-flight dict, `_last_tool_completed`). `StubTeammate` is the echo implementation used in tests.

**`sdk_teammate.py`** — Production teammate backed by `claude-agent-sdk`. Per-turn loop: pull envelope → translate to prompt → query SDK → drain response → send result envelope. Attaches PreToolUse/PostToolUse hooks for tool tracking (F8) and PreSubagentUse/PostSubagentUse hooks for subagent activity tracking (F7). Includes liveness polling (background task detects SDK death) and a per-turn backstop timeout.

**`envelope.py`** — Wire format. Fields: `id` (caller-provided UUID for retry safety), `seq` (broker-stamped monotonic), `sender`, `recipient`, `timestamp`, `payload`.

**`factories.py`** — Selects teammate implementation. `CLAUDE_CREW_TEAMMATE_MODE=stub` → `StubTeammate` (default in tests). `sdk` (default in production) → `SdkTeammate`. SDK mode merges the default subagent pack with `~/.claude/agents/` and project `.claude/agents/`.

**`transcript.py`** — Best-effort JSONL sink. Path resolves via `CLAUDE_CREW_TRANSCRIPT_DIR` → `$XDG_STATE_HOME/claude-crew/transcripts/` → `~/.local/state/claude-crew/transcripts/`. Disabled in tests via `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.

**`redaction.py`** — Tool telemetry redaction (v1 allowlist: Bash, Task, WebFetch). Extracts and redacts secrets from tool args before storage; caps at 256 bytes.

**`subagents/`** — Default subagent pack. Three agents (`explorer`, `planner`, `general-purpose`) defined as markdown files with YAML frontmatter (model, tools, effort, maxTurns). No Bash or Task tool — leaf nodes that cannot recurse further.

### Test conventions

- `conftest.py` auto-sets `CLAUDE_CREW_TEAMMATE_MODE=stub` and `CLAUDE_CREW_TRANSCRIPT_DISABLED=1` for every test.
- Tests that need SDK mode clear the env var explicitly or pass `factory=` to `make_server()`.
- Tests that exercise the transcript sink set `CLAUDE_CREW_TRANSCRIPT_DIR` to a `tmp_path` and unset `CLAUDE_CREW_TRANSCRIPT_DISABLED`.
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.

### Known limitations

**MCP servers must be in user-level config.** SDK teammates load `~/.claude.json` but not project-level MCP config. Register any required MCP server in `~/.claude.json`.

**Shell hook env vars not injected in SDK mode.** `CLAUDE_TOOL_NAME`, `CLAUDE_HOOK_EVENT`, etc. are always empty inside teammate sessions. Use `matcher` in hook config instead of env-var checks.
