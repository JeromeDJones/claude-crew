# BACKLOG

Out-of-scope observations from feature work. Surfaced during implementation, logged here, addressed when prioritized.

Format per workflow.md: `## [YYYY-MM-DD] Feature: <name>` then bulleted entries (What / Where / Why / Suggested action).

---

## [2026-04-30] Pack-declared model not applied at top-level teammate spawn

### `pack.model` flows to subagents but not to top-level teammates
- **What**: `claude_crew/subagents/explorer.md` declares `model: haiku`. When a teammate's Task tool spawns an explorer subagent, that field is honored. But when the lead calls `spawn_teammate(role="explorer")`, `SdkTeammate` falls back to its built-in Sonnet default — the pack's `model` field is silently ignored at the teammate level. Same asymmetry likely applies to `effort`, `maxTurns`, etc.
- **Where**: `claude_crew/factories.py` (teammate factory) vs. the agent-definition loader path used by Task subagents.
- **Why it matters**: Pack files are the right place to declare role-level config. Today the same `role` produces different model behavior depending on whether it's spawned as a teammate or as a subagent — a footgun. Caught live 2026-04-30: spawned an explorer expecting Haiku-shaped recon work; got Sonnet because the lead didn't pass `model=` explicitly.
- **Suggested action**: Fold into Feature #17 (agent definition parity) scope. The factory should consult `pack.model` (and `pack.effort`, `pack.maxTurns`) as defaults when spawn-time overrides are absent. Spawn-time `model=...` still wins; pack provides the role-level baseline.

---

## [2026-04-30] Feature: token-cost-telemetry (#14) follow-ups

### SC-9 scientific-notation guard is fragile at sub-cent costs below ~1e-5
- **What**: `total_cost_usd` is serialized via Python's default `json.dumps` float repr. Probe value `0.0001` renders as `"0.0001"` (safe). Costs below ~`1e-5` (e.g., `0.00001`) would render as `"1e-05"` (scientific notation), which the SC-9 contract forbids and which the dashboard JS may not parse cleanly.
- **Where**: `claude_crew/ui_server.py` `_build_local_instance` per-agent `cost` field; instance summary `cost` field. No `format()` or rounding guard today.
- **Why it matters**: Realistic per-turn costs for cached/short turns can drop into the sub-cent range. A single `1e-05` in JSON breaks the SC-9 contract silently — the dashboard wouldn't crash but the JSON payload would violate the spec.
- **Suggested action**: Add `format(value, ".10f")` (or similar) at the serialization boundary in `_build_local_instance`. Trim trailing zeros if cosmetic. Trivial XS change; defer to a future polish pass or fold into #18 (broker snapshot + dashboard polish).

### Tombstone race-path tests are F14-only; pre-F14 fields had the same gap
- **What**: The `teammate is None` race in `_tombstone_teammate` (called after the teammate self-removed from `_teammates`) was untested before F14 — the F14 sentinel review found the gap because F14 made the path crashable rather than just incomplete. Pre-F14 fields in the `else` branch produced stale tombstones silently; F14 adds three uninitialized vars that turned silence into UnboundLocalError, which is what surfaced it.
- **Where**: `claude_crew/broker.py:_tombstone_teammate` — the `else` branch when teammate is None.
- **Why it matters**: The race is rare but reachable (teammate task self-cleanup before broker kill). Test `test_tombstone_when_teammate_already_removed_does_not_crash` (added 2026-04-30 in F14) covers F14's variant. Other branches may have similar latent issues if a future field is added without remembering this branch.
- **Suggested action**: At each future addition of a new `_at_death` field, mechanically check both the `try`, `except AttributeError`, AND `else` branches initialize it. Consider a single helper `_extract_at_death_fields(teammate, snap_or_none) -> dict` that handles all three branches in one place — eliminates the trip-wire.

### Spec D-4 wording was contradicted by D-8 until the F14 retro
- **What**: D-4 stated "atomic co-assignment — a reader never sees tokens from turn N and cost from turn N-1" but D-8's per-field independence explicitly violates this for malformed ResultMessages. The spec was updated 2026-04-30 to acknowledge the override; would have been better to write D-4 with the override scope from the start.
- **Where**: `doc/features/FEATURE-token-cost-telemetry.md` Phase 2 D-4.
- **Why it matters**: Specs that contain internal contradictions confuse future readers and erode trust in the doc.
- **Suggested action**: Pattern for future SDD specs — when two decisions interact (one constrains, one relaxes), call out the relationship explicitly in BOTH decisions, not just in retrospect.

---

## [2026-04-30] Bug + Feature: multi-instance dashboard aggregation

### Dashboard only shows the local broker — other running instances invisible
- **What**: Each claude-crew instance starts its own UIServer showing only its own broker. The Mission Control design shows N CLI instances in the instance strip, implying all running crews are visible in one place. Currently if you have two instances on ports 7821 and 7822, you need two browser tabs to see both.
- **Where**: `claude_crew/ui_server.py:_build_state()` — hardcoded to one broker; no discovery mechanism exists
- **Why it matters**: The design intent (and SC #4 in PRODUCT-VISION.md) is a single observability surface across all running crews. The current architecture requires the operator to find each instance's port separately and monitor them in isolation.
- **Suggested action**: Instance registry file at `~/.local/state/claude-crew/instances.json` (or similar XDG path). Each UIServer writes `{crew_id, port, pid, started_at}` on startup and removes it on shutdown (atexit + signal handlers). Any dashboard reads the registry and aggregates state from all live instances via their `/api/state` endpoints. The dashboard's instance strip then shows all crews, not just the local one. M/L-size feature — design the registry format and failure modes (stale entries, PID reuse) carefully before implementation.

---

## [2026-04-30] Feature: mission-control-ui (retro follow-ups)

### `ui_server.py` has zero test coverage
- **What**: The entire `ui_server.py` module — `_build_state()`, `_derive_status()`, `_normalize_model()`, the WebSocket handler, and the HTTP routes — has no tests. 387 existing tests all pass, but none touch the new code.
- **Where**: `claude_crew/ui_server.py`; missing `tests/test_ui_server.py`
- **Why it matters**: Any broker refactor that renames `_info`, `_log`, or `_teammates` silently breaks the UI with no failing test to catch it. The `_build_state()` logic (status derivation, model normalization, transcript capping) is untested.
- **Suggested action**: Write `tests/test_ui_server.py` — unit tests for `_derive_status()` and `_normalize_model()`, integration tests for `_build_state()` using a real Broker + StubTeammate, and an HTTP smoke test for `GET /` and `GET /api/state` via Starlette's `TestClient`.

### Broker should expose a `snapshot()` read API
- **What**: `UIServer._build_state()` reads `broker._info`, `broker._log`, and `broker._teammates` directly — all private attributes. This is a fragile coupling: any broker refactor silently breaks `ui_server.py` and no test catches it.
- **Where**: `claude_crew/broker.py` (missing `snapshot()` method); `claude_crew/ui_server.py:_build_state()`
- **Why it matters**: The private attr access is the reason `ui_server.py` can't be unit-tested without a real Broker. A `broker.snapshot() → CrewSnapshot` dataclass would let `_build_state()` be tested with a stub and survive internal broker refactors.
- **Suggested action**: Add `broker.snapshot()` returning a frozen dataclass with `crew_id`, `alive_teammates: list[TeammateSnapshot]`, and `log: list[Envelope]`. Update `_build_state()` to use it. S-size change.

### Multi-crew aggregation — instance strip shows only one crew
- **What**: The Mission Control UI design shows N CLI instances in the instance strip. The current implementation always shows exactly one (the local broker). Multiple independent claude-crew processes each have their own broker and no visibility into each other.
- **Where**: `claude_crew/ui_server.py` (single-broker architecture); no crew registry exists
- **Why it matters**: Doesn't meet the north-star criterion: "both crews' internal conversations stream into a live UI the developer can glance at without context-switching."
- **Suggested action**: Design a crew registry (e.g., a file-based or socket-based discovery mechanism so running UIServers can find each other's brokers). Out of scope until multi-crew use is validated in practice.

### Real token/cost tracking — all agents show $0.000
- **What**: `_build_state()` hardcodes `cost: 0.0` and `tokens: {in: 0, out: 0}` for every agent. The SdkTeammate doesn't currently accumulate cumulative usage stats.
- **Where**: `claude_crew/ui_server.py:_build_state()`; `claude_crew/sdk_teammate.py` (no usage accumulation)
- **Why it matters**: The design shows per-agent cost ($0.612, $0.481, etc.) as a key operational metric. Showing $0.000 everywhere makes the cost column useless.
- **Suggested action**: Route this alongside a usage-telemetry feature. The SDK's response stream likely returns usage stats per turn; accumulate them in `SdkTeammate._total_cost` / `_total_tokens` and surface via `status_snapshot()`.

### Git branch shown as "main" (hardcoded)
- **What**: The instance card and mini-graph metadata row always shows `branch: "main"`. The actual git branch isn't read.
- **Where**: `claude_crew/ui_server.py:_build_state()` — `"branch": "main"` is hardcoded
- **Why it matters**: Misleading when the crew is running on a feature branch. The design uses branch as a contextual identifier for the active work.
- **Suggested action**: At `UIServer.__init__` time, run `git -C <cwd> branch --show-current` via `subprocess.run` (non-blocking, one-time at startup). Cache the result. Fall back to "main" if git is unavailable or cwd isn't a repo.

### Message kind is always "msg" — tool calls and thinking not typed
- **What**: Every envelope in `broker._log` becomes `kind: "msg"` in the transcript. The design's stream columns show three kinds: `msg` (plain text), `tool` (monospace pill with violet ▸), and `thinking` (italic). The difference is visually significant.
- **Where**: `claude_crew/ui_server.py:_build_state()` — `"kind": "msg"` hardcoded
- **Why it matters**: Tool call envelopes and thinking entries look identical to plain messages in the UI. Operators lose the visual signal that distinguishes a model thinking from it calling a tool.
- **Suggested action**: Inspect payload shape: `if isinstance(payload, dict) and payload.get("tool_name")` → `kind: "tool"`; add a `thinking` envelope type in the broker if needed. M-size change requiring broker + ui_server coordination.

---

## [2026-04-29] Observation: recursive crew spawning is one config change away

- **What**: Teammates currently cannot call `spawn_teammate` because the claude-crew MCP server is project-level only. If the MCP server were registered in `~/.claude.json` (user-level), teammates could spawn their own crew members — the broker already handles this correctly regardless of caller.
- **Where**: `~/.claude.json` MCP config; `claude_crew/server.py` spawn_teammate tool
- **Why it matters**: Enables genuine recursive crew expansion — a planner could spawn explorers, a builder could spawn a reviewer, without the lead having to orchestrate every level.
- **Suggested action**: Register claude-crew in `~/.claude.json`, test that a teammate can successfully call `spawn_teammate`, confirm the spawned member appears in `list_crew`. Needs a decision on lifecycle ownership (who kills a teammate spawned by another teammate, not the lead).

---

## [2026-04-28] Feature: agent definition parity + MCP forwarding for SDK teammates

### Primary: extend the loader to cover the full `AgentDefinition` field set

- **What**: `_loader.py`'s `PackFrontmatter` only parses `description`, `model`, `tools`, `effort`, `maxTurns`, `initialPrompt`, `background`. `AgentDefinition` also supports `mcpServers`, `skills`, `permissionMode`, `disallowedTools`, `memory` — none of these are wired into the frontmatter parser. So a `.md` agent file can't declare MCP servers, skills, or a permission mode even though the SDK fully supports them.
- **Where**: `claude_crew/subagents/_loader.py` — `PackFrontmatter` dataclass, `_OPTIONAL` tuple, `_validate_frontmatter()`, `parse_pack_text()`. Same changes needed in `_user_loader.py` if it has its own frontmatter validation.
- **Why it matters**: `tools:` in frontmatter already handles tool restriction per-role — that's the right layer, not `spawn_teammate`. The same logic applies to MCP servers, skills, and permission mode: they're role-level configuration, not spawn-time overrides. An agent definition like this should work but doesn't today:
  ```yaml
  mcpServers:
    - jira
  skills:
    - sdd-workflow
  permissionMode: bypassPermissions
  ```
- **Suggested action**: Add `mcpServers`, `skills`, `permissionMode`, `disallowedTools`, `memory` to `PackFrontmatter` as optional fields. Wire them through `_validate_frontmatter` and `parse_pack_text`. Straightforward — no architecture change, just field additions.

### Secondary: `cwd` and MCP server injection on `spawn_teammate` for spawn-time overrides

- **What**: Two spawn-time params not currently exposed:
  - `cwd: str | None` — working directory for the teammate subprocess. Currently all teammates inherit the directory the MCP server started in. Exposing `cwd` enables multi-repo work (e.g., spawn a builder pointed at `~/dev/my-money-matters` while the lead session runs in `~/dev/claude-crew`). Side effect: `setting_sources: ["project"]` resolves relative to `cwd`, so the teammate automatically picks up the target project's `.claude/CLAUDE.md` and settings — this is probably the right behavior but it means `cwd` changes the full project context, not just the working directory.
  - `mcp_servers: dict[str, Any] | None` — for dynamic/runtime servers not known at agent-definition time. Thread through to `ClaudeAgentOptions.mcp_servers`.
- **Where**: `claude_crew/server.py`, `claude_crew/sdk_teammate.py`, `claude_crew/broker.py`, factory chain.
- **Suggested action**: Add both to `spawn_teammate`, thread through the chain. `cwd` is a clean addition with no unknowns. `mcp_servers` gates on the MCP spike results (see above).

### Spike required: MCP behavior needs empirical verification before design is locked

Three unknowns that must be resolved before Phase 2:

1. **Does `--mcp-config` merge or replace settings-file servers?** When `ClaudeAgentOptions.mcp_servers` is non-empty, the SDK passes `--mcp-config`. If the CLI treats this as a replacement (not a merge), spawn-time `mcp_servers` silently drops globally-configured servers and we need explicit merge logic.

2. **Do globally-configured MCP servers load at all in SDK mode?** The CLI reads `~/.claude/settings.json` via `setting_sources: ["user"]` but it's unverified whether MCP servers defined there are connected when the subprocess runs with `CLAUDE_CODE_ENTRYPOINT=sdk-py`. Same spike as the shell hooks question — needs an empirical test.

3. **Do agent `tools:` lists block MCP tools from connected servers?** If a teammate has `tools: [Read, Grep]` and a globally-loaded MCP server, are that server's tools callable or blocked by the allowlist? If blocked, the agent definition needs to enumerate every MCP tool by name — painful — unless the CLI supports wildcard patterns like `mcp__jira__*`. Needs verification.

**Spike plan**: write a minimal test teammate that connects to a known MCP server (e.g., the Atlassian MCP already configured globally), has a restricted `tools` list, and attempts to call an MCP tool. Run three variations: global-only config, explicit `mcp_servers`, and wildcard in tools list. Results determine the full design.

### Hooks: two systems, two answers

Plugin hooks and "always-include" hooks split across two different mechanisms:

- **Shell-command hooks** (settings.json `hooks:` entries — `PreToolUse`, `PostToolUse`, etc.) — the CLI subprocess reads `~/.claude/settings.json` via `setting_sources: ["user"]`. Whether it also *executes* those hooks in SDK mode (`CLAUDE_CODE_ENTRYPOINT=sdk-py`) is unverified. The interactive harness and the SDK subprocess share the same CLI binary but may differ in hook lifecycle behavior. **Needs a spike before assuming coverage**: add a PostToolUse hook that writes to a log file, spin up a teammate, have it run a tool, check the log. If hooks don't fire in SDK mode this becomes a real gap — either we forward shell hooks explicitly via `ClaudeAgentOptions.extra_args` or document that global shell hooks are lead-only.

- **Python/SDK hooks** (`HookMatcher` with `HookCallback` callables in `ClaudeAgentOptions.hooks`) — these are what claude-crew uses for telemetry, hardcoded in `SdkTeammate._run()`. There's no user-facing way to add always-include Python hooks today. If needed, the right seam is a `base_hooks` param on `SdkTeammateFactory` — merged with telemetry hooks at construction time, applied to every spawn. Low priority until a concrete use case surfaces.

- **Per-role hooks in agent definitions** — `AgentDefinition` doesn't have a `hooks` field; hooks aren't part of the role definition contract. Shell hooks belong in global settings; Python hooks belong at the factory level. Nothing to add here.

---

## [2026-04-28] Feature: skill invocation for SDK teammates (spike first)

- **What**: Allow a subagent to invoke a skill by passing a pointer to its location — not loading the skill's system prompt into the subagent's context, but giving the subagent the ability to *run* the skill as a discrete action (analogous to a lead invoking `/sdd-workflow`). Distinct from `ClaudeAgentOptions.skills`, which injects skill prompt content at session startup.
- **Why it matters**: A builder teammate that could invoke `/sdd-workflow` or `/security-review` mid-task would extend the reach of the workflow skills into multi-agent contexts without requiring the lead to orchestrate every step.
- **Open questions requiring a spike**:
  - How does a subagent invoke a skill — is it a tool call, a prompt injection, or something else?
  - Does the skill run inside the subagent's session context or does it require a fresh session?
  - What's the interaction with the subagent's existing role prompt and tool restrictions?
  - Does the skill's system prompt merge, prepend, or replace the subagent's prompt?
- **Suggested action**: Spike only for now. Do not design the feature until the spike answers what "invoking a skill from a subagent" actually means mechanically. The loader extension feature (above) should ship first — this gates on understanding the subagent skill lifecycle.

---

## [2026-04-27] Feature: #7 subagent-activity envelopes (T5 + sentinel chain follow-ups)

### Phase 3 Scenario 4 BDD comment misleads — `abandoned_batch` vs `subagent_result`
- **What**: The Phase 3 BDD for Scenario 4 says expected output is `subagent_abandoned_batch` with `in_flight_subagents_at_death == 1`. Actual: `subagent_result(tnm_missing=True)` and `in_flight_subagents_at_death == 0` — because `_tombstone_teammate` calls `_end_turn(close_tools=False)` at step 2, draining `_closed_subagent_scratch` before `_close_open_subagents` runs at step 8b. Behavior is semantically correct; the BDD text is wrong.
- **Where**: `doc/features/FEATURE-subagent-activity-envelopes.md` Phase 3 Scenario 4; `tests/test_e2e_subagent_telemetry.py::test_kill_with_scratch_entry_emits_result_from_end_turn`
- **Why it matters**: Future readers using the FEATURE doc as a tombstone-behavior reference get a wrong mental model.
- **Suggested action**: Update the Scenario 4 BDD block to match actual behavior. Add prose: "`_end_turn(close_tools=False)` at tombstone step 2 drains scratch entries before `_close_open_subagents` runs." Trivial doc fix.

### `broker is None` guard in D3 branch skips write but still populates dict
- **What**: In `_on_pre_tool_use` D3 branch, `write_tool_event("subagent_spawn", ...)` is gated on `if broker is not None`. If broker is None, write is skipped but `self._subagent_uses[tool_use_id] = ...` still runs — technically violating F2 (write before store). In practice, hooks only fire when broker is set; None branch is unreachable in production.
- **Where**: `claude_crew/sdk_teammate.py` D3 branch in `_on_pre_tool_use`
- **Why it matters**: Subtle inconsistency if the path ever becomes reachable in tests or future refactors. Inner-4 sentinel flagged as non-blocking.
- **Suggested action**: Either (a) move dict store inside the `broker is not None` block, or (b) add a comment documenting the None branch is unreachable in production. Prefer (b) — skipping dict store would silently break `status_snapshot` in-flight visibility.

### `TaskStartedMessage` / `TaskProgressMessage` not consumed in v1
- **What**: Both explicitly deferred in Phase 2 (co-architect). `TaskStartedMessage` adds spawn→running timing gap; `TaskProgressMessage` is the streaming-activity firehose. Neither has a current consumer.
- **Where**: `claude_crew/sdk_teammate.py` `_collect_response_text` (only `TaskNotificationMessage` handled)
- **Why it matters**: Future feature candidate — streaming subagent activity, richer timing analytics.
- **Suggested action**: Route as a separate feature when a consumer surfaces. `TaskStartedMessage` is S-size; `TaskProgressMessage` re-opens push semantics question and is M-size.

---

## [2026-04-27] Feature: #8 tool-execution telemetry via SDK hooks (sentinel final-review follow-ups + session observations)

### `outcome="orphan_post"` is a sixth value beyond D11's documented five
- **What**: D11 in the F8 spec enumerates five `tool_end.outcome` values: `ok`/`failed`/`interrupted`/`abandoned`/`killed`. The inner-4 fix introduced a sixth — `orphan_post` — for the post-without-pre audit case. Replay tooling consumers reading D11 won't know about it.
- **Where**: `claude_crew/sdk_teammate.py` (orphan-Post writer), `doc/features/FEATURE-tool-execution-telemetry.md` D11 spec
- **Why it matters**: Replay tooling joining `tool_start`/`tool_end` by `tool_use_id` could mis-classify orphan records or fail enum validation.
- **Suggested action**: Update D11 in the FEATURE spec to document the sixth value AND its semantics (audit-only, `duration_seconds: None`, no matching `tool_start`). Alternatively, formalize via an enum in `redaction.py` or a constants module so the source of truth is code, not prose. Trivial doc fix.

### Stale `_get_redaction_version()` ImportError fallback in teammate.py
- **What**: `claude_crew/teammate.py:50-62` has a try-import + `"v1"` string fallback for `REDACTION_VERSION`, with a TODO saying "remove once T1 merged." T1 is merged.
- **Where**: `claude_crew/teammate.py` lines ~50-62
- **Why it matters**: Dead code, confusing to future readers (why is there a fallback?), TODO debt.
- **Suggested action**: Replace with direct `from claude_crew.redaction import REDACTION_VERSION` at module top. Verify no circular-import issue (shouldn't be — redaction has no claude_crew imports). 5-minute change.

### Live-probe `"echo"`-in-args_summary content assertion is model-behavior-dependent
- **What**: `tests/test_e2e_tool_telemetry.py::test_live_a2_probe_real_bash_observed` asserts `"echo"` appears in the `args_summary` of the captured tool_start line. If a future model uses `printf` or `cat <<EOF` to satisfy the prompt, the assertion fails despite the substrate working correctly.
- **Where**: `tests/test_e2e_tool_telemetry.py` live probe assertions
- **Why it matters**: Live probe should test substrate facts (tool_name, tool_use_id pairing, redaction_version, transcript order), not model output choices. Today's assertion is fine but flaky-shaped.
- **Suggested action**: Convert content assertions to "soft/informational" (log but don't fail), keep structural assertions (tool_name=Bash, tool_use_id pairs, redaction_version="v1") as hard assertions. Pattern worth formalizing as a project convention: live-probe assertions check the substrate, not the model. Sentinel-flagged in final review.

### Process pattern: parallel sentinel + co-architect review at gates produced a second convergent catch
- **What**: Sentinel-f8-p1 and co-architect-f8 independently flagged the duplicate-`tool_end` gap (D9 abandon → late Post → second tool_end via Post-without-Pre path) at Phase 2 review. F6's similar convergence was on the in-flight envelope handoff. Two features in a row, two production-impact catches that neither track alone produced.
- **Where**: SDD workflow, Phase 1 + Phase 2 gates
- **Why it matters**: Two-track parallel review is currently a "thing we do" — formalizing it would surface the convergence pattern as a "this would have bit us" indicator and bake the cost (two reviewer teammates) into the process explicitly.
- **Suggested action**: Update `~/.claude/skills/sdd-workflow/SKILL.md` to make parallel sentinel + co-architect review at Phase 1 + Phase 2 a standing requirement, with explicit attention to convergent findings as a high-confidence catch signal. Or, more conservatively, add to the project journal as a confirmed pattern to apply to the next feature, then formalize after one more confirmation.

### Process pattern: lead polling discipline gap
- **What**: Three times this session, lead dispatched teammates and didn't poll for replies until prompted. One reply (sentinel-f8-p1 Phase 2 review) sat in the inbox for ~17 minutes before the lead noticed. The notification mechanism is pull-only; cursor-based `get_messages` requires the lead to actively poll.
- **Where**: lead orchestration during multi-teammate dispatch
- **Why it matters**: Creates visible session-pacing friction. Jerome had to ask "did we check back in?" three times.
- **Suggested action**: Either (a) implement the deferred "Hook-based ambient inbound delivery to lead" feature in PRODUCT-VISION (structural fix), or (b) bake "poll within N minutes of any `send_to` expecting a reply" into lead workflow guidance (process band-aid until (a) ships). Probably (b) first; (a) when MMM-4b real-task validation surfaces enough pain to justify the effort.

---

## [2026-04-27] Feature: #6 telemetry-based teammate liveness (sentinel inner-4 + final review follow-ups)

### MCP-tool-surface coverage gap in T5 e2e
- **What**: T5's `tests/test_telemetry_e2e.py` calls `broker.send()` / `broker.broadcast()` / `broker.get_teammate_status()` directly rather than going through the FastMCP tool surface in `make_server()`.
- **Where**: `tests/test_telemetry_e2e.py` (all 6 scenarios)
- **Why it matters**: Server tools are thin pass-throughs; `test_server.py` covers the MCP layer separately. But for "real wire test" coverage of `_err("teammate_dead", ...)` JSON shape and the `skipped_dead` field's serialization back through FastMCP, an end-to-end through `make_server()` would close the small remaining gap.
- **Suggested action**: When Feature #7 ships, add ≥1 e2e scenario per substrate feature that exercises the full MCP wire (server.py → broker → teammate). Could refactor T5's scenarios to share a common server-fixture, or add a single multi-scenario "wire test" alongside.

### `POST_INTERRUPT_DRAIN_SECONDS` monkeypatch in 2 T4 tests is a fake-fidelity issue
- **What**: Two tests in `test_sdk_teammate.py` (`test_backstop_fires_interrupt_succeeds` and one other) monkeypatch `POST_INTERRUPT_DRAIN_SECONDS = 0.05` because `ProgrammableSDKClient._hang` stays True after `interrupt()` is called.
- **Where**: `tests/test_sdk_teammate.py`, plus the underlying fake at `tests/fakes/programmable_sdk_client.py`
- **Why it matters**: The A2 live probe (Feature #6 T5) confirmed the real SDK terminates `receive_response` on `interrupt()` — so production has no hang here. The monkeypatch is purely a fake-shape band-aid, not papering over a real bug. Cosmetic test smell only.
- **Suggested action**: Enrich `ProgrammableSDKClient` to flip `_hang=False` (or terminate the receive_response generator) when `interrupt()` is called. Removes the monkeypatch. ~10-line change.

### SDK exception name-matching is brittle to SDK refactor
- **What**: `sdk_teammate.py:358-363` matches `"ProcessError"`, `"CLIConnectionError"`, `"BrokenPipe"` substrings against `type(exc).__name__` to decide whether to set `_death_in_flight_envelope` and `_death_suspected`.
- **Where**: `claude_crew/sdk_teammate.py` lines ~358-363 (exception handling in `_handle_one_turn`)
- **Why it matters**: An SDK class rename or a wrapping exception silently bypasses the in-flight handoff path. Worker would then send a generic `api_error` envelope and `_death_in_flight_envelope` would never be set — SC-5b clause 1 silently fails.
- **Suggested action**: Replace substring match with `isinstance` against the actual SDK exception types from `claude_agent_sdk.types`. The earlier SDK spike showed the import surface is opaque from `sdk_teammate.py` today, so this requires a small import re-arch. Pin the SDK version in `pyproject.toml` simultaneously to bound upgrade risk.

### Probe failure inside `_handle_teammate_death` exits the poll task without retombstoning
- **What**: If `teammate.status_snapshot()` raises an exception other than `AttributeError` (only that one is caught at `broker.py:148`), the death handler propagates up to `_liveness_poll_loop`, gets logged, and the loop returns — leaving the teammate alive in `_info` forever.
- **Where**: `claude_crew/broker.py:148` (narrow except clause) interacting with `claude_crew/sdk_teammate.py:_liveness_poll_loop`
- **Why it matters**: Edge case (probe-inside-handler is rare). But the failure mode is silent and unrecoverable without operator intervention.
- **Suggested action**: Either (a) catch broader inside `_tombstone_teammate` and continue with degraded death record, or (b) make `_liveness_poll_loop` retry the death handler on next tick rather than exiting on first handler failure. Prefer (b) — failure is observable and retried, no silent leak.

---

<!-- Add new entries above. Keep this file ordered newest-first. -->

## [2026-04-29] Feature: agent-config-extension (#10)
- **What**: `spawn_teammate` MCP tool accepts `permission_mode: str | None` but does not validate it against `_VALID_PERMISSION_MODES` at the server/broker layer. Pack-declared values are validated at parse time; spawn-time override is not.
- **Where**: `claude_crew/server.py` → `broker.spawn_teammate` → factory chain
- **Why it matters**: Invalid strings reach `ClaudeAgentOptions` and are silently ignored by the SDK — caller gets no error, spawn appears to succeed with wrong behavior
- **Suggested action**: Import `_VALID_PERMISSION_MODES` from `_loader.py` into `server.py`; validate spawn-time `permission_mode` at the MCP tool boundary and return `_err("invalid_argument", ...)` on failure
