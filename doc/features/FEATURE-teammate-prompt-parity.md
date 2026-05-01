# Feature: Teammate Prompt Parity (#21)

**Status**: In Progress (Phase 1)
**Created**: 2026-04-30
**Scope**: Light SDD (Phase 1 brief + Phase 2 mini-spec, 1-2 implementation tasks). Co-architect-recommended scope; skip the Phase 3 deep breakdown and Phase 5 retrospective ceremony unless the implementation surfaces unexpected complexity.

---

## Phase 1: Research & Requirements

### Problem Statement

When `spawn_teammate(role="X")` creates a top-level teammate, `SdkTeammate.__init__` falls back to a generic 8-word default system prompt (`sdk_teammate.py:280-281`):

```python
def _default_system_prompt(role: str) -> str:
    return f"You are a {role}. Help the lead with {role}-level work."
```

The pack-file body for role X (e.g., `claude_crew/subagents/general_purpose.md` — role identity, contract, voice, available tools, anti-patterns) is **never seen by a top-level teammate**. It's only used when role X is invoked as a Task subagent (the SDK pulls it from the agent definition's `prompt` field).

Meanwhile teammates DO get the Task tool implicitly. When `SdkTeammate` passes `agents=self._agents` in `ClaudeAgentOptions` (`sdk_teammate.py:802-806`), the SDK auto-adds Task so the teammate can dispatch to subagents. So teammates *can* spawn subagents — they just have no instruction telling them they should, no awareness of which subagents exist, no role-shaped operating style.

This was operationally surfaced during the F18 session: a persistent Opus co-architect burned **1.3M input tokens across 6 turns** reading raw files in its own context, because its 8-word system prompt told it nothing about delegation. A per-prompt onboarding nudge dropped the next equivalent run to 227K input tokens (-83%) — proving the structural issue and the magnitude of the gap.

A second contradiction makes this gnarlier: pack bodies today contain **assertions that are lies for teammate context**. Both `general_purpose.md:28` and `planner.md:32` literally state "you have no Task tool by design — subagents are leaves." That language is correct when the role is invoked as a leaf subagent. When a teammate reads it, the prompt is asserting a constraint that is empirically false. A naive "just give teammates the pack body too" change would create internal-contradiction prompts (refusals, hedging).

### Success Criteria

- [ ] **SC-1**: When `spawn_teammate(role="X")` succeeds, the resulting `SdkTeammate.system_prompt` contains the pack-file body for role X — NOT the generic 8-word default. Verified by inspecting `teammate._system_prompt` after spawn for each of the three packs (explorer, general_purpose, planner) and asserting the role identity / contract sections appear.

- [ ] **SC-2**: A teammate's system prompt is the concatenation `<pack_body>\n\n<_TEAMMATE_ADDENDUM>`. The addendum is generated at spawn time and contains four ordered sections, each delimited by a stable section sentinel (Markdown heading like `## Available teammates`, `## Delegation`, etc — exact set pinned in Phase 2). Verified by asserting the section sentinels appear in the expected order. **Avoid asserting on prose**: section heading content is the test surface; body wording can change without breaking tests.

- [ ] **SC-3**: Pack-file leaf-assertion content is FACTORED OUT of the markdown body and into a `_LEAF_SUFFIX` constant in the subagent loader (`claude_crew/subagents/_loader.py` or equivalent). Pack body asserts only role-context-true content (true in BOTH teammate and subagent contexts). Verified by grep: the strings "subagents are leaves" and "you have no Task tool" no longer appear in any `claude_crew/subagents/*.md` markdown body.

- [ ] **SC-4**: When a role is invoked as a Task subagent (the existing leaf path), the agent definition's `prompt` field is `<pack_body>\n\n<_LEAF_SUFFIX>`. Preserves the prior leaf semantics: subagents still know they have no Task tool and should not attempt to delegate further. Verified by an existing-style test (`tests/test_subagents.py`) that the assembled subagent prompt still contains the leaf assertion.

- [ ] **SC-5**: Delegation enablement (deterministic). Assert on the assembled teammate prompt that (a) the explorer subagent appears in the peer list, AND (b) the prompt's delegation section names "delegate" / mentions the explorer by name as a place to send file-read work. Stub-mode is sufficient — this proves delegation is *enabled* and *encouraged*, not that the model in fact delegates (model behavior is verified live in Phase 5 follow-up; see Phase 5).

- [ ] **SC-6**: **Static contradiction-lint** (replaces the original "no refusing-via-contradiction" claim — `StubTeammate` echoes envelopes and never consumes the system prompt, so contradiction-driven refusals are unmeasurable in stub mode). Assemble the teammate prompt for each bundled role (explorer, general_purpose, planner). For each assembled prompt, assert ZERO matches against a curated negative-pattern list of substrings that would indicate teammate-context contradictions: e.g., `"you have no Task tool"`, `"subagents are leaves"`, `"cannot spawn"`. The pattern list lives in the test file as a constant. Fast, deterministic, catches the real failure mode (a leaked leaf-assertion the loader missed).

- [ ] **SC-7**: User-level pack files (loaded via `_user_loader.py` from `~/.claude/agents/` or project `.claude/agents/`) are handled by the same prompt-assembly path. If a user defines their own role with leaf-language in the body, the loader either (a) leaves it alone (user's responsibility) OR (b) the addendum at the bottom contradicts and we accept the contradiction for user packs. Co-architect should pick (a) at Phase 2 — bundled packs are our concern, user packs aren't. Document explicitly.

- [ ] **SC-8**: No regression — full test suite passes (currently 530 passed, 10 skipped post-#18). Existing subagent behavior unchanged: `tests/test_subagents.py` still passes without modification (the leaf assertion still appears in the assembled subagent prompt, just sourced from `_LEAF_SUFFIX` instead of inlined in the markdown).

### Questions

- [x] **OQ-1 RESOLVED** (co-architect): append `_LEAF_SUFFIX` at `AgentDefinition` assembly in `_loader.py` (the body→prompt step). Bundled and user packs flow through uniformly. Phase 2 must pin the file:line.

- [x] **OQ-2 RESOLVED** (co-architect): **(b)** — new module `claude_crew/teammate_prompt.py` with constants and assembly function. Distinct concern, testable standalone. (a) bunches concerns; (c) wrongly puts prompt semantics on Broker.

- [x] **OQ-3 RESOLVED** (co-architect): use `AgentDefinition.description` as-is, no truncation. Description is reliably one-line in bundled packs.

- [x] **OQ-4 RESOLVED** (co-architect): user packs missing description → fall back to name-only. Document in addendum-assembly docstring.

- [x] **OQ-5 RESOLVED** (co-architect): pack body owns self-identity. Addendum is purely teammate-vs-subagent context. The role's own name does NOT need to appear in the addendum's "you are X" form.

- [x] **OQ-6 NEW (co-architect-flagged hidden risk)**: peer-list self-filtering. A `planner` teammate listing `"planner"` as one of ITS own peers is wrong (it's listing itself as a delegation target). Filter `self.role` out of `self._agents` when building the peer list. Pinned now to prevent the bug.

- [x] **OQ-7 NEW (co-architect-flagged Phase 1 gap)**: is `_TEAMMATE_ADDENDUM` a static string or a template-with-substitutions? Given SC-2's ordered sections + SC-5's dynamic peer list, it must be a template (assembly function that injects the peer list at spawn time). Static string is insufficient. Phase 2 must define the assembly contract (function signature, inputs, output).

### Constraints & Dependencies

- **Requires**: `claude_crew/sdk_teammate.py` (the spawn path), `claude_crew/subagents/_loader.py` and `_user_loader.py` (pack loading), the three pack files (`explorer.md`, `general_purpose.md`, `planner.md`).
- **Breaking changes at the public API level**: None. `spawn_teammate` signature unchanged; the change is internal to system prompt construction. Operator visible difference: teammates that previously got 8-word prompts now get rich prompts and start delegating.
- **Behavioral changes**: Yes, intentionally. Existing teammates spawned post-#21 will have meaningfully different system prompts. This is the WHOLE point. Mitigation: SC-6 static contradiction-lint catches leaked leaf-assertions in assembled teammate prompts.
- **Forward-compat with #15** (expanded subagent pack — adds reviewer + runner roles): the new packs will benefit from #21 from day one. No additional work needed when #15 lands.
- **Forward-compat with #17** (agent definition parity — adds mcpServers / permissionMode / disallowedTools / memory to PackFrontmatter): independent. #21 is about prompt assembly; #17 is about pack metadata. They can ship in either order.
- **No new dependencies**: stdlib only.
- **Performance**: prompt assembly happens once per spawn. Trivial cost (string concat plus iterating the agents dict).

### Hidden risks identified by co-architect (must address in Phase 2)

These are risks where SC-1 through SC-8 could all pass while the feature is silently wrong:

- **R-1 (peer-list ordering flake)**: peer-list iteration must sort by name (alphabetical), otherwise tests asserting on the addendum's content will couple to dict insertion order — fragile across Python versions and environments. Phase 2 must specify "sort peers by name."
- **R-2 (self-inclusion bug)**: a `planner` teammate listing `"planner"` in its own peer list is incorrect. The teammate cannot delegate to itself. Filter `self.role` out before assembly. Anchored by OQ-6.
- **R-3 (malformed user packs)**: user-level packs from `~/.claude/agents/` may have missing or non-string fields. Defensive handling required: skip malformed entries, log at WARNING, never raise during prompt assembly.
- **R-4 (Task-tool-name coupling)**: addendum should reference *delegation behavior* ("delegate file reads to the explorer subagent"), NOT the literal SDK tool name (`"use Task to delegate"`). Insulates against future SDK tool renames.

**Gate**: Questions captured for Phase 2 routing, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

*(Mini-spec per scope decision — see header.)*

### Architecture Overview

Two divergence points consume the same shared `pack_body` source of truth, with different suffixes/addendums:

```
                       pack file (markdown)
                              │
                              ▼
                    parse_pack_text(body)
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
    AgentDefinition.prompt =          PackFrontmatter.body =
       body + _LEAF_SUFFIX                  body  (raw, unchanged)
                │                           │
                ▼                           ▼
       (consumed by SDK when               (consumed by SdkTeammate
        Task subagents are invoked)        at spawn time)
                                            │
                                            ▼
                              build_teammate_prompt(
                                  role,
                                  pack_body=body,
                                  agents=...,
                              )
                                            │
                                            ▼
                              body + _TEAMMATE_ADDENDUM
                                            │
                                            ▼
                              SdkTeammate._system_prompt
```

The pack body itself stays role-context-true (identity, contract, voice). The leaf-vs-teammate divergence lives entirely in two constants (`_LEAF_SUFFIX`, `_TEAMMATE_ADDENDUM`) appended at the right moment by the right code path.

### Data / API Contracts

```python
# claude_crew/teammate_prompt.py — NEW MODULE

# Curated negative-pattern list for SC-6 contradiction-lint.
# Strings that appear in this tuple MUST NOT appear in any assembled
# teammate prompt. Tightening this list is how we add future
# contradiction guards.
NEGATIVE_PATTERNS: tuple[str, ...] = (
    "you have no Task tool",
    "no Task tool by design",
    "subagents are leaves",
    "cannot spawn",
    "use the Task tool",  # D-5 enforcement: addendum should reference behavior, not tool name
)

# Section sentinels (Markdown headings) for SC-2 ordering assertions.
# Tests assert these appear in the assembled prompt in this order.
# Sentinel text is part of the public test contract; do not change without updating tests.
SENTINEL_CONTEXT = "## Operating context"
SENTINEL_PEERS = "## Available teammates"
SENTINEL_DELEGATION = "## Delegation"
SENTINEL_ANTIPATTERNS = "## Anti-patterns"

def build_teammate_prompt(
    role: str,
    pack_body: str,
    agents: dict[str, Any],
) -> str:
    """Assemble the system prompt for a top-level teammate.

    Returns: pack_body + "\\n\\n" + addendum
    where addendum is:
       SENTINEL_CONTEXT + ...context override prose...
       SENTINEL_PEERS   + ...sorted, self-filtered peer list...
       SENTINEL_DELEGATION + ...delegation framework prose...
       SENTINEL_ANTIPATTERNS + ...anti-patterns prose...

    Args:
        role: the teammate's role (filtered out of the peer list per OQ-6 / R-2).
        pack_body: the raw pack body (no leaf suffix, no frontmatter).
        agents: the agents dict (id → AgentDefinition).
    """

def _build_peer_list(self_role: str, agents: dict[str, Any]) -> str:
    """## Available teammates section.
    - Sorted by name (R-1 ordering flake guard).
    - Excludes self_role (R-2 self-inclusion guard).
    - Defensive on missing/non-string description (R-3 user pack guard).
    """
```

```python
# claude_crew/subagents/_loader.py — MODIFY

# New module-level constant; tests assert this exact string ends every
# subagent prompt.
_LEAF_SUFFIX = """
## Leaf context

You are a leaf subagent. You have no Task tool by design — subagents
are leaves and cannot spawn further subagents. Stop and report when
your task is complete.
"""

# parse_pack_text return shape EXTENDED — was 3-tuple, now 4-tuple.
# The 4th element is the raw body (no leaf suffix). Teammate spawn path
# needs it; subagent path uses agent.prompt which already has the suffix.
def parse_pack_text(text: str, path: Path) -> tuple[str, AgentDefinition, PackFrontmatter, str]:
    ...
    agent_kwargs["prompt"] = body.rstrip() + _LEAF_SUFFIX  # was: body
    ...
    return key, agent, fm, body  # was: return key, agent, fm

# load_default_pack and load_user_pack return shapes EXTENDED to plumb
# the raw body through. Concrete shape TBD by implementation; the
# minimum invariant: callers can look up a role's raw body by name.
```

```python
# claude_crew/sdk_teammate.py — MODIFY

# Replace line 306 (_system_prompt assignment) with:
if system_prompt is not None:
    self._system_prompt = system_prompt  # explicit override wins
else:
    pack_body = self._pack_bodies.get(role)  # populated alongside self._agents
    if pack_body is not None:
        from claude_crew.teammate_prompt import build_teammate_prompt
        self._system_prompt = build_teammate_prompt(role, pack_body, self._agents)
    else:
        # Fallback for roles not in any pack — preserves old behavior
        self._system_prompt = _default_system_prompt(role)
```

```python
# Pack file edits (3 files):
# - claude_crew/subagents/explorer.md
# - claude_crew/subagents/general_purpose.md
# - claude_crew/subagents/planner.md
#
# Remove ALL leaf-context language from the markdown body. Specifically
# the "MUST NOT spawn subagents — you have no Task tool by design" lines
# in general_purpose.md (line 28) and planner.md (similar). The leaf
# semantics now come from _LEAF_SUFFIX appended by the loader.
```

### Design Decisions

- **D-1: Pack body is the shared source of truth, divergence happens at suffix time.** — *Rationale:* alternatives (separate teammate-prompt and subagent-prompt fields in frontmatter, two pack files per role) duplicate the role-identity content. Single body + per-context suffix keeps role identity in one place. — *Carried into:* `parse_pack_text` returns the raw body alongside the leaf-suffixed `AgentDefinition.prompt`; both consumers source from the same body.

- **D-2: `_LEAF_SUFFIX` and `_TEAMMATE_ADDENDUM` live in their respective modules, not in pack files.** — *Rationale:* leaf and teammate semantics are claude-crew architecture concerns, not role-shaped concerns. Pack files describe roles; the architecture appends context-specific framing. — *Carried into:* `_LEAF_SUFFIX` in `claude_crew/subagents/_loader.py`; addendum components in `claude_crew/teammate_prompt.py`.

- **D-3: Peer list is sorted by name and excludes self.** — *Rationale:* R-1 (ordering flake), R-2 (self-inclusion bug). Test stability + correctness in one rule. — *Carried into:* `_build_peer_list` implementation; tests assert sorted order AND exclude-self.

- **D-4: Defensive handling for missing/malformed description.** — *Rationale:* R-3 (user packs may be malformed). Skip-or-fallback rather than raise. — *Carried into:* `_build_peer_list` falls back to name-only when description is missing or non-string; never raises during prompt assembly.

- **D-5: Addendum references delegation BEHAVIOR, not Task tool name.** — *Rationale:* R-4 (Task tool name coupling). Phrasing like "dispatch a subagent" rather than "use the Task tool" insulates against SDK rename. — *Carried into:* the prose in `_DELEGATION` constant; review for any literal `"Task"` references.

- **D-6: `parse_pack_text` returns a 4-tuple `(key, AgentDefinition, PackFrontmatter, raw_body)`.** — *Rationale:* OQ-7 + the subagent/teammate divergence requires both forms of the prompt. Cleanest: return both from the canonical parser; let downstream callers (loader, teammate spawn path) choose what they need. Alternative (re-read pack files at spawn time) adds I/O. — *Carried into:* `parse_pack_text` signature change; `load_default_pack`, `load_user_pack`, and any `strict_parse` paths plumb the body through.

- **D-7: `_default_system_prompt(role)` stays as a fallback.** — *Rationale:* if a teammate is spawned with a role NOT in any loaded pack (custom roles, future user-defined teammate-only roles), the loader returns `pack_body=None` and the legacy 8-word prompt is used. Preserves the no-regress contract for unusual spawn paths. — *Carried into:* the conditional in sdk_teammate.py:306; test confirms a teammate spawned with an unknown role still gets a non-empty prompt.

### Edge Cases

1. **Teammate spawned with role NOT in any loaded pack** — `pack_body=None`, fallback to `_default_system_prompt(role)`. No regression vs. today.
2. **Teammate spawned with `system_prompt=` explicitly provided by caller** — explicit wins; addendum is NOT applied. The caller knows what they want.
3. **Empty agents dict** — `_build_peer_list` returns just the section heading + an empty body. Addendum still has the section sentinel (so SC-2 tests pass).
4. **Single-agent dict where the only agent IS the spawning role** — peer list excludes self → empty list. Same render as case 3.
5. **AgentDefinition with `description=None` or `description=""`** — fall back to name-only line in the peer list.
6. **AgentDefinition with non-string `description`** — defensive: treat as missing, fall back to name-only.
7. **Pack body contains a leaf-language phrase that's also in NEGATIVE_PATTERNS but NOT yet stripped from a pack file** — the SC-6 lint test fails until the pack file is updated. Failure mode is loud and points at the right file.
8. **User-level pack file with leaf-language in the body** — accepted as-is (per OQ-3 / SC-7); user is responsible for the contradiction. Contradiction-lint runs on bundled packs only.
9. **Role name ordering edge case** — peer list sorted by `agents.keys()` Python str sort; stable across Python versions for ASCII names.
10. **`parse_pack_text` 4-tuple shape break** — every existing caller of the 3-tuple form must be updated. Phase 4 task surface includes this.

11. **Explorer-name coupling in `_DELEGATION` prose**. SC-5 bakes "explorer" into the delegation guidance. Correct for bundled packs (explorer is always loaded), but fails for a teammate spawned with a custom `agents` dict that omits explorer — the prose refers to a subagent that doesn't exist. **Resolution**: phrase the delegation guidance as "delegate file reads to a read-only subagent (e.g., explorer if available)" rather than naming explorer unconditionally. SC-5's "names explorer by name" evidence becomes "names explorer by name when present in agents," and the test handles the fallback case. This also future-proofs against pack-customization scenarios.

### Specification

Implementation order:
1. Create `claude_crew/teammate_prompt.py` with `NEGATIVE_PATTERNS`, sentinels, `build_teammate_prompt`, `_build_peer_list`, and the addendum constants.
2. Modify `claude_crew/subagents/_loader.py`: add `_LEAF_SUFFIX`; change `parse_pack_text` to 4-tuple return; append suffix to `agent_kwargs["prompt"]`.
3. Plumb the 4-tuple through `load_default_pack`, `load_user_pack`, any other callers (`_user_loader.py`).
4. Modify `claude_crew/sdk_teammate.py`: store `self._pack_bodies` alongside `self._agents`; replace the `_default_system_prompt` call site with the conditional.
5. Edit `claude_crew/subagents/{explorer,general_purpose,planner}.md` to remove leaf-language from bodies.
6. Tests in `tests/test_teammate_prompt.py` (new file): static contradiction-lint, section-sentinel asserts, peer-list ordering, self-filtering, defensive description handling, fallback-to-default for unknown role, explicit-override-wins.
7. Existing test in `tests/test_subagents.py`: assert `_LEAF_SUFFIX` content appears in the assembled subagent prompt for at least one bundled role (proves the path didn't drop the suffix).

### Assumptions

- **A-1**: `parse_pack_text` is the only place AgentDefinition.prompt is assigned for both bundled and user packs (verified by recon).
- **A-2**: AgentDefinition's `description` field is reliably one-line in bundled packs (verified by reading explorer.md/general_purpose.md/planner.md frontmatter).
- **A-3**: `self._agents` in SdkTeammate is fully populated by the time `_system_prompt` is assigned (i.e., the conditional has access to the full agents dict for peer-list generation). Verified by reading `__init__` order at sdk_teammate.py:306-314.
- **A-4**: No external code consumes `parse_pack_text`'s 3-tuple shape. Verified by grep across the codebase before changing the signature.

### Open Questions

- [ ] **OQ-8** (Phase 4-resolvable, not blocking): should `self._pack_bodies` be a public attribute or private? Bias is private (no current external consumer). Decide during implementation.

**Gate**:
- ✅ Design clear and justifiable
- ✅ Spec comprehensive — no ambiguity (one Phase 4-resolvable OQ left)
- ✅ Edge cases listed (10 items)
- ✅ Error handling specified (fallback path, defensive defaults, fail-loud lint)
- ✅ Cross-feature integration check complete (#15/#17 forward-compat noted in Phase 1 Constraints)
- ✅ Implementable

---

## Phase 3: Task Breakdown

*(1-2 tasks expected per scope decision.)*

---

## Phase 4: Implementation

**Gate**: Tasks complete, quality gates passing, review done.

---

## Phase 5: Completion

### Verification
- [ ] Feature works against Phase 1 success criteria
- [ ] No regressions — full test suite passes
- [ ] Spec updated to match implementation
- [ ] Docs updated

### Retrospective

*(Skipped per scope decision unless implementation surfaced unexpected complexity. Update header status to Shipped at merge.)*

**Gate**: Feature verified, doc updated.
