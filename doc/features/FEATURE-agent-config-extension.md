# Feature: Agent Config Extension (Pipeline #10)

**Status**: Shipped
**Created**: 2026-04-28
**Merged**: 2026-04-29

---

## Phase 1: Research & Requirements

### Problem Statement

Pack files (`.md` role definitions in `claude_crew/subagents/`, `~/.claude/agents/`, `.claude/agents/`) can only declare `description`, `model`, `tools`, `effort`, `maxTurns`, `initialPrompt`, `background` in their YAML frontmatter. The `AgentDefinition` SDK type also supports `skills`, `permissionMode`, `disallowedTools`, `mcpServers`, and `memory` — but `PackFrontmatter` doesn't parse them, so they're silently ignored or flagged as unknown fields by `_user_loader.py`'s strict parser.

Two gaps result:

1. **Subagent-level**: A pack file can't configure `skills`, `permissionMode`, or `disallowedTools` for the role when it's invoked as a Task-tool subagent inside a teammate session. The fields are silently absent from `AgentDefinition`.

2. **Teammate-level**: When a role is spawned as a top-level `SdkTeammate`, `ClaudeAgentOptions` doesn't receive `skills`, `permissionMode`, or `disallowedTools` from the role definition — `SdkTeammate` never extracts them. A `builder` role that needs `permissionMode: bypassPermissions` has no way to declare that today.

Additionally, `spawn_teammate` has no `cwd` parameter, so all teammates always start in the MCP server's CWD. Multi-repo work (spawn a builder pointed at `~/dev/my-money-matters` while the lead runs in `~/dev/claude-crew`) requires the teammate to `cd` itself, which is unreliable.

Spike results (`doc/research/mcp-sdk-behavior.md`) confirmed: global MCP loads reliably via `setting_sources=["user"]` (already the default), so `mcpServers` in frontmatter is deferred — only needed for project-specific servers not in global config, and adding it requires merge logic for REPLACE behavior. `memory` has no clear ClaudeAgentOptions mapping. Both deferred to a follow-up.

### Success Criteria

- [ ] **SC-1 — `skills` in frontmatter → subagent AgentDefinition.** A pack file can declare `skills: [skill-name, ...]`. `PackFrontmatter` parses the list. `parse_pack_text` passes it to `AgentDefinition.skills`. When the SDK spawns this role as a subagent (Task tool), the declared skills are available to it.

- [ ] **SC-2 — `permissionMode` in frontmatter → subagent AgentDefinition.** A pack file can declare `permissionMode: bypassPermissions`. `PackFrontmatter` parses it and validates it against the known literal set (`default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, `auto`) at parse time — invalid values raise `PackLoadError`, not a silent pass-through. Valid values are passed to `AgentDefinition.permissionMode`.

- [ ] **SC-3 — `disallowedTools` in frontmatter → subagent AgentDefinition.** A pack file can declare `disallowedTools: [Bash, WebFetch]`. `PackFrontmatter` parses the list. `parse_pack_text` passes it to `AgentDefinition.disallowedTools`.

- [ ] **SC-4 — Role fields propagate to top-level teammate session.** When `spawn_teammate(role=R)` spawns a `SdkTeammate`, the teammate looks up `self._agents.get(R)` at `ClaudeAgentOptions` construction time (inside `_run()`) and maps fields to `ClaudeAgentOptions` with their snake_case names: `AgentDefinition.permissionMode` → `ClaudeAgentOptions.permission_mode`, `AgentDefinition.disallowedTools` → `ClaudeAgentOptions.disallowed_tools`, `AgentDefinition.skills` → `ClaudeAgentOptions.skills`. Each is applied only if non-None. Two absent-role cases must both be handled: (a) role key not in `self._agents` — `get()` returns `None`, all three default to `None`; (b) role key exists but individual fields are `None` on the `AgentDefinition` — per-field `None` checks apply. Spawn does not fail in either case.

- [ ] **SC-5 — All new fields are optional.** Pack files that do not declare `skills`, `permissionMode`, or `disallowedTools` continue to parse and run without change. No new required frontmatter fields.

- [ ] **SC-6 — No false warnings for newly-valid fields.** `_user_loader.py`'s strict parser currently warns on unrecognized frontmatter keys. After this feature, `skills`, `permissionMode`, and `disallowedTools` are recognized and produce no warning. Truly unknown keys still warn.

- [ ] **SC-7 — `spawn_teammate` accepts `cwd` and `permission_mode`.** The MCP tool gains two optional parameters: `cwd: str | None` and `permission_mode: str | None`. When `cwd` is provided, `ClaudeAgentOptions.cwd` is set. When `permission_mode` is provided, it overrides the role's pack-declared `permissionMode` — spawn-time wins, enabling least-privilege enforcement per crew context. `setting_sources: ["user", "project"]` (already the default) means a `cwd`-targeted project's `.claude/CLAUDE.md` loads automatically.

- [ ] **SC-8 — Both new spawn params thread through the full callsite chain.** `server.spawn_teammate` → `broker.spawn_teammate` → `sdk_factory` → `SdkTeammate.__init__` → `ClaudeAgentOptions`. At every intermediate call site both params default to `None`. `StubTeammate.__init__` also gains both (accepts and ignores) so the `TeammateFactory` protocol stays satisfied. Priority for `permission_mode`: spawn-time arg > role's pack `permissionMode` > SDK default. Integration tests assert both non-None values reach `ClaudeAgentOptions` at construction.

- [ ] **SC-9a — Wiring unit test: `permissionMode` reaches `ClaudeAgentOptions`.** A unit or integration test using the stub/fake SDK client asserts that when the role's `AgentDefinition.permissionMode` is `"bypassPermissions"`, the constructed `ClaudeAgentOptions` has `permission_mode="bypassPermissions"`. Same for `skills` and `disallowedTools`. Catches silent wiring failures without needing a live API call.

- [ ] **SC-9b — Live E2E behavioral proof.** A live integration test (gated by `CLAUDE_CREW_LIVE_TESTS=1`) spawns two teammates from the same role pack file: one with `permissionMode: plan`, one without. Both are instructed to write a file to `cwd=<tempdir>`. Assert: the `plan`-mode teammate does NOT create the file (tool execution is disabled in plan mode — this proves `permissionMode` is wired and respected); the control teammate creates it (proves `cwd` wired). Using `"plan"` for the negative assertion avoids the `"bypassPermissions"` false-positive: a simple file write would succeed under both `"default"` and `"bypassPermissions"` in a non-interactive SDK context. Live-probe checklist applies.

- [ ] **SC-10 — Unknown role does not fail.** When `spawn_teammate(role="nonexistent")` is called and "nonexistent" is not a key in the agents pack, `SdkTeammate` spawns successfully with `skills=None`, `permissionMode=None`, `disallowedTools=None` on `ClaudeAgentOptions` and a generic system prompt. The role-as-name-tag behavior is preserved.

### Questions

- [x] **Q1 — Should `permissionMode` be validated at parse time or at spawn time?** *Answer: validate at parse time.* `PermissionMode` is a `Literal` type, not a Python enum — the SDK accepts any string and silently ignores an invalid value rather than raising. Invalid pack files must fail loudly at load time, not silently at spawn time. `model` passes through because the SDK validates it at API call time with a clear error; `permissionMode` does not have the same guarantee.

- [x] **Q2 — Does extracting role fields in `SdkTeammate` (SC-4) break if the role has no entry in the agents pack?** *Answer: no.* `self._agents.get(self._role)` returns `None` for unknown roles; all three fields then default to `None`, which is identical to today's behavior. No guard needed beyond the `None` check.

- [x] **Q3 — Should spawn-time params be able to override role-level `permissionMode`/`skills`/`disallowedTools`?** *Answer: out of scope for this feature.* Role-level config only. Spawn-time override parameters are the "secondary" item from the original backlog entry and remain deferred.

- [x] **Q4 — `mcpServers` and `memory` — include or defer?** *Answer: defer.* `mcpServers` requires merge logic (spike confirmed REPLACE behavior). `memory` has no clear `ClaudeAgentOptions` mapping. Both deferred to a follow-up feature.

- [x] **Q5 — Should spawn-time params be able to override role-level `permissionMode`?** *Answer: YES — reversed after user challenge.* The lead needs to enforce permission levels per-teammate based on crew context, not just role definition. Example: spawn a reviewer with `permissionMode: plan` (read-only, no tool execution) even if the reviewer pack doesn't declare that — least privilege at the point of use. Pack-level `permissionMode` becomes the default; spawn-time overrides it. Follows the same pattern as `model` and `effort`. Add `permission_mode: str | None = None` to `spawn_teammate`. Spawn-time value wins if provided; falls back to role's pack declaration; falls back to SDK default.

### Constraints & Dependencies

- **Requires**: `#3a` (loader), `#3b` (user loader), both in tree
- **Files touched**: `_loader.py`, `_user_loader.py`, `sdk_teammate.py`, `server.py`, `broker.py`, `factories.py` (6 files)
- **Breaking changes**: None. All new fields optional. New `cwd` parameter defaults to `None`.
- **Performance**: Negligible. One `dict.get` per spawn to look up role AgentDefinition.
- **Test impact**: Existing tests unaffected (new params default to None/no-op). New tests needed for each SC.

### Assumptions

- **Pack files are trusted artifacts.** `bypassPermissions` in a pack file is the user's deliberate choice — same trust level as the system prompt in `~/.claude/agents/`. No validation gate in v1. A teammate can have a wider permission surface than its lead; this is acceptable because the user authored the pack.
- **`cwd` does not change the agent pack.** The merged agents pack is computed at server startup relative to the server's CWD and frozen for the process lifetime. A teammate spawned with `cwd=~/dev/projectB` gets projectA's agent pack, not projectB's. This is the existing `#3b` design decision — not a new constraint.

### Out of Scope

- `mcpServers` in frontmatter (REPLACE behavior requires merge logic — deferred)
- `memory` in frontmatter (no clear ClaudeAgentOptions mapping — deferred)
- Spawn-time `skills` and `disallowed_tools` overrides on `spawn_teammate` — deferred (only `permission_mode` added; Q5 reversal scoped to permission mode only)
- Re-discovering agents from `cwd`'s `.claude/agents/` at spawn time (agents pack fixed at server startup)
- `mcp_servers` on `spawn_teammate` — deferred

**Gate**: Questions answered, success criteria measurable, constraints documented, co-architect and Sentinel review complete, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

Two-layer change touching 6 files:

**Layer 1 — Loader** (`_loader.py`, `_user_loader.py`): `PackFrontmatter` gains three new optional fields. `_validate_frontmatter()` coerces and validates them. `parse_pack_text()` passes non-None values to `AgentDefinition`. `_user_loader.py` requires no code changes — `_ACCEPTED_FRONTMATTER_KEYS` is derived from `PackFrontmatter.__dataclass_fields__` and auto-expands.

**Layer 2 — Spawn chain** (`sdk_teammate.py`, `server.py`, `broker.py`, `factories.py`, `teammate.py`): `cwd` and `permission_mode` thread from `spawn_teammate` MCP tool down through broker → factory → `SdkTeammate.__init__`. In `_run()`, `SdkTeammate` also extracts `skills`, `permissionMode`, `disallowedTools` from the role's `AgentDefinition` (looked up in `self._agents`) and applies them to `ClaudeAgentOptions`. Priority for permission mode: spawn-time arg > role-pack value > None (SDK default).

---

### Layer 1: Loader Changes (`_loader.py`)

#### `PackFrontmatter` — three new optional fields

```python
@dataclass(frozen=True)
class PackFrontmatter:
    description: str
    model: str
    tools: list[str]
    effort: str | None = None
    maxTurns: int | None = None
    initialPrompt: str | None = None
    background: bool | None = None
    # new:
    skills: tuple[str, ...] | None = None
    permissionMode: str | None = None
    disallowedTools: tuple[str, ...] | None = None
```

Stored as tuples (frozen dataclass cannot hold mutable lists). `_OPTIONAL` grows to include all three:

```python
_OPTIONAL = ("effort", "maxTurns", "initialPrompt", "background",
             "skills", "permissionMode", "disallowedTools")
```

#### New module-level constant

```python
_VALID_PERMISSION_MODES = frozenset(
    {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"}
)
```

#### `_validate_frontmatter()` — extended

The current function returns a single `PackFrontmatter(...)` constructor call. After the required-field loop, add a `permissionMode` guard before the return, and expand the constructor:

```python
def _validate_frontmatter(d: dict[str, Any], path: Path) -> PackFrontmatter:
    for field in _REQUIRED:
        if field not in d:
            raise PackLoadError(
                f"pack file {path} missing required frontmatter field '{field}'"
            )

    pm = d.get("permissionMode")
    if pm is not None and pm not in _VALID_PERMISSION_MODES:
        raise PackLoadError(
            f"pack file {path}: unknown permissionMode {pm!r}; "
            f"valid values: {sorted(_VALID_PERMISSION_MODES)}"
        )

    return PackFrontmatter(
        description=str(d["description"]),
        model=str(d["model"]),
        tools=list(d["tools"]),
        effort=str(d["effort"]) if d.get("effort") is not None else None,
        maxTurns=int(d["maxTurns"]) if d.get("maxTurns") is not None else None,
        initialPrompt=(
            str(d["initialPrompt"]) if d.get("initialPrompt") is not None else None
        ),
        background=bool(d["background"]) if d.get("background") is not None else None,
        skills=(
            tuple(str(s) for s in d["skills"]) if d.get("skills") is not None else None
        ),
        permissionMode=pm,
        disallowedTools=(
            tuple(str(t) for t in d["disallowedTools"])
            if d.get("disallowedTools") is not None else None
        ),
    )
```

#### `parse_pack_text()` — extended AgentDefinition construction

Build kwargs conditionally so `AgentDefinition` never receives None for fields it doesn't default:

```python
agent_kwargs: dict[str, Any] = {
    "description": fm.description,
    "prompt": body,
    "tools": list(fm.tools),
    "model": fm.model,
    "effort": fm.effort,
    "maxTurns": fm.maxTurns,
    "initialPrompt": fm.initialPrompt,
    "background": fm.background,
}
if fm.skills is not None:
    agent_kwargs["skills"] = list(fm.skills)
if fm.permissionMode is not None:
    agent_kwargs["permissionMode"] = fm.permissionMode
if fm.disallowedTools is not None:
    agent_kwargs["disallowedTools"] = list(fm.disallowedTools)

agent = AgentDefinition(**agent_kwargs)
```

#### `_user_loader.py` — no code changes required

`_ACCEPTED_FRONTMATTER_KEYS = frozenset(PackFrontmatter.__dataclass_fields__)` is computed at import time from the dataclass fields. Adding the three new fields to `PackFrontmatter` automatically expands the accepted set. No manual update needed.

---

### Layer 2: Spawn Chain Changes

#### `sdk_teammate.py` — `SdkTeammate.__init__`

Two new keyword-only parameters added after the existing list:

```python
def __init__(
    self,
    id: str,
    name: str,
    role: str,
    *,
    model: str = "claude-sonnet-4-6",
    effort: str | None = None,
    system_prompt: str | None = None,
    setting_sources: list[str] | None = None,
    agents: "dict[str, Any] | None" = None,
    cwd: str | None = None,              # new
    permission_mode: str | None = None,  # new — spawn-time override
) -> None:
    ...
    self._cwd = cwd
    self._permission_mode = permission_mode
```

`skills` and `disallowed_tools` are **not** added as `__init__` params — they come exclusively from the role's `AgentDefinition` in `_run()`. Only spawn-time params thread through the chain; role-level fields are extracted at runtime from the agents pack.

#### `sdk_teammate.py` — `SdkTeammate._run()`

After the hooks block and before `options = ClaudeAgentOptions(**opts_kwargs)`, insert:

```python
# Extract role-level fields from the agents pack.
role_def = self._agents.get(self._role)

# permissionMode: spawn-time arg wins; falls back to role-pack; None → SDK default.
effective_pm = self._permission_mode
if effective_pm is None and role_def is not None:
    effective_pm = getattr(role_def, "permissionMode", None)
if effective_pm is not None:
    opts_kwargs["permission_mode"] = effective_pm

# skills and disallowedTools: role-pack only (spawn-time override deferred).
if role_def is not None:
    role_skills = getattr(role_def, "skills", None)
    role_disallowed = getattr(role_def, "disallowedTools", None)
    if role_skills is not None:
        opts_kwargs["skills"] = role_skills
    if role_disallowed is not None:
        opts_kwargs["disallowed_tools"] = role_disallowed

# cwd: spawn-time only.
if self._cwd is not None:
    opts_kwargs["cwd"] = self._cwd
```

`getattr(..., None)` guards against `AgentDefinition` instances that predate this feature and lack the attribute entirely (defensive; in practice all `AgentDefinition` objects are constructed by `parse_pack_text` and will have None defaults).

`self._agents` is always a dict in `_run()` — initialized in `__init__` to `load_default_pack()` when `agents=None` is passed, so `.get()` is always safe.

#### `server.py` — `spawn_teammate` MCP tool

```python
async def spawn_teammate(
    role: str,
    name: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    cwd: str | None = None,             # new
    permission_mode: str | None = None,  # new
) -> dict[str, Any]:
    """Spawn a new teammate with the given role.

    Args:
        role: The teammate's role (e.g., "planner", "builder").
        name: Optional human-friendly name; defaults to role.
        model: Optional model id. Defaults to Sonnet 4.6.
        effort: Optional reasoning effort ("low", "medium", "high", "max").
        cwd: Optional working directory for the teammate subprocess. When set,
            the teammate's project CLAUDE.md is loaded from this path.
        permission_mode: Optional permission mode override. One of "default",
            "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto".
            Overrides the role's pack-declared permissionMode when provided.
    """
    tid = await broker.spawn_teammate(
        role=role, name=name, factory=factory,
        model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
    )
```

#### `broker.py` — `Broker.spawn_teammate`

```python
async def spawn_teammate(
    self,
    role: str,
    name: str | None,
    factory: TeammateFactory,
    model: str | None = None,
    effort: str | None = None,
    cwd: str | None = None,             # new
    permission_mode: str | None = None,  # new
) -> str:
    ...
    teammate = factory(
        teammate_id, resolved_name, role,
        model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
    )
```

#### `factories.py` — `stub_factory`

```python
def stub_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    cwd: str | None = None, permission_mode: str | None = None,  # new — accepted, ignored
) -> Teammate:
    return StubTeammate(id=id, name=name, role=role)
```

#### `factories.py` — `sdk_factory`

```python
def sdk_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    agents: "dict | None" = None,
    cwd: str | None = None, permission_mode: str | None = None,  # new
) -> Teammate:
    ...
    if cwd is not None:
        kwargs["cwd"] = cwd
    if permission_mode is not None:
        kwargs["permission_mode"] = permission_mode
    return SdkTeammate(id=id, name=name, role=role, **kwargs)
```

#### `factories.py` — `default_factory` closure

```python
def factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    cwd: str | None = None, permission_mode: str | None = None,  # new
) -> Teammate:
    return sdk_factory(
        id, name, role, model=model, effort=effort, agents=merged_pack,
        cwd=cwd, permission_mode=permission_mode,
    )
```

#### `teammate.py` — `StubTeammate.__init__`

No change required. `TeammateFactory = Callable[..., Teammate]` uses `...` (open callable), so the protocol boundary is `stub_factory`, not `StubTeammate.__init__`. `stub_factory` absorbs `cwd` and `permission_mode` without forwarding them — that's sufficient. `StubTeammate.__init__` gains no new parameters.

---

### Edge Cases

| Case | Behavior |
|------|----------|
| Role not in agents pack | `self._agents.get(self._role)` → `None`; all three fields default to None; spawn succeeds (SC-10) |
| Role in pack, field is None | Per-field None checks; field not added to opts_kwargs; SDK applies its default |
| spawn-time `permission_mode` + role has `permissionMode` | Spawn-time wins; role-pack value ignored |
| spawn-time `permission_mode=None` + role has `permissionMode: bypassPermissions` | Role-pack value used |
| `skills: []` in pack (empty list) | `tuple()` stored; `is not None` → True; `opts_kwargs["skills"] = []` — applied as empty list, not SDK default |
| `agents={}` passed to `SdkTeammate` | Empty dict; `get(role)` → None; role_def is None; no fields extracted |
| `agents=None` passed (legacy/direct) | `load_default_pack()` substituted; always a dict; `.get()` safe |

### Validation Boundaries

- `permissionMode` validation is at **pack parse time** (`_validate_frontmatter()`), not at spawn time. Invalid values raise `PackLoadError` with the valid set listed. This matches Phase 1 Q1 resolution: the SDK accepts any string silently, so we must validate loudly ourselves.
- `skills` and `disallowedTools` values are coerced to strings (`str(s)`) but not validated — tool names are open-ended and can include MCP tool names like `mcp__atlassian__atlassianUserInfo`.
- `cwd` and spawn-time `permission_mode` strings pass through unvalidated at the MCP boundary. `cwd` validation is left to the SDK subprocess (invalid path → SDK error). `permission_mode` at spawn time is not re-validated against `_VALID_PERMISSION_MODES` — the SDK will silently ignore an invalid value (this is acceptable given pack-level validation is the main gate; the spawn-time param is an escape hatch for programmatic use).

### Test Contracts (SC-9a)

All new tests follow the `_patch_sdk` / `captured["options"]` pattern already established in `test_sdk_teammate.py`:

```python
# SC-9a wiring: permissionMode from role pack reaches ClaudeAgentOptions
def test_permission_mode_from_role_reaches_options(monkeypatch, fake):
    agents = {"builder": AgentDefinition(
        description="test", prompt="be a builder",
        model=_MODEL, tools=["Read"],
        permissionMode="bypassPermissions",
    )}
    captured = _patch_sdk(monkeypatch, fake)
    teammate = SdkTeammate(id="t-1", name="n", role="builder", agents=agents)
    # run teammate and assert...
    assert captured["options"].permission_mode == "bypassPermissions"

# SC-9a: spawn-time overrides role pack
def test_spawn_time_permission_mode_overrides_role_pack(monkeypatch, fake):
    agents = {"builder": AgentDefinition(..., permissionMode="default")}
    teammate = SdkTeammate(..., agents=agents, permission_mode="plan")
    assert captured["options"].permission_mode == "plan"

# SC-9a: skills from role pack
def test_skills_from_role_reach_options(monkeypatch, fake):
    agents = {"builder": AgentDefinition(..., skills=["sdd-workflow"])}
    ...
    assert captured["options"].skills == ["sdd-workflow"]

# SC-9a: disallowedTools from role pack
def test_disallowed_tools_from_role_reach_options(monkeypatch, fake):
    agents = {"builder": AgentDefinition(..., disallowedTools=["Bash", "WebFetch"])}
    ...
    assert captured["options"].disallowed_tools == ["Bash", "WebFetch"]

# SC-10: unknown role does not fail
def test_unknown_role_spawns_without_error(monkeypatch, fake):
    teammate = SdkTeammate(..., role="nonexistent", agents={})
    # No exception; options has no permission_mode/skills/disallowed_tools set
```

### Assumptions

1. `AgentDefinition` accepts `skills`, `permissionMode`, `disallowedTools` as constructor kwargs without raising. (Confirmed by problem statement: these are existing SDK type fields.)
2. `ClaudeAgentOptions` accepts `permission_mode`, `skills`, `disallowed_tools`, `cwd` as kwargs. **Verified** against `.venv/lib/python3.12/site-packages/claude_agent_sdk/types.py` lines 1451–1562: all four fields exist with snake_case names. `skills` type is `list[str] | Literal["all"] | None`; passing a `list[str]` is valid.
3. `AgentDefinition` instances have `permissionMode`, `skills`, `disallowedTools` attributes accessible via `getattr(role_def, field, None)`. If the SDK returns `None` for unset fields rather than raising `AttributeError`, `getattr` with a default is equivalent to direct attribute access — either is safe.
4. An empty `skills: []` in frontmatter is treated as an explicitly empty list (no skills), not as "SDK default". Users who want the default behavior simply omit the field.
5. Pack files are trusted artifacts (noted in Phase 1 Assumptions). `bypassPermissions` in a pack is the user's deliberate intent.

---

## Phase 3: Task Breakdown

Four tasks. T1 and T2 are independent and can be built in parallel. T3 depends on T2. T4 is the E2E gate — depends on T1 + T2 + T3.

---

### T1 — Loader extension (`_loader.py`)

**Covers**: SC-1, SC-2, SC-3, SC-5, SC-6

**Files touched**: `claude_crew/subagents/_loader.py` (code), `tests/test_subagents.py` (tests)

No changes needed in `_user_loader.py` — `_ACCEPTED_FRONTMATTER_KEYS` derives from `PackFrontmatter.__dataclass_fields__` automatically.

**BDD Scenarios**:

```
Scenario: skills parsed from frontmatter
  Given a pack file with `skills: [sdd-workflow, deep-build]` in frontmatter
  When parse_pack_text is called
  Then AgentDefinition.skills == ["sdd-workflow", "deep-build"]

Scenario: permissionMode parsed and passes through
  Given a pack file with `permissionMode: bypassPermissions` in frontmatter
  When parse_pack_text is called
  Then AgentDefinition.permissionMode == "bypassPermissions"
  And no PackLoadError is raised

Scenario: invalid permissionMode raises at parse time
  Given a pack file with `permissionMode: superadmin` in frontmatter
  When parse_pack_text is called
  Then PackLoadError is raised
  And the error message names "superadmin" and lists the valid values

Scenario: disallowedTools parsed from frontmatter
  Given a pack file with `disallowedTools: [Bash, WebFetch]` in frontmatter
  When parse_pack_text is called
  Then AgentDefinition.disallowedTools == ["Bash", "WebFetch"]

Scenario: pack file without new fields parses unchanged (SC-5)
  Given a pack file with no skills, permissionMode, or disallowedTools
  When parse_pack_text is called
  Then AgentDefinition.skills is None or absent
  And no error is raised

Scenario: new fields emit no warning in strict_parse (SC-6)
  Given a pack file with skills, permissionMode, and disallowedTools declared
  When strict_parse (via _user_loader) is called
  Then no "unrecognized frontmatter" warning is emitted for any of the three fields
```

**Verification command** (fails without the feature):
```bash
uv run pytest tests/test_subagents.py -k "skills or permissionMode or disallowedTools or permission_mode or disallowed" -x
```

Write tests first (they fail), then implement.

---

### T2 — SdkTeammate role-field extraction (`sdk_teammate.py`)

**Covers**: SC-4, SC-7 (cwd + permission_mode params), SC-8 (init storage), SC-9a (wiring unit tests), SC-10

**Files touched**: `claude_crew/sdk_teammate.py` (code), `tests/test_sdk_teammate.py` (tests)

**Changes**:
- `SdkTeammate.__init__`: add `cwd: str | None = None` and `permission_mode: str | None = None`; store as `self._cwd` and `self._permission_mode`
- `SdkTeammate._run()`: extract role-level fields from `self._agents.get(self._role)` and add to `opts_kwargs` with priority logic; add `cwd`

**BDD Scenarios**:

```
Scenario: permissionMode from role pack reaches ClaudeAgentOptions
  Given SdkTeammate with agents={"builder": AgentDefinition(permissionMode="bypassPermissions")}
    and role="builder"
  When _run() constructs ClaudeAgentOptions
  Then captured_options.permission_mode == "bypassPermissions"

Scenario: spawn-time permission_mode overrides role pack
  Given SdkTeammate with agents={"builder": AgentDefinition(permissionMode="default")}
    and permission_mode="plan" passed to __init__
  When _run() constructs ClaudeAgentOptions
  Then captured_options.permission_mode == "plan"
  (spawn-time wins over role pack)

Scenario: skills from role pack reach ClaudeAgentOptions
  Given SdkTeammate with agents={"builder": AgentDefinition(skills=["sdd-workflow"])}
    and role="builder"
  When _run() constructs ClaudeAgentOptions
  Then captured_options.skills == ["sdd-workflow"]

Scenario: disallowedTools from role pack reach ClaudeAgentOptions
  Given SdkTeammate with agents={"builder": AgentDefinition(disallowedTools=["Bash"])}
    and role="builder"
  When _run() constructs ClaudeAgentOptions
  Then captured_options.disallowed_tools == ["Bash"]

Scenario: cwd from spawn reaches ClaudeAgentOptions
  Given SdkTeammate with cwd="/tmp/test-proj"
  When _run() constructs ClaudeAgentOptions
  Then captured_options.cwd == "/tmp/test-proj"

Scenario: unknown role does not fail (SC-10)
  Given SdkTeammate with role="nonexistent" and agents={}
  When _run() constructs ClaudeAgentOptions
  Then no exception is raised
  And captured_options has no permission_mode, skills, or disallowed_tools set
    (i.e., those fields are None / their SDK default)

Scenario: role in pack but fields are None — no opts set
  Given SdkTeammate with agents={"builder": AgentDefinition(...)}
    where permissionMode, skills, disallowedTools are all None
  When _run() constructs ClaudeAgentOptions
  Then permission_mode, skills, disallowed_tools are not set in opts_kwargs
```

All scenarios use the `_patch_sdk` / `captured["options"]` pattern established in `test_sdk_teammate.py`. Add to the existing `TestSdkTeammate` class or a new `TestRoleFieldExtraction` class.

**Verification command** (fails without the feature):
```bash
uv run pytest tests/test_sdk_teammate.py -k "permission_mode or skills or disallowed or cwd or role_field or unknown_role" -x
```

Write tests first (they fail), then implement. **No dependency on T1** — `AgentDefinition` already supports these fields; T2 tests construct `AgentDefinition` directly.

---

### T3 — Spawn chain threading (`server.py`, `broker.py`, `factories.py`)

**Covers**: SC-7 (MCP tool params), SC-8 (full chain)

**Depends on**: T2 (`SdkTeammate.__init__` must accept `cwd` and `permission_mode` before factories can forward them)

**Files touched**: `claude_crew/server.py`, `claude_crew/broker.py`, `claude_crew/factories.py`, `tests/test_server_sdk_mode.py` or `tests/test_factories.py`

**Changes** (all four callables — do not miss the inner closure):
1. `server.spawn_teammate` MCP tool: add `cwd` and `permission_mode` params; pass to broker
2. `broker.spawn_teammate`: add `cwd` and `permission_mode`; pass to factory
3. `stub_factory`: add `cwd` and `permission_mode` (accept and ignore; do NOT forward to StubTeammate)
4. `sdk_factory`: add `cwd` and `permission_mode`; conditionally add to `kwargs`
5. **Inner `factory` closure inside `default_factory()`**: add `cwd` and `permission_mode`; pass to `sdk_factory` — this is the callable `broker.spawn_teammate` actually calls in production; missing it causes a `TypeError` at runtime

**BDD Scenarios**:

```
Scenario: cwd threads from stub_factory call to kwargs check
  Given stub_factory called with cwd="/tmp/x" and permission_mode="plan"
  Then no TypeError is raised
  And stub_factory returns a StubTeammate (cwd/permission_mode silently ignored)

Scenario: cwd and permission_mode thread from sdk_factory to SdkTeammate
  Given sdk_factory called with cwd="/tmp/proj" and permission_mode="bypassPermissions"
  When it constructs SdkTeammate
  Then SdkTeammate._cwd == "/tmp/proj"
  And SdkTeammate._permission_mode == "bypassPermissions"

Scenario: default_factory closure forwards cwd and permission_mode
  Given default_factory() returns a factory closure
  When the closure is called with cwd="/tmp/proj" and permission_mode="plan"
  Then sdk_factory receives cwd and permission_mode
  (verify via monkeypatching sdk_factory and asserting kwargs)

Scenario: full chain via MCP server — cwd and permission_mode reach ClaudeAgentOptions
  Given the MCP server in SDK mode (FakeSDKClient patched)
  When spawn_teammate is called with cwd="/tmp/proj" and permission_mode="plan"
  Then the spawned SdkTeammate's ClaudeAgentOptions has cwd="/tmp/proj"
    and permission_mode="plan"
  (follows test_server_sdk_mode.py in-memory harness pattern)
```

**Verification command** (fails without the feature):
```bash
uv run pytest tests/test_factories.py tests/test_server_sdk_mode.py -k "cwd or permission_mode" -x
```

Write tests first (they fail), then implement. Check that the full suite still passes after:
```bash
uv run pytest tests/ -x --ignore=tests/test_live_sdk.py --ignore=tests/test_live_subagents.py --ignore=tests/test_user_loader_live.py --ignore=tests/test_e2e_subagent_telemetry.py --ignore=tests/test_e2e_tool_telemetry.py --ignore=tests/test_telemetry_e2e.py --ignore=tests/test_transcript_e2e.py
```

---

### T4 — E2E integration tests

**Covers**: SC-9b (live behavioral proof), full-chain regression for SC-1 through SC-10

**Depends on**: T1 + T2 + T3 all complete

**Files touched**: `tests/test_subagents.py` (loader → AgentDefinition full chain), `tests/test_live_sdk.py` (SC-9b live probe)

**BDD Scenarios**:

```
Scenario: full loader → AgentDefinition → ClaudeAgentOptions chain (non-live)
  Given a pack file on disk with skills, permissionMode: bypassPermissions, disallowedTools
  When parse_pack_file is called and the result is used as agents={"role": agent_def}
    in SdkTeammate with FakeSDKClient
  When _run() constructs ClaudeAgentOptions
  Then captured_options.permission_mode == "bypassPermissions"
  And captured_options.skills matches the pack's skills list
  And captured_options.disallowed_tools matches the pack's disallowedTools list

Scenario: live — plan-mode teammate cannot write a file (SC-9b, gated by CLAUDE_CREW_LIVE_TESTS=1)
  Given a temporary directory and a role pack file with `permissionMode: plan`
  Given a second spawn with no permissionMode (control)
  When both teammates receive: "Write the string 'hello' to the file 'probe.txt' in your cwd"
  And both are given cwd=<tempdir> at spawn time
  Then the plan-mode teammate does NOT create probe.txt
    (plan mode disables tool execution entirely — proves permission_mode is wired)
  And the control teammate DOES create probe.txt
    (proves cwd is wired and the task format is correct)
  Wait/completion: poll broker.list_crew() until both teammates are dead (alive=False),
    then assert file presence. Timeout: 120s per teammate.
```

For the live test, follow the pattern in `test_live_sdk.py` and `test_user_loader_live.py`. Spawn via `broker.spawn_teammate` directly (not the MCP server) using the `sdk_factory` with a fresh pack dict constructed from `parse_pack_text` of a temp file.

**Verification command**:
```bash
# Non-live (always runs):
uv run pytest tests/test_subagents.py -k "full_chain or loader_to_options" -x

# Live (requires credentials + env var):
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py -k "permission_mode or plan_mode" -x -s
```

**Final regression gate** (full non-live suite must still pass):
```bash
uv run pytest tests/ -x --ignore=tests/test_live_sdk.py --ignore=tests/test_live_subagents.py --ignore=tests/test_user_loader_live.py --ignore=tests/test_e2e_subagent_telemetry.py --ignore=tests/test_e2e_tool_telemetry.py --ignore=tests/test_telemetry_e2e.py --ignore=tests/test_transcript_e2e.py
```

---

## Phase 4: Implementation

**Approach**: Team build — T1+T2 parallel, T3 sequential, T4 E2E gate.

| Task | Commit | Result |
|------|--------|--------|
| T1 — Loader extension | `87f6ce9` | 267 tests passing |
| T2 — SdkTeammate extraction + params | `4a6fe45` | 331 tests passing |
| T3 — Spawn chain threading | `785ddae` | 336 tests passing |
| T4 — E2E integration tests | `f3097b0` | 340 tests passing |

Sentinel review: all SCs pass. H1 (no spawn-time permissionMode validation) logged to BACKLOG. H2 (logger name concern) was a false alarm — `_user_loader.py` uses `"claude_crew.subagents.loader"` matching the test.

---

## Phase 5: Completion

**Merged**: `f3097b0` fast-forwarded onto master 2026-04-29. 340 tests, 0 failures.

### Retrospective

**What went well**:
- Dogfooding paid off — spawning the co-architect as an actual crew member caught the Q5 reversal (spawn-time permissionMode override) in Phase 1, before it got baked into the spec. That reversal made the feature meaningfully more useful.
- The Sentinel-at-Phase-1 pattern caught the SC-9b false confidence problem (bypassPermissions doesn't prove wiring; plan mode does) before Phase 3 task breakdown. Saved a builder round-trip.
- Explicitly calling out the inner `factory` closure in `default_factory()` in the T3 brief worked — builder got it right first try.
- 4-task parallel-then-sequential dependency structure matched the feature shape cleanly. Zero conflicts between T1 and T2.

**What was friction**:
- Worktree isolation failed because the lead session ran from `/home/jerome/dev/tools` (not a git repo). Had to launch builders without isolation and rely on non-overlapping file assignments instead. One slip in file assignment would have caused a conflict.

**Improvements**:
- For claude-crew feature builds specifically, start the lead session from `~/dev/claude-crew` to enable worktree isolation for parallel builders.
- Add spawn-time `permission_mode` validation at the server boundary (BACKLOG) — loader validates at parse time; spawn-time override should too.
