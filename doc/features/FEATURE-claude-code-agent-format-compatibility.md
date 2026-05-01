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

- [ ] **SC-3: `model:` is optional at pack-load.** A pack file without `model:` parses successfully. The resulting `AgentDefinition.model` is `None` OR a documented default (TBD per Phase 2 — likely `"inherit"` to match Claude Code's documented default, then resolved to the SDK's default at spawn time).

- [ ] **SC-4: `tools:` is optional at pack-load.** A pack file without `tools:` parses successfully. The resulting `AgentDefinition.tools` is an empty list. (Spawn behavior: teammate has no tools available — operator must declare tools to make the role functional, but the pack itself loads cleanly.)

- [ ] **SC-5: `tools:` accepts comma-separated string format.** A pack file declaring `tools: Read, Write, Edit, Bash` parses with `tools = ["Read", "Write", "Edit", "Bash"]`. Whitespace around commas tolerated. The existing list form continues to work unchanged.

- [ ] **SC-6: `color:` field silent-accepted.** A pack file declaring `color: blue` parses without an unsupported-key WARN. Value is captured on `PackFrontmatter` but not consumed (UI metadata; claude-crew's dashboard may use it later — out of scope for this feature).

- [ ] **SC-7: System prompt composition revised — substrate guidance leads, agent body appended.**
   - Subagent dispatch path: `AgentDefinition.prompt = SUBSTRATE_SUBAGENT_GUIDANCE + "\n\n" + body`. The leaf-constraint behavior is preserved (subagents are still leaves) but expressed as substrate guidance at the top of the prompt rather than as a trailing suffix.
   - Teammate path: existing `build_teammate_prompt` continues to wrap the body with substrate teammate-context. Verify the order matches the new contract (guidance leads, body appended).
   - Operators reading their teammate's system prompt should see claude-crew's framing first, then their own role definition verbatim.

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

- [ ] **SC-10: Live dogfood: spawn one of Jerome's existing `~/.claude/agents/*.md` files (e.g., `runner.md`) under the new code, observe ZERO WARNs at server startup AND the role works (spawn → send → response).** Closes the noisy-startup-log regression that #17's BACKLOG entry M-2 flagged. Today: 5 WARNs at startup for `name:` keys. Post-feature: 0.

- [ ] **SC-11: Operator-visible startup summary (existing INFO logs verified).** When `build_merged_pack` runs, the existing per-source INFO logs name each pack file loaded. After this feature, those logs are the operator's authoritative record of which packs the substrate is using. (No new code required if existing INFO logs are sufficient — verify in Phase 2 reconnaissance.)

- [ ] **SC-12: `_LEAF_SUFFIX` deprecation/rename handled cleanly.** The existing `_LEAF_SUFFIX` constant is renamed to `_SUBAGENT_GUIDANCE` (or similar) to reflect its new role as substrate guidance. The content is revised to lead with positive framing (what claude-crew is, what the agent's responsibilities are) rather than starting with a constraint. The constraint (no Task tool, leaf-only) is still expressed but as part of the framing.

### Questions

- [ ] **Q-1: When `model:` is absent, what value does `PackFrontmatter.model` hold AND what does it forward to `AgentDefinition`?**
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

*Stub — to be filled when Phase 1 is signed off and the SDD workflow continues.*

Anticipated design pillars (for co-architect three-pushback warmup):

- **Pillar A**: Canonical name resolution. `name:` as primary, file stem as fallback. What happens on collision (two files with same `name:` in different precedence layers)? Existing `merge_packs` whole-replacement applies — but the collision detection uses file stems today. Need to switch detection to canonical name.
- **Pillar B**: System prompt composition refactor. Promote `_LEAF_SUFFIX` to a `build_subagent_prompt(body) -> str` helper symmetric with `build_teammate_prompt`. Decision: where does the helper live (`_loader.py`? new module?). Decision: what's the substrate guidance text content (Q-5 + Q-6).
- **Pillar C**: Optional-field defaulting strategy. `model:` and `tools:` going from required to optional. Where do defaults apply — pack-load or spawn-time? `None` vs explicit-default. Affects every consumer of those fields.

---

## Phase 3: Task Breakdown

*Stub.*

---

## Phase 4: Implementation

*Stub.*

---

## Phase 5: Completion

*Stub.*
