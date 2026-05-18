# Feature: Lead-Scoped Broker Registry

**Status**: Design locked — ready to plan
**Created**: 2026-05-17
**Updated**: 2026-05-17 (post Agent-Teams scoping clarification + identity/hooks research)

## Problem

The broker assumes a single lead session. Its registry, inbox routing, and tool surface all behave as if there's exactly one process calling `spawn_teammate`, `send_to`, `list_crew`, etc. This holds for the original use case (one Claude Code session driving a crew) but breaks for the **conductor-with-coordinators** pattern, where one operator wants to run multiple top-level Claude sessions that each independently drive their own crew through the same MCP server.

The specific trigger: Claude Code's **Agent Teams** feature spawns N top-level agents inside one CLI process. They **multiplex through one shared MCP client connection**, so the broker cannot distinguish callers at the transport layer. Every tool call looks like it came from the same client. Verified live 2026-05-17.

Concretely, when N peer top-level agents (e.g., a Kael "conductor" + multiple `crew-coordinator` agents) share one CLI and one MCP connection:

- `list_crew` returns every teammate across every lead, with no way to filter
- `broadcast` hits every teammate, including those owned by other leads
- `send_to` requires the caller to know names spawned by other leads (no namespace separation)
- Teammate name collisions are possible across leads (the broker rejects duplicates globally, but a lead has no way to know what names are already taken without seeing other leads' state)
- A lead that crashes leaves orphaned teammates the surviving leads have no claim over

Today's workaround is "have one lead." That works only because Agent Teams isn't part of the typical claude-crew workflow. As soon as it is, the broker becomes a shared-mutable-state problem.

## Goal

Scope broker operations by the identity of the calling lead. Each lead sees, addresses, and controls **only** its own teammates — strict siloing. No cross-lead visibility, no cross-lead addressing, no orphan adoption. The MCP-instance boundary used to provide this scoping for free; with Agent Teams, we recreate it inside one MCP instance.

## Design

### Lead identity

FastMCP does **not** expose per-connection identity, and Agent Teams members share one MCP connection — so transport-level identification is not viable. Identity must be **explicitly self-declared** via a handshake tool.

Each lead, at startup, calls:

```
register_lead(name=$CLAUDE_CODE_AGENT, session_id=$CLAUDE_CODE_SESSION_ID)
```

- `CLAUDE_CODE_AGENT` — human-readable agent name from `~/.claude/agents/<name>.md` (e.g., `kael`, `crew-coordinator`). Set by the CLI when launched via `--agent=<name>`.
- `CLAUDE_CODE_SESSION_ID` — UUID, stable per top-level agent process. Always present.

The broker keys ownership internally by `session_id` (guaranteed unique). `name` is the human label used in `list_crew` output and the dashboard.

**Fallback**: if `CLAUDE_CODE_AGENT` is unset (plain `claude` session, no `--agent=`), the lead passes `session_id` as the name. Single-lead operation degenerates to today's behavior.

**Implicit lead registration**: the first tool call from an unregistered connection auto-creates an implicit lead keyed by session_id, preserving the existing single-lead UX (no breaking change for scripts that just call `spawn_teammate` and go). Explicit `register_lead` is required only when the operator wants a human-readable label.

### Scope semantics (strict siloing)

Every teammate is tagged with `owner_session_id` at `spawn_teammate` time. All 8 broker tools are implicitly lead-scoped at the entry point. There is **no** `scope=` arg, **no** cross-lead addressing, **no** opt-in escape hatch:

- `list_crew` returns only the caller's own teammates
- `send_to` can only target teammates owned by the caller; addressing a teammate owned by another lead returns `unknown teammate` (same error as a typo — the other lead's teammates are invisible)
- `broadcast` fans out only to the caller's own teammates
- `kill_teammate` only acts on the caller's own teammates
- `get_messages` returns only inbox messages addressed to the calling lead
- `get_teammate_status` and `get_transcript_path` only resolve teammates the caller owns

### Teammate name uniqueness

Names are unique **within a lead's scope**, not globally. Registry key is `(owner_session_id, teammate_name)`. Two leads can each spawn a teammate named `explorer-1` without collision; each lead resolves `explorer-1` to its own.

### Lead lifecycle

When a lead's CC session ends (clean exit or crash), all of its teammates are **automatically reaped**. No orphaned-teammates state, no adoption, no `transfer_teammate`. Strict ownership means lead death = crew death.

**Mechanism**: a `SessionEnd` hook configured in `~/.claude/settings.json` fires on every top-level agent termination and invokes a new CLI subcommand:

```
claude-crew unregister-lead <session_id>
```

`unregister-lead` is a short-lived client that connects to the running broker via a **Unix socket** (at `$XDG_STATE_HOME/claude-crew/broker.sock` or equivalent) and sends a one-shot "reap this session_id" message. The broker:

1. Looks up all teammates owned by `session_id`
2. Kills each (existing tombstone discipline applies — they show as dead in transcripts)
3. Removes the lead from its registry

The hook config is installed globally in `~/.claude/settings.json` so it fires for every CC session. Sessions that never registered a lead are a no-op (broker has nothing to reap).

**Why a Unix socket + CLI subcommand, not HTTP or direct MCP**:
- MCP is stdio — the hook is a shell command and can't speak MCP
- HTTP requires port allocation and conflict management
- Unix socket lives in user state dir, uses filesystem permissions for auth, no port
- The CLI subcommand keeps the user-facing contract clean (one binary, one command); transport internals can change without users editing hook configs

### Dashboard

Mission Control dashboard groups teammates by lead. Each registered lead is a column (or section), mirroring how distinct broker processes appear today. Orphan handling is N/A — there are no orphans by design.

### Backwards compatibility

A broker with one implicit lead operates exactly as today: no `register_lead` call needed, no `unregister-lead` hook needed (sessions just exit and the broker outlives them; teammates persist until killed or the broker stops). The implicit-lead fallback (first call auto-registers by session_id) means existing scripts and tests run unchanged.

Existing test suites stay green because they spawn one teammate-set per broker — single implicit lead, no scoping observable.

## Acceptance Criteria

- `register_lead(name, session_id)` registers a lead; first tool call from an unregistered session auto-registers an implicit lead keyed by session_id
- A teammate spawned by lead A is not visible to lead B via `list_crew`, `send_to`, `broadcast`, `kill_teammate`, `get_messages`, `get_teammate_status`, or `get_transcript_path`
- `send_to` from lead A targeting a lead-B-owned teammate returns `unknown teammate` (identical to a typo — no information leak about other leads' state)
- `broadcast` from lead A reaches only lead A's teammates
- Two leads can each spawn a teammate named `explorer-1` without collision; each lead resolves `explorer-1` to its own
- `claude-crew unregister-lead <session_id>` reaps all teammates owned by that session and removes the lead from the registry
- `SessionEnd` hook in `~/.claude/settings.json` triggers `unregister-lead` automatically on lead termination (clean exit and crash)
- Sessions that never registered a lead are a no-op for `unregister-lead`
- Single-lead operation (implicit registration, no hook needed) is byte-for-byte unchanged — no test regressions
- Mission Control dashboard groups teammates by lead, mirroring the multi-broker layout

## Resolved Questions

- **FastMCP per-connection identity** — not exposed; Agent Teams members multiplex one connection. Resolution: explicit `register_lead` handshake. (Verified 2026-05-17.)
- **Lead identity source** — `CLAUDE_CODE_AGENT` env var (name) + `CLAUDE_CODE_SESSION_ID` (UUID). Broker keys by session_id; name is the human label. Fallback to session_id when `CLAUDE_CODE_AGENT` is unset.
- **Cross-lead `send_to`, `broadcast`, adoption** — dropped. Strict siloing. Aligns with the conductor-in-the-loop principle: leads coordinate through the operator, not through each other's teammates.
- **`transfer_teammate` / orphan adoption** — dropped. Lead death = crew death.
- **Permission model** — N/A. No cross-lead surface to permission.
- **Lead termination detection** — `SessionEnd` hook in `~/.claude/settings.json` (fires on clean exit and crash) invokes `claude-crew unregister-lead <session_id>` via Unix socket to the running broker.

## Open Questions

- **`SessionEnd` reliability under abrupt kill** — does the hook fire on `SIGKILL` or only `SIGTERM`/clean shutdown? If it can be missed, broker needs a secondary stale-lead reaper (e.g., periodic liveness probe matching the existing dead-teammate pattern).
- **Implicit-lead UX** — should `list_crew` show implicit leads with a synthetic name (e.g., `<session-abc12345>`) or hide them as anonymous? Affects dashboard readability for the single-lead default case.
- **Hook installation** — auto-install on `claude-crew` setup, or document and let the operator add it? Auto-install is friendlier but touches `~/.claude/settings.json` which is operator-owned territory.

## Related

- FEATURE-multi-instance-registry — solves cross-process aggregation; this feature solves cross-lead-within-one-process scoping. Composable.
- Agent Teams (Claude Code experimental feature) — the trigger for this work; not a dependency, since the same problem appears any time two Claude sessions share one MCP server.

## Why Now

Agent Teams is live and active in operator sessions. The moment a Kael conductor + any `crew-coordinator` peer both call `spawn_teammate` against the same broker, they collide in a shared registry with no way to scope ownership. The conductor + claude-crew-teammate-coordinator workaround (Option A, 2026-05-17) is a temporary detour; this feature is the load-bearing fix for the conductor + Agent-Teams-coordinator pattern that the Kael identity is built around.
