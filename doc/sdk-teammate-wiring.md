# SDK Teammate Wiring — Tools, Skills, MCP, Memory

How a claude-crew SDK teammate's tool surface, skill surface, MCP surface, and memory surface are actually wired at spawn time. Written down so the next session doesn't have to re-derive it from `subprocess_cli.py` source.

Audience: maintainers reasoning about pack contracts, allowlist enforcement, or "why is the teammate calling a tool I didn't grant?"

Source of truth for the SDK side: `claude_agent_sdk/_internal/transport/subprocess_cli.py` (read directly when the SDK is upgraded). Source of truth for the claude-crew side: `claude_crew/sdk_teammate.py` — specifically the `_build_options`-style block that translates pack + spawn args into `ClaudeAgentOptions`.

---

## 1. The big picture

A teammate is a **top-level Claude CLI subprocess**, not an in-session subagent. claude-crew constructs `ClaudeAgentOptions` from the pack + spawn args, the SDK serializes those options into CLI flags, the CLI loads its own user/project settings on top of those flags, and the resulting Claude process is what the teammate runs.

That "loads its own settings on top of those flags" step is the source of every surprising wiring leak. The SDK options control what claude-crew **adds**; the CLI's own settings load controls what is **inherited** from user/project/plugin scopes. The two paths compose, and `--strict-*` flags are how you tell the CLI to suppress the inherited side.

This is the central asymmetry vs. the in-session subagent model. **In-session subagents** (spawned via the `Task` tool inside an existing Claude session) get exactly the pack's declared `tools:` and nothing else — no plugin MCP, no user-level hooks, no inherited settings. They are leaf nodes. **SDK teammates** look like subagents from the operator's perspective but are not — they boot a full Claude CLI that does its normal settings discovery unless explicitly told not to.

---

## 2. Tools (the `tools:` pack field)

**Pack contract** (`tools: [A, B, C]` in pack frontmatter):

- Becomes `opts.tools = ["A","B","C"]` → CLI flag `--tools A,B,C` → catalog the model sees in the wire prompt.
- Also becomes `opts.allowed_tools = ["A","B","C"]` → CLI flag `--allowedTools A,B,C` → pre-approval set (no permission prompt).
- Setting BOTH is required — `--allowedTools` alone is pre-approval-only and does NOT shrink the wire prompt catalog. The model still sees the full 35-tool default. (This was the bug fixed in commit `c683922`.)

**Pack semantics:**

| Pack field            | Effective surface                                       |
|-----------------------|---------------------------------------------------------|
| `tools:` omitted      | CLI default catalog (inherit-all, ~35 tools)            |
| `tools: []`           | Empty catalog — true no-tools surface (verified live)   |
| `tools: [A, B]`       | Catalog limited to A, B (+ spawn-time `extra_tools`)    |

**What `--tools` does NOT gate:** plugin-provided MCP tools (see §4). The catalog flag only restricts the base tool set; plugin-MCP enters through a separate channel.

---

## 3. Skills (the `skills:` pack field) — the cascade

This is the wiring most likely to surprise you. A pack declaring `skills: [foo, bar]` does NOT just add skill instructions — it triggers a cascade in the SDK that loads vast amounts of user/project state.

**The cascade** (`subprocess_cli.py::_apply_skills_defaults`):

```python
if options.skills is not None:
    # auto-add Skill(name) patterns to allowed_tools
    for name in skills:
        allowed_tools.append(f"Skill({name})")
    # ⚠️ AUTO-DEFAULT setting_sources when caller didn't specify ⚠️
    if setting_sources is None:
        setting_sources = ["user", "project"]
```

`setting_sources=["user","project"]` is the trigger for the CLI to load:

- `~/.claude/settings.json` and project `.claude/settings.json` (hooks, env, permissions)
- `~/.claude/agents/` and project `.claude/agents/` (sub-agent packs)
- `~/.claude/plugins/` — **enabled plugins' manifests, including their `mcpServers`, skills, hooks, slash commands, and `CLAUDE.md` injections**
- User-level `CLAUDE.md` auto-discovery (memory, instructions)

So: **a pack that declares any `skills:` is implicitly opting into the full user+project settings load**, which includes plugin-provided MCP servers (see §4) regardless of what the pack's own `tools:` allowlist says.

**Consequence:** a pack with `tools: [Read, Grep, Glob, Write, Agent]` and `skills: [plan-feature]` still gets context-mode's `mcp__plugin_context-mode_context-mode__ctx_*` tools wired in if context-mode is installed as a user-level plugin. Verified live 2026-05-26 with `rr-planner` for the `plugin-MCP-isolation` slice.

---

## 4. MCP servers — three sources, two of them surprising

Three independent sources can wire MCP servers into a teammate:

| Source                                                  | claude-crew honors allowlist? | Notes |
|---------------------------------------------------------|-------------------------------|-------|
| Pack `mcpServers:` field                                | Yes — resolved via `_resolve_mcp_servers` | String names → `~/.claude.json`; inline dicts pass through. |
| Spawn-time `mcp_servers` grant                          | Yes — same resolver           | Additive; merged on top of pack. |
| `~/.claude.json` `mcpServers`                           | Deny-by-default via `--mcp-config '{"mcpServers": {}}'` | The honor-pack-tools-allowlist contract (`c683922`) writes an empty `--mcp-config` so the CLI doesn't inherit user-config MCP. |
| **`~/.claude/plugins/cache/<plugin>/.claude-plugin/plugin.json` `mcpServers`** | **NO — leaks through `setting_sources=["user","project"]`** | The CLI loads plugin manifests independently of `--mcp-config` whenever user/project setting sources are enabled. |

**The leak (open as of 2026-05-26):**

Plugin-provided MCP servers bypass the deny-by-default empty `--mcp-config` because the CLI loads them from `~/.claude/plugins/` as part of normal settings discovery — not from the SDK-passed `--mcp-config` payload.

**The fix (one-line, simple):**

Pass `--strict-mcp-config` to the CLI subprocess via the SDK's generic `extra_args` pass-through:

```python
opts_kwargs.setdefault("extra_args", {})["strict-mcp-config"] = None
```

With `--strict-mcp-config`, the CLI ignores all MCP sources except the explicit `--mcp-config` payload (which claude-crew already writes as deny-by-default empty). Plugin-MCP suppression, no other behavior change. Being implemented as task #5 of the `plugin-MCP-isolation` slice.

**Secondary consideration not addressed by `--strict-mcp-config`:** the rest of the `setting_sources=["user","project"]` payload (hooks, sub-agents, slash commands, CLAUDE.md auto-discovery) still loads. Locking those down requires overriding `setting_sources=[]` and loading the pack's own skills via an alternate channel (`--agents`, explicit paths). Trade-off: stricter isolation vs. losing user-level conveniences. Filed but not in scope for the current allowlist fix.

---

## 5. Memory — three layers, all auto-loaded on spawn

claude-crew teammates have memory at three layers. None of these are wired explicitly by claude-crew; the CLI loads them via its normal CLAUDE.md / settings discovery (with the caveats above).

1. **Project CLAUDE.md** (loaded via `cwd`): set via the spawn-time `cwd` arg. The CLI auto-discovers `<cwd>/CLAUDE.md` and any imports. This is the primary mechanism for project-scoped instructions reaching the teammate.

2. **User-level memory** (loaded via `setting_sources=["user","project"]`): `~/.claude/CLAUDE.md`, `~/.claude/agent-memory/<role>/MEMORY.md`, and any agent-specific memory files. **Only loads when `setting_sources` includes `"user"`** — which auto-defaults to true when the pack declares `skills:` (see §3). Without `skills:`, user-level memory does NOT load.

3. **Project-scoped agent memory across runs** (claude-crew's own feature, `multi-scope-agent-memory` slice on master): persistent teammates accumulate a per-project memory file at `~/.claude/projects/<projectpath>/memory/MEMORY.md`. Auto-distilled at `kill_teammate` exit. Future teammates spawned for the same role + same project inherit it.

**Practical rule:** if a teammate needs user-level memory or instructions, the pack must declare at least one skill (forcing the cascade). For pure tool-leaf packs, project CLAUDE.md is the only memory path.

---

## 6. Quick-reference checklist for adding a new pack

When authoring a new pack under `claude_crew/subagents/` or the in-repo agent set:

- [ ] **Tools:** declare the minimum set; pack is leaf-of-trust. `tools: []` for no-tools surfaces.
- [ ] **Skills:** declare only if the pack genuinely needs the cascade. **Every skill you add opens the user/project settings load**, including plugin MCP, plugin hooks, plugin slash commands, and CLAUDE.md auto-discovery. If you don't need that, don't declare skills.
- [ ] **mcpServers:** declare what the pack legitimately needs as named entries (resolved from `~/.claude.json`) or inline dicts. The deny-by-default empty `--mcp-config` is your safety net; the named/inline entries are your opt-in.
- [ ] **Memory expectation:** if the pack needs user-level memory, document that the cascade is intentional. If not, prefer project CLAUDE.md as the only injection path.

---

## 7. Verification recipes

When something looks wrong, the fastest paths to ground-truth:

- **What tools is a live teammate actually using?** Check `~/.local/state/claude-crew/transcripts/<crewid>.jsonl` for `kind: "tool_start" / "tool_end"` records — they carry the exact `tool_name` and `outcome`.
- **What is the SDK shipping to the CLI?** Read `claude_agent_sdk/_internal/transport/subprocess_cli.py::_build_command` against a known pack — the function deterministically maps `ClaudeAgentOptions` → CLI argv.
- **What MCP servers does an installed plugin declare?** `cat ~/.claude/plugins/cache/<plugin>/<version>/.claude-plugin/plugin.json` and look at `mcpServers`.
- **Is `--strict-mcp-config` in effect?** Once the fix lands, the CLI argv should contain `--strict-mcp-config` (visible via debug logging or process inspection). The transcript's plugin-MCP tool_start records should be absent.

---

## 8. History

- **2026-05-19** — honor-pack-tools-allowlist (`c683922`) makes pack `tools:` the wire catalog AND pre-approval set, with `~/.claude.json` mcpServers deny-by-default. Closed the original 107 KB / 35-tool wire-prompt bug on Qwen-backed teammates.
- **2026-05-26** — discovered the plugin-MCP leak via context-mode tools being called by rr-planner despite the pack not granting them. Root cause: SDK auto-defaulting `setting_sources=["user","project"]` whenever `skills:` is declared. Fix scope: add `--strict-mcp-config` via `extra_args`. Tracked as task #5 of the `plugin-MCP-isolation` slice.
