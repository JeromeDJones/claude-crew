# Default Subagent Pack

claude-crew teammates ship with three default subagents available via the
SDK's Task tool: **explorer**, **planner**, **general-purpose**. Each
teammate spawned through `mcp__claude-crew__spawn_teammate` automatically
has these registered; no operator config required.

## Members at a glance

| Subagent | Model | Tools | Budget |
|---|---|---|---|
| `explorer` | Haiku | `Read`, `Grep`, `Glob` | `effort=low`, `maxTurns=10` |
| `planner` | Sonnet | `Read`, `Grep`, `Glob`, `Write` | `effort=high`, `maxTurns=20` |
| `general-purpose` | Sonnet | `Read`, `Grep`, `Glob`, `Edit`, `Write`, `WebFetch`, `WebSearch` | `effort=medium`, `maxTurns=20` |

Each member is defined by a markdown file in this directory with YAML
frontmatter for structural fields and a markdown body that is the
agent's system prompt. Edit any of those files and the change is picked
up on the next teammate spawn.

No member has `Bash` (no shell out) or `Task` (subagents are leaves).
These are load-bearing invariants — see `doc/features/FEATURE-default-subagent-pack.md`.

## Security: CLAUDE.md visibility

Subagents in this pack inherit the parent teammate's `setting_sources`,
which means **they see the same `~/.claude/CLAUDE.md` and project
`CLAUDE.md` content the parent does.** This is intentional: a planner
spawned by your teammate should know your standing instructions the
same way the teammate does.

It also means subagents with network tools — specifically
**`general-purpose`**, which has `WebFetch` and `WebSearch` — can
read CLAUDE.md content and then send requests to external URLs. If
your CLAUDE.md contains secrets (API keys, internal hostnames,
NDA-bound project names, customer identifiers), those values are
reachable by general-purpose's network surface.

**Recommendation:** audit your `~/.claude/CLAUDE.md` and project
`CLAUDE.md` before relying on the default pack with sensitive content.
The clean-room option is to spawn the *teammate* with
`setting_sources=[]` — isolation is a teammate-level concern, not a
subagent-level knob (the SDK does not expose per-subagent
`setting_sources`).

This is not a feature; it's the consequence of the SDK's design and
this pack's chosen posture. Documented honestly so operators can
make informed decisions.

## Customization

Operator-facing override (per-spawn custom subagents, user-defined
`~/.claude/agents/*.md` loading) is **Feature #3b** — not yet
shipped. The internal seam exists: `SdkTeammate(agents=...)` and
`merge_packs(default, user)` accept custom dicts at the
whole-AgentDefinition level (user wins on collision). #3b builds the
operator-facing API on top.
