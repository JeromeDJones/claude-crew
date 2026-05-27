# Role discovery: tell users what they can spawn

**Status:** idea
**Why:** A downstream consumer of claude-crew (human, agent, or SDK client)
has no runtime way to ask "what roles can I spawn here?" The only discovery
paths today are reading the README, reading source files, or trial-and-error
on role names — and the `spawn_teammate` docstring actively misleads with an
`(e.g., "planner", "builder")` example where `builder` is *not* bundled (it
ships in some operators' `~/.claude/agents/` but not with claude-crew). A
copy-paste of that hint fails with "role not found" on a clean install.

## What this idea does

Three small surface fixes — none change runtime behavior, only discovery:

1. **New MCP tool: `list_available_roles`.** Returns the resolved post-merge
   pack — the same view `factories.default_factory()` builds. Per entry:
   `{name, source ("bundled" | "plugin:<short>" | "user" | "project"),
    description, model, tools, skills, mcp_servers}`. The `source` label is
   load-bearing: it tells a caller whether the role will exist on a clean
   install of claude-crew (bundled) or depends on operator-side files
   (user/project/plugin).

2. **Fix the `spawn_teammate` docstring.** Replace the misleading example
   with the actually-bundled set, explain the merge cascade, and point at
   `list_available_roles` for the live view:
   - Bundled roles: `explorer`, `planner`, `general`.
   - Operators can add more via `~/.claude/agents/` (user) and
     `<project>/.claude/agents/` (project); plugins contribute namespaced
     roles (`<plugin>:<role>`).
   - For the live list use `list_available_roles`.

3. **README cross-link.** The README role table already lists the bundled
   set with their tools (good). Add a one-line note pointing at
   `list_available_roles` for the post-merge view, and update the docstring
   reference if it stales.

## Why each piece pulls its weight

- **MCP tool**: any spawned teammate (or external SDK client) can introspect
  what it can dispatch — useful for orchestrators that pick roles
  dynamically, and self-documenting in conversational use.
- **Docstring fix**: it's *misinformation* today, not just incomplete. A
  small fix; high asymmetric value (one paragraph saves "role not found"
  confusion).
- **README cross-link**: minor housekeeping; keeps the doc honest as the
  source of truth shifts to the live tool.

## What this idea explicitly does NOT do

- Does not change role resolution semantics (the merge cascade stays exactly
  as it is).
- Does not add filtering / search / pagination on `list_available_roles` —
  the pack is small (typically <20 roles); a flat list is fine.
- Does not surface tool/MCP discovery (separate domain;
  `list_available_tools` already exists for tools).

## Validation

- Unit test: `list_available_roles` returns the bundled three on a clean
  install with empty `~/.claude/agents/`.
- Unit test: `list_available_roles` reflects merged view with sample
  user/project agents, with correct `source` labels.
- Docstring lint / smoke: `spawn_teammate(role="builder")` on a clean
  install errors with a helpful message that points at `list_available_roles`
  (separate from this idea, but adjacent — could fold in).

## Cross-reference

- Related: `honor-pack-tools-allowlist.md` (the allowlist fix that motivated
  this discoverability work — once role tools are tightly enforced,
  knowing what's available matters more, not less).
