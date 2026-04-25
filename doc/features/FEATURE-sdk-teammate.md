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

*To be filled during SDD Phase 2.*

---

## Phase 3: Task Breakdown

*To be filled during SDD Phase 3.*

---

## Phase 4: Implementation

*To be filled during SDD Phase 4.*

---

## Phase 5: Completion

*To be filled during SDD Phase 5.*
