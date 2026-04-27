# Product Vision: claude-crew

**Created**: 2026-04-25
**Last Updated**: 2026-04-26
**Features Implemented**: 5 (MVP complete); 3 post-MVP substrate items (#6, #7, #8) routed next from Feature #5 retro + Feature #6 Phase 2 design discussion

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
| 3 | A developer can run two independent crews concurrently on one machine without interference | Two crews on different repos, both completing tasks, no message bleed between them | Not started |
| 4 | The full crew conversation (lead ↔ teammates ↔ subagents) is observable in real time for debugging and learning | Live observability surface shows messages flowing across all crews; operator can identify a misbehaving prompt by reading the transcript | Not started |
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

Routed from Feature #5's retro substrate findings, plus #8 added during Feature #6 Phase 2 design (tool-execution opacity surfaced as a real gap). Build order: **#6 first** (eliminates the broker-eats-replies pattern that paced every turn of #5), **then #7** (closes the smaller observability gap; cheap follow-up), **then #8** (closes the tool-execution opacity gap; depends on #6's `get_teammate_status` surface). All three together = full substrate observability for the next real-task run.

| # | Feature | Capability | Crit | Size | Status | Notes |
|---|---|---|---|---|---|---|
| 6 | **Telemetry-based teammate liveness.** Replace `TURN_TIMEOUT_SECONDS` hard wall in `claude_crew/sdk_teammate.py` with stream-activity stamping — every event yielded by `receive_response` updates `last_activity_at`. New MCP tool `get_teammate_status(id)` returns `{alive, last_activity_at, current_turn_started_at}`. Add a subprocess-PID liveness probe so genuine death emits `lifecycle: died`. Drop the wall timeout (or move to a 1hr backstop). Operator policy ("no activity > X min → ping") moves to lead code where it belongs. | 1, 4 | 5 | M | next | The structural fix for S1/S2 substrate findings from #5. Tactical fix already shipped at 600s in `b9bc611`; that band-aid still let two timeouts fire during Phase 4. As long as a hard timeout exists on the wire, S2 (stale-response delivery) stays latent and reappears on any teammate doing long work. Eliminating the timeout makes S2 a non-issue by construction. Probably warrants a full SDD pass. |
| 7 | **Subagent-activity envelopes.** Emit `subagent-spawn` and `subagent-result` envelopes from teammates so the lead can observe capability #2 in real time. Closes the Feature #4 v1 documented limit ("subagent activity does not cross the broker"). Verified in #5 retro that capability #2 IS being exercised in real production work — just not bus-observable; we had to confirm by directly asking each sentinel teammate after the fact. | 2, 4 | 5 | S | next | Smaller than #6. Plan-mode-sized. Sequence: ship #6 first, then run a real-task validation (MMM-4b is a clean candidate), then ship #7 once we have a second real-task data point's worth of signal on what the bus should surface. |
| 8 | **Tool-execution telemetry via SDK hooks.** Register `PreToolUse` and `PostToolUse` hooks on every `SdkTeammate` so the substrate observes tool boundaries directly instead of inferring them from stream gaps. Adds `current_tool`, `current_tool_started_at_wallclock`, `current_tool_args_summary`, `last_tool_duration` to the teammate status payload. Each tool start/end is also stamped as activity (closes the "Bash runs for 20min, SDK stream is silent" gap), and each tool call lands in the JSONL transcript with name + duration + outcome. | 1, 4 | 5 | S-M | next | Identified during Feature #6 Phase 2 design discussion (2026-04-26): the substrate's only observability gap after #6 is *tool execution opacity* — `idle_seconds` climbs during a long Bash invocation even though the subprocess is healthy and working. Hooks fix this directly. Depends on #6 shipping (extends `get_teammate_status` surface). 30-min Phase 1 spike: confirm Agent SDK exposes hooks the same way Claude Code does (95% sure it does via `ClaudeAgentOptions(hooks=...)` — verify before committing). |

### Deferred (v2+)

| Feature | Capability | Notes |
|---|---|---|
| Live multi-crew UI (TUI or web) | 4 | JSONL `tail -f` is MVP floor; rich UI ships once the bus shape is stable. |
| Long-poll `wait_for_messages` tool | 1 | Add when polling latency hurts in real use; until then, `get_messages` covers it. |
| Hook-based ambient inbound delivery to lead | 1 | Polling is the v1 contract; hooks add slickness once we know the bus shape is right. |
| Multi-crew concurrent run as a *validated* scenario at work | 3, 5 | Multi-crew is structural in MVP; the validated work scenario follows. |
| Adapter contract spec for non-SDK runtimes | 1 | Forward-looking design work; lock the bus protocol in v1 implementation, formalize the spec in v2. |
| Expanded default subagent pack (reviewer, runner, archaeologist, etc.) | 2 | Grow the pack as real usage reveals what's missing. |
| Token / cost telemetry per crew | 4 | Useful for the "cost is a real concern" feedback we got from Agent Teams users. |

**Status values:**
- `idea` — captured but not yet evaluated
- `next` — selected for implementation, ready for SDD handoff
- `specced` — SDD feature file created, in progress
- `done` — implemented and verified
- `cut` — removed from pipeline (note why)

**Status values:**
- `idea` — Captured but not yet evaluated
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
