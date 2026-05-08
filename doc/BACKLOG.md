# BACKLOG

Out-of-scope observations from feature work. Surfaced during implementation, logged here, addressed when prioritized.

Format per workflow.md: `## [YYYY-MM-DD] Feature: <name>` then bulleted entries (What / Where / Why / Suggested action).

---

## [2026-05-07] Observation: agent-name casing collision shadows bundled subagents

### Bundled lowercase `explorer` / `planner` shadowed by Claude Code's `Explore` / `Plan`
- **What**: A claude-crew teammate dispatched a subagent by the bundled-pack name `explorer` (lowercase) and got back `Agent type 'explorer' not found. Available agents: builder, Explore, feature-planner, general-purpose, Plan, refactor, repo-reactor:rr-feature-reviewer, …`. The SDK's available-agent set has `Explore` and `Plan` (capitalized — Claude Code built-ins) but **neither** of the bundled lowercase entries (`explorer`, `planner`) appear. The bundled pack ships `claude_crew/subagents/{explorer,planner,general_purpose}.md`; `general-purpose` survives the merge unscathed because no built-in shadows it, but the other two are gone.
- **Where**: Suspected interaction between `claude_crew/subagents/_user_loader.merge_packs` and the SDK's CLI-level agent registration. The pack-level merge keys are case-sensitive (`explorer` vs `Explore` would coexist as distinct keys), so the collision is most likely happening at the SDK boundary — either the CLI normalizes names to a canonical case for collision detection, or some other layer is collapsing `explorer`→`Explore`. Needs tracing.
- **Why it matters**: Anything (teammate prompt, peer list, hand-coded dispatch) that names the bundled subagent with its declared lowercase key gets a "not found" at runtime. The peer-list rendering in `teammate_prompt._build_subagent_list` advertises whatever's in the `agents` dict — if that dict has `explorer` but the SDK only honors `Explore`, the teammate is told a lie about its options. Dual to the rr-implementor experience this session: the dispatch-name and the rendered-name must match, or the operator sees fail-soft errors.
- **Suggested action**: Spike. Run a teammate locally with `agents={"explorer": <def>, "Explore": <def>}` and verify whether the SDK accepts both, picks one, or warns. Then either (a) rename the bundled packs to match the host's canonical case (`Explore.md`, `Plan.md`) — keeps coexistence intact but breaks anyone referencing the lowercase form; (b) emit a startup diagnostic via #25's channel when a bundled-pack key case-conflicts with a host built-in (operator-visible signal); or (c) both. Decide once the SDK behavior is pinned.

---

## [2026-05-07] Feature: plugin-projectpath-prefix-match (#26)

### H1 — `_resolve_agent_def` vs spawn-factory synthetic-AgentDef divergence
- **What**: When a role is **not** in `merged_pack` and `extra_tools` / `extra_skills` are passed to `spawn_teammate`, `factories.py` takes two different paths. The spawn factory at `claude_crew/factories.py:294-301` constructs a *synthetic* `AgentDefinition(description="", prompt="", tools=extras, skills=extras)` so the teammate can run with the operator-supplied extras. `_resolve_agent_def` at `claude_crew/factories.py:362-365` returns `None` for the same case. Result: alive teammate with extras, but `Broker._configs[teammate_id] = None` → empty UI config panel even though the teammate is functional.
- **Where**: `claude_crew/factories.py` — the `factory()` closure (`:252-350`) and the `_resolve_agent_def` helper (`:357-371`) both built off `merged_pack` but diverge on the unknown-role-with-extras path.
- **Why it matters**: This is a code-read finding from the #26 Phase 1 spike — not the operator-reported incident (which was the `projectPath` filter, fixed in #26). It's a real asymmetry: the dashboard's transparency surface lies for any spawn shaped as "unknown role + extras." Today this is rare (most spawns are by known role), but it ambushes the unusual case silently. The spec's Phase 2 scope split it off explicitly.
- **Suggested action**: Extract a single shared helper that takes `(role, merged_pack, extra_tools, extra_skills)` and returns either a real or synthetic `AgentDefinition`. Both `factory()` and `agent_def_resolver()` call it. Eliminates the silent disagreement at the source. Expected size: small (single-task slice).

---

## [2026-05-06] Feature: startup-diagnostics-dashboard (#25)

### Style ERROR-tier startup-diagnostic badge in the dashboard
- **What**: `StartupNoticeRow` assigns class `startup-notice-badge-error` for ERROR-level diagnostics, but `claude_crew/ui/dashboard.html` defines CSS only for `.startup-notice-badge-info` and `.startup-notice-badge-warning`. ERROR badges render the `<span>` with the correct class but no background or color — they fall back to inherited text only.
- **Where**: `claude_crew/ui/dashboard.html` style block.
- **Why it matters**: Spec edge case "logger emits an ERROR" prescribed "WARN-style badge but tinted red." No owned AT exercises ERROR-tier rendering, so the gap fell through both slice-review (Low) and feature-review (Low). Captured ERROR records still appear with level text and message, only visual differentiation is missing — non-blocking.
- **Suggested action**: Add `.startup-notice-badge-error` rule mirroring `.startup-notice-badge-warning` with error-tier oklch colors. Add a fixture-based unit test that plants an ERROR-level record via the source logger and asserts the rendered class + CSS rule resolves.

### Drop redundant catch-and-reraise around `record.getMessage()` in `StartupDiagCollector.emit`
- **What**: `claude_crew/diagnostics.py::StartupDiagCollector.emit` contains a nested `try: raw = record.getMessage() except Exception: raise` block. The inner `try/except` is a no-op — it catches and unconditionally re-raises, which is identical to no try/except at all. The outer `except Exception: self.handleError(record)` already covers `getMessage()` failures.
- **Where**: `claude_crew/diagnostics.py::StartupDiagCollector.emit`.
- **Why it matters**: Readability smell flagged Medium in slice 1's review. Cruft from an earlier draft where the implementor was about to wrap `getMessage()` specifically before deciding the outer `except` covered it. Doesn't affect correctness or tests; Medium findings don't trigger REQUEST-CHANGES.
- **Suggested action**: Delete the inner `try/except` block. Keep the outer `except Exception: self.handleError(record)` per `logging.Handler` convention.

### Refactor `_direct_attach_fallbacks` to return restore pairs explicitly
- **What**: `claude_crew/factories.py::_direct_attach_fallbacks` passes level-restore state back via `collector_handler._restore_levels = []` — a dynamically-set attribute on the `StartupDiagCollector` instance that's not declared in `__init__`. The pattern works (functions have `__dict__`) but creates implicit coupling between the factory module and the handler's internals.
- **Where**: `claude_crew/factories.py::_direct_attach_fallbacks` and its caller in `default_factory()`'s `finally` block.
- **Why it matters**: Surprising on inspection. Reviewer flagged Medium in slice 3. Scope is contained within `default_factory()` so blast radius is small, but a future reader will not know to look on the handler for the restore list.
- **Suggested action**: Change `_direct_attach_fallbacks` to return `tuple[list[Logger], list[tuple[Logger, int]]]` (attached loggers + restore pairs). Caller stores both as locals, uses the second in `finally` to restore. Drops the handler-attribute coupling entirely.

### Planner heuristic: include `claude_crew/server.py` in `taskTouches` when slice introduces factory→broker wiring
- **What**: The `factory-capture-wire` slice in #25 needed an edit to `make_server()` to thread `factory.startup_diagnostics` into `Broker(startup_diagnostics=...)`. The breakout's `taskTouches` listed only `claude_crew/factories.py` — `server.py` was not declared. Slice-touches-check fired; reviewer adjudicated as breakout planning gap (edit was necessary, correct, minimal).
- **Where**: RepoReactor `breakout-feature` planner heuristic / template guidance.
- **Why it matters**: Pattern recurs whenever a slice produces output that must reach the broker — `make_server()` is always the wiring point. The `taskTouches` enforcement is a correct safety net, but the planner should anticipate this so the violation doesn't fire on every factory→broker feature.
- **Suggested action**: Update the breakout-feature planner skill (or template comments) to call out: "if a slice changes `claude_crew/factories.py` in a way that produces data the broker consumes, include `claude_crew/server.py` in that slice's `taskTouches`." Optional: add a synthetic check in `breakout-schema-check.sh` that flags factory-touching slices missing `server.py`.

### Reconcile `unknown_skill` category scope-mismatch in startup-diagnostics-dashboard spec
- **What**: Spec includes `unknown_skill` in the six-category classifier table, AT #3 asserts capture of an `extra_skills` validation WARN (unit-form), but **Out of Scope** explicitly excludes per-spawn `extra_skills` warnings. The implemented v1 capture window covers `_warn_unknown_skills` calls inside `build_merged_pack` — confirm whether any production path actually emits a record matching `category=unknown_skill` at startup time, or whether the category is dead at runtime today.
- **Where**: `claude_crew/diagnostics.py::classify` (category table), `claude_crew/subagents/_user_loader.py::_warn_unknown_skills` (potential emit site).
- **Why it matters**: Plan-review MED-01 raised this 2026-05-06. The category and AT both ship; if `_warn_unknown_skills` is only invoked on per-spawn `extra_skills`, then `unknown_skill` is unreachable at startup and the unit test in `test_diagnostics.py` is a synthetic-only path. Either (a) confirm a startup-time emit site exists and document it, (b) remove the category from v1 and update AT #3, or (c) widen the capture surface to include per-spawn `extra_skills` warnings (changes scope).
- **Suggested action**: Phase 1: trace `_warn_unknown_skills` callers to confirm whether any startup-only invocation exists today. If yes, document the trigger in the feature doc. If no, choose between dropping the category (less code) or expanding the capture surface in a follow-up feature (more value, more scope).

---

## [2026-05-01] Feature: agent-definition-parity (#17)

### Existing user-level packs declaring `memory: user` now emit spawn-time WARN
- **What**: User-level agent files at `~/.claude/agents/{sentinel,builder,scout}.md` (and possibly other operator configs) declare `memory: user` as frontmatter. Pre-#17 this field was silently ignored by forward-compat. Post-#17 it parses successfully into `PackFrontmatter.memory` and triggers the D-8 WARN at every top-level teammate spawn for those roles ("ClaudeAgentOptions has no memory carrier — this field applies only to subagent dispatch contexts"). The WARN is correct (memory has no teammate-path carrier) but is a noise regression for operators who had these packs working pre-#17.
- **Where**: `~/.claude/agents/sentinel.md`, `~/.claude/agents/builder.md`, `~/.claude/agents/scout.md` (operator-level config, not in repo). Spawn-time WARN at `claude_crew/sdk_teammate.py:993-999`.
- **Why it matters**: Common-case impact is small — these packs are typically dispatched as Task subagents (which legitimately use `memory`), not spawned as top-level teammates. But any operator who DOES spawn them as teammates (e.g., for `/sdd-workflow` co-architect via `mcp__claude-crew__spawn_teammate role=feature-planner`) sees a noisy WARN they didn't see before. Sentinel M-2 at #17 final review.
- **Suggested action**: No code change. Document in #17 retrospective. Operators who don't want the WARN remove `memory: user` from their pack frontmatter (it was always a no-op on the teammate path; #17 just made it visible). If WARN noise becomes a real complaint, consider downgrading to DEBUG when `memory == "user"` (the SDK default — declaring it explicitly is essentially a no-op even on subagent dispatch).

### Optional-AgentDef-fields drift guard is incomplete (test asserts hardcoded set, not SDK derivation)
- **What**: `tests/test_user_loader.py::test_optional_fields_set_equals_expected` guards `_OPTIONAL_AGENTDEF_FIELDS` against drift but compares against a hardcoded `expected` set written into the test. If the SDK adds a new optional field to `AgentDefinition` (e.g., a future `tracing` or `quotas` field) and nobody updates `_OPTIONAL_AGENTDEF_FIELDS` AND nobody updates `expected`, the shadow-drop guard misses the new field silently. Same incomplete-guard pattern as the #22 stale-required-keys finding.
- **Where**: `tests/test_user_loader.py` test method; `claude_crew/subagents/_user_loader.py:_OPTIONAL_AGENTDEF_FIELDS` constant.
- **Why it matters**: Low probability today. Grows with each SDK version. The shadow-drop WARN is the only operator-facing signal that a project-level pack silently cleared a lower-precedence field; missing the new field means new operator footguns aren't caught.
- **Suggested action**: Strengthen the test to derive `expected` from `AgentDefinition.__dataclass_fields__` minus the required-in-pack subset (`description`, `model`, `tools`, `prompt`). The required-subset is itself a small enumeration but its drift is easier to catch (pack-load fails on missing required fields). Sentinel L-2 at #17 final review.

---

## [2026-05-01] Feature: global-skills-for-sdk-teammates (#23)

### general-purpose teammate context size grew with #23 SC-6 — measure and consider general-purpose-light variant
- **What**: SC-6 flipped bundled `general_purpose.md` from `settingSources: []` → `settingSources: ["user", "project"]` (required for skill discovery). Side effect: a spawned `general-purpose` teammate now loads CLAUDE.md again, partially undoing #11's lightweight-context win for this specific role. This is intentional per #23 A-3 (parity-of-invocation requires the setting sources), but the token-cost delta at live spawn is unmeasured.
- **Where**: `claude_crew/subagents/general_purpose.md` frontmatter; observable post-spawn in `_system_prompt` size and at the SDK boundary in initialize-time tokens. `test_teammate_prompt.py`'s prompt-size assertions test the pack-load path, not live spawn — they will not catch this.
- **Why it matters**: As `/crew-showcase` and similar dogfood patterns spawn `general-purpose` teammates routinely, a bigger system prompt = more tokens per turn = real cost over many sessions. Operators who don't need skills on `general-purpose` are paying for them.
- **Suggested action**: After #15 (reviewer/runner pack), measure the token delta on a live `general-purpose` spawn pre/post-#23. If the delta is meaningful (>~1k tokens), consider a bundled `general-purpose-light` variant (no skills, settingSources=[]) for high-fanout dispatch scenarios. Operators who want narrow defaults today can override via `~/.claude/agents/general-purpose.md`.

### ~~Promote vision row #25 (startup diagnostics on dashboard) from idea → next when scope allows~~ — RESOLVED 2026-05-07
- **What**: #23 ships the skill-discovery WARN at pack-load time. The WARN goes to stderr only — Mission Control cannot surface it because pack-load happens before any teammate envelope exists. Documented in README under Custom Roles → Skills, but stderr-only is a weak operational story; operators must tail the claude-crew server stderr to catch their config errors.
- **Where**: `claude_crew/subagents/_user_loader.py:_warn_unknown_skills` and the existing pack-shadow INFO logs. Future home is `BrokerSnapshot` reserved field or sibling channel that `UIServer` reads. Vision row #25 already filed.
- **Why it matters**: As skill+role surface grows (post-#15 reviewer/runner), more configs will be authored, more typos will happen. Today they're invisible until a runtime invocation fails. A startup-notices panel on the dashboard closes the operator-feedback loop and serves future startup-time diagnostics on the same channel (frontmatter typos, MCP issues, bundled-pack shadowing).
- **Resolution**: Shipped 2026-05-07 as #25 (`doc/features/FEATURE-startup-diagnostics-dashboard.md`). `BrokerSnapshot.startup_diagnostics` reserved field carries a frozen tuple of `StartupDiagnostic` records captured during `build_merged_pack()`; dashboard renders them in a collapsible Startup Notices panel. Five remaining follow-ups (ERROR-tier badge CSS, no-op try/except cleanup, fallback-state refactor, planner heuristic, `unknown_skill` category reconciliation) tracked under [2026-05-06] above.

---

## [2026-05-01] Subagent dispatch telemetry gaps — F7 misses Agent dispatches, args_summary blind

### F7 subagent tracking and redaction allowlist both miss the `Agent` tool
- **What**: Two adjacent gaps in subagent observability surfaced during `/crew-showcase` re-run on 2026-05-01. (1) F7 subagent tracking did not fire when a teammate dispatched a subagent that F8 reported as the `Agent` tool. Transcript on `t-464ae0769323` recorded `tool_start`/`tool_end` for `Agent` (37.4 s, outcome=ok), but `last_subagent_completed` stayed null and no `subagent_start`/`subagent_end` records were written — `PreSubagentUse`/`PostSubagentUse` hooks aren't catching what `PreToolUse`/`PostToolUse` sees as `Agent`. (2) `Agent` is missing from the v1 redaction allowlist (`Bash`, `Task`, `WebFetch` per CLAUDE.md and `claude_crew/redaction.py`). Result: every Agent dispatch logs `args_summary: null`, so the dashboard cannot show *which* subagent role was invoked.
- **Where**: F7 hook wiring in `claude_crew/sdk_teammate.py` (PreSubagentUse/PostSubagentUse handlers); F8 redaction allowlist in `claude_crew/redaction.py` and any redaction tests pinning the v1 allowlist set.
- **Why it matters**: These gaps compound the just-logged fail-soft pathology above. When a dispatched subagent fabricates output, the operator's only signal is the prose itself — the dashboard cannot show "subagent X was dispatched with task Y" because (a) the subagent_completed slot is empty and (b) args_summary is null. The crew-showcase re-run hit this directly: explorer-2 dispatched some subagent (we still don't know which role) that fabricated its 3-file summary, and the telemetry surface offered no way to identify the responsible subagent. Naming likely cause: the SDK exposes the dispatch tool as `Agent`, while F7 hooks and the redaction allowlist were both written assuming the Claude Code name `Task`. One word, two surfaces, both miss.
- **Suggested action**: Two-part. (1) Add `Agent` to the v1 redaction allowlist alongside `Task` (or unify on whichever name the Agent SDK actually emits) — bump to v2 if schema callers care. Capture `subagent_type` from the tool args so the dashboard can show the dispatched role. (2) Verify F7's PreSubagentUse/PostSubagentUse hooks fire on `Agent` dispatches — if the SDK uses a different hook event for this tool, wire it. Add a live SDK test that dispatches a subagent and asserts both `last_subagent_completed` is populated AND `last_tool_completed.args_summary` names the role. This is the natural next step after the fail-soft contract fix lands — refusing-loud doesn't help if the operator can't see which subagent did the refusing.

---

## [2026-05-01] Pack subagents fail-soft when handed tasks outside their tool surface

### Subagents fabricate output instead of refusing when asked to do work their tools can't do
- **What**: When a teammate dispatches a pack subagent (e.g. `general-purpose`) with a task that requires a tool the subagent doesn't have (most commonly: Bash), the subagent fail-softs — it produces plausible-looking but fabricated output instead of refusing. Caught live 2026-05-01 during a `/crew-showcase` run: `tour-delegator` dispatched a general-purpose subagent with a `find ... -exec wc -l` task. The subagent returned a clean markdown table of file paths and line counts that looked correct at a glance — none of the files existed in the repo. Paths like `agents/orchestrator.py` and `tools/bash.py` were drawn from training data, not from a Bash invocation that returned an error.
- **Where**: `claude_crew/subagents/general_purpose.md` (contract), `explorer.md`, `planner.md`. The `general_purpose.md` contract says "Run shell commands (you have no Bash tool by design — do not ask the caller to give you one)" but never instructs the subagent to STOP and refuse when handed a shell-requiring task. Same gap exists implicitly in the other pack files for any tool they lack.
- **Why it matters**: Silent fabrication is the worst failure mode for a delegation substrate. The lead trusts the subagent's output as if it came from a real tool call. Errors that should fail loud and route back as `tool_error` envelopes are instead laundered through the subagent's prose into confidently-wrong reports. This violates the project's stated principle "fail loud and fail fast" (rules/coding-standards.md). The hallucination is shaped by the role contract — a stricter contract would surface the failure.
- **Suggested action**: Two-line contract addition to each pack file under "You MUST": `If a task requires a tool you do not have, refuse with a single line stating which tool is missing and stop. Do not attempt to substitute reasoning or training-data recall for tool invocation.` Pair with one live SDK test per pack role that asks the subagent to do something requiring a missing tool and asserts the response (a) names the missing tool and (b) does NOT contain fabricated content matching a known shape. Folds naturally into the same scope as the existing 2026-04-30 "Teammate vs. subagent system-prompt parity" backlog item.

---

## [2026-04-30] Teammate vs. subagent system-prompt parity

### Pack system prompt assumes subagent context; teammates get the same prompt but different tools
- **What**: `claude_crew/subagents/*.md` pack files are written for SUBAGENT invocation. `general_purpose.md` says "You MUST NOT spawn subagents (you have no Task tool by design — subagents are leaves)" — true when spawned as a Task subagent, FALSE when spawned as a top-level teammate via `spawn_teammate(role="general-purpose")`. Teammates have Task tool access; subagents don't. The system prompt is the same for both, so a teammate is told it has no Task tool while in fact it does.
- **Where**: `claude_crew/subagents/general_purpose.md` (and any future role used both ways), the teammate spawn path in `factories.py` / `sdk_teammate.py` that consumes the pack.
- **Why it matters**: Caught live 2026-04-30 — the persistent Opus co-architect (a teammate) burned 1.3M input tokens across 6 turns reading raw files in its own context instead of delegating to explorer subagents. The instruction wasn't there to delegate. Per-prompt nudges work but should be system-prompt-level for any non-leaf teammate.
- **Suggested action**: Two-part. (1) Append a "Delegate raw file reads to explorer subagents" section to teammate system prompts at spawn time (factories.py extension). (2) Consider splitting pack content into `subagent_prompt` and `teammate_prompt` blocks, or adding a `teammate_addendum` field to PackFrontmatter. Folds naturally into Feature #17 (agent definition parity) scope OR a separate small feature.

---

## [2026-04-30] Persistent crew teammates accumulate context cost across turns

### SDK session state is cumulative; persistent agents get expensive across many turns
- **What**: `SdkTeammate` calls `client.query(prompt, session_id="<crew_id>-<teammate_id>")` per turn. The Anthropic CLI maintains conversation state per session_id, so every turn re-includes ALL prior turns in the model's context window. Token cost scales with conversation length. F14 cost telemetry confirmed this empirically — a co-architect with 6 turns hit 1.3M input tokens, 9× more than a reviewer with 1 turn (similar output sizes).
- **Where**: `claude_crew/sdk_teammate.py:794-797` (session_id construction).
- **Why it matters**: Persistent crew teammates that take many lead-driven prompts during a feature design pass become disproportionately expensive. Anthropic's prompt caching mitigates (cache reads at 10% rate; the 1.3M-token co-architect cost $1.75 instead of $19.50), but it still scales linearly with turn count.
- **Suggested action**: No code change needed — this is intrinsic to how SDK sessions work. Operationally, restart-per-feature beats persist-across-features for high-turn-count roles like co-architect. Document this in PROJECT-VISION's "operational notes" section so future operators know. Could ALSO be addressed by a future feature: `spawn_teammate(reset_session=True)` or a `restart_teammate(id)` MCP tool that flushes the SDK conversation while keeping the broker registration.

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

## [2026-05-02] Feature: token-cost-telemetry (#14) — UX gap
- **What**: Token/cost telemetry rolls up only at end-of-turn (when `ResultMessage` arrives from the SDK). For long-running parent turns with heavy subagent dispatch (e.g., a sentinel review fanning out 30+ Agent tool calls over 5-7 minutes), the dashboard reads `0/0/$0.00` for the entire duration. Tokens populate cleanly when the turn completes — observed via `total_input_tokens=143095` after a 7-minute sentinel run that had shown 0 the whole time.
- **Where**: `claude_crew/sdk_teammate.py:_collect_response_text` (extracts only from `ResultMessage.usage`), per #14 D-1 design decision (`ResultMessage` is single source).
- **Why it matters**: Operator-facing observability gap. The dashboard's whole point is showing in-flight cost; long parallel dispatches are exactly the case where you want to watch cost grow. Currently you watch nothing for minutes.
- **Suggested action**: Investigate whether SDK emits incremental usage events on `AssistantMessage` or via tool-result callbacks. If yes, augment `_collect_response_text` to surface mid-turn updates. May conflict with #14 D-1 ("`ResultMessage` is single source") — re-evaluate that decision against the operator-experience signal.

## [2026-05-02] Feature: claude-code-agent-format-compatibility (#15) — RESOLVED 2026-05-02
- **What**: SDK runtime behavior on `AgentDefinition(tools=[])` was unverified at merge time.
- **Resolution**: Live test added at `tests/test_format_compat_e2e.py::TestLiveSdkToolsEmptyEnforcement`. Spawns a parent teammate with full tools and an `agents` dict containing a `probe-no-tools` role declaring `tools=[]`. Parent dispatches the probe via Task and asks it to read a file with a unique marker. Test passes when marker does NOT appear in parent's reply (SDK enforced empty tools). Verified against real SDK 2026-05-02 — marker was not present, **SDK correctly enforces `tools=[]`**. Phase 2 security decision validated at the SDK boundary.

## [2026-05-02] Feature: claude-code-agent-format-compatibility (#15)
- **What**: `_warn_shadow_drop` enhancement (Q-9 deferred). When a user-level pack's `name:` value collides with a project-level pack's stem (or vice versa) and the underlying file stems differ, the WARN message could name BOTH stems alongside the canonical name to give operators visibility into the "I named the file `runner.md` but it shadowed `senior-runner` because both had `name: runner`" failure mode.
- **Where**: `claude_crew/subagents/_user_loader.py:_warn_shadow_drop` (lines ~362-431) and the upstream dict-shape — needs path data plumbed through.
- **Why it matters**: Post-#15 shadow detection works correctly via canonical-name dict-key comparison, but the WARN message names the canonical name only. Cross-stem mismatches are silent at the message level. Operator has to trace through file stems manually to debug "why did my project pack shadow the wrong user pack?"
- **Suggested action**: Plumb a path-by-canonical-name mapping through `discover_dir → load_user_agents → load_project_agents → build_merged_pack` so `_warn_shadow_drop` can name both stems. ~30-line follow-up.

## [2026-05-02] Feature: claude-code-agent-format-compatibility (#15)
- **What**: `_split_frontmatter` rejects Windows `\r\n` line endings in YAML frontmatter delimiters. Pre-existing limitation, not introduced by #15. Affects operators authoring agent files on Windows.
- **Where**: `claude_crew/subagents/_loader.py:_split_frontmatter`. Hard-codes `"---\n"` as the delimiter; `\r\n` files raise `PackLoadError("does not start with YAML frontmatter delimiter '---'")`.
- **Why it matters**: claude-crew's agent format-compatibility promise is "consume operator's existing files." A Windows-authored agent file will fail to load even if content is valid Claude Code format. #15 spec A-4 noted explicitly out of scope.
- **Suggested action**: Normalize `\r\n` → `\n` at the top of `_split_frontmatter` before delimiter checks. Three-line fix.

## [2026-05-02] Feature: claude-code-agent-format-compatibility (#15) — process
- **What**: Lockstep site inventories for rename refactors should be auto-generated, not hand-curated.
- **Where**: SDD workflow Phase 2 spec authoring (`~/.claude/skills/sdd-workflow/SKILL.md` or `TEMPLATE.md`).
- **Why it matters**: #15's SC-12 spec said 13 lockstep sites for the `_LEAF_SUFFIX` rename; pre-T4 sentinel sweep found 21 (8 missed including the load-bearing usage site). Hand-curated lists undercount by ~40%. Mid-build sentinel caught it but it's brittle — the next refactor task without a sentinel pass would ship broken.
- **Suggested action**: Add to TEMPLATE.md / SDD Phase 2 guidance: "for any rename or refactor task, the SC's lockstep inventory must be the verbatim output of `grep -rn <symbol>` from repo root." Two-minute step at spec time, eliminates an entire class of mid-build sentinel finds.

## [2026-05-01] Feature: current-tool-badge-prominence (#22)
- **What**: T5 sad-path scenario `test_unreachable_remote_no_now_wallclock_leakage` verifies the `_unreachable_instance` helper output and JSON round-trip in isolation, but does NOT exercise the actual `_build_state` aggregation path under multi-instance setup.
- **Where**: `tests/test_e2e_badge_pipeline.py:174` (current test) and `tests/test_e2e_multi_instance.py` (where the gap should be closed).
- **Why it matters**: A future refactor of `_unreachable_instance` that adds `now_wallclock` to its output would pass the current test. The full HTTP-aggregation path under a real unreachable remote is not covered by the F22 contract test. Production risk is low because `_unreachable_instance` is the single source for unreachable-instance dicts, but the regression-guard guarantee is weaker than implied.
- **Suggested action**: Add an assertion to one of the multi-instance E2E tests in `test_e2e_multi_instance.py` that, when the aggregation path receives an unreachable instance, the resulting JSON has no `now_wallclock` field on that instance. ~10 line follow-up; same fixture as existing test.

## [2026-04-29] Feature: agent-config-extension (#10)
- **What**: `spawn_teammate` MCP tool accepts `permission_mode: str | None` but does not validate it against `_VALID_PERMISSION_MODES` at the server/broker layer. Pack-declared values are validated at parse time; spawn-time override is not.
- **Where**: `claude_crew/server.py` → `broker.spawn_teammate` → factory chain
- **Why it matters**: Invalid strings reach `ClaudeAgentOptions` and are silently ignored by the SDK — caller gets no error, spawn appears to succeed with wrong behavior
- **Suggested action**: Import `_VALID_PERMISSION_MODES` from `_loader.py` into `server.py`; validate spawn-time `permission_mode` at the MCP tool boundary and return `_err("invalid_argument", ...)` on failure

## [2026-05-01] Feature: tool-events-dashboard-stream (#19)
- **What**: Agent-header `current_tool` badge is plaintext in a secondary scrolling status row, with no animation on the tool name itself (only the status dot pulses). Under wide layouts with many active agents, the badge may require horizontal scroll to see.
- **Where**: `claude_crew/ui/dashboard.html` AgentStreamColumn component (lines 467–508)
- **Why it matters**: SC-7 of #19 deliberately keeps the tool-event stream completed-only — the in-flight visibility job belongs to this badge. If the badge isn't prominent, a long-running tool (90s Bash) shows zero stream signal until completion, which leaves the original "operator stares at silence" problem partially unsolved (per co-architect pushback C in #19 Phase 1 review).
- **Suggested action**: Promote `current_tool` to the pinned agent header alongside the avatar/name; add a subtle pulse or shimmer when a tool has been in-flight >5s; consider adding tool elapsed-time so an operator sees "Bash · 23s" growing.
