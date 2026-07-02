# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How SDK teammates wire their tools / skills / MCP / memory

Before reasoning about pack contracts, allowlist enforcement, or "why is the teammate calling a tool I didn't grant?" — read `doc/sdk-teammate-wiring.md`. It captures the asymmetry between in-session subagents (leaf nodes, no inheritance) and SDK teammates (top-level Claude CLI subprocesses that auto-load user/project settings, including plugin MCP, whenever the pack declares `skills:`). Re-derive only when the SDK is upgraded.

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

### Run live tests before merging

This project's whole job is real SDK orchestration, so **stub-mode tests can pass while live behavior regresses** — role resolution against the real merged pack, real teammate spawn/instantiate, real subprocess lifecycle. The default `uv run pytest` (stub) suite is necessary but **not sufficient**. Before merging a behavioral change, run the live SDK suite:

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live*.py
```

These hit the real cloud Anthropic API (real `claude` subprocesses — they cost tokens and take minutes). Scope by judgment: at minimum run the live tests covering the surface you touched; run the **full** live suite before a release / version bump. Pure-documentation or config-only changes may skip. Any feature that adds behavior which only manifests live (SDK / teammate / factory / shape / instantiate paths) ships **with** a live test, and that test runs before merge — a green stub suite is not a merge signal on its own. (A live test must actually *exercise* the path: assert a real teammate turn/response, not just the synchronous structure a stub would also satisfy.)

Run the MCP server directly:

```bash
uv run claude-crew   # starts the FastMCP server (requires auth)
```

## Architecture

claude-crew is a local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

### Core components

**`server.py`** — FastMCP server. Exposes 18 tools to the lead: `spawn_teammate`, `send_to`, `broadcast`, `get_messages` (long-poll via `wait_seconds`), `get_wait_endpoint` (non-blocking message-wait URL), `list_crew`, `kill_teammate`, `get_teammate_status`, `get_transcript_path`, `list_available_tools`, `refresh_agents` (reload agent definitions from disk into the in-memory pack; future-spawns-only), `surface_document` (push a markdown artifact to Mission Control), `propose_shape` (register a shape as a pending human-approval gate; non-blocking by default, `wait=True` retains blocking path), `resolve_shape` (chat-channel approve/decline), `list_pending_shapes`, `instantiate_shape` (spawn exactly the approved crew; pre-flight role resolution all-or-nothing; records a `Topology`), `adapt_shape` (apply one verb to a pre-instantiation shape; re-gates via M1.5), `reshape_crew` (apply one verb to an **already-running live crew**; M3.5; reuses M1.5 gate; on approval mutates the live crew — no respawn for additions, spawn-then-kill for swap, graceful kill for drop, override write for set_gate). This is the only surface the lead touches.

**`shapes.py`** — Shape schema (NEW in `workflow-shape-composition-m0`). `Shape`, `ShapeNode`, `ShapeEdge` frozen dataclasses + `ShapeValidationError` + `parse_shape(data, *, source)` (validates loudly; accepts dict or YAML string) + `shape_to_mermaid(shape)` (emits `graph TD` source for the dashboard renderer). Pure data — no broker/SDK dependency.

**`broker.py`** — Single source of truth for team state. Owns the teammate registry, append-only message log, per-inbox queues, monotonic sequence counter, and dedup set. Tombstones dead teammates (marks dead, preserves in registry for status queries). Writes lifecycle and envelope records to the transcript sink. Also owns the shape proposal registry (`ShapeProposal` state machine: `pending` → `approved`/`declined`/`timed_out` → `instantiated`, gated by an `asyncio.Condition` long-poll) and recorded topologies (`Topology`: edges + per-edge mode + slot→teammate map). Both are surfaced on `BrokerSnapshot`.

**`teammate.py`** — Abstract base class. Defines the inbox-consumption loop, activity tracking (`_begin_turn` / `_end_turn` / `_stamp_activity`), and tool tracking (`_tool_uses` in-flight dict, `_last_tool_completed`). `StubTeammate` is the echo implementation used in tests.

**`sdk_teammate.py`** — Production teammate backed by `claude-agent-sdk`. Per-turn loop: pull envelope → translate to prompt → query SDK → drain response → send result envelope. Attaches PreToolUse/PostToolUse hooks for tool tracking (F8) and PreSubagentUse/PostSubagentUse hooks for subagent activity tracking (F7). Includes liveness polling (background task detects SDK death) and a per-turn backstop timeout. **D0 (M3.5)**: the in-process `send_to` MCP server is wired unconditionally for every `SdkTeammate` at spawn — the security boundary is `broker.authorize_send` at delivery time, not the tool's presence. This enables respawn-free live edge additions via `reshape_crew`.

**`envelope.py`** — Wire format. Fields: `id` (caller-provided UUID for retry safety), `seq` (broker-stamped monotonic), `sender`, `recipient`, `timestamp`, `payload`.

**`factories.py`** — Selects teammate implementation. `CLAUDE_CREW_TEAMMATE_MODE=stub` → `StubTeammate` (default in tests). `sdk` (default in production) → `SdkTeammate`. SDK mode merges the default subagent pack with `~/.claude/agents/` and project `.claude/agents/`. The SDK factory exposes `factory.known_roles` — a zero-arg callable returning `tuple(holder.pack.keys())` read live off the merged pack holder — used by `instantiate_shape`'s pre-flight role resolution. The stub factory does not set this attribute by default (tests inject it to exercise the pre-flight).

**`transcript.py`** — Best-effort JSONL sink. Path resolves via `CLAUDE_CREW_TRANSCRIPT_DIR` → `$XDG_STATE_HOME/claude-crew/transcripts/` → `~/.local/state/claude-crew/transcripts/`. Disabled in tests via `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.

**`redaction.py`** — Tool telemetry redaction (v1 allowlist: Bash, Task, WebFetch). Extracts and redacts secrets from tool args before storage; caps at 256 bytes.

**`diagnostics.py`** — Startup-time diagnostic capture. `StartupDiagnostic` frozen dataclass + `StartupDiagCollector` `logging.Handler` subclass + `collect_startup_diagnostics()` context manager. `factories.default_factory()` wraps `build_merged_pack()` with the collector; the frozen tuple is threaded through `Broker(startup_diagnostics=...)` to `BrokerSnapshot.startup_diagnostics` and surfaced on the dashboard via the Startup Notices panel. Six-category classifier (shadow / unknown_skill / unknown_mcp_server / frontmatter / plugin / other). Stderr propagation preserved — additive handler, never silences.

**`subagents/`** — Default subagent pack. Three agents (`explorer`, `planner`, `general`) defined as markdown files with YAML frontmatter (model, tools, effort, maxTurns). No Bash or Task tool — leaf nodes that cannot recurse further.

### Test conventions

- `conftest.py` auto-sets `CLAUDE_CREW_TEAMMATE_MODE=stub` and `CLAUDE_CREW_TRANSCRIPT_DISABLED=1` for every test.
- Tests that need SDK mode clear the env var explicitly or pass `factory=` to `make_server()`.
- Tests that exercise the transcript sink set `CLAUDE_CREW_TRANSCRIPT_DIR` to a `tmp_path` and unset `CLAUDE_CREW_TRANSCRIPT_DISABLED`.
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_reshape.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
- **Imports at module top.** Inline imports inside test functions are a code smell — put new imports in the module's existing import block. The only exception is guarding optional dependencies (the `HookMatcher` `try/except ImportError` in `test_fidelity_audit.py` is the right pattern).
- **`asyncio.get_running_loop()`, never `asyncio.get_event_loop()`** inside coroutines — the latter is deprecated since Python 3.10 and emits warnings.
- **Bound unbounded async-iterator drains.** When iterating an open-ended async generator (e.g., `client.receive_response()`), wrap the drain in `asyncio.wait_for(..., timeout=T)` so a hung SDK subprocess surfaces as a clean timeout instead of a process-blocking hang. Set `T` from the spec's declared hang-detection budget (90s is the established `_wait_for_lead` default).
- **HOME-monkeypatch needs SDK auth preservation.** Tests that `monkeypatch.setenv("HOME", tmp_path)` to plant skill/plugin/agent fixtures must copy `~/.claude/.credentials.json` and `~/.claude.json` into the tmp HOME before spawning an SDK subprocess; without those, the subprocess returns `"Not logged in · Please run /login"`. Capture the real HOME at module-import time (`expanduser("~")` post-monkeypatch resolves to the tmp dir). See `_preserve_sdk_auth` in `tests/test_fidelity_audit.py` for the canonical helper.
- **LLM-relayed sentinels must be relay-safe by construction.** Tests that assert subagent dispatch by checking sentinel relay through a Task subagent are inherently probabilistic — LLMs occasionally truncate, paraphrase, or drop characters when relaying long opaque strings. Constrain sentinel design: **≤12 hex characters** (preferred) or URL-safe pronounceable tokens (`fidelity-bx7q`, `probe-ax9m`). Avoid 32-char `uuid.uuid4().hex` for anything that crosses the LLM relay boundary. For non-relay assertions (file-write sentinels, hook-callback sentinels), longer tokens are fine. Verified 2026-05-16: `TestBundledPackDispatchFidelity` flaked at 32 hex (cycle-0 of `fidelity-audit-followups`'s validation gate), passed cleanly at 12 hex on retry and post-shortening.
- **Validate the whole suite when changing widely-consumed behavior.** A feature/spec validation command scoped to only the changed feature's own test files (`-k` filters or a hand-picked file list) can pass while a cross-cutting regression merges undetected. Anytime a change alters behavior other suites assert on — removing or renaming a log WARN, changing a contract/return shape, flipping a default — the validation gate must run the **full** `uv run pytest`, not a keyword-filtered subset. Verified 2026-05-20: the `multi-scope-agent-memory` feature removed the "only 'user' is supported" memory WARN; its RR validation ran only `tests/test_teammate_memory.py tests/test_sdk_teammate.py`, so the broken assertion in `tests/test_e2e_pack_parity.py` (which asserted that WARN) slipped through to master and was caught only on the follow-up's full-suite run.
- **Live tests asserting teammate→teammate peer delivery must use `direct` edges.** Gated edges (the `ShapeEdge` default) route messages to the coordinator's inbox via `broker._send_routed`, not the recipient's inbox — a peer-delivery assertion against a gated edge will time out rather than fail fast. Verified 2026-06-30 (`tests/test_live_reshape.py` AT-17/18): initial live runs timed out because test fixtures used the default gated mode; switching to `direct` edges resolved it. No prior live test had exercised a real teammate `send_to` before M3.5 — M2 only validated the broker-side routing at stub level.

### SDK behavior — verified invariants

**`AgentDefinition(tools=[])` is enforced by the SDK** for the pack's own tool list: `tools=[]` means no pack-declared tools. Verified live 2026-05-02 (`tests/test_format_compat_e2e.py::TestLiveSdkToolsEmptyEnforcement`): a parent teammate dispatches a Task subagent declaring `tools=[]` and asks it to read a marker file; the marker never reaches the parent because the subagent has no Read available. **This is load-bearing for #15's safe-by-default `tools=()` design** — operators omitting `tools:` get a no-tool agent at the SDK boundary, NOT silent inherit-all. Re-run the live test if upgrading `claude-agent-sdk`. **D0 (M3.5) addendum:** claude-crew unconditionally wires `mcp__crew-send__send_to` as framework infrastructure for every `SdkTeammate` — so in practice a `tools:[]` pack yields `opts.tools == ['mcp__crew-send__send_to']`, not `[]`. The safe-by-default seal is preserved by `broker.authorize_send` (presence ≠ reach: a teammate holding the tool but lacking an authorized topology edge gets `UnauthorizedEdgeError`), not by tool absence.

**`AgentDefinition(model=None)` is wire-safe.** The SDK serializes via `{k: v for k, v in asdict(agent_def).items() if v is not None}` (`claude_agent_sdk/_internal/client.py:157`); the CLI conditionally appends `--model` only if truthy (`subprocess_cli.py:253-254`). Absent `model:` in a pack = no `--model` flag = SDK default at spawn.

**Token/cost telemetry rolls up at end-of-turn.** `_collect_response_text` extracts from `ResultMessage.usage` per #14 D-1. Long parent turns with heavy subagent dispatch (e.g., 30+ Tasks over 5+ minutes) show `0/0/$0.00` for the entire duration; tokens populate cleanly when the parent's turn returns. Observed 2026-05-02 with a 7-minute sentinel review (final: 143k in / 8k out / $1.40). Tracked as a UX gap in `doc/BACKLOG.md`.

### Known limitations

**MCP servers must be in user-level config.** SDK teammates load `~/.claude.json` but not project-level MCP config. Register any required MCP server in `~/.claude.json`.

**Shell hook env vars not injected in SDK mode.** `CLAUDE_TOOL_NAME`, `CLAUDE_HOOK_EVENT`, etc. are always empty inside teammate sessions. Use `matcher` in hook config instead of env-var checks.

**Windows `\r\n` line endings rejected in pack frontmatter.** `_split_frontmatter` hard-codes `"---\n"`; Windows-authored agent files raise `PackLoadError`. Pre-existing limitation. Tracked in `doc/BACKLOG.md`.

**plan-mode write gate is enforced client-side by claude-crew, not the SDK.** As of claude-agent-sdk 0.1.68 / Claude Code CLI 2.1.177, `permission_mode="plan"` presents an approval UI instead of silently blocking in headless (non-interactive) sessions, so Writes proceed without user approval. claude-crew compensates by denying `Write`, `Edit`, `NotebookEdit`, and `MultiEdit` in the `PreToolUse` hook whenever `_effective_permission_mode == "plan"`. Read-only tools (`Read`, `Grep`, `Glob`, `Bash`, `WebFetch`, `Task`) remain available. This enforcement fires before the SDK's plan-gate UI would appear, so the gate is reliable regardless of SDK behavior.

### Dashboard is a multi-instance LEADER — any new lazy-fetch endpoint MUST be crew-aware

The Mission Control dashboard (`ui_server.py`) is **not** single-instance. One instance binds the leader port (`7821`); others become followers on ephemeral ports and register in `InstanceRegistry`. The leader **aggregates** every instance: `_build_state` calls `_fetch_remote_state` to pull each follower's `/api/state` and merges their agents + transcripts into one view keyed by `crew_id`. The operator almost always views the **leader**, which is showing rows that belong to **other instances' brokers**.

**The trap:** a dashboard modal/feature that lazy-fetches per-row data with a *same-origin relative* URL (e.g. `GET /tool-output/<teammate>/<id>`) hits the **leader's** broker — which does **not** contain remote instances' teammates. Every click on a remote row → 404. This is invisible to single-instance stub tests and to per-data-path tracing; it only surfaces with ≥2 live instances. (It bit the click-to-view-tool-output feature: shipped green, broke on first real multi-instance use. Fixed in `fix/tool-output-multi-instance-proxy`.)

**The rule:** any new dashboard endpoint that serves per-instance data must (1) carry the row's `crew_id` (inject it onto the record in `_build_local_instance`, like `tool_use_id`/`crew_id` on `kind:"tool"` records), and (2) route in the handler — serve locally when `crew_id == self._broker.crew_id`, else look the crew up in `InstanceRegistry` and **proxy** to that instance's port (mirror `_fetch_remote_state`; see `_proxy_tool_output`). Add a **multi-instance** test (`test_e2e_multi_instance.py` / leader→follower proxy), not just a single-instance one — a single-instance test will pass while the feature is broken for the actual deployment.
