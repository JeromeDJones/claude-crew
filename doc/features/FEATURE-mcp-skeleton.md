# Feature: MCP Server Skeleton + Tool Surface

**Status**: Planning
**Created**: 2026-04-25
**Maps to**: PRODUCT-VISION.md Feature Pipeline #1, Capability #1, Success Criterion #1

---

## Phase 1: Research & Requirements

### Problem Statement

claude-crew's foundation is an MCP server that the lead Claude Code session uses to spawn, address, and supervise a crew of teammates. Before any real Agent-SDK teammates exist (Feature #2), we need the bus protocol and the lead's tool surface in place — running, registered, callable, and validated end-to-end with stub teammates.

This feature is the substrate every other MVP feature builds on. Without it:
- Feature #2 has nothing to attach `ClaudeSDKClient` instances to
- Feature #3a/#3b has no surface to expose subagent configs through
- Feature #4 has no message stream to transcribe
- Feature #5 has nothing to validate against

The deliberate sequence is: prove the bus shape with stubs first, then plug in real agents. If the tool surface or message envelopes need to change, we'd rather find that out before we've built the SDK runtime layer on top.

### Success Criteria

- [ ] An MCP server runs over stdio and registers with Claude Code via `claude mcp add`
- [ ] The lead can call `spawn_teammate(role, name)` and receive a teammate id; the teammate is a stub that echoes any messages it receives
- [ ] The lead can call `send_to(teammate_id, payload)` and the message is delivered to the stub teammate
- [ ] The lead can call `get_messages(since=...)` and retrieve all messages the lead is the recipient of, in order, with no duplicates
- [ ] The lead can call `broadcast(payload)` and all teammates receive it
- [ ] The lead can call `list_crew()` and see all currently spawned teammates with their roles
- [ ] The lead can call `kill_teammate(teammate_id)` and the teammate is removed; subsequent sends to that id return a clear error
- [ ] Each message has a structured envelope: `id` (uuid), `sender`, `recipient`, `timestamp`, `payload`. Duplicates by `id` are silently deduplicated by the broker.
- [ ] All seven tools have implementation-level tests (the broker's behavior in isolation) and integration-level tests (the tools called through the MCP server)

### Questions
- [ ] **Process model for stub teammates** — does each stub teammate live as an in-process asyncio task in the MCP server, or as a separate subprocess? Default lean: in-process asyncio task for v1 — no need for OS-level isolation when teammates are stubs, and the SDK runtime in Feature #2 also runs in-process. Confirm during design phase.
- [ ] **Message ordering guarantee** — strict per-recipient FIFO, or just causal? Default lean: per-recipient FIFO (simpler, matches user mental model).
- [ ] **`get_messages` pagination / cursor semantics** — does the lead pass `since=<message_id>` or `since=<timestamp>`? Default lean: monotonic integer sequence per crew, cursor is the last seen sequence number. Easier to reason about than timestamps.

### Constraints & Dependencies

- **Python 3.12+**, `mcp[cli]` SDK (already installed in `~/dev/claude-crew/spikes/mcp-pubsub-spike` — same SDK applies here)
- **stdio transport only** for v1 (matches Claude Code's `claude mcp add -- <cmd>` registration model)
- **Local-machine only.** No network transport, no auth, no remote brokers. Trust boundary is the local user.
- **No persistence across MCP server restarts** in this feature. The crew lives for the lifetime of the lead's session. Cross-session persistence is a v2 question.
- **No real Agent SDK runtime yet** — this feature uses stub teammates only. The seam where teammates plug in must be clean enough that Feature #2 can swap stubs for `ClaudeSDKClient` instances without changing the tool surface.
- **Breaking changes**: N/A (greenfield)
- **Performance implications**: The bus is in-process and message volume is low (human-driven lead, a few messages per turn). No throughput concerns for v1.

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
