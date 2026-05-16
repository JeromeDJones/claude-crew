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

**`diagnostics.py`** — Startup-time diagnostic capture. `StartupDiagnostic` frozen dataclass + `StartupDiagCollector` `logging.Handler` subclass + `collect_startup_diagnostics()` context manager. `factories.default_factory()` wraps `build_merged_pack()` with the collector; the frozen tuple is threaded through `Broker(startup_diagnostics=...)` to `BrokerSnapshot.startup_diagnostics` and surfaced on the dashboard via the Startup Notices panel. Six-category classifier (shadow / unknown_skill / unknown_mcp_server / frontmatter / plugin / other). Stderr propagation preserved — additive handler, never silences.

**`subagents/`** — Default subagent pack. Three agents (`explorer`, `planner`, `general-purpose`) defined as markdown files with YAML frontmatter (model, tools, effort, maxTurns). No Bash or Task tool — leaf nodes that cannot recurse further.

### Test conventions

- `conftest.py` auto-sets `CLAUDE_CREW_TEAMMATE_MODE=stub` and `CLAUDE_CREW_TRANSCRIPT_DISABLED=1` for every test.
- Tests that need SDK mode clear the env var explicitly or pass `factory=` to `make_server()`.
- Tests that exercise the transcript sink set `CLAUDE_CREW_TRANSCRIPT_DIR` to a `tmp_path` and unset `CLAUDE_CREW_TRANSCRIPT_DISABLED`.
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
- **Imports at module top.** Inline imports inside test functions are a code smell — put new imports in the module's existing import block. The only exception is guarding optional dependencies (the `HookMatcher` `try/except ImportError` in `test_fidelity_audit.py` is the right pattern).
- **`asyncio.get_running_loop()`, never `asyncio.get_event_loop()`** inside coroutines — the latter is deprecated since Python 3.10 and emits warnings.
- **Bound unbounded async-iterator drains.** When iterating an open-ended async generator (e.g., `client.receive_response()`), wrap the drain in `asyncio.wait_for(..., timeout=T)` so a hung SDK subprocess surfaces as a clean timeout instead of a process-blocking hang. Set `T` from the spec's declared hang-detection budget (90s is the established `_wait_for_lead` default).
- **HOME-monkeypatch needs SDK auth preservation.** Tests that `monkeypatch.setenv("HOME", tmp_path)` to plant skill/plugin/agent fixtures must copy `~/.claude/.credentials.json` and `~/.claude.json` into the tmp HOME before spawning an SDK subprocess; without those, the subprocess returns `"Not logged in · Please run /login"`. Capture the real HOME at module-import time (`expanduser("~")` post-monkeypatch resolves to the tmp dir). See `_preserve_sdk_auth` in `tests/test_fidelity_audit.py` for the canonical helper.

### SDK behavior — verified invariants

**`AgentDefinition(tools=[])` is enforced by the SDK** as a true no-tools surface. Verified live 2026-05-02 (`tests/test_format_compat_e2e.py::TestLiveSdkToolsEmptyEnforcement`): a parent teammate dispatches a Task subagent declaring `tools=[]` and asks it to read a marker file; the marker never reaches the parent because the subagent has no Read available. **This is load-bearing for #15's safe-by-default `tools=()` design** — operators omitting `tools:` get a no-tool agent at the SDK boundary, NOT silent inherit-all. Re-run the live test if upgrading `claude-agent-sdk`.

**`AgentDefinition(model=None)` is wire-safe.** The SDK serializes via `{k: v for k, v in asdict(agent_def).items() if v is not None}` (`claude_agent_sdk/_internal/client.py:157`); the CLI conditionally appends `--model` only if truthy (`subprocess_cli.py:253-254`). Absent `model:` in a pack = no `--model` flag = SDK default at spawn.

**Token/cost telemetry rolls up at end-of-turn.** `_collect_response_text` extracts from `ResultMessage.usage` per #14 D-1. Long parent turns with heavy subagent dispatch (e.g., 30+ Tasks over 5+ minutes) show `0/0/$0.00` for the entire duration; tokens populate cleanly when the parent's turn returns. Observed 2026-05-02 with a 7-minute sentinel review (final: 143k in / 8k out / $1.40). Tracked as a UX gap in `doc/BACKLOG.md`.

### Known limitations

**MCP servers must be in user-level config.** SDK teammates load `~/.claude.json` but not project-level MCP config. Register any required MCP server in `~/.claude.json`.

**Shell hook env vars not injected in SDK mode.** `CLAUDE_TOOL_NAME`, `CLAUDE_HOOK_EVENT`, etc. are always empty inside teammate sessions. Use `matcher` in hook config instead of env-var checks.

**Windows `\r\n` line endings rejected in pack frontmatter.** `_split_frontmatter` hard-codes `"---\n"`; Windows-authored agent files raise `PackLoadError`. Pre-existing limitation. Tracked in `doc/BACKLOG.md`.
