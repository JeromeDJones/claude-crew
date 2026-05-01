# Product Vision: claude-crew

**Created**: 2026-04-25
**Last Updated**: 2026-04-30
**Features Implemented**: 14 + post-#13 polish + per-agent dashboard tokens (MVP + #6 telemetry-based liveness + #7 subagent-activity envelopes + #8 tool-execution telemetry + #9 get_messages long-poll + #10 agent-config-extension + #11 lightweight-subagent-context + #12 mission-control-ui + #13 multi-instance-registry + leader election + race-free port binding + dashboard UX polish + #14 token/cost telemetry)
**Next up**: #18 broker snapshot + dashboard polish *(recommended next)* · #19 tool-use events in dashboard · #16 message kind typing · #15 expanded subagent pack · #17 agent definition parity · #20 peer messaging between teammates *(idea)*

---

## Vision & Purpose

*What is this product? What problem does it solve? What's the north star?*

claude-crew is a local multi-agent orchestrator. A Claude Code session you drive — the **lead** — spawns a crew of Agent-SDK-driven Claude instances that take on specialized roles, can themselves spawn subagents for focused work, and coordinate through an MCP server that acts as supervisor, message bus, and observability surface. The crew works a single problem together; the lead is your interface to it.

A single machine can run multiple independent crews in parallel — each its own lead plus SDK helpers, each on a different problem in a different area of the code — without coordination overhead between them. claude-crew exists because Claude Code can't natively coordinate parallel peer Claudes, and the existing workarounds (tmux send-keys, Anthropic's experimental Agent Teams) either compromise reliability or accept hard scope limits like "one team per session, no nesting."

**claude-crew is foundational, not a finished workflow product.** It is the runtime layer — bus, lifecycle, persistence, observability — that other products are built *on top of*. RepoReactor (planner/builder/reviewer for software development) is one such product. A research crew, a content crew, an ops crew would each be others. claude-crew provides primitives; the role definitions, system prompts, and team workflows live a layer up. Agent Teams is the closest analogue from Anthropic, but it bundles runtime + workflow + UI into one opinionated stack; claude-crew unbundles them and exposes the runtime as a clean substrate.

**North star:** A developer runs two crews side-by-side on one workstation. Crew A is RepoReactor in its sandboxed Docker container, three SDK agents in planner/builder/reviewer roles, working on a feature in their codebase. Crew B is a separate lead with three SDK agents debugging a production issue in a different repo. Both crews' internal conversations stream into a live UI the developer can glance at without context-switching. The orchestration is solid enough that the developer ships personal projects on it *and* deploys it at work. The message bus is a first-class primitive, not a workaround.

> **Note on implementation lock-in:** Agent SDK is the v1 sub-agent runtime. A future adapter layer can let other runtimes (headless Claude Code, custom processes, other LLM providers) join a crew through the same bus without redesigning the protocol. Out of scope for v1, designed-for in the bus contract.

---

## Target Users & Needs

### Primary Users

Working software developers who already use Claude Code as their daily AI development environment. The operator profile spans personal use (Jerome at home, on side projects), professional use (Jerome at work, or any developer at their employer), and through that range to anyone who has hit Claude Code's coordination ceiling and wants a way past it without giving up the Claude Code UX they already know.

Not "everyone who codes." The user is someone deep enough into Claude Code to have felt its multi-agent limits and frustrated enough to want a real fix.

### Pain Points

- **Subagents can't spawn subagents.** Claude Code's subagent model bottoms out at one level — a planner subagent can't itself delegate research, exploration, or focused implementation to its own subagents. Recursive decomposition stops dead, which caps how deeply any single role can scope and divide its own work.
- **Agent Teams members can't spawn subagents either, and they're expensive.** Anthropic's official multi-agent answer has the same recursion ceiling, plus the cost of running multiple full Claude Code sessions concurrently — both in tokens and in operational overhead from the experimental flag's known limits.
- **The tmux send-keys workaround isn't enterprise-grade.** It works for tinkering: fragile ANSI parsing, terminal-environment lock-in, prompt collisions when idle-detection lies. Not something you build a real product on. A serious team needs a serious primitive.

### Desired Outcome

After adopting claude-crew, a developer can:

- **Write skills that use persistent teammates** — agents that hold context across many exchanges within a coding session, accumulating memory across the work, instead of ephemeral one-shot subagents that lose state between invocations.
- **Spawn each teammate as a specifically-roled agent** (analogous to a `claude --agent <name>` invocation) with its own system prompt, memory, model choice, and specialty — different roles, different specializations, different prompts.
- **Let those teammates recursively spawn their own subagents** for focused exploration, planning, review, and research — without the one-level recursion ceiling Claude Code imposes.
- **Watch the crew's conversation live** to debug behavior, learn what prompts and team shapes work, and refine the team over time as a byproduct of using it.
- **Run all of this reliably enough to use at work** — not as a personal experiment, but as a tool a serious developer trusts on real production codebases.

---

## Success Criteria

*How do you know this product is working? Every feature should advance at least one of these.*

| # | Criterion | How to Measure | Status |
|---|-----------|---------------|--------|
| 1 | A lead can spawn N persistent role-specialized teammates and exchange messages with them across a session, each teammate holding context across all exchanges | Scripted session with 3 teammates, 10+ exchanges each, teammates correctly reference earlier exchanges | Not started |
| 2 | A teammate can recursively spawn its own subagents for focused work | Planner teammate spawns explorer + researcher subagents during a real planning task | Not started |
| 3 | A developer can run two independent crews concurrently on one machine without interference | Two crews on different repos, both completing tasks, no message bleed between them | **Structurally met (2026-04-30)** — Multi-instance registry (#13) uses per-instance XDG files (keyed by crew_id) with PID liveness; two instances can co-exist without message bleed or registry corruption. Validated scenario (two real crews completing tasks end-to-end) is still a deferred v2 proof point. |
| 4 | The full crew conversation (lead ↔ teammates ↔ subagents) is observable in real time for debugging and learning | Live observability surface shows messages flowing across all crews; operator can identify a misbehaving prompt by reading the transcript | **Substantially met (2026-04-30)** — Mission Control UI (#12) ships a real-time browser dashboard. #13 extends it to all running instances: each peer's agents, status, and transcript stream into the same dashboard via server-side HTTP fanout. Token/cost tracking and crew-level filtering remain deferred. |
| 5 | The system runs reliably enough to use on real work — completes a non-trivial task end-to-end without operator intervention beyond directing the lead | Successful real-task runs at home and at work, instrumented with a "needed manual rescue?" flag | **Met (home, 2026-04-26)** — MMM-35 backend slice shipped via SDD with claude-crew teammates, all 8 tripwires clean, ~$15-20 spend. Substrate findings captured for next-pass improvements. |

**Guidance for writing criteria:**
- Frame as outcomes, not outputs ("users can X" not "build feature Y")
- Include both leading indicators (usage, engagement) and lagging indicators (retention, growth)
- 3-5 criteria is the sweet spot
- Review and update in Phase 4 after each feature ships

---

## Core Capabilities

*The essential functional pillars that define what this product does. Not a feature list — these are the broad capability areas that set the product's scope. Ordered by priority.*

### 1. MCP-supervised crew with persistent role-specialized teammates

*The product must be able to spawn, address, and persist a crew of role-specialized teammates that a lead Claude Code session drives through MCP tools.*

This is the foundational capability — the substrate everything else rides on. An MCP server registered with the lead exposes a tool surface (`spawn_teammate`, `send_to`, `broadcast`, `list_crew`, `get_messages`, `wait_for_messages`, `kill_teammate`) and owns the lifecycle of N Agent-SDK-driven teammates. Each teammate is configured with role, system prompt, model, tool set, and its own persistent context that survives across many exchanges within a session. Messages flow through the bus with structured envelopes (sender, recipient, payload, unique id for dedup). The lead drives interaction via tools; inbound delivery to the lead is polling-first (the lead asks), with a long-poll `wait_for_messages` for active sync points.

**In scope:** the bus protocol, the tool surface, the lifecycle, persistent teammate context, message envelopes with dedup.
**Out of scope for v1:** lead-side hooks for ambient delivery (planned for v2 — polling is the v1 contract), authentication, network transport (local Unix-socket or in-process only).

### 2. Recursive subagent decomposition within teammates

*The product must be able to let teammates spawn their own subagents for focused work — exploration, planning, review, research.*

This is the capability that breaks Claude Code's one-level recursion ceiling. Each teammate, being an Agent-SDK agent, is configured with the Task tool and a set of subagent definitions appropriate to its role (a planner gets `explorer` and `researcher`; a builder gets `codebase-archaeologist` and `runner`; a reviewer gets `independent-checker`). When a teammate calls `Task`, the SDK spawns a child agent loop with its own context, runs it to completion, returns the result. Subagents are leaf nodes — they don't themselves spawn further. (We could relax that later but the v1 contract is two levels: lead → teammate → subagent.)

**In scope:** subagent definitions per role, Task-tool wiring, subagent results visible in the crew's transcript for observability.
**Out of scope for v1:** more than two levels of recursion, subagent-to-subagent messaging, dynamic subagent definition.

### 3. Multi-crew concurrency on one host

*The product must be able to run multiple independent crews side-by-side on a single machine without interference.*

Each crew is its own MCP server instance bound to its own lead. Crews are isolated by default — different process trees, different message buses, different transcripts. Two leads on the same machine each have their own claude-crew, each spawn their own teammates, and the systems do not see or interfere with each other unless explicitly configured to. RepoReactor running in its sandboxed Docker container is one such crew; an unrelated crew working a different repo runs alongside it.

**In scope:** per-crew isolation, ability to identify and watch any active crew, no cross-talk by default.
**Out of scope for v1:** cross-crew messaging or coordination (deferred — interesting in v2+).

### 4. Live observability across all crews

*The product must be able to surface the full crew conversation (lead ↔ teammates ↔ subagents) in real time for debugging, learning, and trust-building.*

The bus already sees every message. Observability is exposing it: a JSONL transcript file per crew is the v1 floor; a live UI (TUI or web) showing all active crews simultaneously, filterable by role and crew, is the v1 ceiling. The use case is dual: debugging when something misbehaves, and learning what prompts and team shapes actually work. Without this, users can't refine their crews — they can only guess.

**In scope:** structured JSONL transcript per crew, multi-crew live UI, basic filtering and search.
**Out of scope for v1:** transcript export tooling, replay/scrubbing, post-hoc analytics dashboards.

---

## Differentiators & Constraints

### What Makes This Different

- **Foundational, not opinionated.** claude-crew provides runtime primitives (bus, lifecycle, persistence, observability). It does not bake in roles, workflows, or methodologies. RepoReactor and similar products live a layer up.
- **Recursive subagent decomposition by default.** Teammates can spawn their own subagents — the limitation that makes Claude Code's native subagents and Agent Teams members feel underpowered for serious work.
- **Persistent teammate context across many exchanges.** Teammates aren't ephemeral one-shots; they hold state across the session, accumulating memory the way a human collaborator would.
- **Multi-crew on one host as a first-class shape.** Run two or more crews on different problems concurrently without interference, instead of being capped at "one team per session."
- **Observability as a first-class output, not a debugging afterthought.** Structured transcripts and a live UI make the crew's behavior legible — both for debugging and for learning what works.
- **Adapter-ready bus contract.** The protocol is defined separately from the SDK runtime so future runtimes (headless Claude Code, custom processes, other LLM providers) can join without redesigning the bus.

### Alternatives & Landscape

| Alternative | Strength | Gap claude-crew Fills |
|---|---|---|
| **Claude Agent Teams** (Anthropic, experimental) | Built into Claude Code; first-class wake mechanism; shared task list | Single team per session, no nesting, no recursive subagents, opinionated stack you can't unbundle, experimental flag with known limits |
| **tmux send-keys orchestrators** (Tmux-Orchestrator, amux, primeline-ai/claude-tmux-orchestration) | Works today on actual Claude Code instances | Fragile ANSI parsing, terminal-only, prompt-collision risk, not enterprise-grade — fine for tinkering, not for serious products |
| **Claude Agent SDK directly** | Full programmatic control of agent loops | You'd reimplement the bus, lifecycle, multi-crew isolation, and observability yourself — that's exactly what claude-crew packages |
| **BMAD framework** | Strong methodology and prompt scaffolding for agile workflow | Single-session prompt engineering — does not address parallel coordination at all. Complementary, not competitive. |
| **Custom orchestrators built per project** | Tailored to one team's needs | Fragmentation, no shared substrate, every team rebuilds the same primitives |
| **Doing it manually with two terminals** | Zero infrastructure | No coordination, no shared memory, no observability, doesn't scale past two |

claude-crew is *not* an alternative to RepoReactor, BMAD, or workflow products in general — those are consumers of claude-crew (or runnable on top of it). The competitive frame is against substrate options: Agent Teams, raw SDK, tmux hacks.

### Constraints

- **Solo developer, limited time budget.** v1 must be small enough that one person can ship it in a few weekends. Big-architecture choices that don't pay off in the first 4 features get cut.
- **Local-machine only for v1.** No network transport, no remote crews, no auth. The trust boundary is the local user.
- **Python first** for the runtime — Anthropic's Agent SDK has the most mature surface there, and Jerome ships Python faster than TypeScript. TypeScript bindings are deferred until there's a clear consumer.
- **Polling-first inbound, hooks deferred to v2.** Keeps v1 portable across MCP clients; we don't lock the contract to Claude Code internals before we have user data.
- **Two-level recursion cap (lead → teammate → subagent).** SDK can technically go deeper; v1 caps it because deeper trees multiply cost and reasoning depth. Lifted only with evidence it pays off.
- **Single Claude Code lead per crew in v1.** Headless / scripted leads are interesting but defer until the interactive case is solid.
- **Enterprise-grade quality bar.** Tests at both implementation and integration layers, structured logging, error surfacing — this is meant to run on real work, not as a science project.

### Open Verification Items

These are claims the architecture leans on but that we haven't empirically confirmed yet. Each should be a small spike before or during the feature it gates.

- **SDK memory behavior** — confirm what persists across `ClaudeSDKClient` calls within a session, what loads from `~/.claude/CLAUDE.md` and project `CLAUDE.md` (likely via `setting_sources`), and whether Claude Code's auto-memory subsystem (`~/.claude/projects/<encoded>/memory/`) is active for SDK programs running outside the CLI. Verify before Feature #2. **Resolved** in `doc/research/sdk-memory.md` — auto-memory not active at parent level; CLAUDE.md loaded by CLI default.
- **Subagent context isolation** — confirm subagents do not auto-inherit parent context, CLAUDE.md, or memory unless explicitly configured. Verify before Feature #3a. **Resolved** in `doc/research/sdk-subagents.md` — conversation history and `system_prompt` isolated; CLAUDE.md inherits via parent's `setting_sources` (intentional product stance per #3a).
- **Token budget knobs per subagent** — confirm `max_turns` and `max_thinking_tokens` are per-subagent, not session-wide. Verify before Feature #3a. **Resolved** in `doc/research/sdk-subagents.md` — `AgentDefinition.maxTurns` and `effort` are per-subagent and enforced.
- **Top-level teammate auto-memory access** — `sdk-memory.md` concluded the SDK does not activate Claude Code's auto-memory subsystem, but the test was indirect. After Feature #3a, ran `scripts/auto_memory_probe.py`. **Resolved:** the auto-memory subsystem does not auto-populate, but the path under `~/.claude/projects/<encoded>/memory/` is fully writable by SDK-spawned teammates. Cross-session teammate memory is therefore a small capability lift (explicit read/write in teammate prompts or a thin wrapper), not the v2 architectural rebuild we'd assumed. Updated `sdk-memory.md` §3 with findings.

---

## Feature Pipeline

*Prioritized shortlist of feature candidates. MVP is the first five rows. Deferred items below the divider are v2+.*

### MVP

| # | Feature | Capability | Crit | Size | Status | Notes |
|---|---|---|---|---|---|---|
| 1 | **MCP server skeleton + tool surface.** stdio MCP server registered with the lead, exposes `spawn_teammate`, `send_to`, `broadcast`, `get_messages`, `list_crew`, `kill_teammate`. Tools are wired but teammates are stubs that echo. | 1 | 1 | M | done | Foundation. 49 tests passing + stdio smoke test. See `doc/features/FEATURE-mcp-skeleton.md`. |
| 2 | **One persistent SDK teammate, end-to-end.** `spawn_teammate` actually creates a `ClaudeSDKClient`, holds the reference in the broker, routes messages to/from it. Validates the persistence claim by exchanging 10+ messages and confirming context is preserved. | 1 | 1 | M | done | 89 mocked + 4 live tests + stdio smoke. SDK memory verification resolved in `doc/research/sdk-memory.md`. Real-bug-find: SDK's RateLimitInfo.status='allowed' is informational. Validated end-to-end against my-money-matters via Claude Code. |
| 3a | **Default subagent pack.** Ship `explorer`, `planner`, `general-purpose` agent definitions bundled with claude-crew. Each teammate is configured with this pack as available subagents on spawn. Models, tools, and system prompts chosen to mirror Claude Code's built-ins (haiku for explorer, sonnet for planner). | 1, 2 | 1, 2 | S | done | 37 unit/integration tests + 1 live E2E. Pack files in `claude_crew/subagents/*.md` with YAML frontmatter loader; #3b's user-agent loader rides the same parser. SDK behavior verified in `doc/research/sdk-subagents.md`: subagents inherit CLAUDE.md (intentional, security note in pack README), conversation/system_prompt isolated, per-subagent `maxTurns`/`tools` enforced. See `doc/features/FEATURE-default-subagent-pack.md`. |
| 3b | **Agent-definition loader.** Parse user-defined `~/.claude/agents/*.md` and project-level `.claude/agents/*.md`, convert YAML+markdown to SDK `AgentDefinition` objects, merge into the available subagent set. Skip unsupported fields with warnings. | 2 | 1, 2 | S | done | Plan-mode build (not full SDD); rode the `merge_packs` seam from #3a. 21 unit/integration + 1 live E2E (~$0.20). Project > user > default precedence; frozen-at-startup project root; per-file 256KB / per-dir 100-file caps; INFO log on every shadow event. See `doc/features/FEATURE-agent-definition-loader.md`. |
| 3c | **Cross-session teammate memory (full mirror).** Teammates read and write `~/.claude/projects/<encoded>/memory/MEMORY.md` and per-entry `*.md` files using the same structure Claude Code's auto-memory subsystem uses (index file + frontmatter-tagged entries with `name`/`description`/`type`). Loaded into the teammate on spawn via system prompt or initialPrompt; appended-to during work via explicit instructions in the pack's prompts. Same path Claude Code uses, so a teammate's memory stays compatible if the SDK ever activates auto-memory natively. Per-teammate-identity scoping (each role gets its own memory file) is the default; an opt-in shared mode is a follow-up. | 1 | 1 | M | deferred (gate not triggered by #5 retro) | **Gate evaluation (2026-04-26 post-#5 retro):** Memory was NOT the load-bearing gap in the real-task run. What WAS load-bearing: (a) substrate observability — bus doesn't see subagent activity inside teammates; (b) timeout policy — hard-walled SDK turn timeout vs telemetry-based liveness. #3c stays deferred. Revisit ONLY if a future real-task run actually surfaces a memory gap (e.g., teammates losing context mid-multi-session work). Empirical findings in `doc/research/sdk-memory.md`. |
| 4 | **JSONL transcript per crew.** Every message that crosses the bus (lead ↔ teammate ↔ subagent) appended to a structured JSONL file per crew, with sender, recipient, timestamp, payload, message id. Floor for observability — `tail -f` is the v1 dashboard. | 4 | 4 | S | done | Per-line schema with `kind` discriminator (envelope vs lifecycle), crew_id primary key, XDG_STATE_HOME path. v1 covers lead↔teammate; subagent activity inside SDK does not cross the broker (documented limitation). 119 tests. |
| 5 | **Real-task validation.** Use claude-crew on a non-trivial real task at home with one of Jerome's existing roles. Pass criterion: task completes end-to-end without operator intervention beyond directing the lead, and "needed manual rescue?" flag is false. | all | 5 | M | done | The proof point. PASSED on 2026-04-26 with MMM-35 (Bank CSV import backend slice). All 8 tripwires clean; capability #2 verified twice (sentinel teammates spawned subagents). Retro in Product Journal. Substrate findings (S1 timeout, S2 stale-response, observability gap on subagent activity) drive the next claude-crew work block. |

### Post-MVP Substrate (v1.1)

Routed from Feature #5's retro substrate findings, plus #8 added during Feature #6 Phase 2 design (tool-execution opacity surfaced as a real gap). Build order revised this session (2026-04-27): **#6 first** (shipped) → **#8 ahead of #7** because #7's load-bearing design question (what shape should subagent-activity envelopes take) needs real-task signal that we don't have yet, while #8's load-bearing question was empirical (does Agent SDK expose hooks the same way Claude Code does — answered yes, this session). Now **#6 + #8 shipped**; #7 deferred until a real-task validation run (likely MMM-4b) gives signal on what subagent-activity envelopes should surface.

| # | Feature | Capability | Crit | Size | Status | Notes |
|---|---|---|---|---|---|---|
| 6 | **Telemetry-based teammate liveness.** Replace `TURN_TIMEOUT_SECONDS` hard wall in `claude_crew/sdk_teammate.py` with stream-activity stamping — every event yielded by `receive_response` updates `last_activity_at`. New MCP tool `get_teammate_status(id)` returns `{alive, last_activity_at, current_turn_started_at}`. Add a subprocess-PID liveness probe so genuine death emits `lifecycle: died`. Drop the wall timeout (or move to a 1hr backstop). Operator policy ("no activity > X min → ping") moves to lead code where it belongs. | 1, 4 | 5 | M | done | **Shipped 2026-04-27** (`5ccdac9`). Full SDD: 12 SCs, 12 design decisions D1-D12, 5 implementation tasks via team-build with claude-crew teammates. 218 non-live tests + live SDK A2 probe (PASS). Cohesion delivered: `teammate_dead` peer error code, tombstones queryable post-mortem, `broadcast` returns `skipped_dead`, `kill_teammate` and SDK death share tombstone code path. S1 and S2 dead by construction; backstop calls `client.interrupt()` first. Substrate dogfooded during build (3 S1 fires, 0 stale responses, 0 rescue tripwires). See `doc/features/FEATURE-telemetry-liveness.md`. |
| 7 | **Subagent-activity envelopes.** Emit `subagent-spawn` and `subagent-result` envelopes from teammates so the lead can observe capability #2 in real time. Closes the Feature #4 v1 documented limit ("subagent activity does not cross the broker"). Verified in #5 retro that capability #2 IS being exercised in real production work — just not bus-observable; we had to confirm by directly asking each sentinel teammate after the fact. | 2, 4 | 5 | S | done | **Shipped 2026-04-27** (`fa8be42`). Full SDD: 14 SCs, 10 design decisions D1-D10, 5 tasks via team-build (Sonnet 4.6 medium). 323 tests + clean sentinel chain (inner-4 PASS, final PASS). Key design: pull model (SC-6), separate subagent namespace (D3), emit-first ordering (F2 fix), dual-drain on death (F1 fix), D10 limbo-state eliminated. T5 discovered that scratch entries get `subagent_result(tnm_missing=True)` at death (not `abandoned_batch`) because `_end_turn(close_tools=False)` runs before `_close_open_subagents` in tombstone path — ruled semantically correct by final sentinel. See `doc/features/FEATURE-subagent-activity-envelopes.md`. |
| 8 | **Tool-execution telemetry via SDK hooks.** Register `PreToolUse` and `PostToolUse` hooks on every `SdkTeammate` so the substrate observes tool boundaries directly instead of inferring them from stream gaps. Adds `current_tools` (list, supports parallel-tool case), `last_tool_completed`, `redaction_version` to the teammate status payload. Each tool boundary stamps activity (closes the "Bash runs for 20min, SDK stream is silent" gap); each tool call emits paired `tool_start`/`tool_end` JSONL transcript records. Versioned redactor (v1) + per-tool extractor allowlist (Bash/Task/WebFetch). Latent `default.jsonl` race fixed as a bonus via per-teammate `session_id`. | 1, 4 | 5 | M | done | **Shipped 2026-04-27** (`0d974ad`). Full SDD: 16 SCs, 12 design decisions D1-D12, 5 tasks via team-build with claude-crew teammates. 295 tests + live SDK A2 probe (PASS, 7.9s, ~$0.05). Independent convergence between sentinel-f8-p1 and co-architect-f8 on the duplicate-tool_end gap (D8 fifth guard with `_recently_closed_tool_use_ids` LIFO) — strong "would have bit us" signal at Phase 2 review. Substrate dogfooded across the build (no S1 fires; Feature #6 telemetry held). Phase 1 + Phase 2 each empirically grounded by live SDK spikes (hook ordering, subagent propagation, parallel-tool, PermissionRequest interleave). See `doc/features/FEATURE-tool-execution-telemetry.md`. |
| 9 | **`get_messages` long-poll (blocking wait with early return).** Add a `wait_seconds: float = 0` parameter to the existing `get_messages` MCP tool. When `wait_seconds > 0` and no messages are available at call time, the server blocks until a message arrives for the lead or the timeout elapses — whichever comes first. Returns immediately if messages are already waiting. Zero (default) preserves current behaviour exactly. Implementation: add `asyncio.Condition` to the broker; `store_message` notifies on delivery; the async `get_messages` handler awaits the condition with a timeout. No new tool, no new protocol, no client changes — just smarter waiting. | 1 | 5 | S | **done** | **Shipped 2026-04-28** (`66e2e8f`). Promoted from deferred 2026-04-27. Broker `asyncio.Condition` + `wait_seconds` param on `get_messages`. Eliminates polling burn during team builds. See `doc/features/FEATURE-get-messages-long-poll.md`. |
| 10 | **Agent config extension.** Add `skills`, `permissionMode`, `disallowedTools` to `PackFrontmatter` so pack files can declare them. Wire `permissionMode` and `disallowedTools` through to `ClaudeAgentOptions` when roles are spawned as top-level teammates. Add `cwd` to `spawn_teammate` for multi-repo work. | 1, 2 | 1 | S | **shipped** | Merged 2026-04-29. 340 tests. 4-task team build. Sentinel clean. Spawn-time permissionMode validation gap logged to BACKLOG. |
| 11 | **Lightweight subagent context.** Explorer, planner, and general-purpose bundled agents load full user CLAUDE.md via `setting_sources=["user","project"]` by default — wasted tokens for utility roles. Add `setting_sources` to `PackFrontmatter` so roles can declare `setting_sources: []`. Update bundled explorer and general-purpose to use minimal context. Requires a parallel channel alongside the agents pack (AgentDefinition has no `setting_sources` field). | 1, 2 | 1 | S | **done** | **Shipped 2026-04-29.** `settingSources` in PackFrontmatter with validation; parallel `role_ss` dict threaded through full loader cascade; factory closure passes `setting_sources=role_ss.get(role)` to SdkTeammate. explorer: `[]`, general-purpose: `[]`, planner: `[project]`. 387 tests. SC-5 live probe deferred. See `doc/features/FEATURE-lightweight-subagent-context.md`. |
| 13 | **Multi-instance registry and unified dashboard aggregation.** When multiple claude-crew instances run concurrently, each instance's Mission Control dashboard shows state from all peers. Per-instance XDG JSON files (`~/.local/state/claude-crew/instances/<crew_id>.json`) with PID liveness and atomic writes; UIServer fans out HTTP GET to peers, merges results, marks unreachable instances; SIGTERM deregisters cleanly. | 3, 4 | 5 | M | **done** | **Shipped 2026-04-30** (`153dd16`). Full SDD: 9 SCs, 10 design decisions D1-D10, 5 tasks Kael direct. 486 tests (68 new — registry unit, ui_server async, e2e multi-instance). Sentinel caught SIGTERM event-loop placement bug in Phase 2 spec (before any code); post-implementation Sentinel added `is_local=True` search robustness and SC-9 timeout coverage. "Live multi-crew UI" deferred item now shipped. See `doc/features/FEATURE-multi-instance-registry.md`. |
| 14 | **Token/cost telemetry per crew.** `SdkTeammate` captures `ResultMessage.total_cost_usd` and `ResultMessage.usage` per-turn-drain (cumulative session totals — overwrite, not accumulate). Three new fields on `SdkTeammate` (input/output tokens, cost) surface via `status_snapshot()`. `TeammateInfo` gains three `_at_death` fields preserved at tombstone. `UIServer._build_local_instance()` reads from snap for live agents and from tombstone fields for dead, summing both into the instance summary so "what has this session cost me" survives teammate death. Cache tokens included in input. Subagent (Task tool) cost auto-rolls into parent via shared session_id — no extra plumbing. | 4 | 5 | M | **done** | **Shipped 2026-04-30** (`55adf47` merge, `79612e2` per-agent dashboard tokens). Full SDD: 10 SCs, 12 design decisions, 5 builder tasks (T1-T5) + 2 sentinel reviews. OQ-3 (cadence) and OQ-5 (session_id stability) and A-8 (subagent cost rollup) all spiked in main session at Phase 2 gate; both sentinel reviews caught real bugs (missing assertion, broker race-path UnboundLocalError) before merge. Manual test confirmed live SDK traffic shows real per-agent cost ($0.01–$0.06 for one-turn responses) and tombstone aggregate preserves dead teammate cost. SC #4 dashboard gap closed. See `doc/features/FEATURE-token-cost-telemetry.md`. |
| 15 | **Expanded default subagent pack.** Add `reviewer` and `runner` roles to the bundled subagent pack. `reviewer`: code review, security audit, spec review — Sonnet, read-only + Write for annotations. `runner`: test execution, build orchestration, scripted tasks — Sonnet, Bash + Read. Both follow the existing pack frontmatter + `settingSources` patterns from #11. | 2 | 3 | S | **next** | Promoted from deferred. These are the roles that real-task builds (MMM-4b et al.) have been spinning up without a pack definition. `archaeologist` deferred until a real use case surfaces. |
| 16 | **Message kind typing in transcript stream.** Transcript envelopes currently all emit `kind: "msg"`. Inspect payload shape to emit `kind: "tool"` for tool-call envelopes and `kind: "thinking"` for thinking-block envelopes. Dashboard stream columns then show the visual distinction (monospace pill vs. italic) the design intends. Requires coordination between broker envelope format and `UIServer._build_local_instance()`. | 4 | 3 | S | **next** | Backlog item from #12 retro. Tool calls and thinking entries are visually identical to plain messages today; operators lose the signal. |
| 17 | **Agent definition parity (mcpServers, permissionMode, disallowedTools in PackFrontmatter).** `PackFrontmatter` is missing half the `AgentDefinition` field set — `mcpServers`, `permissionMode`, `disallowedTools`, `memory` cannot be declared in `.md` agent files today. Additive field extensions to `PackFrontmatter` + `_validate_frontmatter` + `parse_pack_text`, no architecture change. Also wire `spawn_teammate` permission_mode validation at the MCP boundary (currently invalid strings reach the SDK silently). | 1, 2 | 2 | S | **next** | Backlog items from #10 retro. Role-level config belongs in pack files, not spawn-time overrides — the validation gap makes incorrect spawns invisible to the caller. |
| 18 | **Broker `snapshot()` read API + dashboard polish.** Add `broker.snapshot()` returning a frozen dataclass (`crew_id`, `alive_teammates`, `log`) so `UIServer` stops reading `_info`, `_log`, `_teammates` directly. Removes fragile private-attr coupling and makes UIServer unit-testable without a live broker. Bundle with two small dashboard fixes: git branch read at UIServer init (`git branch --show-current`, cached, fallback "main") instead of hardcoded `"main"`; and remove the stale `_get_redaction_version()` ImportError fallback in `teammate.py`. | 4 | 2 | S | **next** | Backlog items from #12 and #8 retros. The private-attr coupling is the reason `ui_server.py` can't be tested in isolation. All three changes are XS–S individually; bundled as one feature to keep the pipeline lean. |
| 19 | **Tool-use events in the dashboard stream.** Surface `tool_start`/`tool_end` JSONL transcript records in the Mission Control message columns alongside envelope messages. Requires a broker-level per-teammate event list (or a parallel channel alongside `_log`) that `UIServer` reads and merges into the transcript stream, emitting `kind: "tool"` entries the dashboard already knows how to render. SDK teammates already write these records to the JSONL sink — the gap is getting them into the in-memory state the UI polls. | 4 | 4 | M | **next** | Surfaced during #13 session (2026-04-30). Dashboard already has `MiniMessage` render paths for `kind: "tool"` and `kind: "thinking"` — the visual layer is done; only the data pipeline is missing. Pairs naturally with #16 (message kind typing) and #18 (broker snapshot API, which cleans up the private-attr reads this feature would add). Recommend implementing after #18 so the snapshot API is the clean read surface. |
| 20 | **Peer messaging between teammates.** Teammates gain a `send_to(teammate_id, text)` MCP tool so peer A can message peer B without the lead routing every turn. Architecture: broker exposes a Unix-socket MCP endpoint; each `SdkTeammate` is spawned with `ClaudeAgentOptions.mcpServers` pointing at that socket. Sender id auto-stamped from the teammate's identity (no spoofing). Recipient dead/tombstoned returns the existing `teammate_dead` error from #6. Lead receives a copy of every peer envelope (preserves observability — lead stays orchestrator, not bottleneck). Hop counter (TTL=4) on envelope drops runaway A→B→A→B loops with a logged warning. v1 is `send_to` only; `broadcast` and teammate-side `get_messages` deferred. | 1, 2 | 5 | M | **idea** | Surfaced 2026-04-30 during F14 session — Jerome flagged that today's lead→teammate→lead routing forces the lead onto the critical path of every cross-teammate exchange. Unblocks async collaboration patterns (builder→reviewer handoff, scout→planner→builder pipelines). Load-bearing decision: socket-based MCP transport for the broker (today's broker MCP is stdio-only, child of the lead process; multiple teammates need a shared non-stdio endpoint). Adjacent but distinct from "Recursive crew spawning" (lifecycle question, deferred) and "MCP server injection + cwd on spawn_teammate" (mechanism overlap — peer messaging would naturally consume that work; consider co-scheduling). Open Qs for Phase 1: socket auth model (teammate authenticates as itself, or trusts spawn-time configuration?); does lead-as-observer copy go to transcript-only or also lead's inbox? |

### Deferred (v2+)

| Feature | Capability | Notes |
|---|---|---|
| Hook-based ambient inbound delivery to lead | 1 | Polling is the v1 contract; hooks add slickness once we know the bus shape is right. Needs spike: do shell hooks fire in SDK mode (`CLAUDE_CODE_ENTRYPOINT=sdk-py`)? |
| Multi-crew concurrent run as a *validated* scenario at work | 3, 5 | Multi-crew is structural in MVP; the validated work scenario follows. |
| Adapter contract spec for non-SDK runtimes | 1 | Forward-looking design work; lock the bus protocol in v1 implementation, formalize the spec in v2. |
| MCP server injection + cwd on spawn_teammate | 1, 2 | Needs spike: does `--mcp-config` merge or replace? Do tool allowlists block MCP tools? Three unknowns documented in BACKLOG. |
| Recursive crew spawning (teammate calls spawn_teammate) | 2 | One user-level config change away structurally, but lifecycle ownership (who kills a teammate spawned by another teammate) needs a design decision first. |
| Skill invocation from SDK teammates | 2 | Needs spike: what does "invoking a skill from a subagent" mean mechanically? Gates on MCP spike results. |

**Status values:**
- `idea` — captured but not yet evaluated
- `next` — selected for implementation, ready for SDD handoff
- `specced` — SDD feature file created, in progress
- `done` — implemented and verified
- `cut` — removed from pipeline (note why)
- `next` — Selected for implementation, ready for SDD handoff
- `specced` — SDD feature file created, in progress
- `done` — Implemented and verified
- `cut` — Removed from pipeline (note why)

**Prioritization guide:**
- Does it serve a core capability? (If no → cut or reconsider)
- Does it advance a success criterion? (If no → cut or reconsider)
- What's the effort vs. impact? (High impact + low effort → build first)
- Are there dependencies? (Blocked features → build blockers first)

---

## Product Journal

*Running log of major milestones, direction shifts, and learnings. This is the organic lifecycle signal — no rigid phases, just observable history.*

### 2026-04-30 — #14 Token/Cost Telemetry — Shipped

Merged to master 2026-04-30 (`55adf47`); per-agent dashboard tokens follow-on (`79612e2`). Full pipeline working against real SDK traffic — verified by spawning three live `general-purpose` teammates, sending substantive prompts, and watching the dashboard cost column tick from `$0.000` to real per-agent values ($0.01–$0.06 for one-turn responses). Killed one teammate; instance-summary cost did NOT drop (SC-3 acceptance signal — tombstone contributes to "what has this session cost me").

Below is the design-time journal entry as originally written; retained because the spike findings remain useful reference material:

---



Phase 1 + Phase 2 + Phase 3 task breakdown completed for the last visible SC #4 gap. Two architectural questions resolved by spike rather than guessing:

**OQ-3 (cost source / cadence):** Read the SDK source. `client.receive_response()` terminates at exactly one `ResultMessage` per turn drain (claude_agent_sdk/client.py:566-605), and `total_cost_usd` / `usage` carry **session-cumulative** values across `query()` calls. The CLI maintains state per session_id. **Resolution: use `ResultMessage` directly with overwrite semantics** — no rate table, no per-turn delta math, no double-counting risk.

**OQ-5 (session_id stability):** Sentinel flagged that the cumulative-overwrite design hinges on session_id staying constant across `query()` calls. Spiked sdk_teammate.py:794-797: session_id is constructed as `f"{crew_id}-{teammate.id}"` per turn — both inputs immutable for the teammate's lifetime. ✅ Stable.

**Subagent cost coverage (A-8):** Verified via `claude_agent_sdk/types.py:1047` — `TaskNotificationMessage.session_id` matches the parent. Subagents execute within the same SDK session; their cost auto-rolls into the parent's `total_cost_usd`. **No extra plumbing needed for F7 cost coverage.** This is a pleasant surprise — the architecture's session-scoped accounting did the work for us.

**Tombstone aggregate semantics:** SC-3 commits to "what has this session cost me" — not "what are the alive agents costing." The instance-summary aggregate iterates both alive teammates (live snap) and tombstones (`_at_death` fields) in one pass over `broker._info.values()`. Respawn under the same role naturally co-exists with the prior tombstone (broker keys by id, not name) — the aggregate sums both. Operators will see cost numbers that don't drop when a teammate dies.

**Process pattern reinforced:** Co-architect (Opus) + reviewer (Sonnet) at Phase 1 SC gate caught 5 design risks before any code. Sentinel review at Phase 2 caught 5 more, including the OQ-5 session_id assumption that would have silently undercounted by ~10× if wrong. Two cheap spikes (~10 min each) validated decisions that would have been a full re-cut to discover during implementation. Crew-assisted SDD continues to pay for itself.

### 2026-04-30 — #13 Post-Implementation Polish + Leader Election Shipped

Hardening and UX work carried out after #13's initial implementation during live testing. All shipped before merge to main.

**Leader election:** The first instance now atomically claims port 7821 — the stable, bookmarkable URL. Followers get OS-assigned ephemeral ports and poll every 20 seconds; when 7821 is free, the follower promotes itself (new UIServer on 7821, logs the URL, clears ephemeral port from registry).

**Race-free port binding (`_bind_ui_socket`):** The original `_pick_ui_port()` had a probe-release-rebind window — two concurrent instances could both see 7821 as free and both fail uvicorn's re-bind, falling back to ephemeral. The fix keeps the socket open from probe to serve, passing it to uvicorn via `fd=sock.fileno()`. `SO_REUSEADDR` handles TIME_WAIT residue from a previous leader. `listen()` is the serialization point — with `SO_REUSEADDR`, both can `bind()` but only one can `listen()`, confirmed empirically. Four new tests in `TestBindUiSocket` cover concurrent-caller isolation, hold-while-open, and TIME_WAIT fallback.

**Orphan process fix:** MCP stdin close wasn't propagating to the UIServer — the uvicorn coroutine kept the process alive after Claude Code exited. Fix: `_mcp_then_cancel()` calls `tg.cancel_scope.cancel()` in its finally block.

**Circular fanout fix:** Instance A's `/api/state` was calling B which was calling A, deadlocking. Fix: `?local=1` query param on remote calls skips the registry fanout at the receiving end.

**SDK teammate suppression:** SDK teammates were spawning UIServers (inheriting the host's MCP config), polluting the instance registry. Fix: inject `CLAUDE_CREW_UI_PORT=0` via `ClaudeAgentOptions(env={...})` in `SdkTeammate`.

**HTML no longer cached:** UIServer was caching dashboard HTML on first read, requiring a process restart for CSS changes to take effect on hard refresh. Removed the cache — reads the file on every request at negligible cost.

**Dashboard UX:** Bidirectional messages in agent columns (lead→agent and agent→lead). Agent headers show name bold + role dimmed when they differ. Message bodies extracted from `payload["text"]` instead of raw JSON; cap raised 500→2000 chars. Theme: Clearwater-inspired blue palette, higher-contrast lightness range (bg-0: 0.91 → bg-4: 0.54).

**Advances criteria:** SC #3 and #4 fully shipped and manually validated with two live concurrent instances.

### 2026-04-30 — Feature #13 (Multi-Instance Registry + Unified Dashboard) Shipped

The dashboard is no longer single-crew. Any instance's Mission Control now shows all co-running instances — agents, status, and transcript — in a unified view without a page refresh.

**What shipped:**
- `InstanceRegistry` — XDG-aware per-instance JSON files (`~/.local/state/claude-crew/instances/<crew_id>.json`). Atomic writes via `os.replace`; PID liveness check on every read; stale/corrupt entries self-healing (deleted on detection). No inter-process locking needed — per-instance files are inherently collision-free.
- `UIServer` async migration — `_build_state()` converted to `async def`; `_build_local_instance()` extracted; `_fetch_remote_state()` fans out to peers' `/api/state` with a long-lived `httpx.AsyncClient(timeout=2.0)`. Failed/slow remotes produce `status: "unreachable"` entries, never blocking the push cycle.
- Dashboard visual distinction — `is_local: true` badge chip on the local instance; red border + "unreachable" label for dead remotes.
- SIGTERM handler — registers on startup, deregisters on clean shutdown. Handler installed via `asyncio.get_running_loop().add_signal_handler()` inside `async def _run()` (not before `anyio.run()` — anyio creates its own loop, so earlier placement would silently never fire).

**Process highlights:**
- Kael direct, 5 tasks. Phase 2 Sentinel caught the SIGTERM event-loop placement bug from the spec before any code was written — saving a subtle correctness issue that tests alone wouldn't have caught (handler would register without error but never fire).
- Post-implementation Sentinel caught two more: `instances[0]` fragile ordering assumption fixed to `is_local=True` search; no SC-9 (timeout bound) test coverage added via `TestRemoteTimeout` with a real 10s-sleep server.
- 68 new tests: `test_instance_registry.py` (19), `test_ui_server.py` async migration + new tests (total 40), `test_e2e_multi_instance.py` (20). Full suite: 486 passed, 9 skipped.

**Advances criteria:** SC #3 (structurally met — per-instance files eliminate interference), SC #4 (substantially met — all crews visible in one dashboard).

**Pipeline impact:** "Live multi-crew UI" deferred item closed. Token/cost tracking per crew remains the next SC #4 gap. SC #3 validated-scenario proof point (two real crews completing independent tasks end-to-end) is still deferred.

### 2026-04-27 — Feature #6 (Telemetry-Based Teammate Liveness) Shipped

The first post-MVP substrate item, prerequisite for the next real-task validation. Built across 2 calendar days (started 2026-04-26 evening, merged 2026-04-27) via full SDD workflow with team-build delegation to claude-crew teammates.

**What shipped:**
- `TURN_TIMEOUT_SECONDS` constant deleted from `sdk_teammate.py`. The 600s wall (band-aid `b9bc611` from Feature #5) is gone. The only remaining per-turn timer is a 1-hour configurable backstop that calls `client.interrupt()` *first* before erroring — closes S2 by construction at the 1hr boundary.
- New per-teammate telemetry: `_last_activity_monotonic` + `_last_activity_wallclock` stamped on every `receive_response()` event (including non-text events — `RateLimitEvent`, `TaskNotificationMessage`); `_current_turn_started_at_wallclock` set/cleared at turn boundaries.
- New MCP tool `get_teammate_status(teammate_id)` returns `{alive, last_activity_at_wallclock, current_turn_started_at_wallclock, idle_seconds}` for live teammates; full death record (`died_at_wallclock, exit_code, idle_seconds_at_death, last_activity_at_wallclock_at_death`) for tombstoned teammates. Docstring explicitly notes `idle_seconds` reflects SDK stream activity only — long tool execution shows as idle. Feature #8 will close that gap.
- Per-teammate liveness poll task (5s default, env-overridable) reads `client._transport._process.returncode` with broad-Exception degrade-open. Dead subprocess detected within 5s while idle; immediately while in-turn via existing `ProcessError` exception path.
- New peer error code `teammate_dead` (joins `unknown_teammate`/`duplicate`). `kill_teammate` reroutes through the same tombstone code path as SDK-death detection — emits `lifecycle: kill` (not `died`). Tombstones persist for broker lifetime; queryable via `get_teammate_status`.
- `broadcast` now returns `skipped_dead: [...]` so silent skips become explicit (cohesion directive).
- New `lifecycle: died` transcript event type with full death record.

**Vision impact:**
- Feature #6 → done. **Substrate is now telemetry-based.** Lead-side policy ("ping if idle > N min in-turn", "kill and respawn if backstop fires twice on same teammate") moves out of substrate constants and into operator code where per-task tuning is possible.
- Capabilities #1 and #4 directly advanced. Cohesive enterprise-quality MCP surface delivered: peer error codes, queryable tombstones, explicit skip-reporting on broadcast.
- **Feature #8 routed during Phase 2 design.** Tool-execution opacity (a 20-min Bash build = 20 min of `idle_seconds` climbing on a healthy subprocess) was identified as the substrate's only remaining observability gap after #6. Routed as a proper feature pipeline row with explicit Feature #6 dependency. PreToolUse / PostToolUse SDK hooks are the architectural answer.
- **Feature #3c (cross-session memory) stays deferred.** Did not surface during this build either — same gate-question outcome as Feature #5's retro.

**Design highlights:**
- **Sentinel-as-teammate reviewed THREE times** across the lifecycle (Phase 1 SCs, Phase 2 spec coverage, Phase 4 inner-4 + final merge-readiness). Each pass caught real gaps. Phase 1 sentinel turned 8 SCs into 12 (cleanup-on-failure additions). Phase 2 sentinel caught the in-flight envelope handoff gap that would have silently lost requesters mid-death. Inner-4 sentinel caught an `asyncio.get_event_loop()` deprecation and 2 missing transcript assertions. Pattern: front-load sentinel review at SC-time, not just code-time.
- **Co-architect-as-teammate (Opus 4.7, persistent across the whole feature)** at every gate. The "name three things you'll push back on" warmup prompt at spawn time pre-loaded the design's load-bearing risks (single-writer death detection, backstop ordering, probe scope) before any specific design existed. All three became Phase 2 design pillars.
- **Pre-design SDK-access spike at Phase 1 gate** (not Phase 2) resolved `_transport._process.pid` + `client.interrupt()` empirically before Phase 2 designed against assumptions. Co-architect explicitly cited this as why the design didn't deadlock on unknowables. Pattern: when a feature's load-bearing assumption is empirical, spike at the gate.
- **Single-writer rule for death detection** — only the periodic poll task writes `lifecycle: died` and tombstones. The in-turn worker, on observing SDK death, sets a `_death_in_flight_envelope` field and `_death_suspected` flag, returns without sending an envelope. Poll task's next tick (≤5s) idempotently runs the death handler. Eliminates the duplicate-event race that two-source detection introduces.
- **Backstop interrupt-first contract (SC-11)** — when the 1hr backstop fires, the teammate calls `await client.interrupt()` with a 30s grace BEFORE sending the error envelope. If interrupt itself hangs or raises, the teammate sets `_death_suspected=True` and the poll task escalates to tombstone. Closes residual S2 risk at the 1hr boundary.
- **The honesty scenario for Feature #8 is empirically proven, not described.** `tests/test_telemetry_e2e.py::test_idle_seconds_during_long_tool_execution_is_honest` demonstrates the gap — when Feature #8 ships, this test becomes the regression check that #8 actually closes it.

**Substrate dogfooding stats (built while running on the substrate it replaces):**
- 3 S1 timeout fires across builders (T3, T4, T5) — each recovered via "IGNORE PRIOR" status ping. Total operator overhead: ~5min wall.
- 0 S2 stale-response incidents.
- 0 rescue tripwires fired (all 8 from Feature #5's framework stayed clean).
- The lead/teammate domain split (declared at Phase 4 kickoff) held perfectly: lead authored all spec docs, teammates wrote all production code + tests + ran all verification.

**Cost / time accounting:**
- Wall time: ~6 hours from Phase 1 kickoff to Phase 5 close.
- Token spend: estimated $15-20.
- Teammates spawned: 7 (5 builders, 1 persistent sentinel, 1 persistent co-architect — sentinel and co-architect reused across all 5 phases).
- Live SDK probe runs: 1 (A2 concurrent interrupt during drain), $0.05, 2.88s wall, PASS — empirically confirmed the load-bearing assumption that, if wrong, would silently re-introduce S2 at the 1hr boundary.

**Process highlights worth keeping:**
- **Pre-Phase-3 grep audit of test contract change** (D11's `unknown_teammate` → `teammate_dead`) executed by lead BEFORE drafting Phase 3 task list. Surfaced 2 server tests + 1 broker test needing updates; routed as Phase 3 task scope, not Phase 4 surprises. Worked perfectly. Formalize as: "before Phase 3 task breakdown, run test blast-radius grep for any contract change introduced in Phase 2."
- **"Three pushback areas" warmup prompt for persistent co-architects** is portable and reusable across features. Worth capturing as a formal pattern.

**Pipeline impact:**
- #6 → done. **Substrate is telemetry-based, not timer-based.**
- #7 (subagent-activity envelopes) is logical next per #5 retro sequencing. Smaller (S size, plan-mode-sized).
- **MMM-4b (payee normalization) is the natural next real-task validation** — exercises the new substrate while shipping product. The build sequence the #5 retro proposed (claude-crew #6 → MMM-4b → claude-crew #7) is now ready to execute.

### 2026-04-26 — Feature #5 (Real-Task Validation) — MVP Gate PASSED

The MVP proof point. Used claude-crew to drive a real, scoped MMM ticket end-to-end (MMM-35: Bank CSV import + duplicate detection — backend slice) via the SDD workflow with planner / co-architect / builder / sentinel teammates orchestrated over the bus. Spec, design, implementation, and merge all carried by teammates; lead orchestrated only.

**Result: PASSED.** Backend slice merged to MMM `main` (`2a0b6ed`). All 8 rescue tripwires clean across the entire run. SC criteria for #5 met:
- ≥2 distinct teammate roles invoked by the lead — **4 used** (planner, co-architect, sentinel, builder)
- ≥1 recursive subagent spawn by some teammate — **verified twice** (both sentinel teammates spawned parallel Haiku subagents for file ingestion, then synthesized in main session — capability #2 exercised in real production work)
- Frozen scope, objective completion check (~56 BDD scenarios green), familiar codebase, single repo — all bar items met

**Pipeline impact:**
- Feature #5 → done. **MVP is now functionally complete.**
- Feature #3c (cross-session memory) is unblocked from its #5-retro gate. Per the gate question — *"did real-task work surface a need for cross-session memory, or did it surface something else?"* — the answer is **something else.** Memory wasn't load-bearing in this run (single session per phase, FEATURE.md as the durable artifact, SESSION.md handling cross-session context). What WAS load-bearing was **substrate observability and timeout policy.** #3c stays gated; revisit only if the next real-task run actually surfaces a memory gap.

**What this run validated:**
- The lead/teammate domain split (declared upfront in the FEATURE.md framework) prevented every potential rescue-tripwire fire. Lead authored only spec docs; teammates wrote all code.
- Pre-SDD prep paid off. The 7-criterion task bar, 8-tripwire framework, and 3hr/$10/$20/5-teammate ceiling were all set BEFORE kicking off SDD on #5 — when each one was tested, it held.
- The bus carries real cross-role traffic at production density. Tasks 1-5 + two sentinel reviews + one final sentinel review = ~15 substantive teammate exchanges in Phase 4 alone, in addition to all of Phase 1 and Phase 2.
- Mid-flight design pivots (Jerome's OQ-2 pushback rejecting nullable `budgetId` mid-Phase-1) absorbed cleanly without breaking the framework.

**Substrate findings — must be addressed before the next real-task run:**

The original Phase 1 of #5 was **paused mid-run** because of substrate bugs that were pacing the work. Took a substrate detour, shipped one fix, restarted, completed. The findings doc at `doc/research/feature-5-substrate-findings.md` captures S1 + S2; this retro adds the post-mortem signal.

- **S1 — SDK turn timeout fired on legitimate replies.** Original 120s timeout was too short for medium-effort Sonnet/Opus replies (5+ false fires in the original run, 2 more in the resumed Phase 4). **Fix shipped:** raised to 600s in `claude_crew/sdk_teammate.py` (commit `b9bc611`, merged `d09a779`). Tactical, not structural — the underlying issue is that timeout doesn't cancel the SDK subprocess.
- **S2 — Stale-response delivery.** When the timeout fires, the SDK subprocess keeps generating; the next turn can receive the OLD response. Manifested as "out-of-order replies" twice in the original run, paper-mitigated with "IGNORE PRIOR" prefix workaround. With S1 raised to 600s, S2 still latent but rarer.
- **The right structural fix is telemetry-based liveness.** Replace the hard timeout with stream-activity stamping + a `get_teammate_status` MCP tool. The lead polls instead of waiting on a wall-clock deadline; operator policy ("no activity for >X min → status ping") lives in lead code, not buried in a teammate constant. Captured in the substrate findings doc; deserves its own claude-crew SDD pass.
- **Capability #2 is observable only out-of-band.** Subagent activity inside teammates does NOT cross the broker (per Feature #4's documented v1 limitation). Verified the capability by directly asking each sentinel teammate after the fact. Future enhancement: emit `subagent-spawn` envelopes (or surface MCP server stderr through the bus) so the lead can observe capability #2 in real time. Smaller fix than the timeout-replacement; same theme — bus tells lead what's actually happening.

**Process highlights worth keeping:**
- **Pre-SDD prep as its own first-class step.** For high-stakes runs, naming the task / tripwires / ceiling BEFORE kicking off SDD prevented the proof-point ambiguity Jerome's prior co-architect had warned about. Worth formalizing as a recommended pattern in the SDD workflow doc.
- **Co-architect at gates (Opus, persistent).** Caught both M1 (D7 idempotency guard "Phase 3 TODO" was actually a correctness gap masquerading as a punt) and M2 (per-pair `findById` would have been an N×N pattern). Sentinel-as-teammate caught test-coverage gaps; co-architect caught *design* gaps. Different roles, both pulled weight.
- **The mid-Phase-1 design pivot worked.** Jerome rejected the lead+co-architect's initial OQ-2 resolution (nullable `budgetId`) on workflow grounds. The substrate absorbed the pivot — planner rewrote Phase 1 for the staging-table approach, lead cleaned up two leftover inconsistencies, run continued without losing momentum. This is the "domain expert override → sub-agent re-plan" loop working as intended.

**Cost / time accounting (entire #5 effort, across original + resumed sessions):**
- Total wall-clock: ~5 hours including the substrate-fix detour
- Token spend: ~$15-20 across all teammates
- Teammates spawned: ~10 across the full run
- Tripwire fires: 0
- Substrate timeouts: 7 (5 in original Phase 1, 2 in Phase 4) — all false positives; all recovered

### 2026-04-26 — Feature #3b (Agent-Definition Loader) Completed
- Advances criterion: #2 (recursive subagent decomposition) — by widening the available pack from 3 bundled members to "3 bundled + every agent the user has defined in `~/.claude/agents/` and the project's `.claude/agents/`."
- Pipeline impact: removes the artificial constraint on Feature #5 (real-task validation). Dogfooding claude-crew on a real task can now use Jerome's actual agent set (`scout`, `builder`, `feature-planner`, `runner`, `sentinel`) instead of just the toy three-pack.
- Design highlights:
  - Composition: `merge_packs(merge_packs(default, user), project)` — project shadows user shadows default. Matches Claude Code's own precedence rule for memory and settings.
  - Project root resolved once at MCP-server startup, frozen for process lifetime. Per-spawn resolution would let a teammate's pack silently change with `cwd` mid-session — a footgun we explicitly closed.
  - `discover_dir` caps at 100 files per directory and 256 KB per file (Q6 design pin-down). Pathological inputs can't block MCP startup.
  - `strict_parse` wraps `parse_pack_file` to warn on unsupported frontmatter keys without breaking forward-compat silence for the bundled pack. Accepted-key set is derived from `PackFrontmatter.__dataclass_fields__` so a new field can't drift the warning logic.
  - Shadowing emits INFO logs (not WARNING) on every shadow event. Documented contract, not a problem — but useful when debugging "why does my user-level agent behave differently here."
- Process: **plan-mode, not full SDD.** Phase 1 was sharp enough to build from. Co-architect (Opus 4.7, medium) reviewed both the ordering decision (#3b → #5, defer #3c) and Phase 1 itself; Sentinel passed Phase 1 acceptance criteria, then reviewed the implementation. The retrospective signal: the SDD ceremony's value scales with new design surface, not feature size — for a feature that's mostly "wire existing pieces with three design-pinned questions," plan-mode + a structured Phase 1 doc is sufficient and cheaper.
- Sentinel post-implementation pass caught 7 items (no Critical), including a phantom lazy import that fired the `feedback_lazy_imports.md` rule (the "cycle" justification was imaginary) and an uncovered code branch (project-shadows-default with no user-level entry).
- Live E2E (~$0.20, 22s) verified the full path against a real SDK: planted user-level and project-level agents both reached `SdkTeammate` and produced on-disk side effects (`Write` tool fired in each subagent, output verified by file presence not narration).
- Pipeline impact: #3b → done. **Logical next: #5** (real-task validation, MVP gate) with a deliberate pre-task design pass per co-architect's note — name the task, name the rescue tripwires, scope to one session — to keep the proof point unambiguous.

### 2026-04-25 — Feature #3a (Default Subagent Pack) Completed
- Advances criteria: #1 (persistent role-specialized teammates) and #2 (recursive subagent decomposition — the differentiated capability that breaks Claude Code's one-level ceiling).
- Resolves vision verification items:
  - **Subagent context isolation:** subagents inherit parent's `setting_sources` (and therefore CLAUDE.md — intentional, documented as a product principle in the pack README); conversation history and `system_prompt` are isolated.
  - **Per-subagent token budgets:** `AgentDefinition.maxTurns` and `effort` are per-subagent and enforced. `tools` allowlist is real sandboxing.
  - Findings in `doc/research/sdk-subagents.md`.
- Bonus finding from the spike that shrinks #4's future scope: subagent activity is observable in the parent's stream via `TaskStartedMessage` / `TaskProgressMessage` / `TaskNotificationMessage` SystemMessages. Transcript widening for subagents is small, not architectural.
- Design highlights:
  - Pack: `explorer` (Haiku, read-only), `planner` (Sonnet, read + Write only — no Edit, scope-creep guard via `initialPrompt`), `general-purpose` (Sonnet, full minus Bash and Task). No member has Bash (prompts are not security boundaries) or Task (subagents are leaves — load-bearing for cost/lineage reasoning).
  - Three `.md` files with YAML frontmatter; loader is hermetic (no network, fully in-repo); `merge_packs` rides on per-key whole-AgentDefinition override (user wins) — internal seam shipped now so #3b's loader builds cleanly.
  - SC-8 failure handling: `_collect_response_text` now returns `TurnDrainResult(text, last_failed_task_notif)`; SC-8(a) synthesizes envelope from `TaskNotificationMessage.summary` when parent text is empty; unconditional WARNING log on every failed task notif (operator visibility preserved on the recovery path too).
- Live E2E (~$0.30, 53s) proved tool-name correctness via observable side-effects: planner's `Write` actually wrote a file; general-purpose's `WebFetch` actually retrieved example.com's `<h1>`. Cheaper signal than asserting the SDK's internal contract.
- Test-design lesson captured: live isolation probes must NOT include the answer in the subagent's prompt — first run failed because the UUID was inside Q2's text and the subagent just repeated it. Fixed by planting a unique secret in the parent's T1 conversation only.
- Process: Full SDD workflow with co-architect (Opus, persistent) at every gate. Pre-Phase-1 SDK spike (~$0.20, two runs) blocked-on-research → cheap and decisive; without it, Phase 1 would have designed against assumptions that didn't survive contact with the SDK. Sentinel pass post-T3 caught a silent contract drift (`background=False` lost between Phase 1 decision and pack files) — five fix-now items folded in before the live test.
- Pipeline impact: #3a → done. Logical next: #3b (user-agent loader, builds on the merge seam) or #5 (real-task validation — MVP proof point).
- Open follow-up logged: top-level teammate auto-memory access probe via the live co-architect (~$0.02). Not blocking; cheap to run when convenient.

### 2026-04-25 — Feature #4 (JSONL Transcript) Completed
- Advances criteria: #4 (full crew conversation observable in real time — `tail -f` is the v1 dashboard).
- Design highlights:
  - Per-line schema `{v, kind, ts, crew_id, ...}` with `kind: envelope|lifecycle` discriminator. crew_id stamped on every line so concatenated transcripts (multi-crew analysis) need no filename cross-reference.
  - Lifecycle events: `started`, `spawn`, `kill` (with `reason: explicit|shutdown` — distinct so shutdown cascades aren't misread as N explicit kills), `shutdown`.
  - XDG_STATE_HOME path; `CLAUDE_CREW_TRANSCRIPT_DIR` override; `CLAUDE_CREW_TRANSCRIPT_DISABLED=1` opts out.
  - Discoverability: `[claude-crew]` stderr stamp on init + new MCP tool `get_transcript_path`.
  - Tolerate-and-disable on init failure; transient write failures log to stderr but do not disable. Broker is never load-bearing on transcript success.
- v1 limitation documented: SDK subagent activity (Task tool calls inside SdkTeammate) does NOT cross the broker, so transcript shows lead ↔ teammate only. Subagent observability is a Feature #3a-shaped concern.
- Process: Plan-mode (not full SDD ceremony). Used dogfooded co-architect (Opus, persistent across the feature) for design review — caught `kind: lifecycle` discriminator, `kill` reason threading, schema versioning per line. Sentinel still caught vacuous tests + missing transient-failure coverage. Different roles, both pulled weight.
- Pipeline impact: Feature #4 → done. Feature #3a → next (recursive subagent decomposition — the differentiated capability).

### 2026-04-25 — Feature #2 (SDK Teammate) Completed
- Advances criteria: #1 (lead spawns persistent role-specialized teammates and exchanges messages — now via real `ClaudeSDKClient`, not stubs)
- Resolves vision verification item: **SDK memory behavior**. Findings in `doc/research/sdk-memory.md`:
  - Conversation persistence is automatic via subprocess + `session_id`
  - CLAUDE.md is loaded by Claude CLI defaults regardless of `setting_sources`
  - Auto-memory subsystem is not active for SDK programs (source-confirmed)
- Real-task validation: tested end-to-end against my-money-matters via Claude Code's `claude mcp add`. Architect teammate read the codebase and gave an accurate 3-sentence summary. Full stack (lead → claude-crew → SdkTeammate → claude CLI subprocess) works on real work.
- Real-bug-find from the live spike: `RateLimitInfo.status='allowed'` is informational telemetry; was being treated as a hard failure, breaking every live session at turn 1. Caught by the gated UUID-recall test — exactly the failure mode that mocked tests can't surface.
- Vision shift: none — on track.
- Pipeline impact: Feature #2 → done. Feature #3a or #4 next, both are natural follow-ups (recursive subagents vs JSONL observability).

### 2026-04-25 — Feature #1 (MCP skeleton) Completed
- Advances criteria: #1 (lead can spawn N persistent role-specialized teammates and exchange messages — proven for stub teammates; SDK teammates land in Feature #2)
- Learnings:
  - FastMCP from the `mcp[cli]` SDK gave us a clean tool-registration surface; one decorator per tool, broker delegation is trivial
  - In-memory MCP harness (`create_connected_server_and_client_session`) makes tool-layer integration tests fast and reliable, but requires `async with` inside each test (asyncgen fixtures hit anyio cancel-scope-task-mismatch)
  - Stdio smoke via subprocess is essential — it's the only path that proves the registered console script actually works the way Claude Code will invoke it
- Vision shift: none — on track
- Pipeline impact: Feature #1 → done. Feature #2 → next.

### 2026-04-25 — Product Initialized
- Vision document created.
- **Origin:** spun out of an FDE/RepoReactor design discussion. The original framing was "lightweight MCP messaging product for multiple Claude Code instances on one machine." Research surfaced that MCP push doesn't reach the model in Claude Code (verified via `~/dev/mcp-pubsub-spike`), Anthropic's experimental Agent Teams already covers the simple case but is capped (one team/session, no nesting, no recursion, no UI), and the Agent SDK gives full programmatic control over agent loops. Architecture pivoted to: SDK-driven teammates orchestrated by an MCP supervisor, with the lead Claude Code session as the user's interface to a crew.
- **Reframe to platform.** claude-crew is foundational runtime, not a workflow product. RepoReactor and similar products are *consumers* of claude-crew.
- **Key architectural locks:** Agent SDK as v1 sub-agent runtime; MCP server as supervisor + bus; polling-first inbound (hooks deferred); two-level recursion ceiling for v1; Python first; local-machine only; multi-crew via per-crew MCP server instances.
- **MVP scope:** five features. Foundation → persistent teammate → subagent pack + loader → transcript → real-task validation.
- **Open verification items** noted for spike-on-build: SDK memory behavior, subagent context isolation, per-subagent token budgets.

<!-- Add entries as features complete and the product evolves:

### [DATE] — [Feature Name] Completed
- Advances criteria: [which ones]
- Learnings: [what you discovered]
- Vision shift: [any changes to direction, or "none — on track"]
- Pipeline impact: [new features added, priorities changed, features cut]

### [DATE] — Vision Revision
- What changed: [specific sections updated]
- Why: [what prompted the revision]
- Impact: [how this affects the pipeline]
-->

---

## Quick Reference: The Full Cycle

```
1. Initialize (Phase 1) — Fill this document for a new product
2. Prioritize (Phase 2) — Review pipeline, pick what to build
3. Handoff (Phase 3)    — Create FEATURE-*.md via SDD, seed with vision context
4. Reflect (Phase 4)    — After SDD completion, update this doc
5. Repeat from Phase 2
```

**Key files:**
- This document: `doc/PRODUCT-VISION.md`
- Feature specs: `doc/features/FEATURE-[name].md` (created via SDD workflow)
- SDD template: `~/.claude/skills/sdd-workflow/TEMPLATE.md`
