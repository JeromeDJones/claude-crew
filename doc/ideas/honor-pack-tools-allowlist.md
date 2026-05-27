# Honor pack-declared tools as allowlist; deny MCP unless granted

**Status:** idea — ship ASAP for token savings
**Why now:** local-backed (Qwen/Gemma) teammates are economically gated by wire
prompt size. A single explorer spawn currently sends **~107 KB / ~27K tokens** —
the pack body is only 1.7 KB; **103 KB is tool defs (35 tools)** the SDK auto-
injects because we never honor the pack's declared tool list. Fixing this
alone drops the explorer wire prompt to ~3 KB and turn-1 latency from ~7 min
to seconds on Qwen 3.6.

## What this idea does (scope of the minimum fix)

Make claude-crew honor what Anthropic's own subagent semantics already promise:

1. **`tools:` present in a pack** → use exactly that list as the teammate's
   tool surface (allowlist). No SDK-injected "everything else."
2. **`tools:` absent (key omitted) or `tools: null`** → inherit all tools
   (mirrors Claude Code subagent semantics).
3. **`tools: []` (explicit empty)** for top-level teammates → wire-equivalent
   to inherit-all (**SDK limitation** — `claude_agent_sdk` only emits
   `--allowedTools` when the list is non-empty;
   see `subprocess_cli.py:238`'s `if effective_allowed_tools:` guard).
   Both omitted and explicit-empty collapse to "no `--allowedTools` flag" at
   the wire, so the CLI uses its default (inherit-all). For SUBAGENTS dispatched
   via Task, the distinction IS preserved (per AgentDefinition serialization);
   only top-level teammates collapse the two cases.
4. **MCP: pack does not declare `mcpServers:` and spawn does not grant any
   via `mcp_servers=`** → **no MCP servers attached.** Today claude-crew
   resolves `mcp_servers` from `~/.claude.json` by default, silently inheriting
   every server the operator has configured (Gmail/Calendar/Drive auth tools
   were observed in our wire dump). The fix: deny by default, opt-in by
   spawn-time grant.

## What this idea explicitly does NOT do

- Does **not** introduce the spawn-time `tools=` allowlist parameter (that's
  the follow-up idea).
- Does **not** change `extra_tools=` semantics (it stays additive on top of
  pack-declared tools, same as today — just on top of a now-correctly-honored
  list).
- Does **not** add `disallowedTools` symmetry or pack-as-floor reasoning
  (the follow-up idea).
- Does **not** touch skills resolution.

The split is intentional: this is the high-value, low-risk first step. The
follow-up (`pack-as-floor-spawn-additive.md`) builds on this foundation with
the richer contract.

## Current behavior (the bug, with evidence)

Verified 2026-05-25 via ccr request-body log + the explorer pack file
(`claude_crew/subagents/explorer.md`):

```
pack frontmatter         tools: [Read, Grep, Glob]   (3 tools)
wire request             tools: 35 tool defs        (Agent, Bash, Edit, Write,
                                                     TeamCreate, Monitor, ... +
                                                     6 mcp__* auth tools)
```

The pack's `tools:` is parsed (we see it in `factories.py`'s subagent
resolution path) but only applied to the *subagent definitions the teammate
can dispatch via Task* — never to the teammate's own session. The top-level
SDK options pass `allowed_tools=None` → SDK CLI grants everything by default.

MCP plumbing: `_resolve_mcp_servers` in `sdk_teammate.py` reads
`~/.claude.json` and attaches all configured servers, silently.

## Proposed implementation sketch (high level)

- When constructing `ClaudeAgentOptions` for the top-level teammate in
  `sdk_teammate.py`:
  - If the resolved pack declares `tools:` (a list, possibly empty), set
    `allowed_tools` to that list.
  - If `tools:` is omitted or `null`, leave `allowed_tools` unset (inherit-all).
- `_resolve_mcp_servers` should return an empty dict unless the spawn call
  explicitly grants MCP servers OR the pack declares `mcpServers:`. Drop the
  automatic `~/.claude.json` inheritance.
- Update bundled packs (`explorer.md`, `general.md`, `planner.md`) — already
  declare tight `tools:` lists; no change needed there.

## Validation

- **Wire-level**: spawn a default explorer, inspect ccr request body, assert
  tool count and total bytes ≤ a budget (e.g. tools ≤ 5, request body ≤ 10 KB).
- **Behavior**: spawn an explorer, ask it to try a denied tool (Bash); assert
  the tool is not available (model can't call it, OR the SDK rejects).
- **MCP**: spawn a teammate with no `mcp_servers=`, confirm zero MCP tools
  in the request body.
- **Backward compat**: existing `extra_tools=` callers still see their extras
  added on top of pack tools.
- **Local-backend smoke**: turn-1 latency on Qwen for a default explorer
  should drop dramatically (~7 min → seconds for a trivial reply).

## Cross-reference

- Follow-up: `pack-as-floor-spawn-additive.md` (the full contract).
- Authoritative behavior we're aligning with:
  https://platform.claude.com/docs/en/agent-sdk/subagents
