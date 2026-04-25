# Feature: One Persistent SDK Teammate, End-to-End

**Status**: Planning (Phase 1)
**Created**: 2026-04-25
**Maps to**: PRODUCT-VISION.md Feature Pipeline #2, Capability #1, Success Criteria #1, #5

---

## Phase 1: Research & Requirements

### Problem Statement

Feature #1 shipped the bus, the tool surface, and a `Teammate` ABC with a `StubTeammate` echoing payloads. That validates the protocol but doesn't validate the *thing* claude-crew exists to provide: a real, persistent, role-specialized teammate that thinks. Feature #2 is where the product becomes itself.

We need to plug a `ClaudeSDKClient` into the existing seam (`Teammate` ABC) such that:

1. The lead can `spawn_teammate(role="...")` and get back a real LLM-driven agent with its own session, system prompt, tool set, and memory.
2. Subsequent `send_to` calls route the lead's payload as a user-turn to that teammate; the teammate's response routes back via `broker.send` into the lead's inbox.
3. Context persists across many turns within a session — turn 10 remembers turn 1.
4. The bus, broker, and tool surface from Feature #1 stay untouched. The seam was designed for this.

This feature also resolves the **SDK memory behavior** verification item from the vision (the other two — subagent isolation, per-subagent token budgets — are gated behind Feature #3a, not this one). After this feature ships, we should have a documented answer to: what persists, what loads from CLAUDE.md, whether Claude Code's auto-memory subsystem activates for SDK programs.

Without this feature, claude-crew is a stub message bus. With it, it's the foundation it claims to be.

### Success Criteria

- [ ] **SC-1: SdkTeammate wraps ClaudeSDKClient.** A new `SdkTeammate(Teammate)` class lives alongside `StubTeammate`. It instantiates a `ClaudeSDKClient` on `start()`, holds the reference for the teammate's lifetime, and tears it down cleanly on `shutdown()`.
- [ ] **SC-2: The MCP `spawn_teammate` tool can produce SdkTeammates.** The server's teammate factory is selectable (env var, config, or default) such that the same MCP tool surface yields stub teammates in tests and SDK teammates in production. Stub mode is preserved for tests; SDK mode is the new default.
- [ ] **SC-3: Round-trip through SDK works.** `send_to(teammate_id, "Say hello in three words")` causes the SdkTeammate to call its `ClaudeSDKClient`, receive a model response, and enqueue an envelope back to the lead via `broker.send`. The lead's `get_messages` returns it.
- [ ] **SC-4: 10+ turn persistence verified, deterministically.** A scripted test exchanges at least 10 messages with one SdkTeammate. On turn 1, the lead sends a freshly generated UUID inside a "remember this token" prompt; on turn 10, the lead asks the teammate to repeat the token verbatim. The test asserts **exact substring match** of the original UUID in the teammate's reply — no semantic-resemblance judgment, no model-guessable content. Intermediate turns (2–9) drive normal conversation so the recall is across real history, not a two-message echo.
- [ ] **SC-5: Memory behavior documented.** A short note in `doc/research/sdk-memory.md` records: (a) what persists across `ClaudeSDKClient` calls within one session, (b) whether/how `~/.claude/CLAUDE.md` and project `CLAUDE.md` load (via `setting_sources`), (c) whether the auto-memory subsystem (`~/.claude/projects/.../memory/`) is active for SDK programs running outside the Claude Code CLI. Each finding empirically verified, not inferred from docs alone. *Accepted risk:* if the spike returns "behavior unclear" for any item, that *is* the documented finding and the feature still ships.
- [ ] **SC-6: Errors surface as a defined envelope, do not crash.** Define a teammate-originated error envelope shape: `payload = {"error": "<code>", "message": "<human readable>", "from": "<role>"}` with `code` from a small enum (`api_error`, `rate_limited`, `invalid_response`, `internal`). On any SDK exception, the SdkTeammate constructs and sends one such envelope back to the original sender via `broker.send`, then returns to its inbox loop ready for the next message. Test conditions: (a) mocked SDK exception → error envelope arrives at lead with the right `code` and `from`, (b) the teammate's worker task remains alive afterward and processes a subsequent message normally, (c) the broker remains operational, (d) other teammates are unaffected.
- [ ] **SC-7: Clean shutdown closes the SDK client.** `kill_teammate(teammate_id)` returns within a bounded time (≤ 5 seconds), `ClaudeSDKClient.close()` (or the SDK's documented async-context-exit equivalent) is called exactly once, no orphaned background tasks remain (`asyncio.all_tasks()` post-shutdown shows no SdkTeammate-owned tasks alive), and HTTP sessions held by the SDK are released. Verified by: (a) test with a mock SDK client asserts `close()` was called, (b) post-shutdown task introspection asserts the teammate's worker task is `done()`.
- [ ] **SC-8: Auth validated at server startup.** When SDK mode is active, the server validates auth before serving any tool calls and **refuses to start** if no usable credential is available. Acceptable credentials, in priority order: (1) an authenticated Claude Code session on the local machine, (2) `ANTHROPIC_API_KEY` in the environment. If neither works, the server exits with a clear stderr message listing both options. Stub mode (used by tests) does not require auth. Verified by: integration test that boots the server in SDK mode with no credentials and asserts a fast, structured exit.
- [ ] **SC-9: End-to-end via real stdio.** A smoke test (`scripts/sdk_smoke_test.py` or extending the existing one) drives spawn → 3-turn exchange (with a deterministic recall token) → kill against an SdkTeammate over the actual MCP stdio transport, with the real Anthropic API. Gated behind `CLAUDE_CREW_LIVE_TESTS=1` to avoid surprise costs.

### Resolved Decisions

- [x] **SDK package: Claude Code SDK** (a.k.a. Claude Agent SDK; pinned in Phase 2 to the actual installable name — most likely `claude-agent-sdk` on PyPI). Phase 2 gathering confirms the class surface.
- [x] **Default model: Sonnet 4.6** (`claude-sonnet-4-6`) for general teammates. Roles can override at spawn time in a later feature.
- [x] **Auth: prefer Claude Code session, fall back to API key.** Many operators already have Claude Code authenticated locally; the SDK should pick that up without an explicit `ANTHROPIC_API_KEY`. Phase 2 confirms whether the SDK supports this directly. If only API key works, fall back to `ANTHROPIC_API_KEY` from the environment. Either way, auth is validated at server startup (SC-8) and the server refuses to start with a clear error if no usable credential is found.
- [x] **Live-test gating: `CLAUDE_CREW_LIVE_TESTS=1`.** Default off. SC-9 is the only test that hits the real API; SC-1–#8 use mocks/fakes.

### Constraints & Dependencies

**Hard requirements:**
- Python 3.12+ (already)
- The Claude Code SDK / Claude Agent SDK Python package (name pinned in Phase 2)
- A usable Claude credential available to the SDK: an authenticated Claude Code session on the local machine OR `ANTHROPIC_API_KEY` in the environment
- Local-machine only — no remote crew, no shared sessions

**Soft constraints:**
- **StubTeammate path must be preserved.** Existing tests rely on it; future bus-level features (transcript JSONL, broadcast semantics) will too. The factory selection mechanism is the seam.
- **No changes to the broker's contract.** Feature #1's design committed to a stable broker; Feature #2 lands on the seam, not under it. If a real change to `Broker` becomes necessary, that's a Phase 2 finding to escalate, not a quiet edit.
- **No changes to the MCP tool surface.** Same six tools, same return shapes. The "now teammates are real" change is invisible to the lead.
- **Cost discipline.** Live tests gated. Mocks/fakes drive most of the test layer. SC-8 is one bounded smoke test, not a suite.

**Breaking changes:** None at the public API. Internally, the default `factory` parameter in `make_server()` shifts from `_default_factory` (stub) to an SDK factory; tests that want the stub pass an explicit factory.

**Performance:**
- Each `send_to` blocks the SdkTeammate's worker task on a model response (seconds, not milliseconds). The broker's per-recipient FIFO queue absorbs concurrent sends — the teammate processes one at a time. This is correct: a single teammate is a single conversation; serializing turns is the contract.
- Cross-teammate concurrency is intact: two teammates being prompted simultaneously run in parallel (separate worker tasks, separate SDK clients, separate inboxes).

**Concurrency boundary (out of scope):**
- **Spawn-during-shutdown is not supported in v1.** The MCP server processes one tool call at a time on a single asyncio loop; `kill_teammate` and `spawn_teammate` cannot interleave at the tool boundary. `broker.shutdown_all()` is called only by the server's terminal lifecycle (process shutdown) and is the last operation before exit. We do not add atomicity guards for hypothetical concurrent-spawn-during-shutdown — that scenario can't occur given the server's request handling, and Feature #1's `Broker` was designed with this assumption. Reopens only if multi-threaded MCP transport or in-flight cancellation lands later.

### Out of Scope (Explicit)

These belong to later features; flagging here to prevent scope drift in Phase 2:

- **Subagent decomposition within the teammate** (Feature #3a/b). The SdkTeammate ships without configured subagents in this feature; recursive Task-tool wiring is the next feature's job.
- **Default subagent pack and `~/.claude/agents/` loader** (#3a, #3b).
- **Transcript JSONL** (#4). The bus log already exists in-memory; durable persistence is a later concern.
- **Multi-crew validation** (deferred from MVP).
- **Streaming responses to the lead.** The teammate's response is a single envelope; streaming-as-it-types is an enhancement we can revisit.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

The Feature #1 architecture is unchanged. We add one new class (`SdkTeammate`), one factory selector in the server, and one startup-time auth check.

```
┌─────────────────────────────────────────────────────────┐
│  MCP Server                                             │
│   ├ tools (unchanged)                                   │
│   └ make_server(broker, factory) ← factory now selectable
│                                                         │
│  Broker (unchanged)                                     │
│                                                         │
│  Teammate ABC (unchanged)                               │
│   ├ StubTeammate    (kept; tests use it)                │
│   └ SdkTeammate ←── NEW                                 │
│        wraps: ClaudeSDKClient (async ctx mgr)           │
│        which wraps: claude CLI subprocess               │
│                                                         │
│  Auth                          ← NEW                    │
│   └ validate_auth_or_exit() at server startup           │
└─────────────────────────────────────────────────────────┘
```

**Key resource shape from Phase 2 gathering:** `ClaudeSDKClient` is itself an async context manager that spawns a `claude` CLI subprocess. So **one SdkTeammate = one subprocess**, and our existing per-teammate `asyncio.Task` becomes the *manager* of that subprocess's lifecycle through the SDK's context manager. The broker's contract is unchanged; the cost shape changes (real money, real RAM, real subprocess).

### SDK API contract (pinned from gathering)

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

options = ClaudeAgentOptions(
    model="claude-sonnet-4-6",
    system_prompt=role_specific_prompt,
    setting_sources=["user", "project"],   # loads CLAUDE.md from ~/.claude and cwd
    max_turns=None,                         # we manage turn count via inbox cycles
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("hello", session_id="default")
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_so_far += block.text
        # ResultMessage terminates the iteration
```

Persistence behavior we rely on: **multiple `query()` calls on the same client instance with the same `session_id` automatically share conversation history** via the persistent subprocess. SC-4's deterministic UUID-recall test depends on this — empirically verified by the SC-5 spike when the feature ships.

### SdkTeammate Design

```python
class SdkTeammate(Teammate):
    def __init__(self, id: str, name: str, role: str,
                 model: str = "claude-sonnet-4-6",
                 system_prompt: str | None = None,
                 setting_sources: list[str] | None = None) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._model = model
        self._system_prompt = system_prompt or _default_system_prompt(role)
        self._setting_sources = setting_sources if setting_sources is not None else ["user", "project"]
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._run(), name=f"sdk-{self.id}")

    async def _run(self) -> None:
        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=self._system_prompt,
            setting_sources=self._setting_sources,
        )
        try:
            async with ClaudeSDKClient(options=options) as client:
                while True:
                    env = await self._inbox.get()
                    if env is _SHUTDOWN_SENTINEL:
                        return
                    await self._handle_one_turn(client, env)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Catastrophic failure outside per-turn handling.
            await self._send_error_envelope(
                to=LEAD_ID, code="internal",
                message=f"SdkTeammate {self.id} crashed: {exc}",
            )

    async def _handle_one_turn(self, client: ClaudeSDKClient, env: Envelope) -> None:
        prompt = _payload_to_prompt(env.payload)
        if not prompt:
            await self._send_error_envelope(
                to=env.sender, code="invalid_response",
                message="empty prompt — nothing to send to model",
            )
            return
        try:
            await client.query(prompt, session_id="default")
            text = await asyncio.wait_for(
                _collect_response_text(client),
                timeout=TURN_TIMEOUT_SECONDS,  # 120s default
            )
        except asyncio.TimeoutError:
            await self._send_error_envelope(
                to=env.sender, code="invalid_response",
                message=f"no ResultMessage within {TURN_TIMEOUT_SECONDS}s — subprocess may be stuck",
            )
            return
        except Exception as exc:
            await self._send_error_envelope(
                to=env.sender, code=_classify_error(exc), message=str(exc),
            )
            return
        if not text:
            await self._send_error_envelope(
                to=env.sender, code="invalid_response",
                message="model returned no text content",
            )
            return
        await self._broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=self.id, recipient=env.sender,
            timestamp=time.time(),
            payload={"text": text, "from": self.role},
        ))

    async def _send_error_envelope(self, *, to: str, code: str, message: str) -> None:
        await self._broker.send(Envelope(
            id=new_message_id(),
            seq=0,
            sender=self.id,
            recipient=to,
            timestamp=time.time(),
            payload={"error": code, "message": message, "from": self.role},
        ))

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SHUTDOWN_SENTINEL)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._task = None
```

**Why this shape:**
- `async with ClaudeSDKClient(...)` is the SDK's documented lifecycle. Wrapping the inbox loop inside the context manager means subprocess teardown happens automatically when the loop exits — no manual `close()` call to forget.
- Error handling is *per-turn*, not per-task. A single failed turn produces an error envelope and the loop continues. SC-6 #(b): "the teammate's worker task remains alive afterward and processes a subsequent message normally."
- Catastrophic failures (the `try` outside the loop) catch issues like SDK construction failure or subprocess crash. Those get one final error envelope and exit.
- Shutdown is bounded to 5s with hard cancel as the fallback — satisfies SC-7.

### `_collect_response_text` — Helper Contract

```python
TURN_TIMEOUT_SECONDS = 120  # generous default; tunable per-role later

async def _collect_response_text(client: ClaudeSDKClient) -> str:
    """Drain `client.receive_response()` until the iterator terminates.

    Behavior:
      - Concatenates every TextBlock from every AssistantMessage in arrival order.
      - Ignores tool-use blocks, thinking blocks, and SystemMessages (they are
        SDK-internal; surfacing them is a Feature #4 concern).
      - On RateLimitEvent: raises a RateLimitedError so the caller emits the
        `rate_limited` error code.
      - Returns "" if the iterator terminates with no AssistantMessage text.
        Caller is responsible for treating "" as `invalid_response`.

    Termination:
      - The SDK's `receive_response()` terminates when it observes a
        ResultMessage. The caller wraps this helper in `asyncio.wait_for` to
        bound the wait — non-termination of `receive_response()` (subprocess
        crash, never-arrives ResultMessage) becomes a TimeoutError, not a hang.
    """
```

The caller (`_handle_one_turn`) treats the empty-string return as `invalid_response`. RateLimit is a distinct exception; tool-use blocks are silently ignored for v1 (Assumption A2 — SC-4 will catch the case where text-free responses break recall, surfacing the need for tool-use handling as a follow-up).

### Payload conventions

Inbound to a teammate (lead's `send_to`):
- If `payload` is a `str`: that's the user prompt.
- If `payload` is `{"prompt": "...", "extras": {...}}`: extract `prompt`, ignore extras for now (forward-compatible).
- Otherwise: `_payload_to_prompt` JSON-encodes it. Roles are free to interpret their inbound payloads however; we only need to deliver a string to `client.query()`.

Outbound from a teammate (broker.send to lead):
- Success: `payload = {"text": "<assistant reply>", "from": "<role>"}`
- Error: `payload = {"error": "<code>", "message": "<human readable>", "from": "<role>"}`

The shape difference (`text` key vs `error` key) is the lead's discriminator. No version field needed — adding fields later is forward-compatible.

### Error code enum

| Code | When |
|---|---|
| `api_error` | `anthropic.APIError` or any `claude_agent_sdk` exception about API behavior |
| `rate_limited` | Catches `RateLimitEvent` in the response stream OR a 429-like exception |
| `invalid_response` | Response stream ended without an `AssistantMessage`, or text was empty |
| `internal` | Anything else (catch-all) |

`_classify_error(exc)` does string matching on exception class names. Conservative; we expand the enum only when a real failure surfaces a need for a finer category.

### Factory selection

```python
# claude_crew/factories.py  (new file)
import os
from claude_crew.teammate import StubTeammate
from claude_crew.sdk_teammate import SdkTeammate

def stub_factory(id, name, role):
    return StubTeammate(id=id, name=name, role=role)
stub_factory.requires_auth = False  # marker

def sdk_factory(id, name, role):
    return SdkTeammate(id=id, name=name, role=role)
sdk_factory.requires_auth = True  # marker

def default_factory():
    """Return the factory selected by the CLAUDE_CREW_TEAMMATE_MODE env var.

    Values:
      "sdk" (default in production) — SdkTeammate, requires auth
      "stub"                        — StubTeammate (used by tests)
    """
    mode = os.environ.get("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
    return sdk_factory if mode == "sdk" else stub_factory
```

`make_server()` becomes:

```python
def make_server(broker=None, factory=None) -> FastMCP:
    broker = broker if broker is not None else Broker()
    factory = factory if factory is not None else default_factory()
    if getattr(factory, "requires_auth", False):
        validate_auth_or_exit()
    ...
```

The `requires_auth` attribute is the contract — any factory that produces a teammate needing Anthropic credentials sets it `True`. Factory-identity comparison was rejected (fragile under wrappers/partials/lambdas). Custom factories opt in by setting the attribute.

Tests that need the stub explicitly pass `factory=stub_factory` or set `CLAUDE_CREW_TEAMMATE_MODE=stub` — both work. The existing test suite needs one tweak (set the env var or pass the factory) for tests that currently expect stub semantics from default `make_server()`.

### Auth Validation

```python
# claude_crew/auth.py  (new file)
import os
import sys
from pathlib import Path

def has_usable_credential() -> bool:
    """Return True iff the SDK is likely to find a working credential."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    if (Path.home() / ".claude" / ".credentials.json").exists():
        return True
    # macOS Keychain: best-effort. We accept presence of the credentials file
    # as the primary signal; on macOS without that file, we fall through to
    # surfacing the SDK's own error on first call rather than guessing.
    return False

def validate_auth_or_exit() -> None:
    if has_usable_credential():
        return
    sys.stderr.write(
        "claude-crew: no Claude credentials found.\n"
        "  Run 'claude login' to set up Claude Code session auth, or\n"
        "  export ANTHROPIC_API_KEY=<your-key>.\n"
    )
    sys.exit(2)
```

This is a *fast* check — no subprocess spawn, no API call. SDK errors at first send still surface via SC-6's error envelope path. The startup check exists so a misconfigured operator gets a clear message *before* ever spawning a teammate, not on the first turn.

### Test Strategy

| Layer | Target | Approach |
|---|---|---|
| Implementation | `auth.has_usable_credential()` | Monkeypatch env + `Path.exists` |
| Implementation | `_payload_to_prompt`, `_classify_error`, `_collect_response_text` | Direct calls |
| Implementation | `SdkTeammate` lifecycle | **Fake `ClaudeSDKClient`** — class with same async-ctx-manager shape, scripted `query`/`receive_response` |
| Implementation | Error envelope — mocked SDK exception | Fake client raises in `query()`; assert error envelope arrives |
| Implementation | Shutdown — close called once | Fake client tracks `__aexit__` invocations |
| Integration | MCP server with sdk_factory and fake SDK | Plug fake into the factory; drive tools through in-memory MCP harness |
| Integration | Auth validation startup behavior | Spawn server subprocess with no credentials; assert exit code 2 + stderr |
| Live (gated) | SC-4 deterministic UUID recall over 10+ turns | Real `ClaudeSDKClient`, gated `CLAUDE_CREW_LIVE_TESTS=1` |
| Live (gated) | SC-9 stdio smoke test | `scripts/sdk_smoke_test.py`, gated |

The fake `ClaudeSDKClient` is the workhorse. Spec:

```python
class FakeSDKClient:
    """Test double for ClaudeSDKClient. Async context manager.

    Configure with a list of canned responses (or callables) keyed by turn.
    Tracks: __aenter__/__aexit__ counts, query() prompts received, session_ids.
    """
    # Implementation in tests/fakes/sdk.py
```

Tests inject the fake by monkey-patching `claude_crew.sdk_teammate.ClaudeSDKClient` to point at it.

### Module Layout (additions)

```
claude_crew/
├── auth.py             ← NEW
├── factories.py        ← NEW
├── sdk_teammate.py     ← NEW
└── server.py           ← MODIFIED (factory plumbing + auth check)

tests/
├── fakes/
│   └── sdk.py          ← NEW: FakeSDKClient + helpers
├── test_auth.py        ← NEW
├── test_sdk_teammate.py ← NEW
├── test_factories.py   ← NEW
└── test_server.py      ← MODIFIED: existing tests pass factory=stub_factory

scripts/
├── smoke_test.py       (existing — uses stub mode)
└── sdk_smoke_test.py   ← NEW (gated, real API)

doc/research/
└── sdk-memory.md       ← NEW (SC-5 spike output)
```

### Cross-Feature Integration Check

What other code references the entities we're touching?

- **`make_server`** — called from `claude_crew.cli` (production entry) and `tests/test_server.py` (in-memory client). Both will keep working: production gets the new SDK default + auth check; tests opt into stub mode by env var or explicit `factory=`.
- **`StubTeammate`** — referenced from `tests/test_stub_teammate.py` and `tests/test_server.py`. Unchanged. The factory selector just chooses *between* it and `SdkTeammate`.
- **`Broker`, `Envelope`, `Teammate` ABC** — unchanged. The seam holds.

No other consumers exist (the codebase is small enough to grep manually). Verified by `grep -rn "make_server\|StubTeammate\|Broker\|Envelope\|Teammate" claude_crew/ tests/ scripts/`.

### SC-5 Spike: Plan for Memory Doc

The doc `doc/research/sdk-memory.md` records empirical results. Plan:

1. **Persistence within a session** — same UUID-recall test as SC-4 confirms it. Include the test code and result text.
2. **CLAUDE.md loading** — start a teammate with `setting_sources=["user","project"]`, ask "What does my CLAUDE.md say about X?" where X is something specific to `~/.claude/CLAUDE.md`. Confirm hit. Repeat with `setting_sources=None` and confirm miss.
3. **Auto-memory subsystem** — start a teammate, ask "What memories do you have access to?" Inspect whether the teammate references files under `~/.claude/projects/.../memory/`. Verify by cross-referencing the source code finding (it should not).

These three runs cost ~$0.10 total. Gated, run once, results captured.

### Design Decisions

- **Default `setting_sources=["user","project"]`.** *Why:* claude-crew teammates should feel like collaborators that already have access to standing user instructions, just like Claude Code does. Operators can override per-spawn in a future feature; for now it's the right default.
- **Per-turn error handling, not per-task.** *Why:* a single failed turn shouldn't kill the teammate's whole session — the persistent subprocess is expensive to recreate, and the user's reasonable expectation is "one bad turn, try again."
- **`session_id="default"` everywhere.** *Why:* one client instance = one teammate = one conversation. Multi-session per teammate is a future-feature ergonomics concern, not v1.
- **Auth check is fast and pre-flight only.** *Why:* don't gate on a successful API call (slow, costs money, may rate-limit) — gate on credentials being present in a place the SDK can find. Real auth failures still surface via the error envelope path on first send.
- **`CLAUDE_CREW_TEAMMATE_MODE` env var for factory selection.** *Why:* the lead is a Claude Code session; the env var travels through `claude mcp add` cleanly and is the simplest knob. Adding a config file is yagni for v1.

### Edge Cases

- **Empty payload to send_to** → `_payload_to_prompt` returns `""`; SDK accepts empty prompt? *Defensive:* if prompt is empty, send error envelope (`code=invalid_response`, `message="empty prompt"`) without invoking SDK.
- **Concurrent sends to same teammate** → broker FIFO serializes; the SdkTeammate's inbox loop processes one at a time. Each turn awaits `query` + `receive_response` to completion before pulling next from inbox. Correct: a single teammate is a single conversation.
- **Concurrent sends to different teammates** → separate inbox loops, separate subprocesses, parallel via asyncio. Each teammate's subprocess runs independently.
- **Subprocess crash mid-turn** → SDK raises `CLIConnectionError`; caught in `_handle_one_turn`; error envelope sent (`code=internal` or `api_error`). Per Q1 resolution: subsequent turns will *all* fail with the same error until the lead respawns the teammate. The teammate's worker task and `async with` block stay alive (per SC-6) and continue to drain the inbox, sending an error envelope per turn. v1 trades self-heal complexity for operator-driven respawn.
- **`receive_response()` never terminates** → `_handle_one_turn` wraps `_collect_response_text` in `asyncio.wait_for(timeout=TURN_TIMEOUT_SECONDS)`. Timeout produces `invalid_response` error envelope; the in-flight stream is cancelled. The next turn proceeds normally because `client.query()` writes to a fresh stream slot.
- **Lead sends after `kill_teammate`** → broker raises `UnknownTeammateError`; tool returns `{"error": "unknown_teammate"}`. Already covered by Feature #1.
- **Shutdown while a turn is in flight** → `_SHUTDOWN_SENTINEL` is queued behind the in-flight turn. `wait_for(timeout=5.0)` gives the in-flight turn a budget; if it doesn't finish, hard cancel. SDK subprocess gets SIGTERM via `__aexit__`. Correct under SC-7.
- **`max_turns` exhausted by SDK** → SDK raises or returns a `ResultMessage` with stop reason. `_collect_response_text` returns whatever it accumulated; if empty, error envelope (`code=invalid_response`).
- **Rate limit during stream** → `RateLimitEvent` arrives in `receive_response()`. `_collect_response_text` watches for it; if seen, send error envelope (`code=rate_limited`).

### Assumptions (default-accept)

- **Assumption A1** — `ClaudeSDKClient.__aexit__` reliably terminates the subprocess. If it doesn't on some failure path, SC-7's "no orphaned tasks" check will catch it. *Default:* trust the SDK; if a leak is observed during testing, add a watchdog that explicitly SIGKILLs.
- **Assumption A2** — Receiving an `AssistantMessage` with `TextBlock`s is sufficient to satisfy a turn. We ignore other block types (tool use, thinking) for v1 — tools land in Feature #3a, thinking surfaces (if at all) for observability in Feature #4.
- **Assumption A3** — Default `system_prompt` per role is a one-liner like `"You are a {role}. Help the lead with {role}-level work."` — just enough for the SDK to be polite. Better role prompts are Feature #3a.

### Resolved (formerly Open Questions)

- [x] **Q1 — Subprocess death = teammate death (no auto-recovery in v1).** Confirmed in SDK source (`subprocess_cli.py:573–581`): `ClaudeSDKClient.query()` checks `self._process.returncode` and raises `CLIConnectionError` if non-zero. `connect()` is only called once via `__aenter__`; there is no reconnection logic. Decision for v1: **accept. The error envelope path catches the first failed turn and the teammate continues to fail every subsequent turn until killed and respawned.** Adding a rebuild-on-death branch is a yagni follow-up. The lead can detect repeated `api_error`/`internal` errors from the same teammate and respawn it.
- [x] **Q2 — Existing test suite impact (Sentinel item 7).** Once `default_factory()` returns `sdk_factory` by default, `tests/test_server.py` and `tests/test_stub_teammate.py` need updates. Plan: add an autouse pytest fixture in `tests/conftest.py` that sets `CLAUDE_CREW_TEAMMATE_MODE=stub` for the existing test suite. New SDK tests opt into SDK mode explicitly (or pass `factory=` directly). This is the cleanest seam — keeps existing tests verbatim, isolates the new mode to its own files. Recorded here so Phase 3 task breakdown plans for it.

### FakeSDKClient Contract (test double specification)

To satisfy Sentinel's concern about test fidelity, the fake's contract is:

```python
class FakeSDKClient:
    """Async context manager that mimics ClaudeSDKClient's externally-observed contract."""

    def __init__(self, *, options=None, scripted_responses=None):
        # scripted_responses: list[list[Message] | Callable[[str], list[Message]]]
        # Each element is the response for the Nth query() call. Either a static
        # list of message objects (AssistantMessage / ResultMessage) or a callable
        # that takes the prompt string and returns one.
        self.queries_received: list[tuple[str, str]] = []  # (prompt, session_id)
        self.aenter_count = 0
        self.aexit_count = 0
        self._pending: list[Message] | None = None

    async def __aenter__(self): self.aenter_count += 1; return self
    async def __aexit__(self, *a): self.aexit_count += 1

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries_received.append((prompt, session_id))
        idx = len(self.queries_received) - 1
        spec = self._scripted[idx] if idx < len(self._scripted) else []
        self._pending = spec(prompt) if callable(spec) else list(spec)

    async def receive_response(self) -> AsyncIterator[Message]:
        # Yield exactly the messages set up by the most recent query().
        # Terminates after yielding (or when ResultMessage is yielded).
        for msg in (self._pending or []):
            yield msg
            if isinstance(msg, ResultMessage):
                return
        self._pending = None
```

**Key fidelity property:** each `query()` resets the stream for the next `receive_response()` iteration — matches SDK's behavior where messages produced by query N are consumed before query N+1 starts. Multi-turn tests that call `query()` then `receive_response()` then `query()` then `receive_response()` work the same way under the fake as under the real SDK.

**Configurable failure modes:**
- Pass a callable that raises → simulates `query()` failure
- Yield no `ResultMessage` and `_pending` exhausts → simulates dead-stream / hang scenario (caller's `wait_for` should fire)
- Yield a `RateLimitEvent` → tests the rate-limited code path

### What Phase 2 Does Not Resolve (intentional)

- Per-role default subagent packs — Feature #3a
- Streaming response delivery to the lead — out of scope, vision-deferred
- Token cost telemetry per teammate — Feature #4 / deferred
- Multi-session per teammate (`session_id` switching) — out of scope for v1

---

## Phase 3: Task Breakdown

Five tasks. Each independently testable, BDD scenarios trace back to Phase 1 SCs and Phase 2 edge cases.

---

### Task 1: Auth check + factory selection scaffolding
**Depends on**: None | **Blocks**: Tasks 2, 3

Lay the seam before plugging in the real teammate. New files: `claude_crew/auth.py`, `claude_crew/factories.py`, `tests/conftest.py`. Modify: `claude_crew/server.py` (factory plumbing).

**Acceptance Criteria**:
```
Scenario: Auth detection finds Claude Code OAuth credentials file
  Given ~/.claude/.credentials.json exists
  And ANTHROPIC_API_KEY is unset
  When has_usable_credential() is called
  Then it returns True

Scenario: Auth detection prefers ANTHROPIC_API_KEY env var
  Given ANTHROPIC_API_KEY is set
  When has_usable_credential() is called
  Then it returns True

Scenario: Auth detection finds nothing
  Given no ANTHROPIC_API_KEY, no CLAUDE_CODE_OAUTH_TOKEN, no .credentials.json
  When has_usable_credential() is called
  Then it returns False

Scenario: validate_auth_or_exit prints to stderr and exits with code 2 when no auth
  Given no usable credentials
  When validate_auth_or_exit() is called
  Then SystemExit(2) is raised
  And stderr mentions both "claude login" and "ANTHROPIC_API_KEY"

Scenario: make_server invokes auth check when factory.requires_auth is True
  Given a factory with requires_auth = True
  And no usable credentials
  When make_server(factory=factory) is called
  Then SystemExit(2) is raised

Scenario: make_server skips auth check for stub_factory
  Given stub_factory (requires_auth = False)
  And no usable credentials
  When make_server(factory=stub_factory) is called
  Then no exception is raised; server is constructed normally

Scenario: Existing test suite still passes after default factory flips to SDK
  Given conftest.py sets CLAUDE_CREW_TEAMMATE_MODE=stub via autouse fixture
  When the existing pytest suite runs (test_envelope, test_broker,
       test_stub_teammate, test_server)
  Then all 49 prior tests pass without modification beyond the conftest fixture
```

**Verification**: `uv run pytest tests/test_auth.py tests/test_factories.py tests/test_envelope.py tests/test_broker.py tests/test_stub_teammate.py tests/test_server.py` — all pass; no SDK calls made.

---

### Task 2: SdkTeammate + FakeSDKClient + implementation tests
**Depends on**: Task 1 | **Blocks**: Tasks 3, 5

The core. New files: `claude_crew/sdk_teammate.py`, `tests/fakes/__init__.py`, `tests/fakes/sdk.py`, `tests/test_sdk_teammate.py`.

**Acceptance Criteria**:
```
Scenario: Round-trip through fake SDK delivers response envelope
  Given an SdkTeammate spawned with FakeSDKClient scripted to reply "hi back"
  When the broker delivers a payload "hello" to the teammate
  Then the lead receives an envelope with payload {"text": "hi back",
       "from": <role>}
  And the fake client recorded one query with prompt "hello"

Scenario: Multi-turn maintains separate query/response per turn
  Given an SdkTeammate with fake scripted for 3 different responses
  When the broker delivers 3 messages in sequence
  Then 3 response envelopes arrive at the lead in order
  And the fake recorded 3 queries with the correct session_id "default"

Scenario: SDK exception during query produces error envelope, loop continues
  Given an SdkTeammate with fake whose first query() raises an exception
  And the fake's second query() succeeds normally
  When the broker delivers two messages
  Then envelope 1 has payload {"error": <code>, "message": ..., "from": <role>}
  And envelope 2 has payload {"text": <success text>, "from": <role>}
  And the teammate's worker task remains alive throughout

Scenario: Empty prompt is rejected without invoking SDK
  Given an SdkTeammate
  When the broker delivers an envelope with payload ""
  Then the lead receives an error envelope with code "invalid_response"
  And the fake recorded zero queries

Scenario: Stream that never terminates produces timeout error envelope
  Given an SdkTeammate with TURN_TIMEOUT_SECONDS patched to 0.1
  And a fake that yields messages but never a ResultMessage
  When the broker delivers a message
  Then within ~0.5s the lead receives an error envelope with code
       "invalid_response" and message mentioning "timeout"
  And the next delivered message processes normally

Scenario: Empty text response produces invalid_response error
  Given a fake that yields only tool-use blocks (no TextBlock)
  When the broker delivers a message
  Then the lead receives an error envelope with code "invalid_response"

Scenario: Shutdown calls __aexit__ on the SDK client exactly once
  Given an SdkTeammate with FakeSDKClient
  When the teammate is killed
  Then fake.aexit_count == 1
  And asyncio.all_tasks() shows no SdkTeammate-named tasks alive

Scenario: Shutdown while turn is in flight respects 5s timeout
  Given an SdkTeammate with a fake that hangs forever in receive_response()
  When kill_teammate is called while a turn is in flight
  Then kill_teammate returns within 6 seconds
  And the worker task is done()
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py` — all pass; no real SDK calls.

---

### Task 3: MCP server SDK-mode integration + auth-failure subprocess test
**Depends on**: Tasks 1, 2 | **Blocks**: Task 5

Integration tests through the actual MCP harness with the SDK factory wired in (using the fake). One test spawns the server as a subprocess with no auth and asserts exit code 2. New file: `tests/test_server_sdk_mode.py`.

**Acceptance Criteria**:
```
Scenario: spawn → send_to → get_messages with SDK factory + fake
  Given make_server(factory=fake_sdk_factory) wired through the in-memory
       MCP harness
  When the lead calls spawn_teammate(role="planner")
  And calls send_to(teammate_id, "what is 2+2?")
  And polls get_messages
  Then the response envelope has payload {"text": <fake's scripted reply>,
       "from": "planner"}

Scenario: list_crew shows SDK teammates the same as stub teammates
  Given two SDK teammates spawned via the MCP tool
  When the lead calls list_crew
  Then teammates contains both, with roles intact

Scenario: kill_teammate cleanly tears down SDK teammate
  Given an SDK teammate
  When the lead calls kill_teammate
  Then the response is {"ok": true}
  And subsequent send_to to that teammate returns
       {"error": "unknown_teammate"}

Scenario: Server subprocess refuses to start without auth in SDK mode
  Given env: ANTHROPIC_API_KEY unset, CLAUDE_CODE_OAUTH_TOKEN unset,
       HOME pointed at a tempdir without .claude/.credentials.json,
       CLAUDE_CREW_TEAMMATE_MODE=sdk (or unset; sdk is default)
  When the claude-crew console script is invoked
  Then it exits with code 2 within 2 seconds
  And stderr contains "claude login" and "ANTHROPIC_API_KEY"

Scenario: Server subprocess starts cleanly in stub mode without auth
  Given env: no auth, CLAUDE_CREW_TEAMMATE_MODE=stub
  When the claude-crew console script is invoked
  Then it does not exit; it serves on stdio
  And a basic spawn_teammate / get_messages exchange succeeds
```

**Verification**: `uv run pytest tests/test_server_sdk_mode.py` — all pass.

---

### Task 4: SC-5 spike + memory doc + gated live SC-4 persistence test
**Depends on**: Task 2 | **Blocks**: None
**Cost**: ~$0.10–0.50 in API calls when run live

Real API. Gated behind `CLAUDE_CREW_LIVE_TESTS=1`. Produces `doc/research/sdk-memory.md` recording empirical findings on the three memory questions. New file: `tests/test_live_sdk.py`.

**Acceptance Criteria**:
```
Scenario: Live 10-turn UUID recall (SC-4)
  Given CLAUDE_CREW_LIVE_TESTS=1
  And usable Claude credentials
  And an SdkTeammate spawned with model="claude-sonnet-4-6"
  And a freshly generated UUID v4 string
  When turn 1 sends "Remember this exactly: <uuid>. I'll quiz you later."
  And turns 2-9 send unrelated chat ("What is 17 * 23?", "Name three rivers",
      etc.)
  And turn 10 sends "Repeat the exact UUID I asked you to remember on turn 1"
  Then turn 10's response contains the UUID as an exact substring

Scenario: Live CLAUDE.md loading via setting_sources
  Given CLAUDE_CREW_LIVE_TESTS=1
  And usable Claude credentials
  And an SdkTeammate spawned with setting_sources=["user", "project"]
  When sent "What name does my user-level CLAUDE.md tell you to call me?"
  Then the response contains "Jerome" or "Kael"
       (one of the strings written into ~/.claude/CLAUDE.md)

Scenario: Live CLAUDE.md NOT loaded by default
  Given CLAUDE_CREW_LIVE_TESTS=1
  And an SdkTeammate spawned with setting_sources=None
  When sent "What name does my user-level CLAUDE.md tell you to call me?"
  Then the response does NOT contain "Jerome" or "Kael"
       (or explicitly says it has no access to such information)

Scenario: doc/research/sdk-memory.md exists and records findings
  When `cat doc/research/sdk-memory.md`
  Then the file contains sections for:
       (1) conversation persistence within a session
       (2) CLAUDE.md loading behavior with and without setting_sources
       (3) auto-memory subsystem (~/.claude/projects/.../memory/) status
  And each section cites at least one source-code reference and one empirical
      observation
```

**Verification**:
- Without gate: `CLAUDE_CREW_LIVE_TESTS=` `uv run pytest tests/test_live_sdk.py` → tests skip cleanly with reason "live API gated"
- With gate: `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py` → all pass; doc/research/sdk-memory.md is updated/present

---

### Task 5: End-to-end stdio smoke test (gated, real API)
**Depends on**: Tasks 1, 2, 3 | **Blocks**: None

Real subprocess, real MCP stdio transport, real API. The proof that the registered console script + SDK mode + auth all work together in production shape. New file: `scripts/sdk_smoke_test.py`.

**Acceptance Criteria**:
```
Scenario: Live stdio round-trip via the registered console script
  Given CLAUDE_CREW_LIVE_TESTS=1
  And usable Claude credentials
  When `uv run python scripts/sdk_smoke_test.py` is invoked
  Then the script:
       (1) spawns claude-crew as a subprocess via stdio_client
       (2) sends initialize, list_tools (asserts the same 6 tools)
       (3) calls spawn_teammate(role="parrot")
       (4) sends 3 messages; for each, polls get_messages and prints
           the teammate's reply
       (5) calls kill_teammate
       (6) exits 0 with "✓ smoke test passed" on stdout

Scenario: Smoke script with no live gate prints a clear skip message
  Given CLAUDE_CREW_LIVE_TESTS unset
  When the script is invoked
  Then it exits 0 quickly with stdout: "skipped (set CLAUDE_CREW_LIVE_TESTS=1)"
  And no subprocess is spawned, no API call is made
```

**Verification**:
- Without gate: `uv run python scripts/sdk_smoke_test.py` → "skipped"
- With gate: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/sdk_smoke_test.py` → exits 0, prints success

---

### Coverage trace

Every Phase 1 SC and every Phase 2 edge case is covered by at least one scenario above:

| SC / Edge case | Task / Scenario |
|---|---|
| SC-1 (SdkTeammate wraps client) | T2: round-trip + lifecycle scenarios |
| SC-2 (factory selection works) | T1: factory + auth scenarios; T3: integration |
| SC-3 (round-trip works) | T2: round-trip; T3: SDK-mode integration |
| SC-4 (10-turn UUID recall) | T4: live UUID recall scenario |
| SC-5 (memory doc) | T4: doc-exists scenario + recall + setting_sources scenarios |
| SC-6 (errors don't crash) | T2: SDK exception, empty prompt, timeout, empty text |
| SC-7 (clean shutdown, close once) | T2: aexit_count, in-flight shutdown |
| SC-8 (auth validated at startup) | T1: validate_auth scenarios; T3: subprocess-no-auth |
| SC-9 (live stdio smoke) | T5: live stdio scenario |
| Edge: empty payload | T2: empty prompt scenario |
| Edge: subprocess crash → continued errors | T2: SDK exception scenario (loop continues, error envelope per turn) |
| Edge: receive_response never terminates | T2: timeout scenario |
| Edge: rate limit | covered by T2 SDK-exception family (RateLimitedError raises) |
| Edge: shutdown mid-turn | T2: shutdown-during-turn scenario |
| Existing tests don't break | T1: conftest stub-mode fixture scenario |

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task (T5) with happy + sad (skip) coverage
- ✅ Verification commands fail without the feature
- ✅ Every Phase 2 edge case traces to a scenario
- ✅ User approved

---

## Phase 4: Implementation

*To be filled during SDD Phase 4.*

---

## Phase 5: Completion

*To be filled during SDD Phase 5.*
