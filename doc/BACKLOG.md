# BACKLOG

Out-of-scope observations from feature work. Surfaced during implementation, logged here, addressed when prioritized.

Format per workflow.md: `## [YYYY-MM-DD] Feature: <name>` then bulleted entries (What / Where / Why / Suggested action).

---

## [2026-05-16] Feature: fidelity-audit-suite (#27)

### Wire `ResultMessage.usage` extraction into each live test body — CLOSED by fidelity-audit-followups (2026-05-16)

- **What**: The autouse cost fixture in `tests/test_fidelity_audit.py` reads a module-global `_test_cost_data` dict and writes one JSONL line per test. No live test body in the suite ever populates `_test_cost_data` with extracted `ResultMessage.usage` data — all live tests write `{input_tokens: 0, output_tokens: 0, cost_usd: 0.0, wall_seconds: <real>}`. The cost artifact is structurally valid (AT11 field-presence passes) but semantically empty.
- **Where**: Each test class in `tests/test_fidelity_audit.py` that calls `_spawn_and_ask` or drives a live SDK turn — needs a `ResultMessage` break-point capture and storage into `_test_cost_data[request.node.nodeid]`. Auth-failure class stores zeros explicitly (no real SDK call).
- **Why it matters**: The suite is meant to be the canonical per-run cost record for the fidelity moat. All-zeros defeats the point; real pricing is the only signal that tells a developer when claims are getting more expensive.
- **Suggested action**: Wire `ResultMessage.usage` extraction at each live-turn break point across the 7 real SDK classes. Medium effort — behavior-changing and must be validated under `CLAUDE_CREW_LIVE_TESTS=1` with real API spend. Feature-review MEDIUM-02 (`feature.spec-satisfaction.cost-telemetry-zero`).

### Extend `discover_dir` to discover `*.yaml`/`*.yml` agent files (AT8 yaml-loader gap) — CLOSED by fidelity-audit-followups (2026-05-16)

- **What**: `TestAgentFormatYamlPolymorphism` manually parses the `.yaml` agent file via `yaml.safe_load` and constructs `AgentDefinition` inline; `discover_dir` is only called for the markdown side (it globs `*.md`). AT8's claim — "both markdown-with-frontmatter AND pure-YAML pack entries load via `build_merged_pack`" — is over-stated: the test asserts dispatch-side fidelity, not loader-side fidelity. A regression breaking YAML support in the loader would not flip the test.
- **Where**: `claude_crew/subagents/_loader.py::discover_dir` (glob pattern `*.md`); `tests/test_fidelity_audit.py::TestAgentFormatYamlPolymorphism`.
- **Why it matters**: The fidelity suite's purpose is to fail loudly when a claim erodes. The AT8 gap silently exempts the loader from that contract.
- **Suggested action**: Either (a) extend `discover_dir` to glob `*.yaml`/`*.yml` in addition to `*.md` and route AT8 through `build_merged_pack` end-to-end, or (b) amend the AT8 spec row to read "both formats dispatch once instantiated as `AgentDefinition`" and tighten the test docstring to match. Option (a) is the higher-value fix. Feature-review MEDIUM-01 (`feature.spec-satisfaction.yaml-loader-bypass`).

_Note: five RepoReactor coordinator/skill improvements surfaced by this feature (state-op map setter, `.rr/` gitignore handling, prior-task slice-review digest, breakout-reviewer glob-overlap heuristic, spec-Assumption prior-art verification) are tracked in the **repo-reactor** repo's BACKLOG — they're plugin-level concerns, not claude-crew product concerns._

---

## [2026-05-12] Feature candidate: fidelity-audit live-test suite

### CLI-fidelity moat needs asserted commitment, not incident response

claude-crew's central differentiator (per VISION) is that teammates obey Claude Code's rules — CLAUDE.md, skills, hooks, permission modes, MCP, plugins, agent format. Today, each fidelity gap is closed reactively when a real-task run trips over it (recent examples: bundled-pack shadowing dropping `explorer`/`planner` — caught by chance, zero unit-test signal across 978 tests; `model:` ignored at top-level teammate spawn; Windows `\r\n` rejected; shell hook env vars don't fire in SDK mode). Each is small; cumulatively they erode the moat.

Proposal: a single live-gated test suite (`CLAUDE_CREW_LIVE_TESTS=1`) — one class per fidelity claim — asserting parity with CLI behavior. Run pre-release.

Suite contents (initial):
- Bundled-pack dispatch — every pack-declared agent reaches the SDK (extends the [2026-05-08] one-off entry)
- Skill discovery — `extra_skills` declared in spawn reach the SDK and the agent can invoke them
- Hook firing — `PreToolUse` / `PostToolUse` / `PreSubagentUse` / `PostSubagentUse` fire as expected; document the shell-env-var carve-out as an explicit invariant
- Plugin scope filter — plugin-namespaced agents resolve per the documented precedence
- MCP resolution — user-level MCP config reaches teammates (project-level documented as out-of-scope until the spike below resolves)
- Agent format YAML polymorphism — `tools:` / `disallowedTools:` accept string-or-list (regression coverage for the latent character-iteration bug)
- Frontmatter normalization — Windows `\r\n` accepted (gated on Windows `\r\n` fix landing)

Promoted to vision-pipeline-row consideration: yes. This is the moat-as-asserted-commitment feature.

Related: **subsumes the [2026-05-08] "Live integration test for bundled-pack dispatch" entry.** That entry has the seed test-shape; planner should fold its intent into the suite and treat the bundled-pack-dispatch class as one of several, not the only one. The one-off entry can be archived once #27 ships.

### Notes for the implementing workflow (RepoReactor / SDD)

- **API cost: real.** Live-gated tests hit the SDK and consume Anthropic API budget. Don't run on every push; gate behind `CLAUDE_CREW_LIVE_TESTS=1` (existing pattern from `test_live_sdk.py`, `test_format_compat_e2e.py`). The unit-test green path stays free; the live suite is a pre-release gate.
- **Scope tightly per test class.** One round-trip per fidelity claim where possible. Avoid suites that spin up multiple long-running teammates if a single short turn proves the invariant.
- **Reuse the `test_format_compat_e2e.py::TestLiveSdkToolsEmptyEnforcement` pattern.** That test is the existing prior-art for "spawn a real teammate, assert an SDK-boundary invariant, ~$0.05 per run." Each fidelity claim should follow that shape.
- **Pin SDK version for the live suite's CI run (if/when CI runs it).** Several invariants (e.g., `AgentDefinition(model=None)` wire-safety, `tools=[]` no-tool enforcement) are claims about *current SDK behavior*; an SDK upgrade can silently change them. The suite IS the canary for those changes.
- **Windows `\r\n` test gates on the underlying fix landing.** Until the three-line frontmatter fix ships, write the live test as `xfail` with a comment naming the gate; flip to a hard assertion when the fix lands.
- **Document the shell-env-var hook carve-out as an explicit invariant**, not a TODO. The live test should assert "shell hook env vars are NOT injected in SDK mode" — that's the current contract, and turning it into a test prevents accidental "fixes" that would break other assumptions.

---

## [2026-05-12] Feature candidate: external-distribution onboarding gate

### "Works for someone who didn't build it"

Per the sharpened vision (commit `73b9d61`), external distribution is a goal — gated explicitly on the install and onboarding holding up for someone who didn't build it. Today there is no validation of this gate. Operator onboarding is a tacit-knowledge surface.

Proposal: a structured onboarding feature.

Phase 1 (research/instrumented):
- Hand the repo + a one-line install instruction to one or more cold operators (Jerome's coworkers, online volunteers, anyone unfamiliar with the internals)
- Watch them try (recorded screen-share or async with screenshot/error reports)
- Write up what broke

Phase 2 (fix top 3):
- Whatever the top three blockers are — install path beyond `uv sync`, `claude mcp add` clarity, auth error surfacing, dashboard-port discovery, Windows support (`\r\n` fix already on the board), README "Getting Started" mismatches.

Phase 3 (validate):
- Hand to a fresh cold operator and measure time-to-first-message.

Sub-items already on the board that fold in:
- Windows `\r\n` frontmatter rejection ([2026-05-02])
- `_warn_shadow_drop` enhancement: name both stems ([2026-05-02])
- Any MCP spike findings ([2026-04-28]) that produce operator-facing footguns

Sizing: L. Worth treating as its own SDD pass once the immediate fidelity items clear.

---

## [2026-05-12] Recurring: Agent Teams landscape watch

### Anthropic Agent Teams is a moving target; differentiator framing decays if not refreshed

VISION's Alternatives table characterizes Agent Teams as "experimental flag with known limits" (single team per session, no nesting, no recursive subagents). That's accurate at time of writing but Anthropic ships fast. If Agent Teams gains recursive subagents or multi-team support, parts of the differentiator framing need to update.

Cadence: quarterly. Next check: 2026-08-12.

Check:
- Current Agent Teams capabilities (recursion, multi-team, observability, MCP integration)
- Whether the "Known Limits" line in VISION's Alternatives table is still accurate
- Whether claude-crew has new differentiators worth elevating (or lost any worth dropping)

This is vision-doc maintenance, not a feature. Tracking here so it doesn't drift.

---

## [2026-05-08] Live integration test for bundled-pack dispatch — ARCHIVED: subsumed by #27 fidelity-audit-suite (2026-05-16)

### No regression signal for CLI-rule changes that silently drop user-submitted agents
- **What**: The 2026-05-07 → 2026-05-08 dispatch-drop bug (CLI silently dropping `explorer`/`planner` from the available-agent list) had **zero** test signal. The full unit suite (978 tests at the time) passed throughout, while in production teammates were getting "Agent type not found" at runtime. The drop happens inside the `claude` CLI subprocess after the SDK initialize request — none of our unit-level fixtures exercise that boundary, so any future change to CLI agent-registration rules (or a regression that reintroduces a name match / `skills: all`) sails past CI undetected.
- **Where**: New live test alongside `tests/test_live_sdk.py` and `tests/test_live_subagents.py`, gated by `CLAUDE_CREW_LIVE_TESTS=1`. Spawn a real teammate via the live SDK path, ask it to enumerate available agent types via the Task tool schema, assert every key in `load_default_pack()` is dispatchable. Optionally also dispatch each bundled subagent with a trivial task and assert non-error return.
- **Why it matters**: The class of bug we just paid for in confusion is structurally invisible to unit tests. A live canary test is the cheap insurance — runs nightly or pre-release, catches CLI rule changes and pack-config regressions (e.g., someone reintroducing `skills: all` or naming a bundled agent after a built-in) before they ship.
- **Suggested action**: One test class, ~30-50 lines, ~1 hour of work. Reuse the live SDK harness pattern. Skipped by default; add to a release-readiness checklist.

---

## [2026-05-08] Cleanup batch: defensive dead-code + #25 follow-ups

### Bundled cleanup PR for accumulated low-risk mechanical work
- **What**: Several small, mechanical, low-risk cleanups have accumulated. Each is too small to ship alone but worth bundling.

  **Defensive dead-code reachable only via the now-rejected `skills="all"` path:**
  - `claude_crew/factories.py:277` — extras-merge handling for the `"all"` literal in `agent_def.skills`
  - `claude_crew/broker.py:281` — config serialization that wraps `"all"` into `["all"]`
  - `claude_crew/subagents/_user_loader.py:448` — skill-name validation skip when `skills == "all"`
  - `tests/test_broker.py:1900-1925` — round-trip tests that exercise the `["all"]` serialization

  Since `_loader._validate_frontmatter` (post-2026-05-08) rejects `skills: "all"` at parse time with a `PackLoadError`, no in-repo path produces an `agent_def.skills == "all"` value anymore. External callers could still inject it via `extra_skills` on `spawn_teammate` (currently typed `list[str] | None`, so they'd have to type-violate) — defensive but unreachable from real flows.

  **#25 follow-ups (per SESSION.md):**
  - ERROR-tier startup-diagnostic badge CSS (`.startup-notice-badge-error` rule missing)
  - Drop no-op `try/except` around `record.getMessage()` in `StartupDiagCollector.emit`
  - Refactor `_direct_attach_fallbacks` to return restore pairs (drop handler-attribute coupling)
  - Breakout-feature planner heuristic — include `server.py` in `taskTouches` for factory→broker slices
  - Reconcile `unknown_skill` category — confirm startup-time emit site or drop

- **Where**: Across `claude_crew/` and `tests/`. See individual SESSION.md / BACKLOG references for the #25 items.
- **Why it matters**: Each item alone is too small to justify a dedicated PR. Bundled, they cleanly retire post-fix dead code and close out the #25 follow-up tail. Reduces ambient noise (unreachable branches, missing CSS, etc.) and signals that the #25 ship is fully wrapped.
- **Suggested action**: Single PR, "cleanup: post-fix dead code + #25 followups." Estimated effort: an afternoon.

---

## [2026-05-08] Fixed: bundled `general-purpose` shadowing dropped `explorer` and `planner` (resolved)

### Original observation (2026-05-07) — hypothesis was wrong
The original entry guessed case-insensitive collision between bundled `explorer`/`planner` and CLI built-ins `Explore`/`Plan`. Spike work disproved that. The names don't collide; the CLI doesn't do case-insensitive matching.

### Real cause: two undocumented CLI drop rules
The Claude Code CLI silently drops user-submitted agents (sent over the SDK initialize request) under two conditions:

1. **Built-in name match.** If any name in the `agents` dict matches a CLI built-in (`Explore`, `Plan`, `general-purpose`, `statusline-setup`), every *other* user-submitted agent is dropped. The name-matching one is "kept" but shadowed by the CLI's own version.
2. **Invalid `skills="all"` on `AgentDefinition`.** The SDK types `AgentDefinition.skills` as `list[str] | None`; the `"all"` literal is only valid at the session level (`ClaudeAgentOptions.skills`). Sending `"all"` on a per-agent definition is invalid wire data; the CLI silently drops the offending agent and cascades to drop the other user-submitted agents in the same dict.

The bundled `general_purpose.md` triggered both rules simultaneously: name matched a CLI built-in AND it declared `skills: all` in frontmatter. `explorer` and `planner` were innocent bystanders dropped by the cascade.

### Fix shipped
- Renamed bundled `general_purpose.md` → `general.md`, frontmatter `name: general` (no longer collides with CLI built-in).
- Removed `skills: all` from the bundled file (it was never working anyway — bundled `general-purpose` was always shadowed by the CLI built-in, so the field never reached a real running agent).
- Pack-loader (`_loader.py`) now rejects `skills: "all"` at parse time with a `PackLoadError` pointing operators to `ClaudeAgentOptions.skills` for the session-level form.
- `PackFrontmatter.skills` type narrowed from `tuple[str, ...] | Literal["all"] | None` to `tuple[str, ...] | None`.
- Tests added: `TestPackContents::test_bundled_general_key_loads` (rename), `TestSkillsAllForm::test_skills_all_string_raises_pack_load_error` (rejection).
- `explorer.md` and `planner.md` left untouched.

### Followup observations (not blocking)

**Defensive dead code now unreachable.** With `skills="all"` rejected at parse time, three branches that handle the literal are unreachable from real code paths:
- `claude_crew/factories.py:277` — extras-merge handling for `"all"` literal
- `claude_crew/broker.py:281` — config serialization that wraps `"all"` into `["all"]`
- `claude_crew/subagents/_user_loader.py:448` — skill-name validation skip for `"all"`

External consumers could still send `agent_def.skills = "all"` through the spawn-extras path, so the defensive code is harmless, but it's documenting a now-impossible wire format. Cleanup ticket: remove the branches, narrow the types, and the corresponding test_broker.py round-trip tests.

**Built-in subagents (`Explore`, `Plan`, `general-purpose`, `statusline-setup`) don't have `Task`** — verified via spike. So the bundled pack's recursion-safety claim is redundant with built-ins. The bundled pack's *real* value is **role-shaped tool surfaces**: `explorer` is read-only, `planner` is write-only-for-new-docs (no Bash on either), `general` is broad (Bash included). Built-ins are uniformly broad. If we ever decide role-shaped surfaces aren't worth maintaining a custom pack for, the bundled pack can be retired entirely.

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

## [2026-04-30] Teammate vs. subagent system-prompt parity — ARCHIVED: shipped as #21 (2026-04-30)

---

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

## [2026-04-30] Bug + Feature: multi-instance dashboard aggregation — ARCHIVED: shipped as #13 (2026-04-30)

---

## [2026-04-30] Feature: mission-control-ui (retro follow-ups)

### `ui_server.py` has zero test coverage
- **What**: The entire `ui_server.py` module — `_build_state()`, `_derive_status()`, `_normalize_model()`, the WebSocket handler, and the HTTP routes — has no tests. 387 existing tests all pass, but none touch the new code.
- **Where**: `claude_crew/ui_server.py`; missing `tests/test_ui_server.py`
- **Why it matters**: Any broker refactor that renames `_info`, `_log`, or `_teammates` silently breaks the UI with no failing test to catch it. The `_build_state()` logic (status derivation, model normalization, transcript capping) is untested.
- **Suggested action**: Write `tests/test_ui_server.py` — unit tests for `_derive_status()` and `_normalize_model()`, integration tests for `_build_state()` using a real Broker + StubTeammate, and an HTTP smoke test for `GET /` and `GET /api/state` via Starlette's `TestClient`.

### Broker should expose a `snapshot()` read API — ARCHIVED: shipped as #18 (2026-04-30)

### Multi-crew aggregation — instance strip shows only one crew — ARCHIVED: shipped as #13 (2026-04-30)

### Real token/cost tracking — all agents show $0.000 — ARCHIVED: shipped as #14 (2026-04-30)

### Git branch shown as "main" (hardcoded) — ARCHIVED: shipped as #18 (2026-04-30)

### Message kind is always "msg" — tool calls and thinking not typed — ARCHIVED: shipped as #16 + #19 (`kind: "tool"` via #19; `kind: "thinking"` deliberately cut per SESSION.md — ThinkingBlock responses dropped at sdk_teammate.py:159, extended-thinking rare in standard usage)

---

---

## [2026-04-28] Feature: agent definition parity + MCP forwarding for SDK teammates — ARCHIVED: shipped as #17 (2026-05-01)

---

## [2026-04-28] Feature: skill invocation for SDK teammates (spike first) — ARCHIVED: shipped as #23 (2026-05-01)

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

**Reclassified: substrate-era; see below.**

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

**Reclassified: substrate-era; see below.**

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

## [2026-05-02] Feature: claude-code-agent-format-compatibility (#15) — ARCHIVED: shipped as #15 (2026-05-02)

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

---

## Substrate-era observations (deferred)

*Entries below assume the broader "substrate" framing the vision has stepped back from (see commit `73b9d61`). They're preserved here because the underlying observations remain valid; if a consumer-side need surfaces, promote back into the active list. Until then, they should not leak design budget.*

## [2026-04-29] Observation: recursive crew spawning is one config change away

- **What**: Teammates currently cannot call `spawn_teammate` because the claude-crew MCP server is project-level only. If the MCP server were registered in `~/.claude.json` (user-level), teammates could spawn their own crew members — the broker already handles this correctly regardless of caller.
- **Where**: `~/.claude.json` MCP config; `claude_crew/server.py` spawn_teammate tool
- **Why it matters**: Enables genuine recursive crew expansion — a planner could spawn explorers, a builder could spawn a reviewer, without the lead having to orchestrate every level.
- **Suggested action**: Register claude-crew in `~/.claude.json`, test that a teammate can successfully call `spawn_teammate`, confirm the spawned member appears in `list_crew`. Needs a decision on lifecycle ownership (who kills a teammate spawned by another teammate, not the lead).

## [2026-04-27] TaskStartedMessage / TaskProgressMessage not consumed in v1

*(Sub-entry from `[2026-04-27] Feature: #7 subagent-activity envelopes` umbrella — see parent for context.)*

- **What**: Both explicitly deferred in Phase 2 (co-architect). `TaskStartedMessage` adds spawn→running timing gap; `TaskProgressMessage` is the streaming-activity firehose. Neither has a current consumer.
- **Where**: `claude_crew/sdk_teammate.py` `_collect_response_text` (only `TaskNotificationMessage` handled)
- **Why it matters**: Future feature candidate — streaming subagent activity, richer timing analytics.
- **Suggested action**: Route as a separate feature when a consumer surfaces. `TaskStartedMessage` is S-size; `TaskProgressMessage` re-opens push semantics question and is M-size.

## [2026-04-27] Lead polling discipline gap — process pattern

*(Sub-entry from `[2026-04-27] Feature: #8 tool-execution telemetry` umbrella — see parent for context.)*

- **What**: Three times this session, lead dispatched teammates and didn't poll for replies until prompted. One reply (sentinel-f8-p1 Phase 2 review) sat in the inbox for ~17 minutes before the lead noticed. The notification mechanism is pull-only; cursor-based `get_messages` requires the lead to actively poll.
- **Where**: lead orchestration during multi-teammate dispatch
- **Why it matters**: Creates visible session-pacing friction. Jerome had to ask "did we check back in?" three times.
- **Suggested action**: Either (a) implement the deferred "Hook-based ambient inbound delivery to lead" feature in PRODUCT-VISION (structural fix), or (b) bake "poll within N minutes of any `send_to` expecting a reply" into lead workflow guidance (process band-aid until (a) ships). Probably (b) first; (a) when MMM-4b real-task validation surfaces enough pain to justify the effort.

## [2026-04-30] Persistent crew teammates accumulate context cost across turns

### SDK session state is cumulative; persistent agents get expensive across many turns
- **What**: `SdkTeammate` calls `client.query(prompt, session_id="<crew_id>-<teammate_id>")` per turn. The Anthropic CLI maintains conversation state per session_id, so every turn re-includes ALL prior turns in the model's context window. Token cost scales with conversation length. F14 cost telemetry confirmed this empirically — a co-architect with 6 turns hit 1.3M input tokens, 9× more than a reviewer with 1 turn (similar output sizes).
- **Where**: `claude_crew/sdk_teammate.py:794-797` (session_id construction).
- **Why it matters**: Persistent crew teammates that take many lead-driven prompts during a feature design pass become disproportionately expensive. Anthropic's prompt caching mitigates (cache reads at 10% rate; the 1.3M-token co-architect cost $1.75 instead of $19.50), but it still scales linearly with turn count.
- **Suggested action**: No code change needed — this is intrinsic to how SDK sessions work. Operationally, restart-per-feature beats persist-across-features for high-turn-count roles like co-architect. Document this in PROJECT-VISION's "operational notes" section so future operators know. Could ALSO be addressed by a future feature: `spawn_teammate(reset_session=True)` or a `restart_teammate(id)` MCP tool that flushes the SDK conversation while keeping the broker registration.
