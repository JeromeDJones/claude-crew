# Feature: Native Claude Code Agent Format Compatibility

**Status**: Draft (Phase 1 — awaiting SDD entry)
**Created**: 2026-05-01
**Branch**: TBD (`feature/claude-code-agent-format-compat` proposed)
**Replaces vision row**: #15 (originally "Expanded default subagent pack" — that framing was wrong; see below)

---

## Phase 1: Research & Requirements

### Problem Statement

claude-crew is a substrate. Its value to operators beyond Jerome depends on it being able to consume **whatever agent definitions the operator already has** in `~/.claude/agents/` and `<project>/.claude/agents/` — without requiring those operators to author claude-crew-specific files. Today, claude-crew accepts a SUBSET of valid Claude Code agent files: some load with operator-visible WARNs, some fail to load entirely with `PackLoadError`, and the system prompt composition is opinionated in a way that conflicts with Claude Code's documented contract.

The original vision row #15 ("Expanded default subagent pack — add `reviewer` and `runner` roles to the bundled pack") was the wrong abstraction. Jerome already has rich user-level agent definitions (`~/.claude/agents/{builder,sentinel,runner,scout,feature-planner}.md`) that work in his Claude Code session and load (mostly) into claude-crew today. The substrate's job is to make those — and any other operator's user-level agents — **just work**, not to bundle alternatives. Distribution-grade claude-crew means parity with Claude Code's agent file format and behavior so operators can drop in their existing definitions and get the same role surface they have in their CLI session.

This feature closes that gap. It does NOT bundle additional roles; it makes the bundled set (and the user/project loaders) accept the full Claude Code frontmatter surface and apply correct system prompt composition.

### Authoritative References

Claude Code's agent file format: https://code.claude.com/docs/en/sub-agents.md (Section "Supported frontmatter fields", lines 234–256; section "Write subagent files", lines 210–232).

Validated against Claude Code docs by `claude-code-guide` agent on 2026-05-01.

### Background — Field Surface Mapping

**Claude Code's COMPLETE frontmatter surface (16 fields):**

| Field | Required | Type | Behavior |
|---|---|---|---|
| `name` | yes | string | Unique identifier (lowercase, hyphens). Canonical agent name. |
| `description` | yes | string | When Claude should delegate to this agent. |
| `tools` | no | comma-string OR list | Tools the agent can use; inherits all if omitted. |
| `disallowedTools` | no | string/list | Tools to deny. |
| `model` | no | string | Alias (`sonnet`, `opus`, `haiku`, `inherit`) or full ID. |
| `permissionMode` | no | enum | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`. |
| `maxTurns` | no | number | Max agentic turns before agent stops. |
| `skills` | no | string list | Skill names to preload into agent context. |
| `mcpServers` | no | (str \| dict) list | MCP servers by name or inline config. |
| `hooks` | no | hook config | Lifecycle hooks scoped to this agent. |
| `memory` | no | enum | `user`, `project`, `local` — persistent memory directory scope. |
| `background` | no | bool | Run as non-blocking background task. |
| `effort` | no | enum or number | Reasoning effort: `low`, `medium`, `high`, `xhigh`, `max`. |
| `isolation` | no | string | `worktree` for temp git worktree isolation. |
| `color` | no | enum | Display color (UI metadata). |
| `initialPrompt` | no | string | Auto-submitted as first user turn when agent runs as main session (via `--agent`). |

**claude-crew's `PackFrontmatter` today (post-#17, 13 fields):**

`description` (req) · `model` (req) · `tools` (req) · `effort` · `maxTurns` · `initialPrompt` · `background` · `skills` · `permissionMode` · `disallowedTools` · `settingSources` (claude-crew extension, not in Claude Code) · `mcpServers` · `memory`.

**Body** = system prompt per Claude Code docs: *"The body becomes the system prompt that guides the subagent's behavior. Subagents receive only this system prompt (plus basic environment details), not the full Claude Code system prompt."*

### The Gaps

**Format-compatibility gaps (in scope):**

1. **`name:` field — claude-crew DROPS WITH WARN.** Claude Code REQUIRES it. Today the file stem is the role key; `name:` produces `unsupported frontmatter key(s) ['name']; dropping`. Five WARNs at every server startup for Jerome's user-level packs alone. Should be silent-accepted AND used as the canonical role key (file stem becomes fallback).

2. **`model:` is REQUIRED.** Claude Code: optional. A Claude Code agent file without `model:` (using inherit) silently disappears as a role under claude-crew (load fails with `PackLoadError`, `load_user_agents` skips with WARN, role unavailable at spawn). Should be optional; default to SDK default (Sonnet 4.6) when absent.

3. **`tools:` is REQUIRED.** Claude Code: optional (inherits all from parent). Same silent-disappearance failure mode. Should be optional; absent = empty list (no tools), since claude-crew's teammates and subagents have no parent to inherit from.

4. **`tools:` accepts only YAML list.** Claude Code accepts comma-separated string (`tools: Read, Write, Edit`) OR list (`tools: [Read, Write, Edit]`). Most user-level Claude Code agents use comma-string format. Need to accept both at parse time.

5. **`color:` field WARNs as unsupported.** UI-only metadata in Claude Code. Should silent-accept.

6. **System prompt composition is wrong.** Today `_loader.py:116` produces `AgentDefinition.prompt = body.rstrip() + _LEAF_SUFFIX` — body FIRST, leaf-suffix SECOND. Per Claude Code docs, the body IS the entire system prompt; the SDK adds basic env details, nothing else. claude-crew's leaf-suffix exists for a real reason (preventing infinite subagent recursion in our cost-accounting model), but its position is wrong AND its framing should be substrate-guidance, not a trailing constraint.

   **Jerome's design call**: substrate guidance FIRST (claude-crew's operating context for the agent), agent body APPENDED. This gives operators both: claude-crew's contextual framing (what crew is, where it runs, accountability model) plus the agent's role-specific instructions verbatim. Order matters — substrate-guidance leads as foundation, role body specializes.

**Format-compatibility gaps (deferred to a future hook-aware feature):**

7. **`hooks:` field — claude-crew has NO support.** Per-agent lifecycle hooks. Real new behavior; interacts with claude-crew's existing F8/F7 SDK hook wiring. **Defer to vision row #24** (subagent-dispatch routing guard + post-output validation hook) which is already the hook-aware feature in the pipeline. Cross-reference at SC-N below.

8. **`isolation: worktree`** — claude-crew has NO support. Worktree isolation per agent. Real substrate value but architecturally separate. Defer to the hook-related feature scope OR file as a new vision row #15b.

**Already correctly mapped (post-#17 — no change needed):**

`description`, `model` (when present), `tools` (when present, list form), `permissionMode`, `disallowedTools`, `maxTurns`, `skills`, `mcpServers`, `memory`, `background`, `effort`, `initialPrompt`.

**claude-crew's keepers (NOT in Claude Code — keep as substrate value-add):**

`settingSources` — controls skill + CLAUDE.md discovery at the SDK boundary. Not a Claude Code field; explicitly a claude-crew design surface. Stays as is.

### Success Criteria

- [ ] **SC-1: `name:` field silent-accepted at pack-load.** A user pack file declaring `name: my-role` parses without an `unsupported frontmatter key` WARN. The value is captured on `PackFrontmatter`.

- [ ] **SC-2: `name:` is the canonical role key when present; file stem is the fallback.** A pack file at `~/.claude/agents/my-file.md` declaring `name: senior-reviewer` becomes a spawnable role under the key `senior-reviewer` (NOT `my-file`). A pack file without `name:` continues to use the file stem (existing behavior). Verified by spawn: `mcp__claude-crew__spawn_teammate(role="senior-reviewer")` succeeds.
   - **SC-2a (cross-stem `name:` collision):** Two files in the same precedence layer with different stems but identical `name:` values (e.g., `~/.claude/agents/old-runner.md` declaring `name: runner` and `~/.claude/agents/runner.md` declaring no `name:` or `name: runner`) MUST surface as a collision at pack-load time with a WARN that names BOTH file paths and the canonical name. Determinism contract: alphabetical-by-canonical-name wins, alphabetical-by-stem is the tiebreak. Tested via `discover_dir` directly.

- [ ] **SC-3: `model:` is optional at pack-load.**
   - **Type change**: `PackFrontmatter.model` annotated as `Optional[str]`; `"model"` removed from `_REQUIRED` (see `_loader.py:198-331`); `_validate_frontmatter`'s `str(d["model"])` cast guarded behind a presence check.
   - A pack file without `model:` parses successfully; resulting `AgentDefinition.model` is `None`. Per Q-1 resolution: SDK applies its own default; verified via SDK-boundary probe in Phase 2 before Phase 3 task breakdown.

- [ ] **SC-4: `tools:` is optional at pack-load.** A pack file without `tools:` parses successfully. The resulting `AgentDefinition.tools` is an empty list. INFO log emitted at pack-load: `"agent <role> has no tools declared — teammate will spawn but cannot invoke tools"` (per Q-2 resolution). Verified via stub-mode pytest asserting both the empty-list outcome AND the INFO log.

- [ ] **SC-5: `tools:` accepts comma-separated string format AND closes a latent silent-corruption bug.**
   - Bug today (`_loader.py:315`): `tools=list(d["tools"])` iterates a string into characters when `d["tools"]` is `"Read, Write"`. This ships today as a regression vector for any operator who happened past the required-field check with a comma-string. Fix: type-coercion helper invoked before `list()`.
   - Coercion contract (per Q-3 resolution): split on `,`, strip whitespace from each entry, drop empty entries.
   - Pytest scenarios required (all must pass):
     1. List form unchanged: `tools: [Read, Write]` → `["Read", "Write"]`.
     2. Plain comma-string: `tools: Read, Write, Edit, Bash` → `["Read", "Write", "Edit", "Bash"]`.
     3. `mcp__`-prefixed entries: `tools: Read, mcp__knowledge-graph__search_codebase_definitions` → exact-match, no character-iteration corruption.
     4. Trailing comma: `tools: Read, Write,` → `["Read", "Write"]` (empty entries dropped, no error).
     5. Quoted entries: `tools: "Read", "Write"` → `["Read", "Write"]` (YAML strips quotes; coercion path tolerates).
     6. Regression guard: `tools: Read` (single string, no commas) → `["Read"]` (does NOT iterate to `["R","e","a","d"]`).

- [ ] **SC-6: `color:` field silent-accepted.** A pack file declaring `color: blue` parses without an unsupported-key WARN. Value is captured on `PackFrontmatter` but not consumed (UI metadata; claude-crew's dashboard may use it later — out of scope for this feature).

- [ ] **SC-7: System prompt composition revised — substrate guidance leads on subagent path; teammate path retains body-first/addendum-last ordering as a deliberate carve-out.**
   - **Subagent dispatch path**: `AgentDefinition.prompt = SUBSTRATE_SUBAGENT_GUIDANCE + "\n\n" + body.rstrip()`. The leaf-constraint behavior is preserved (subagents are still leaves) but expressed as substrate guidance at the top.
   - **Teammate path carve-out**: `build_teammate_prompt` (`teammate_prompt.py:111`) keeps body-first then addendum, because the addendum injects peer-list context computed from agents the body may already mention. The "guidance leads" framing applies ONLY to the subagent path; the teammate path's existing order is documented as a deliberate exception in the helper docstring.
   - **Empty-body guard preserved**: `parse_pack_text`'s existing `body.strip()` guard (raising `PackLoadError` on empty/whitespace-only body) MUST fire before the composition step in the subagent path. A pytest asserts that a pack with a whitespace-only body raises `PackLoadError` post-refactor.
   - **Operators reading the subagent prompt** see claude-crew's framing first, then their role definition verbatim.

- [ ] **SC-8: A user pack file using ONLY Claude Code's required fields (`name` + `description`) loads cleanly, becomes a spawnable role, and emits zero unsupported-key WARNs.** This is the parity smoke test. Plant a minimal pack file:
   ```yaml
   ---
   name: minimal-probe
   description: Smoke test for Claude Code minimal agent format.
   ---
   You are a probe.
   ```
   Behavior: pack loads under role `minimal-probe`; spawn succeeds; teammate has no tools (SC-4); teammate has SDK default model (SC-3); zero pack-load WARNs.

- [ ] **SC-9: Existing claude-crew packs continue to load identically — no regression for the extension fields.** The bundled pack (`general_purpose.md`, `explorer.md`, `planner.md`) and Jerome's user-level packs (`builder.md`, `sentinel.md`, etc.) load with the same `PackFrontmatter` shape and `AgentDefinition` content as before, modulo the now-silent `name:` field handling.

- [ ] **SC-10a (stub-mode WARN check):** Pack-load against the real `~/.claude/agents/` directory under `CLAUDE_CREW_TEAMMATE_MODE=stub`. Asserts: (a) every file loads without `PackLoadError`, (b) zero `unsupported frontmatter key` WARNs are emitted (was 5+ pre-feature), (c) every file becomes a spawnable role under either its `name:` value or stem fallback. Always-on in CI.

- [ ] **SC-10b (live dogfood, gated):** Under `CLAUDE_CREW_LIVE_TESTS=1`, spawn one of Jerome's user-level agents (e.g., `runner`) via `spawn_teammate`, send it a trivial prompt, assert a response envelope returns. Validates the end-to-end pipeline (pack-load → SDK construction → live spawn → response). Skipped by default in CI; runs locally for dogfood.

- [ ] **SC-11: Pack-load INFO contract.** `build_merged_pack` emits one INFO log per pack source that names: source label (bundled / user / project), source path, count of packs loaded, list of role keys loaded. Post-feature: this is the operator's authoritative startup record. Verified via pytest with `caplog` asserting log structure and content for each source.

- [ ] **SC-12: `_LEAF_SUFFIX` rename — promoted to a helper, all consumer sites updated in lockstep.**
   - Promote `_LEAF_SUFFIX` to a module-level helper `build_subagent_prompt(body: str) -> str` per Q-4 resolution. Helper returns `SUBSTRATE_SUBAGENT_GUIDANCE + "\n\n" + body.rstrip()`.
   - Substrate guidance text revised to lead with positive framing (per Q-5 resolution; finalized in Phase 2). Constraint (leaf, no Task tool) expressed within the framing, not as trailing imperative.
   - **All in-tree consumer sites updated:** `tests/test_subagents.py:138,148,158,164` (4 import-by-name + assertions); `tests/test_teammate_prompt.py:23,267` (1 import + 1 endswith assertion); `claude_crew/subagents/__init__.py:53` (docstring reference). Lockstep update is part of the same commit as the rename — CI must stay green.

- [ ] **SC-13: Transcript `role` field stability when `name:` ≠ stem.** When `name:` differs from file stem, the role passed to `broker.spawn_teammate(role=...)` (and stored in `TeammateInfo.role`, surfaced in transcript records at `broker.py:145,152,606,639`) is the canonical name. Transcript records use the canonical name consistently. Test: load a pack whose stem and `name:` differ, spawn it, assert transcript record `role` field equals the canonical name (NOT the stem). Closes the silent observability-contract drift sentinel flagged.

- [ ] **SC-14: `disallowedTools` accepts comma-separated string format (parity with `tools:`).** Same coercion contract as SC-5. Reuses the same helper. Three pytest scenarios: list form (regression), comma-string form, single-entry string. Rationale: half-done parity is worse than full parity; the helper cost is zero.

### Resolved Decisions (post-sentinel + co-architect Phase 1 review)

- **D-1 (resolves Q-1):** When `model:` is absent: `PackFrontmatter.model = None`; `AgentDefinition.model = None`; SDK applies its own default. **Phase 2 must probe** `AgentDefinition(model=None)` against the SDK to confirm it is wire-safe before Phase 3 task breakdown.
- **D-2 (resolves Q-2):** When `tools:` is absent: `PackFrontmatter.tools = []`; `AgentDefinition.tools = []`; INFO log emitted at pack-load naming the role and stating the role has no tools.
- **D-3 (resolves Q-3):** Comma-string `tools:` parsing — split on `,`, strip whitespace per entry, drop empty entries. No tool-name validation. Same helper used for `disallowedTools` (per SC-14).
- **D-4 (resolves Q-4):** Promote `_LEAF_SUFFIX` to a module-level helper `build_subagent_prompt(body: str) -> str` in `_loader.py` (or new module `subagent_prompt.py` if Phase 2 prefers symmetry-by-filename). Helper is asymmetric with `build_teammate_prompt` (no role/agents arg) — symmetry is aspirational, not contractual.
- **D-5 (resolves Q-5):** Substrate guidance text content — finalized in Phase 2. Constraint: model-agnostic phrasing (per cross-pillar X.2), positive framing first, leaf constraint expressed within framing.
- **D-6 (resolves Q-6):** Teammate path retains body-first/addendum-last ordering (peer-injection requires body to land first). Documented as deliberate carve-out in helper docstring. SC-7 explicitly limits "guidance leads" to subagent path.
- **D-7 (resolves Q-7):** When `name:` value differs from file stem, emit a transition INFO log naming both at pack-load. When two files in the same precedence layer have the same canonical name (different stems), emit WARN naming both file paths AND the canonical name. Determinism contract per SC-2a.
- **D-8 (resolves Q-8):** Add `name` and `color` to `PackFrontmatter` dataclass (auto-propagates to `_ACCEPTED_FRONTMATTER_KEYS` via `_user_loader.py:57`). `hooks` and `isolation` remain UNDECLARED — the existing unsupported-key WARN stays as a typo-catcher and signals operators that those features are not yet honored. Deferral is explicit, not silent.
- **D-9 (new — scoping decision from co-architect E.3):** `disallowedTools` comma-string parity is IN SCOPE (SC-14). Same helper, near-zero cost. Half-done parity is worse than full.

### Open Questions (deferred to Phase 2)

- [ ] **Q-9: Cross-layer mixed-resolution shadow detection.** A user-level pack with `name: scout` and a project-level pack at `scout.md` (no `name:`) — both resolve to canonical key `scout`. Shadow detection in `_warn_shadow_drop` (`_user_loader.py:362-431`) compares dict keys; this works correctly post-canonicalization. But during the transition period (operator with mixed authoring conventions), what's the diagnostic story? Phase 2 to specify whether `_warn_shadow_drop` reports the canonical name only, or names both the canonical name AND the divergent stems.

- [ ] **Q-10: `name:` validation surface.** Operator-supplied `name:` could be garbage: `My Role` (whitespace), `feature/planner` (slashes), `42` (YAML-coerced int), 500-char string, empty string. Phase 2 decision: validate at pack-load (reject with `PackLoadError`), normalize silently (lowercase, replace whitespace with hyphens), or warn-and-accept? Claude Code's docs say "lowercase, hyphens" but don't specify the validation mode. Recommended starting position: reject with `PackLoadError` if `name` does not match `^[a-z0-9][a-z0-9-]*$`; emit a clear error message naming the file and the offending value.

- [ ] **Q-11: Bundled pack `name:` declaration.** The bundled pack files (`general_purpose.md`, `explorer.md`, `planner.md` in `claude_crew/subagents/`) do NOT currently declare `name:`. Should this feature add `name:` to all three for self-consistency, or leave them as stem-keyed (since they already match)? Recommended: add `name:` to all three — substrate dogfoods its own contract. Cheap.

### Original Questions (now resolved — kept for traceability)

- [x] **Q-1: When `model:` is absent, what value does `PackFrontmatter.model` hold AND what does it forward to `AgentDefinition`?**
   Options:
   - (a) `None` on PackFrontmatter; `None` on AgentDefinition; SDK applies its own default (Sonnet 4.6).
   - (b) `"inherit"` on PackFrontmatter (matching Claude Code's documented sentinel value); `"inherit"` on AgentDefinition; SDK CLI handles the inherit semantics.
   - (c) Hardcoded default at pack-load (e.g., `"sonnet"`); pack-load resolves the absence.

   Recommended answer: **(a)**. Don't bake claude-crew's defaults into the loader; let the SDK decide. Matches "consume what the user has" mindset.

- [ ] **Q-2: When `tools:` is absent, is the resulting AgentDefinition.tools `None` or `[]` (empty list)?**
   Claude Code says "inherits all if omitted." claude-crew has no parent to inherit from. **Recommended: empty list with a startup INFO** ("agent X has no tools declared — teammate will spawn but cannot invoke tools"). The operator's choice to omit `tools:` is preserved; the diagnostic is loud enough to surface "this role can't actually do work."

- [ ] **Q-3: `tools:` comma-string parsing — what whitespace and edge cases are accepted?**
   - Trailing comma? (`tools: Read, Write,`) — strip empty entries.
   - Quoted entries? (`tools: "Read", "Write"`) — YAML normally strips quotes; should work.
   - Tools with hyphens or `mcp__`-prefixes? (`tools: Read, mcp__knowledge-graph__search_codebase_definitions`) — must work; these are real Claude Code agent tool entries.
   
   Recommended: split on `,`, strip whitespace from each, drop empty entries. Don't validate tool names against a known set (Claude Code doesn't either).

- [ ] **Q-4: Where exactly does the substrate guidance text live for the SUBAGENT path?**
   Today: `_loader.py:_LEAF_SUFFIX` constant. Tomorrow:
   - Same file, renamed to `_SUBAGENT_GUIDANCE` (or `_SUBSTRATE_SUBAGENT_PROMPT`)?
   - Promoted to a module-level helper (`build_subagent_prompt(body) -> str`) symmetric with `teammate_prompt.build_teammate_prompt`?
   
   Recommended: **promote to a helper.** Symmetry with the teammate path; testable in isolation; easier to evolve the guidance text without touching `parse_pack_text`.

- [ ] **Q-5: Substrate guidance content — what does it actually say?**
   Today's `_LEAF_SUFFIX`:
   > "You are a leaf subagent. You have no Task tool by design — subagents are leaves and cannot spawn further subagents. Stop and report when your task is complete."
   
   Should be revised to lead with positive framing:
   > "You are operating as a subagent within claude-crew, a multi-agent substrate coordinated via an MCP server. The crew lead has dispatched you to complete a focused task. You are a leaf node in this dispatch — you cannot spawn further subagents, and your tool surface is fixed by your role definition. Complete the assigned task, report your findings clearly, and exit."
   
   Phase 2 to refine. Goal: substrate-context FIRST, constraint expressed as part of the framing not a trailing imperative.

- [ ] **Q-6: Symmetric question for TEAMMATE path — what does the substrate guidance say there?**
   `build_teammate_prompt` already exists. Verify Phase 2 that it follows the same "substrate guidance leads, agent body follows" pattern. May require minor revision; may already be correct.

- [ ] **Q-7: Does the canonical-name change (`name:` over file stem) affect existing role lookups?**
   Today every pack file in `~/.claude/agents/` is keyed by file stem. Jerome's `feature-planner.md` declares `name: feature-planner` — same string, no behavioral change. **Risk case**: a pack at `~/.claude/agents/old-name.md` declaring `name: new-name`. After this feature, the role key is `new-name`. If anywhere in the codebase / docs / scripts hardcodes `old-name`, that breaks. **Recommended: emit a transition INFO log** when `name:` and file stem differ, naming both for the operator's awareness.

- [ ] **Q-8: Do we update `_ACCEPTED_FRONTMATTER_KEYS` to explicitly include `name`, `color`, `hooks`, `isolation`?**
   `_ACCEPTED_FRONTMATTER_KEYS` is auto-derived from `PackFrontmatter.__dataclass_fields__`. Adding `name` and `color` to the dataclass propagates structurally. `hooks` and `isolation` are deferred (per scope). **Recommended: add `name` and `color` to the dataclass; leave `hooks` and `isolation` as future fields. The current "unsupported key" WARN is a useful catch-typo signal — keep it for fields we don't support yet.**

### Constraints & Dependencies

- **Requires**: existing `PackFrontmatter` / `_validate_frontmatter` / `parse_pack_text` (`claude_crew/subagents/_loader.py`); existing `_user_loader.strict_parse` and `build_merged_pack`; existing `teammate_prompt.build_teammate_prompt`; existing `_LEAF_SUFFIX` constant.

- **Breaking changes**: **Behavioral** — for any operator pack file whose `name:` value differs from its file stem, the role key changes. This is the desired Claude Code parity behavior, but it IS a behavior change. A migration INFO log per Q-7 mitigates. **Format-additive** — accepting `name:` and `color:` and comma-string `tools:` and absent `model:` / `tools:` are all additive (relax-only); no operator pack that loads today should fail to load tomorrow.

- **Performance implications**: None. Two additional optional fields parsed at pack-load; one additional comma-split branch on `tools`; one constant rename.

- **Cross-cutting touches**: `_loader.py` (PackFrontmatter, `_validate_frontmatter`, `parse_pack_text`, `_LEAF_SUFFIX` rename + new helper); `_user_loader.py` (no change expected — `_ACCEPTED_FRONTMATTER_KEYS` propagates from dataclass); `teammate_prompt.py` (verify ordering of substrate guidance vs body); `factories.py` (no change); `sdk_teammate.py` (no change — `_run` consumes `AgentDefinition.prompt` as-is).

- **Tests touched**: `test_pack_loader.py`, `test_subagents.py`, `test_user_loader.py`. Possibly `test_teammate_prompt.py` for the system prompt composition assertions. New live-dogfood test that loads `~/.claude/agents/runner.md` and asserts zero startup WARNs.

- **Vision row reconciliation**:
  - Vision row #15 ("Expanded default subagent pack") is wrong-framed; rewrite to "Native Claude Code agent format compatibility" and replace the row's body wholesale.
  - Vision row #24 (subagent-dispatch routing guard + post-output validation hook) absorbs the deferred `hooks:` field work; cross-reference here.
  - Vision lines 205-216 ("Hooks: two systems, two answers") includes the inaccurate claim *"Per-role hooks in agent definitions — AgentDefinition doesn't have a hooks field; hooks aren't part of the role definition contract."* — this is wrong (claude-code-guide confirms `hooks:` IS supported per agent in Claude Code). Update with the corrected reference at the end of this feature.
  - File a new vision row OR fold into #24 for `isolation: worktree` (small but real substrate feature).

**Gate**:
- ✅ Authoritative reference confirmed (claude-code-guide WebFetch of Claude Code docs).
- ⏳ Phase 1 SCs reviewed by sentinel before user sign-off.
- ⏳ Phase 1 SCs reviewed by co-architect (three-pushback warmup) for Phase 2 design pillars.
- ⏳ Q-1..Q-8 answered.
- ⏳ Vision row #15 update drafted (post-Phase-2; replaces the existing row body).
- ⏳ User confirmation to proceed.

---

## Phase 2: Design & Specification

### Architecture overview

The feature lives entirely inside `claude_crew/subagents/_loader.py` and its sibling `_user_loader.py`, plus the `subagents/__init__.py` orchestrator and three test files. No changes to broker, server, sdk_teammate, transcript, ui_server, or factories — verified by Phase 2 consumer-inventory pass: every downstream consumer treats the role string as opaque and pass-through.

The change shape:

1. **PackFrontmatter** gains two fields (`name`, `color`), and `model`/`tools` go optional.
2. **`parse_pack_text`** returns the canonical name as the key; file stem becomes the fallback when `name:` is absent.
3. **A new `_coerce_str_or_list` helper** sits in front of `tools` and `disallowedTools` validation, closing the latent corruption bug at `_loader.py:315` and `:324`.
4. **`_LEAF_SUFFIX` becomes `build_subagent_prompt(body)` helper**, with substrate guidance moved to a prefix on the subagent path. Teammate path is unchanged (deliberate carve-out).
5. **Bundled pack files** (`general_purpose.md`, `explorer.md`, `planner.md`) gain `name:` declarations matching their existing role keys (substrate dogfoods its own contract, no behavior change).

Every SC (1–14) maps to a specific, testable change in this surface. No SC requires multi-file refactoring across the broker/server boundary.

### Pillar designs

#### Pillar A+D — Canonical name resolution & data flow

**Data contract change to `PackFrontmatter`** (`_loader.py:25-66`):

```python
@dataclass(frozen=True)
class PackFrontmatter:
    description: str                                     # required (only required field post-feature)
    name: str | None = None                              # NEW — canonical role key when present
    model: str | None = None                             # WAS required; None → SDK default
    tools: tuple[str, ...] = field(default_factory=tuple) # WAS required; empty tuple → no tools
    color: str | None = None                             # NEW — UI metadata, captured-and-ignored
    # ...all existing optional fields unchanged...
```

`_REQUIRED` shrinks to `("description",)`. The pre-existing `_OPTIONAL` tuple gains `"name"` and `"color"` (auto-propagates to `_ACCEPTED_FRONTMATTER_KEYS` in `_user_loader.py:57`).

**`tools` is `tuple`, not `list`** (sentinel M-1): consistent with `disallowedTools` (already `tuple`) and consistent with `frozen=True` semantics — a `list` field on a `frozen` dataclass is mutable in-place under the immutability illusion. The default is `field(default_factory=tuple)` (no mutable-default pitfall); the validator wraps the coerced list in `tuple(...)` before constructing the instance.

**Security-driven asymmetry between `model` and `tools` defaults**: `model` defaults to `None` (SDK applies its default — Sonnet 4.6, no security implications). `tools` defaults to empty tuple (NOT `None`) — claude-crew teammates have NO PARENT to inherit from, and the SDK's "inherits all if omitted" semantic would silently grant full tool access to an operator who simply forgot to declare `tools:`. Empty-tuple-default is safe-by-default. Shadow-drop coverage for `tools` (sentinel H-2) is solved separately by extending `_check_drop`, not by routing through `None`.

**Canonical key resolution in `parse_pack_text`** (`_loader.py:138`):

```python
canonical_key = fm.name if fm.name else path.stem.replace("_", "-")
```

The canonical key is what `parse_pack_text` returns as the first tuple element. Every downstream consumer (`discover_dir`, `merge_packs`, `__init__.load_default_pack`, `factories.build_merged_pack`) transparently inherits canonical-name keying because they all use `parse_pack_text`'s returned key as their dict key — no consumer re-derives from the path.

**Validation of `name:` value (resolves Q-10):**

Two-stage validation at pack-load:

1. **Type check** (sentinel M-4): `if raw_name is not None and not isinstance(raw_name, str): raise PackLoadError(f"name in {path}: must be a string, got {type(raw_name).__name__}")`. Catches YAML int (`name: 42`), bool (`name: true`), list, dict before they reach the regex.
2. **Regex check**: `^[a-z0-9][a-z0-9-]*$`. On mismatch: `PackLoadError(f"invalid name '{value}' in {path}: must match [a-z0-9][a-z0-9-]*")`. This matches Claude Code's documented "lowercase, hyphens" contract.

The two-stage form (sentinel H-1 fix) is intentional. A single regex check would silently accept `name: 42` because `str(42) = "42"` matches the regex (digits are in `[a-z0-9]`). Type-check first ensures YAML-native types other than string fail with a clear error message, and produces predictable behavior independent of YAML-coercion accidents.

YAML edge cases handled by the validator:
- `name: 42` → YAML int → fails type check → `PackLoadError("name in {path}: must be a string, got int")`.
- `name: true` → YAML bool → fails type check → `PackLoadError("name in {path}: must be a string, got bool")`.
- `name: ""` → empty string passes type check → fails regex (no match on empty) → `PackLoadError`.
- `name: "My Role"` → passes type check → fails regex (uppercase + space) → `PackLoadError`.
- `name: ` (key present, value missing) → YAML `None` → treat as absent (fall back to stem). The type-check `if raw_name is not None` clause makes this explicit.

**Bundled pack `name:` (resolves Q-11):**

Add `name:` to `general_purpose.md`, `explorer.md`, `planner.md`. Values match existing role keys exactly (`general-purpose`, `explorer`, `planner`). The existing `_FILE_FOR_KEY` mapping at `__init__.py:37-40` and the `loaded_key == key` assertion at `__init__.py:65-68` continue to pass unchanged. Substrate dogfoods its own format.

**Cross-stem collision in `discover_dir`** (`_user_loader.py:173-190`):

Today the collision check is `if key in pack:`. After this feature, `key` is canonical, so the check works correctly out of the box. The WARN message is updated to:

```
duplicate canonical name '{name}' in {dir}: {path_a} and {path_b} (alphabetically later wins)
```

Determinism contract: when canonical names collide within a single directory layer, the file path that sorts alphabetically later wins. Stems are NOT used as a tiebreak — canonical name IS the tiebreak target. (If two different stems produced the same canonical name, the file with the later path-sort wins; if two files have the same stem, the filesystem prevents the collision in the first place.)

**Cross-layer mixed-resolution shadow detection (resolves Q-9):**

`_warn_shadow_drop` (`_user_loader.py:362-431`) compares dict keys. Post-feature the keys are canonical; the comparison works correctly. Diagnostic enhancement: when canonical name collides across layers AND the underlying file stems differ, the WARN message names both stems alongside the canonical name:

```
project pack '{canonical}' (file: {project_path}) shadows user pack '{canonical}' (file: {user_path}) — fields dropped: [...]
```

This gives operators visibility into the "I named the file `runner.md` but it shadowed `senior-runner` because both had `name: runner`" failure mode.

**Transition INFO** (per D-7) emitted by `parse_pack_text` when `fm.name` and `path.stem.replace("_", "-")` differ:

```
INFO claude_crew.subagents.loader: pack {path} declares name '{name}' (file stem: '{stem}') — canonical key is '{name}'
```

One INFO per divergent pack, at load-time.

#### Pillar E — Comma-string / list coercion

**New helper in `_loader.py`:**

```python
def _coerce_str_or_list(value: Any, field_name: str) -> list[str]:
    """Coerce YAML string or list into a list of stripped strings.

    Closes a latent bug: bare list(d["tools"]) iterates a string into characters.
    Used for tools and disallowedTools — both fields accept Claude Code's
    string-or-list YAML polymorphism.

    Strict on element types: list elements must be strings. Coercing
    [None, "Read"] silently to ["None", "Read"] (sentinel L-1) produces
    bogus tool names that fail at spawn time with cryptic errors.
    """
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for s in value:
            if not isinstance(s, str):
                raise PackLoadError(
                    f"{field_name}: list element must be a string, "
                    f"got {type(s).__name__}: {s!r}"
                )
            stripped = s.strip()
            if stripped:
                result.append(stripped)
        return result
    raise PackLoadError(
        f"{field_name}: expected string or list, got {type(value).__name__}"
    )
```

Called from `_validate_frontmatter`:
- Line 315 (was: `tools=list(d["tools"])`) → `tools=_coerce_str_or_list(d["tools"], "tools") if "tools" in d else []`
- Lines 324-326 (was: `tuple(str(t) for t in d["disallowedTools"])`) → `disallowedTools=tuple(_coerce_str_or_list(d["disallowedTools"], "disallowedTools")) if "disallowedTools" in d else None`

**Edge-case contract (per D-3):**
- `tools: Read, Write, Edit` → `["Read", "Write", "Edit"]` ✓
- `tools: Read,,Write` → `["Read", "Write"]` (empty entries dropped silently)
- `tools: Read,` → `["Read"]` (trailing comma tolerated)
- `tools: ""` → `[]` (silent empty list — see X.3 resolution below)
- `tools: Read` (single string, no comma) → `["Read"]` (closes the regression case where today's code returns `["R","e","a","d"]`)
- `tools: [Read, Write]` → `["Read", "Write"]` (list form unchanged)
- `tools: 42` → `PackLoadError` (clear type error)

**X.3 resolution:** `tools: ""` and absent `tools:` both yield `[]`, but only the **absent** case emits the no-tools INFO (D-2). Rationale: an explicit empty string is operator intent (they wrote it); absence may be unintended. The diagnostic protects the "operator forgot to declare tools" failure mode without nagging the explicit case.

#### Pillar C — Optional `model:` and `tools:`

**SDK-boundary probe result (resolves blocking Q from Phase 1 gate):** `AgentDefinition(model=None)` is wire-safe. The SDK at `client.py:157` filters `None` fields via `{k: v for k, v in asdict(agent_def).items() if v is not None}`; the CLI at `subprocess_cli.py:253-254` only appends `--model` if truthy. **No defensive `"inherit"` fallback needed.**

**Pack-load behavior:**
- `model:` absent → `fm.model = None` → `AgentDefinition(model=None)` → SDK serialization filters `None` (`client.py:157`); CLI omits `--model` flag; SDK applies its own default at spawn.
- `tools:` absent → `fm.tools = ()` → `AgentDefinition(tools=())` → teammate spawns with empty tool surface (claude-crew, NOT SDK, owns this default for security reasons); INFO emitted at pack-load:
  ```
  INFO claude_crew.subagents.loader: agent '{key}' has no tools declared — teammate will spawn but cannot invoke tools
  ```

**Shadow-drop WARN coverage** (sentinel H-2 resolution):

The naive fix — adding `"model"` and `"tools"` to `_OPTIONAL_AGENTDEF_FIELDS` (`_user_loader.py:341-344`) — provides ZERO actual coverage for `tools`. `_check_drop` tests `higher_val is None`, but `AgentDefinition(tools=())` is empty tuple, NOT `None`. A project-level pack that drops `tools:` (silently stripping the user-level pack's tool surface) goes undetected.

**Two-part fix:**

1. **Add `"model"` to `_OPTIONAL_AGENTDEF_FIELDS`.** Works as expected — `model=None` is the absent sentinel; `_check_drop` detects the drop on the existing `is None` branch.
2. **Extend `_check_drop` with a new branch for collection-shrinkage.** Iterate `_COLLECTION_FIELDS = ("tools",)` — only `tools` qualifies; losing the tool surface silently is dangerous. `disallowedTools=[]` was deliberately preserved as operator intent in #17 (`test_explicit_empty_in_higher_does_NOT_warn`) — removing a restriction is intentional, not a silent drop. WARN names the field, the lost entries, and both file paths.
3. **Update stale comment** at `_user_loader.py:337-340` ("a drop cannot occur because tools/model are required") — both assertions become false post-feature. Replace with comment reflecting new semantics.

This preserves claude-crew's safe-by-default `tools=()` semantics while closing the silent shadow-drop gap.

#### Pillar B — Subagent prompt composition

**New helper in `_loader.py`** (replaces `_LEAF_SUFFIX` constant):

```python
SUBSTRATE_SUBAGENT_GUIDANCE = """\
## Substrate context

You are operating as a subagent within claude-crew, a multi-agent substrate
coordinated via an MCP server. The crew lead has dispatched you to complete a
focused task. You are a leaf node in this dispatch — your role definition fixes
your tool surface, and you cannot spawn further subagents (no Task tool by
design). Complete the assigned task, report your findings clearly, and exit.

---

"""

def build_subagent_prompt(body: str) -> str:
    """Compose a subagent's system prompt: substrate guidance, then role body.

    Asymmetric with build_teammate_prompt (which keeps body-first ordering for
    peer-list injection). Subagent path leads with substrate framing because
    subagents have no peer context to inject — the framing is foundational.
    """
    return SUBSTRATE_SUBAGENT_GUIDANCE + body.rstrip()
```

Composition site (`_loader.py:141`) becomes:
```python
prompt = build_subagent_prompt(body)
```

**Empty-body guard preserved:** `parse_pack_text`'s existing check `if not body.strip(): raise PackLoadError(...)` continues to fire BEFORE composition. Whitespace-only bodies cannot reach `build_subagent_prompt`.

**Lockstep updates (per SC-12 — full inventory from Phase 2 reconnaissance + sentinel M-2):**

Runtime composition site (the actual rename target):
- `claude_crew/subagents/_loader.py:85` (constant definition: replace `_LEAF_SUFFIX` with `SUBSTRATE_SUBAGENT_GUIDANCE` + `build_subagent_prompt`)
- `claude_crew/subagents/_loader.py:141` (composition site: `body.rstrip() + _LEAF_SUFFIX` → `build_subagent_prompt(body)`)

Test imports + assertions (must update in same commit):
- `tests/test_subagents.py:138` (import), `:148`, `:158`, `:164` (4 assertions on `_LEAF_SUFFIX`)
- `tests/test_subagents.py:173` (assertion `_LEAF_SUFFIX not in bodies[key]` → `SUBSTRATE_SUBAGENT_GUIDANCE not in bodies[key]`; logic unchanged — raw body must contain neither the old suffix nor the new prefix)
- `tests/test_teammate_prompt.py:23` (import), `:267` (`assert prompt.endswith(_LEAF_SUFFIX)` becomes `assert prompt.startswith(SUBSTRATE_SUBAGENT_GUIDANCE)` for subagent path)
- `tests/test_teammate_prompt.py:96, 264, 268` (string literals containing `_LEAF_SUFFIX` in error/assertion messages — non-blocking but stale post-rename; update for clarity)

Docstring references (must update):
- `claude_crew/teammate_prompt.py:13` (mentions `_LEAF_SUFFIX`)
- `claude_crew/subagents/__init__.py:53` (mentions `_LEAF_SUFFIX`)
- `claude_crew/subagents/_loader.py:101,127` (existing docstrings reference the constant by name)

Total: **13 sites** (was sentinel-flagged at 9; full sweep finds 13). All updated in the same commit as the rename — CI must stay green.

**Import path for `SUBSTRATE_SUBAGENT_GUIDANCE` and `build_subagent_prompt`** (sentinel M-5): keep them importable via `from claude_crew.subagents._loader import SUBSTRATE_SUBAGENT_GUIDANCE, build_subagent_prompt`. This matches the existing `_LEAF_SUFFIX` import pattern. Names are public (no `_` prefix) inside the private `_loader.py` module — the existing convention. No `__all__` change in `subagents/__init__.py` needed.

**Teammate path carve-out** (verified from `teammate_prompt.py:111`): existing formula `f"{pack_body.rstrip()}\n\n{addendum}"` is left UNCHANGED. The addendum injects peer-list context computed from sibling agents — body must lead so the addendum can append peer-aware context. This carve-out is documented in `build_teammate_prompt`'s docstring with a one-line rationale ("body-first because addendum injects late-bound peer context").

### Data contract: post-feature `PackFrontmatter`

| Field | Type | Required? | Default | Notes |
|---|---|---|---|---|
| `description` | `str` | yes | — | Only required field |
| `name` | `str \| None` | no | `None` | Canonical role key when present; regex `^[a-z0-9][a-z0-9-]*$` |
| `model` | `str \| None` | no | `None` | None → SDK default at spawn |
| `tools` | `list[str]` | no | `[]` | Accepts string or list at YAML level |
| `color` | `str \| None` | no | `None` | UI metadata, captured-and-ignored |
| `effort`, `maxTurns`, `initialPrompt`, `background`, `skills`, `permissionMode`, `disallowedTools`, `settingSources`, `mcpServers`, `memory` | unchanged | no | unchanged | Pre-existing optionals |

`_REQUIRED = ("description",)`. `_OPTIONAL` adds `"name"` and `"color"`.

### Edge-case matrix

| Case | Behavior | Test? |
|---|---|---|
| Pack with only `name:` + `description:` | Loads. `tools=[]`, `model=None`. INFO for no-tools. | SC-8 |
| Pack with `name: My Role` (invalid) | `PackLoadError` ("must match [a-z0-9][a-z0-9-]*"). | New test |
| Pack with `name: 42` (YAML int) | `PackLoadError` ("must be a string, got int") — type-check before regex. | New test |
| Pack with `name: true` (YAML bool) | `PackLoadError` ("must be a string, got bool"). | New test |
| Pack with `tools: [None, "Read"]` | `PackLoadError` ("list element must be a string, got NoneType"). | New test |
| Project pack drops `tools:` shadowing user pack with `tools: [Read, Write]` | Shadow-drop WARN naming the dropped tools (sentinel H-2). | New test |
| Pack with `name:` empty value (YAML None) | Falls back to stem, no INFO (no divergence). | New test |
| Pack with `tools: Read, Write, Edit` | Comma-coerced to list. | SC-5 |
| Pack with `tools: Read` (no comma, single string) | `["Read"]` — closes char-iteration bug. | SC-5 |
| Pack with `tools: ""` | `[]`, no INFO (operator intent). | SC-5 / X.3 |
| Pack with no `tools:` | `[]`, INFO emitted. | SC-4 |
| Pack with no `model:` | `model=None`. | SC-3 |
| Two files in same dir, both `name: runner` | Collision WARN; alphabetically-later path wins. | SC-2a |
| User pack `name: scout`, project pack stem `scout.md` no `name:` | Cross-layer shadow detected (canonical key match); shadow WARN names both files. | New test |
| Whitespace-only body | `PackLoadError` (existing guard fires before composition). | SC-7 |
| `disallowedTools: Bash, WebFetch` | Comma-coerced via shared helper. | SC-14 |
| `_LEAF_SUFFIX` removed | All 9 lockstep sites updated; CI green. | SC-12 |

### Implementation surface (file:line touch list)

**`claude_crew/subagents/_loader.py`:**
- Add module logger: `logger = logging.getLogger(__name__)` (sentinel L-2 — `_loader.py` has none today; transition INFO and no-tools INFO need a logger).
- `PackFrontmatter` dataclass (lines 25-66): add `name`, `color`; relax `model`, `tools` to optional. `tools` becomes `tuple[str, ...]` (sentinel M-1).
- `_REQUIRED` (line 68): shrink to `("description",)`.
- `_OPTIONAL` (lines 69-71): add `"name"`, `"color"`.
- `_LEAF_SUFFIX` constant (line 85): remove. Replace with `SUBSTRATE_SUBAGENT_GUIDANCE` constant + `build_subagent_prompt` function.
- `parse_pack_text` (line 138): canonical-key resolution; transition INFO log on stem/name divergence; two-stage `name` validation (type-check then regex).
- Composition site (line 141): use `build_subagent_prompt(body)`.
- `_validate_frontmatter` (lines 198-331): `_coerce_str_or_list` helper (with strict element-type check); `name` validation; `model`/`tools` optional handling; no-tools INFO.

**`claude_crew/subagents/_user_loader.py`:**
- `_OPTIONAL_AGENTDEF_FIELDS` (lines 341-344): add `"model"` (works via existing `is None` branch). Note: `"tools"` does NOT go here (sentinel H-2) — see new collection-shrinkage branch in `_check_drop`.
- Stale comment at lines 337-340 ("a drop cannot occur because tools/model are required"): update to reflect new optional semantics.
- `_check_drop`: extend with collection-shrinkage branch (or add sibling `_check_drop_collection`) for `tools` and `disallowedTools`. Detect non-empty→empty drop across layers.
- `discover_dir` collision WARN (lines 173-190): updated message naming canonical name + both paths. Existing message already includes both `prior` and `path`; enhancement is to reframe as canonical-name collision and include the canonical name in the message text.
- `_warn_shadow_drop` (lines 362-431): enhanced message when stems differ (per Q-9 resolution).
- `_ACCEPTED_FRONTMATTER_KEYS` (line 57): no manual change — auto-propagates from dataclass.

**`claude_crew/subagents/__init__.py`:**
- Docstring at line 53: update `_LEAF_SUFFIX` reference.
- `load_default_pack` (lines 44-73): no logic change; bundled pack `name:` declarations match keys, assertion holds.

**`claude_crew/teammate_prompt.py`:**
- Docstring at line 13: update `_LEAF_SUFFIX` reference; document body-first carve-out.
- No logic change.

**Bundled pack files** — add `name:` to frontmatter (substrate dogfood):
- `claude_crew/subagents/general_purpose.md`: `name: general-purpose`
- `claude_crew/subagents/explorer.md`: `name: explorer`
- `claude_crew/subagents/planner.md`: `name: planner`

**Tests** (lockstep; new tests in same files):
- `tests/test_pack_loader.py`: SC-1, SC-2, SC-2a, SC-3, SC-4, SC-5 (6 scenarios), SC-6, SC-8, name-validation Q-10 cases, X.3 INFO behavior.
- `tests/test_subagents.py`: SC-12 lockstep, SC-7 (substrate-prefix assertion).
- `tests/test_user_loader.py`: cross-layer shadow detection, transition INFO, `_OPTIONAL_AGENTDEF_FIELDS` shadow-drop coverage.
- `tests/test_teammate_prompt.py`: SC-12 lockstep (constant rename), teammate-path carve-out preserved.
- `tests/test_pack_loader.py` or new file: SC-9 (regression), SC-10a (real `~/.claude/agents/` zero-WARN check), SC-11 (caplog-driven INFO contract test), SC-13 (transcript role stability).
- `tests/test_live_sdk.py` or new gated file: SC-10b (live spawn → response with `~/.claude/agents/runner.md`).

### Assumptions (default-accept; correct if wrong)

- **A-1**: Operator's existing `~/.claude/agents/` files do not declare `name:` values that violate `^[a-z0-9][a-z0-9-]*$`. (Spot-check confirms: Jerome's 5 files use `name: builder`, `sentinel`, `runner`, `scout`, `feature-planner` — all valid.)
- **A-2**: No external script or doc refers to bundled pack role keys via stem (e.g., something hardcoding `general_purpose` instead of `general-purpose`). Adding `name:` to bundled packs preserves current keys.
- **A-3**: `_OPTIONAL_AGENTDEF_FIELDS` shadow-drop messaging is acceptable as-is — we only need to add `model` and `tools` to the set, not redesign the message.
- **A-4**: Windows line-ending support (`\r\n` in frontmatter delimiters) is **out of scope** — `_split_frontmatter` rejects it today, this feature does not touch the issue. Logged as BACKLOG candidate.
- **A-5**: `name:` collisions across precedence layers (user vs project vs bundled) continue to use existing `merge_packs` whole-replacement semantics — no field-level merging, no transition warning beyond the existing shadow-drop WARN. The Q-9 enhancement only improves message clarity when stems diverge.

### Open Questions (none load-bearing for Phase 3 entry)

All Phase 1 questions resolved. Phase 2 surfaces no new blocking questions.

### Phase 2 gate

- ✅ SDK-boundary probe for `AgentDefinition(model=None)` complete (wire-safe; no defensive default needed).
- ✅ Q-9, Q-10, Q-11 answered.
- ⏳ Co-architect Phase 2 review via `mcp__claude-crew__spawn_teammate(role="feature-planner")` — substrate dogfood per session directive.
- ⏳ Sentinel Phase 2 review (pseudocode-reader lens — "what would break at runtime if I implemented exactly this?").
- ⏳ User confirmation to proceed to Phase 3.

---

## Phase 3: Task Breakdown

Five tasks, sequenced per Phase 2's synthesis order (E → C → A+D → B → E2E). Each task lands in its own commit on `feature/claude-code-agent-format-compat`.

### T1 — Pillar E: comma-string coercion + latent-bug fix

**Scope**: `_coerce_str_or_list` helper. Applied to `tools` and `disallowedTools` in `_validate_frontmatter`. Module logger added to `_loader.py`. Closes the silent character-iteration bug at `_loader.py:315` and `:324`.

**BDD scenarios:**
```
Scenario: tools as comma-separated string
  Given a pack file declaring `tools: Read, Write, Edit, Bash`
  When parse_pack_text runs
  Then PackFrontmatter.tools is ("Read", "Write", "Edit", "Bash")

Scenario: tools as single string (regression — closes character-iteration bug)
  Given a pack file declaring `tools: Read`
  When parse_pack_text runs
  Then PackFrontmatter.tools is ("Read",)
  And NOT ("R", "e", "a", "d")

Scenario: tools with mcp__ prefixed entry
  Given a pack file declaring `tools: Read, mcp__knowledge-graph__search_codebase_definitions`
  When parse_pack_text runs
  Then PackFrontmatter.tools includes "mcp__knowledge-graph__search_codebase_definitions" exactly

Scenario: tools with trailing comma
  Given `tools: Read, Write,`
  Then tools is ("Read", "Write")

Scenario: tools list form unchanged
  Given `tools: [Read, Write]`
  Then tools is ("Read", "Write")

Scenario: tools with non-string list element
  Given `tools: [None, Read]`
  Then PackLoadError "list element must be a string, got NoneType"

Scenario: tools type error
  Given `tools: 42`
  Then PackLoadError "expected string or list, got int"

Scenario: disallowedTools comma-string parity
  Given `disallowedTools: Bash, WebFetch`
  Then disallowedTools is ("Bash", "WebFetch")
```

**Verification**: `uv run pytest tests/test_pack_loader.py -k "coerce or comma_string"` — fails today (helper doesn't exist; latent bug returns chars), passes after T1.

**SCs covered**: SC-5, SC-14.

**Dependencies**: none.

---

### T2 — Pillar C: optional model + tools + shadow-drop coverage

**Scope**: Make `model:` and `tools:` optional in `PackFrontmatter`. Shrink `_REQUIRED` to `("description",)`. Change `tools` field type to `tuple[str, ...]` with `field(default_factory=tuple)`. Add no-tools INFO log. Add `"model"` to `_OPTIONAL_AGENTDEF_FIELDS` (`_user_loader.py`). Extend `_check_drop` with collection-shrinkage branch for `tools`/`disallowedTools`. Update stale comment at `_user_loader.py:337-340`.

**BDD scenarios:**
```
Scenario: pack without model loads cleanly
  Given a pack file with no `model:` field
  When parse_pack_text runs
  Then PackFrontmatter.model is None
  And AgentDefinition.model is None

Scenario: pack without tools loads cleanly with INFO
  Given a pack file with no `tools:` field
  When parse_pack_text runs
  Then PackFrontmatter.tools is ()
  And AgentDefinition.tools is ()
  And caplog at INFO contains "agent '<key>' has no tools declared"

Scenario: explicit empty-string tools is silent
  Given `tools: ""`
  Then tools is ()
  And NO no-tools INFO is emitted (operator intent)

Scenario: model shadow-drop detected (project shadows user)
  Given user-level pack with `model: opus`
  And project-level pack at same canonical name with no `model:`
  When build_merged_pack runs
  Then WARN "field 'model' dropped" with both file paths

Scenario: tools shadow-drop detected (collection shrinkage)
  Given user-level pack with `tools: [Read, Write]`
  And project-level pack at same canonical name with no `tools:`
  When build_merged_pack runs
  Then WARN names dropped tools and both file paths

Scenario: regression — existing required-field packs still load
  Given the bundled general_purpose.md pack (has model + tools)
  When parse_pack_text runs
  Then the resulting PackFrontmatter is identical to pre-feature shape modulo new optional fields
```

**Verification**: `uv run pytest tests/test_pack_loader.py tests/test_user_loader.py -k "optional or shadow_drop"` + full suite green. T1 must be merged first (T2 depends on `tools` being a tuple coerced through the new helper).

**SCs covered**: SC-3, SC-4, SC-9 (regression).

**Dependencies**: T1.

---

### T3 — Pillar A+D: canonical name resolution

**Scope**: Add `name` and `color` to `PackFrontmatter`. Two-stage validation (type-check + regex). `parse_pack_text` resolves canonical key. Transition INFO on stem/name divergence. `discover_dir` collision WARN reframe. `_warn_shadow_drop` enhanced message when stems differ. Add `name:` declarations to all three bundled packs (substrate dogfood).

**BDD scenarios:**
```
Scenario: name field silent-accepted
  Given a pack file declaring `name: senior-reviewer`
  When parse_pack_text runs
  Then no "unsupported frontmatter key" WARN is emitted
  And PackFrontmatter.name is "senior-reviewer"

Scenario: canonical name overrides stem
  Given a pack file at /tmp/agents/old-file.md declaring `name: senior-reviewer`
  When parse_pack_text runs
  Then the returned key is "senior-reviewer"
  And caplog at INFO contains "declares name 'senior-reviewer' (file stem: 'old-file')"

Scenario: stem fallback when name absent
  Given a pack file at /tmp/agents/scout.md with no `name:` field
  When parse_pack_text runs
  Then the returned key is "scout"
  And no transition INFO is emitted (names match)

Scenario: name validation — int rejected
  Given `name: 42`
  Then PackLoadError "must be a string, got int"

Scenario: name validation — bool rejected
  Given `name: true`
  Then PackLoadError "must be a string, got bool"

Scenario: name validation — invalid chars
  Given `name: My Role`
  Then PackLoadError "must match [a-z0-9][a-z0-9-]*"

Scenario: name validation — empty string
  Given `name: ""`
  Then PackLoadError "must match [a-z0-9][a-z0-9-]*"

Scenario: name validation — null falls back to stem
  Given `name: ` (YAML None)
  When parse_pack_text on /tmp/agents/scout.md runs
  Then the returned key is "scout" (stem fallback)

Scenario: cross-stem collision in same dir
  Given /tmp/agents/runner-a.md declaring `name: runner`
  And   /tmp/agents/runner-b.md declaring `name: runner`
  When discover_dir runs
  Then WARN names canonical name "runner" AND both file paths
  And the alphabetically-later path wins

Scenario: color field silent-accepted
  Given `color: blue`
  Then no unsupported-key WARN
  And PackFrontmatter.color is "blue"

Scenario: bundled packs declare matching name
  Given the three bundled pack files
  When load_default_pack runs
  Then loaded_key == _FILE_FOR_KEY-derived key for each
  And no transition INFO is emitted (names match stems)

Scenario: transcript role stability
  Given a user pack at /tmp/agents/old-file.md declaring `name: scout`
  When broker.spawn_teammate(role="scout") runs
  Then the transcript record's "role" field is "scout"
  And TeammateInfo.role is "scout"
```

**Verification**: `uv run pytest tests/test_pack_loader.py tests/test_user_loader.py tests/test_subagents.py -k "canonical or name_field or color_field or transition_info or transcript_role"` + full suite green.

**SCs covered**: SC-1, SC-2, SC-2a, SC-6, SC-13.

**Dependencies**: T1, T2.

---

### T4 — Pillar B: subagent prompt composition refactor

**Scope**: Replace `_LEAF_SUFFIX` constant with `SUBSTRATE_SUBAGENT_GUIDANCE` constant + `build_subagent_prompt(body)` helper in `_loader.py`. Update composition site at `_loader.py:141`. Update all 13 lockstep sites: 4 test imports/asserts + 1 substring-not-in test in `test_subagents.py`; 1 import + 1 endswith→startswith assertion + 3 stale string literals in `test_teammate_prompt.py`; 4 docstrings (`teammate_prompt.py:13`, `subagents/__init__.py:53`, `_loader.py:101,127`).

**BDD scenarios:**
```
Scenario: subagent prompt leads with substrate guidance
  Given a pack body "You are a probe."
  When build_subagent_prompt(body) runs
  Then result starts with SUBSTRATE_SUBAGENT_GUIDANCE
  And result ends with "You are a probe."

Scenario: substrate guidance is model-agnostic
  Given SUBSTRATE_SUBAGENT_GUIDANCE constant
  Then it does NOT contain the word "Sonnet" or "Opus" or "Haiku"
  And it does NOT mention any specific model name

Scenario: empty body raises PackLoadError before composition
  Given a pack file with whitespace-only body
  Then PackLoadError "body must not be empty"
  And build_subagent_prompt is NOT called

Scenario: teammate path carve-out preserved
  Given build_teammate_prompt(role, pack_body, agents)
  Then the result is f"{pack_body.rstrip()}\n\n{addendum}" (body-first ordering unchanged)
  And the result does NOT start with SUBSTRATE_SUBAGENT_GUIDANCE
  (Teammate path explicitly does NOT use build_subagent_prompt)

Scenario: lockstep — 13 sites updated, CI green
  Given the rename commit
  Then no _LEAF_SUFFIX symbol remains anywhere in the codebase
  And `grep -r _LEAF_SUFFIX` from repo root returns zero matches
  And full test suite passes
```

**Verification**: `uv run pytest` (full suite) + `! grep -r _LEAF_SUFFIX claude_crew tests` (must return non-zero, i.e., no matches).

**SCs covered**: SC-7, SC-12.

**Dependencies**: T3.

---

### T5 — E2E pipeline + parity smoke test (final task before merge)

**Scope**: Cohesive end-to-end tests that exercise the full feature pipeline through `parse_pack_text` → `discover_dir` → `merge_packs` → `factories.build_merged_pack` → spawn (stub-mode for happy/sad path; live-gated for full SDK probe). Caplog-driven INFO contract test. Real `~/.claude/agents/` zero-WARN smoke test.

**BDD scenarios (each is its own pytest):**
```
Scenario: SC-8 happy path — minimal pack file
  Given a pack file with ONLY name + description in frontmatter and a non-empty body
  When build_merged_pack runs against a tmp_path containing it
  Then the pack loads under the canonical role
  And the role is spawnable via stub-mode broker
  And NO unsupported-key WARNs are emitted
  And the resulting AgentDefinition has tools=() and model=None

Scenario: SC-10a stub-mode WARN check — real ~/.claude/agents
  Given the operator's actual ~/.claude/agents/ directory
  When build_merged_pack runs against it under stub mode
  Then every file loads (no PackLoadError)
  And caplog contains ZERO "unsupported frontmatter key" WARNs
  And every file becomes a spawnable role

Scenario: SC-10b live SDK dogfood (gated by CLAUDE_CREW_LIVE_TESTS=1)
  Given ~/.claude/agents/runner.md exists
  When mcp__claude-crew__spawn_teammate(role="runner") runs (live SDK)
  And a trivial prompt is sent
  Then a response envelope returns
  And caplog at startup contains ZERO unsupported-key WARNs

Scenario: SC-11 INFO contract per pack source
  Given build_merged_pack runs against bundled + user + project packs
  Then caplog at INFO contains exactly one "loaded N packs from <source>" line per source
  And each line names the source label, source path, count, and role keys

Scenario: SC-9 regression — bundled pack contract unchanged
  Given the three bundled pack files (now declaring name:)
  When load_default_pack runs
  Then the (pack, role_ss, bodies) tuple shape is unchanged
  And every key in PACK_MEMBERS is present
  And every prompt starts with SUBSTRATE_SUBAGENT_GUIDANCE

Scenario: cross-layer mixed-resolution shadow detection
  Given user-level ~/.claude/agents/old-name.md declaring `name: scout`
  And project-level <project>/.claude/agents/scout.md with no name:
  When build_merged_pack runs
  Then WARN naming canonical name "scout" AND both stems

Scenario: sad path — malformed YAML
  Given a pack file with invalid YAML in the frontmatter
  When build_merged_pack runs
  Then PackLoadError surfaces with the file path
  And other valid sibling files still load

Scenario: sad path — name validation cascade
  Given a directory with one valid pack and one pack declaring `name: 42`
  When discover_dir runs
  Then the invalid pack triggers a WARN and is skipped
  And the valid pack still loads
```

**Verification**: `uv run pytest tests/test_format_compat_e2e.py` (new file) + `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_format_compat_e2e.py::test_live_runner_dogfood` (gated, run locally before merge).

**SCs covered**: SC-8, SC-10a, SC-10b, SC-11, SC-9 (regression). Plus the cross-layer shadow case from T3 promoted to E2E for full-pipeline validation.

**Dependencies**: T1, T2, T3, T4 — all behavior must be in place.

---

### Phase 3 gate

- ✅ 5 tasks, each independently testable, each with verification command that fails today.
- ✅ Every Phase 1 SC (SC-1..SC-14) traces to a BDD scenario in T1..T5.
- ✅ Dedicated E2E test task (T5) with happy + sad path coverage.
- ✅ Dependencies linear and minimal: T1 → T2 → T3 → T4 → T5.
- ⏳ Implementation strategy decision: Kael direct vs team build vs deep build.
- ⏳ User approval of plan.

---

## Phase 4: Implementation

*Stub.*

---

## Phase 5: Completion

*Stub.*
