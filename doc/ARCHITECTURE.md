# Architecture: claude-crew

**Created**: 2026-06-09 (harvested from project `CLAUDE.md` + `teammate-death-diagnostics` feature retro)
**Last Updated**: 2026-06-09

claude-crew is a local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

---

## Module Roles

### `claude_crew/server.py`

FastMCP server. The only surface the lead touches. Exposes 12 MCP tools:

| Tool | Purpose |
|------|---------|
| `spawn_teammate` | Spawn a new SDK teammate from the pack |
| `send_to` | Route an envelope to a specific teammate |
| `broadcast` | Send a message to all live teammates |
| `get_messages` | Long-poll for inbound messages (lead's inbox) |
| `get_wait_endpoint` | Non-blocking message-wait URL (avoids blocking `get_messages`) |
| `list_crew` | Snapshot of all live and tombstoned teammates |
| `kill_teammate` | Terminate a specific teammate |
| `get_teammate_status` | Per-teammate status payload (includes death-record fields) |
| `get_transcript_path` | Path to the crew JSONL transcript |
| `list_available_tools` | Available tool names for a teammate |
| `refresh_agents` | Reload agent definitions from disk; future-spawns-only |
| `surface_document` | Push a markdown artifact to Mission Control |

### `claude_crew/broker.py`

Single source of truth for team state. Owns the teammate registry, append-only message log, per-inbox queues, monotonic sequence counter, and dedup set. Tombstones dead teammates (marks dead, preserves in registry for status queries). Writes lifecycle and envelope records to the transcript sink.

Key method: `_tombstone_teammate` — called when a teammate dies. Reads the teammate's final snapshot (step 4), populates death-record fields, calls `_close_open_tools` to abandon in-flight tools (step 6), and serializes the result to the transcript.

### `claude_crew/teammate.py`

Abstract base class. Defines the inbox-consumption loop, activity tracking (`_begin_turn` / `_end_turn` / `_stamp_activity`), and tool tracking (`_tool_uses` in-flight dict, `_last_tool_completed`). `StubTeammate` is the echo implementation used in tests.

### `claude_crew/sdk_teammate.py`

Production teammate backed by `claude-agent-sdk`. Per-turn loop: pull envelope → translate to prompt → query SDK → drain response → send result envelope. Attaches PreToolUse/PostToolUse hooks for tool tracking (F8) and PreSubagentUse/PostSubagentUse hooks for subagent activity tracking (F7). Includes liveness polling (background task detects SDK death) and a per-turn backstop timeout.

Also owns the **stderr ring buffer subsystem** and **death-site telemetry** — see below.

### `claude_crew/envelope.py`

Wire format. Fields: `id` (caller-provided UUID for retry safety), `seq` (broker-stamped monotonic), `sender`, `recipient`, `timestamp`, `payload`.

### `claude_crew/factories.py`

Selects teammate implementation. `CLAUDE_CREW_TEAMMATE_MODE=stub` → `StubTeammate` (default in tests). `sdk` (default in production) → `SdkTeammate`. SDK mode merges the default subagent pack with `~/.claude/agents/` and project `.claude/agents/`.

### `claude_crew/transcript.py`

Best-effort JSONL sink. Path resolves via `CLAUDE_CREW_TRANSCRIPT_DIR` → `$XDG_STATE_HOME/claude-crew/transcripts/` → `~/.local/state/claude-crew/transcripts/`. Disabled in tests via `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.

### `claude_crew/redaction.py`

Tool telemetry redaction (v1 allowlist: Bash, Task, WebFetch). Extracts and redacts secrets from tool args before storage; caps at 256 bytes. Also used by `SdkTeammate._stderr_tail_redacted()` to sanitize ring contents before they leave the `SdkTeammate` instance.

### `claude_crew/diagnostics.py`

Startup-time diagnostic capture. `StartupDiagnostic` frozen dataclass + `StartupDiagCollector` logging handler + `collect_startup_diagnostics()` context manager. Six-category classifier (shadow / unknown_skill / unknown_mcp_server / frontmatter / plugin / other). Surfaced on the dashboard via the Startup Notices panel.

### `claude_crew/subagents/`

Default subagent pack. Three agents (`explorer`, `planner`, `general-purpose`) defined as markdown files with YAML frontmatter (model, tools, effort, maxTurns). No Bash or Task tool — leaf nodes that cannot recurse further.

---

## Telemetry Subsystem: Stderr Capture & Death Diagnostics

Added in `teammate-death-diagnostics` (2026-06-09). Purely additive — no control-flow change.

### Stderr ring buffer (`SdkTeammate`)

```
SdkTeammate
 ├── _stderr_ring: deque[str]       (maxlen=50)
 ├── _stderr_ring_bytes: int        (running byte total)
 ├── _on_stderr_line(line) → None  (SDK callback; never raises)
 └── _stderr_tail_redacted() → str | None
```

**Constants**:
- `_STDERR_RING_MAXLEN = 50` — max lines retained
- `_STDERR_RING_BYTE_CAP = 65_536` — 64 KB total byte budget

**Eviction**: dual-axis. The `deque(maxlen=50)` evicts oldest when line count exceeds 50. The byte cap is enforced separately: when adding a line would exceed `_STDERR_RING_BYTE_CAP`, oldest entries are popped until the cap is satisfied before appending.

**Callback**: `_on_stderr_line` is registered as `opts_kwargs["stderr"] = self._on_stderr_line` in `SdkTeammate._run` (line 1432 at time of authoring). It fires on the event loop once per decoded stderr line from the subprocess. It uses `try/except Exception: return` to guarantee it never raises — a raising callback would terminate the SDK transport's `_handle_stderr` coroutine silently, losing all future stderr for the session.

**Redaction**: `_stderr_tail_redacted()` joins the ring contents and runs `redact_output` before returning. Raw ring content never leaves `SdkTeammate` through any code path. Returns `None` when the ring is empty.

### Producer/consumer snapshot-key contract

`SdkTeammate.status_snapshot()` exposes two new keys consumed by `broker._tombstone_teammate`:

| Key | Type | Semantics |
|-----|------|-----------|
| `stderr_tail` | `str \| None` | Redacted ring tail. `None` when ring is empty. |
| `in_flight_tools` | `list[dict]` | Tools in flight at snapshot time (`current_tools` at that instant). Always a list when the snapshot is readable (never `None` from this path). |

**None-vs-`[]` semantics** (critical for consumers):

| `in_flight_tools_at_death` value | Meaning |
|----------------------------------|---------|
| `None` | Snapshot could not be read (teammate was `None`, or `status_snapshot()` raised `AttributeError`) |
| `[]` | Snapshot readable; no tool was in flight at death |
| `[{...}, ...]` | Tools that were in flight when the snapshot was taken |

The same `None` = unavailable / `[]` = empty distinction applies to `stderr_tail_at_death`:
- `None` = ring was empty **or** snapshot unavailable (the caller cannot distinguish these two sub-cases from the death record alone)
- `str` = redacted tail content

### Death-record fields (`TeammateInfo`)

```python
@dataclass
class TeammateInfo:
    ...
    stderr_tail_at_death: str | None = None
    in_flight_tools_at_death: list[Any] | None = None
```

Populated in `_tombstone_teammate` at **step 4** — before `_close_open_tools` abandons tools at step 6. This ordering is load-bearing: reading the snapshot after `_close_open_tools` would always yield an empty `in_flight_tools`. Both fields are serialized onto the dead-teammate status dict under the same key names and appear in the dashboard payload and JSONL transcript.

### Death-site WARNING

Emitted at the `ProcessError` / `CLIConnectionError` / `BrokenPipeError` catch arm in `SdkTeammate._handle_one_turn` (primary death path only; the graceful-flush arm is out of scope). Log format:

```
WARNING teammate <id> died: exc=<ExcClass> exit_code=<N> last_tool=<name> stderr_tail=<redacted-tail>
```

Uses `%`-style lazy logging args (not f-strings) to match the module's existing call style and avoid formatting cost when the level is suppressed.

### Redaction-before-persist invariant

**Invariant**: the raw stderr ring never leaves `SdkTeammate`. Every code path that reads the ring for external consumption (`_stderr_tail_redacted()`, called by `status_snapshot()`, the death-site WARNING, and transitively by the broker death-record population) passes the joined content through `redact_output` first. The raw ring exists only inside `SdkTeammate` and is garbage-collected with the object after death.

---

## Verified SDK Behavioral Invariants

These are empirically confirmed facts about the `claude-agent-sdk` / Claude CLI boundary. Re-verify when upgrading `claude-agent-sdk`.

**`AgentDefinition(tools=[])` enforces a true no-tools surface.** Verified live 2026-05-02: a subagent declaring `tools=[]` has no tools available at the SDK boundary — operators omitting `tools:` get a no-tool agent, NOT silent inherit-all.

**`AgentDefinition(model=None)` is wire-safe.** The SDK serializes via `{k: v ... if v is not None}`; absent `model:` in a pack = no `--model` flag = SDK default at spawn.

**Token/cost telemetry rolls up at end-of-turn.** `ResultMessage.usage` is populated when the parent's turn returns. Long turns show `0/0/$0.00` throughout; tokens populate cleanly at turn completion.

**Claude CLI emits no stderr during normal turns.** All output, including verbose/debug messages, routes to stdout as a JSON stream (`--output-format stream-json`). Verified empirically (teammate-death-diagnostics, 2026-06-09): `subprocess.Popen` with `stderr=PIPE` on `claude --output-format stream-json --verbose` produces 0 stderr bytes. `SdkTeammate._stderr_ring` therefore only populates during error/crash scenarios. Live tests verifying ring population must inject via `_on_stderr_line` directly; they cannot rely on a healthy turn producing stderr output.

---

## Dashboard: Multi-Instance Architecture Note

The Mission Control dashboard (`ui_server.py`) is not single-instance. One instance binds the leader port (7821); others become followers on ephemeral ports and register in `InstanceRegistry`. The leader aggregates every instance: `_build_state` calls `_fetch_remote_state` to pull each follower's `/api/state` and merges their agents + transcripts into one view keyed by `crew_id`.

**Key rule**: any new dashboard endpoint that serves per-instance data must carry `crew_id` and route in the handler — serve locally when `crew_id == self._broker.crew_id`, else proxy to the right follower instance. A single-instance test will pass while the feature is broken for the actual deployment.

---

## Test Conventions

- `conftest.py` auto-sets `CLAUDE_CREW_TEAMMATE_MODE=stub` and `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_stderr.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
- `asyncio.get_running_loop()`, never `asyncio.get_event_loop()` inside coroutines.
- Bound unbounded async-iterator drains with `asyncio.wait_for(..., timeout=T)`.
- HOME-monkeypatch tests must copy `~/.claude/.credentials.json` and `~/.claude.json` into the tmp HOME.
- LLM-relayed sentinels: ≤12 hex characters (preferred) to avoid truncation/paraphrasing across the LLM relay boundary.
- Full `uv run pytest` (not `-k` subset) when changing widely-consumed behavior.
