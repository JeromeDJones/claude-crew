# Pack-as-floor, spawn-additive: the full claude-crew teammate contract

**Status:** idea — design follow-up to `honor-pack-tools-allowlist.md`
**Depends on:** that idea must land first (it establishes pack `tools:` and
`mcpServers:` as authoritative). This idea generalizes the same model to
spawn-time overrides, disallowedTools, and skills.

## Principle

The pack is the **floor** of the teammate's capability surface — it sets the
minimum guaranteed restrictions. The operator's spawn call can only **grow**
the surface (more tools, more MCP, more skills) or **further restrict** it
(more denials). The spawn call can never grant something the pack permits at a
lower level than the pack already does — packs are reviewable, in-repo, and
authoritative for the role.

Mirrors Claude Code's own subagent semantics so habits transfer: anyone who
knows `.claude/agents/*.md` reads claude-crew packs identically.

## The full contract

### Tools (`tools:`)

| Pack frontmatter | Spawn `tools=` (TBD param) | Spawn `extra_tools=` | Effective allow |
|---|---|---|---|
| omitted / `null` | not yet defined | `[X]` | all + X = all (X redundant, log it) |
| `[A, B]` | not yet defined | `[C]` | `[A, B, C]` (union) |
| `[]` | not yet defined | `[C]` | `[C]` (pack locked empty, spawn adds C) |

(`tools=` allowlist parameter on `spawn_teammate` is a possible future
addition; for now, all additions go through `extra_tools=`.)

**Rule:** union. Pack contributes a baseline; spawn additions grow it. Spawn
cannot drop a pack-declared tool.

### MCP servers (`mcpServers:` / `mcp_servers=`)

| Pack frontmatter | Spawn `mcp_servers=` | Effective MCP set |
|---|---|---|
| omitted | omitted | none (deny-by-default) |
| omitted | `[X]` | `[X]` |
| `[Y]` | omitted | `[Y]` |
| `[Y]` | `[X]` | `[X, Y]` (union) |

**Rule:** same union pattern as tools. Pack-declared servers are always
attached; spawn-time grants add to them. Spawn cannot drop pack-declared MCP.

### disallowedTools (`disallowedTools:`)

The denylist that subtracts from whatever the teammate would otherwise have.

| Pack frontmatter | Spawn `disallowed_tools=` | Effective denyset |
|---|---|---|
| omitted | omitted | empty (no extra denials) |
| omitted | `[B]` | `[B]` |
| `[X]` | omitted | `[X]` |
| `[X]` | `[B]` | `[X, B]` (union) |

**Rule:** union, like tools/MCP. Critically, **spawn can deny a tool the
pack permits** (e.g. pack has `tools: [A, B]`, spawn passes
`disallowed_tools=[B]` → effective `[A]`). This is **valid and intended**:
the operator is restricting at runtime within the pack's permission set.
This is the asymmetric power the spawn call has — it can never escalate
beyond pack, but it can de-escalate at runtime via the denylist.

**Implementation discipline:** the resolver must apply `disallowedTools`
*after* computing the allow set, and must enforce it strictly — a tool in
both `tools` and `disallowedTools` is denied. Test the precedence explicitly.

### Skills

Same model. Pack `skills:` is the floor; spawn `extra_skills=` (already
exists) is additive. No skill auto-inheritance from the operator's
environment beyond what the pack declares + what spawn grants.

## Edge cases worth nailing in tests

1. **Wide-open pack + spawn additions:** pack omits `tools:`, spawn passes
   `extra_tools=[X]`. Effective surface = all (already had X). Log a
   debug-level note that the addition was redundant; do not fail.
2. **Pack `tools:[]` + spawn additions:** explicit empty pack lets the spawn
   call build the surface from zero. `extra_tools=[A]` → effective `[A]`.
3. **Pack tool denied by spawn:** pack `tools:[A, B]` + spawn
   `disallowed_tools=[B]` → effective allow `[A]`. The spawn DENY wins over
   the pack ALLOW — the operator is restricting at runtime within the pack's
   capability set, which is legitimate.
4. **Pack MCP + spawn MCP overlap:** pack `mcpServers:[X]` + spawn
   `mcp_servers=[X]` → effective `[X]` (dedup).
5. **`tools: null` vs missing key vs `tools: []`:**
   - `null` ≡ missing → inherit-all
   - `[]` → empty surface
   Test all three explicitly so a YAML quirk can't silently change semantics.

## Validation strategy

- **Unit-level**: a `resolve_surface(pack, spawn_args) → ResolvedSurface`
  pure function with happy + sad cases for every cell of the tables above.
- **Integration-level**: spawn teammates with each combination, inspect the
  upstream-gateway request body or `ClaudeAgentOptions`, assert the effective tool/MCP
  lists.
- **Regression check**: bundled `explorer`/`planner`/`general` teammates
  spawned without any spawn-time additions still get the same tool sets they
  did after `honor-pack-tools-allowlist.md` landed.

## Cross-reference

- Prerequisite: `honor-pack-tools-allowlist.md`
- Authoritative behavior we're aligning with:
  https://platform.claude.com/docs/en/agent-sdk/subagents
