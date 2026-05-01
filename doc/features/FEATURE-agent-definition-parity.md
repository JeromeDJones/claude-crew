# Feature: Agent Definition Parity (#17)

**Status**: Planning (Phase 1 — Sentinel + co-architect reviewed, awaiting user sign-off)
**Created**: 2026-05-01
**Branch**: `feature/agent-definition-parity`

---

## Phase 1: Research & Requirements

### Problem Statement

`PackFrontmatter` (the schema for `.md` agent files in bundled, user-level, and project-level pack locations) covers most of `claude_agent_sdk.AgentDefinition` — but two fields are silently dropped: **`mcpServers`** and **`memory`**. A pack author who declares either in YAML frontmatter sees no error and no effect; the value is read into the raw dict and thrown away.

Separately, `spawn_teammate(permission_mode=...)` accepts the string at the MCP boundary with **no validation**. Invalid strings (e.g. `"superuser"`) travel through factory → SdkTeammate → SDK options unchecked. The pack-loader path validates the same value (`_validate_frontmatter` raises `PackLoadError` on bad strings), but the spawn-time path has no equivalent.

Phase 1 reconnaissance also surfaced a **subagent-vs-teammate bifurcation** that compounds the problem. `parse_pack_text` builds an `AgentDefinition` with the full field set. The subagent path (Task tool dispatch from inside a teammate) gets that AgentDefinition serialized via `asdict()` into the SDK initialize message — fields ride the wire intact. The top-level teammate path is `SdkTeammate._build_options()` (`claude_crew/sdk_teammate.py:880-904`), which constructs a `ClaudeAgentOptions` and **manually copies a subset** of fields from `role_def` (`permissionMode`, `skills`, `disallowedTools`). It does NOT copy `mcpServers`, and `ClaudeAgentOptions` has no `memory` field at all. Today, four optional AgentDefinition fields are silently dropped on the teammate path: `mcpServers`, `memory`, `maxTurns`, `background`, `initialPrompt`. Adding `mcpServers`/`memory` to PackFrontmatter without wiring `_build_options()` would *widen* the bug class — the field would work for a role spawned as a Task subagent and silently die for the same role spawned as a top-level teammate, with zero feedback to the author.

These are all **role-level configuration leaks**: the system promises a config surface and silently fails to honor it. Pack authors can't safely declare MCP server requirements per role; lead sessions can't trust their `permission_mode` argument is rejected when wrong; the same role definition behaves differently depending on how it's invoked.

**Important correction to PRODUCT-VISION row 191:** the vision states `permissionMode` and `disallowedTools` are missing from PackFrontmatter. Phase 1 reconnaissance proves both are already parsed, validated, and forwarded to AgentDefinition (`claude_crew/subagents/_loader.py:132-135, 176-181`) — and unlike `mcpServers`/`memory`, both ARE wired in `_build_options()`. The actual gap is `mcpServers` + `memory` + the MCP-boundary validation gap on `permission_mode`. Vision row will be updated as part of Phase 5.

### Success Criteria

- [ ] **SC-1: `mcpServers` field accepted in PackFrontmatter and forwarded to AgentDefinition.** A pack `.md` declaring `mcpServers: ["my-server"]` (or the inline-dict form) parses successfully, the value is stored on `PackFrontmatter`, and the resulting `AgentDefinition.mcpServers` contains the same value. Verified by unit tests on `parse_pack_text` covering both forms and a negative case (mixed list element rejected at validation).

- [ ] **SC-2: `memory` field accepted in PackFrontmatter and forwarded to AgentDefinition.** A pack `.md` declaring `memory: "project"` parses, stores, and forwards. Verified by unit test on `parse_pack_text`.

- [ ] **SC-3: `_validate_frontmatter` rejects malformed `mcpServers` and `memory`.** `memory` outside `{"user", "project", "local"}` raises `PackLoadError` naming field, bad value, and pack file path. `mcpServers` rejects: not-a-list, list with non-(str|dict) elements, dict elements without a `type` key in `{"stdio", "sse", "http", "sdk"}`. Validation depth is **shallow** — we don't deep-validate McpServerConfig dict bodies; the SDK fails loudly with its own message if a config is malformed. Verified by negative unit tests.

- [ ] **SC-4: `spawn_teammate(permission_mode=...)` rejects invalid values at the MCP boundary by raising `mcp.server.fastmcp.exceptions.ToolError`.** A spawn call with `permission_mode="superuser"` raises `ToolError` before any factory/broker work happens; the message names the field, the bad value, and the accepted set (the 6 PermissionMode literals from the SDK). **Test approach (SF-3):** route through FastMCP's tool dispatch (not a direct Python call to the closure) so the `ToolError` is observed AT THE MCP PROTOCOL BOUNDARY — proving that ToolError is forwarded verbatim and not wrapped by FastMCP's exception handler. Pattern: use the existing `_client_with_sdk_mode()` test harness, call `s.call_tool("spawn_teammate", {"permission_mode": "superuser", ...})`, assert the response carries a tool error with the expected message substring.

- [ ] **SC-5: `mcpServers` is wired on the top-level teammate path, not just the subagent path.** A role declaring `mcpServers` in its pack, when spawned via `spawn_teammate`, has the value translated and applied to `ClaudeAgentOptions.mcp_servers` so the teammate session honors it. **Translation strategy** (Q-5/D-4): (1) inline-dict entries pass through with `name` stripped; (2) string-name entries are resolved against `~/.claude.json`'s `mcpServers` map and inlined; (3) unresolvable string entries are skipped at spawn with a structured WARN. Verified by: (a) unit test asserting the inline options-builder in `_run` consumes `role_def.mcpServers` and produces the expected dict (both forms, including stripped `name`), (b) unit test asserting unresolvable string entries are skipped + WARN logged, (c) unit test asserting `home_dir` injection works for test isolation (MF-3/D-11), (d) one live SDK test under `CLAUDE_CREW_LIVE_TESTS=1` that spawns a teammate with a pack declaring a known string-form `mcpServers` entry, observes the server is reachable from the teammate session, AND asserts name-collision behavior when the same name appears in both pack and `~/.claude.json` (SF-1 — verifies D-5's additive claim with explicit tie-break).

- [ ] **SC-6: `memory` is documented as subagent-context-only and emits a WARN if a role declaring `memory` is spawned as a top-level teammate.** `ClaudeAgentOptions` has no `memory` field, so there is no carrier for this on the teammate path. Rather than reject at load time (the same role pack legitimately gets used both ways), we WARN at spawn time. Verified by a unit test asserting the WARN is logged when `spawn_teammate` is called for a role whose pack declares `memory`, and by a docstring update on `PackFrontmatter.memory` explaining the constraint.

- [ ] **SC-7: Startup WARN for unresolvable string-form `mcpServers` entries.** Mirrors the existing `_warn_unknown_skills` pattern. At pack-load time, for each pack with string-form `mcpServers` entries, parse `~/.claude.json`'s top-level `mcpServers` key set; emit a structured WARN naming the role, the unresolved server name, and the path checked. Inline-dict entries are not warned (they're self-contained). Verified by a unit test feeding a fake `~/.claude.json` and asserting the WARN.

- [ ] **SC-8: Strict user-loader (`_user_loader.py`) recognizes the new keys.** `_ACCEPTED_FRONTMATTER_KEYS` is auto-derived from `PackFrontmatter.__dataclass_fields__` (`_user_loader.py:56`), so adding the fields propagates structurally — but the SC requires a *behavioral* test: loading a user pack with `mcpServers` and `memory` does NOT emit "unsupported key" warnings.

- [ ] **SC-9: No regression in existing pack consumers.** Bundled packs (`explorer.md`, `planner.md`, `general_purpose.md`) still load. All existing tests in `test_pack_loader.py`, `test_subagents.py`, `test_user_loader.py`, `test_skills_e2e.py`, `test_server_sdk_mode.py` still pass. Verified by `uv run pytest`.

- [ ] **SC-10: PRODUCT-VISION row 191 corrected.** The "missing permissionMode + disallowedTools" claim is removed; the actual scope (mcpServers + memory + MCP-boundary validation + teammate-path wiring) is reflected. Verified by reading the updated vision row.

### Questions

- [ ] **Q-1: Should `spawn_teammate` gain new MCP-boundary args for `mcp_servers` / `memory` / `disallowed_tools` as spawn-time overrides?** **Answered: NO.** Pack-only for new fields per vision row 191 ("role-level config belongs in pack files"). SC-4 (tightening `permission_mode` validation) is a fix to an existing arg, not new surface. `maxTurns`/`background`/`initialPrompt` MCP-arg exposure also deferred — out of scope for #17.

- [ ] **Q-2: What `mcpServers` shape do we accept in pack YAML?** **Answered: both `str` and `dict[str, Any]` entries** in the list, mirroring `AgentDefinition.mcpServers: list[str | dict[str, Any]]` exactly. String entries reference servers in `~/.claude.json`; dict entries are inline McpServerConfig. Validation is shallow per SC-3. String entries can fail at runtime if the named server isn't registered — surfaced via SC-7's startup WARN, mirroring how `skills` handles unresolvable names.

- [ ] **Q-3: MCP-boundary error format for SC-4.** **Answered: `raise ToolError(message)` from `mcp.server.fastmcp.exceptions`.** Raising `ValueError` would be wrapped by FastMCP into `"Error executing tool spawn_teammate: ..."`, losing signal. `ToolError` is forwarded verbatim. Test approach: call the tool coroutine directly via `make_server()` and assert `pytest.raises(ToolError, match=...)`.

- [ ] **Q-4: Defer `maxTurns`, `background`, `initialPrompt` exposure at MCP spawn boundary.** **Answered: yes, defer.** All three exist in PackFrontmatter and are honored when set in pack YAML; spawn-time MCP-arg exposure is *spawn-API expansion*, not *pack parity*. File a small backlog item if a use case emerges.

- [ ] **Q-5: `mcpServers` translation strategy on the teammate path.** **Resolved by SDK code reading (no live probe needed).** Findings from `claude_agent_sdk/_internal/transport/subprocess_cli.py:289-314`, `_internal/client.py:136-177`, and CLAUDE.md confirmation:
  1. `ClaudeAgentOptions.mcp_servers` (dict form) is serialized to inline JSON `{"mcpServers": {...}}` and passed to the CLI as `--mcp-config`. The string/Path form passes through directly as a config file path.
  2. User-level `~/.claude.json` `mcpServers` are auto-loaded by the CLI when `--setting-sources` includes `user` (the SDK default `["user", "project"]`). This is how teammates today reach `~/.claude.json`-registered servers without explicit `mcp_servers` plumbing — confirmed by `~/.claude.json` containing `atlassian` and `claude-crew` and existing teammates using them.
  3. `AgentDefinition.mcpServers` (per-agent, subagent path) rides via the SDK initialize message and acts as a per-agent allowlist scoping which servers from the session pool the subagent can see. The top-level teammate path has no analogous per-agent scoping mechanism — the teammate IS the session-level agent.
  
  **Strategy locked into SC-5**: inline-dict entries pass through to `ClaudeAgentOptions.mcp_servers`; string-name entries are resolved at spawn time against `~/.claude.json`'s top-level `mcpServers` map and inlined. Unresolvable names → WARN + skip. This makes pack `mcpServers` declarations self-describing for the teammate (the teammate ends up with exactly the named server configs in its `mcp_servers` dict, regardless of what user-settings would auto-load).

- [ ] **Q-6: Scope decision — fold Pillar 4 (multi-source shadow-drop WARN) into #17, or file separate?** **Recommendation: fold in.** `merge_packs` does whole-AgentDefinition replacement at the role-key level (`_user_loader.py:322`); a project-level shadow that doesn't mention `mcpServers` silently *clears* the bundled value. The footgun exists today for `skills`/`disallowedTools`/`permissionMode`; #17 widens the blast radius by adding two more silently-replaceable fields. Co-architect's proposal: emit a structured WARN when `merge_packs` detects a higher-precedence pack drops an optional AgentDefinition field that a lower-precedence pack set. ~30 lines + one test. The cost of folding in is small; the cost of a separate ticket is re-paging the merge stack later. **User confirmed: fold in.** This becomes SC-11.

- [ ] **SC-11 (added per Q-6 resolution): Warn-on-shadow-drop in `merge_packs`.** When merging packs across precedence sources (bundled → user → project), if a higher-precedence pack lacks an optional AgentDefinition field that a lower-precedence pack set, emit a structured WARN naming the role, the field, and the source paths. Applies uniformly to all optional AgentDefinition fields (`mcpServers`, `memory`, `skills`, `disallowedTools`, `permissionMode`, `maxTurns`, `background`, `initialPrompt`, `effort`, `tools`). Required fields (`description`, `model`) are not warned (the higher-precedence pack must declare them; that's a pre-existing contract). Verified by unit tests on `merge_packs` covering: (a) drop of `mcpServers` warns, (b) drop of `memory` warns, (c) higher-precedence pack with all-fields-explicit does not warn.

### Constraints & Dependencies

- **Requires**: existing `PackFrontmatter` / `_validate_frontmatter` / `parse_pack_text` (`claude_crew/subagents/_loader.py`), existing `_user_loader._ACCEPTED_FRONTMATTER_KEYS` and `merge_packs`, existing `spawn_teammate` MCP tool (`claude_crew/server.py:51-84`), `_build_options` in `SdkTeammate` (`claude_crew/sdk_teammate.py:880-904`), existing `claude_agent_sdk.AgentDefinition`, `ClaudeAgentOptions`, `PermissionMode`, `McpServerConfig` (vendored in `.venv`), `mcp.server.fastmcp.exceptions.ToolError`.
- **Breaking changes**: **No.** All pack-side additions are additive. `_build_options()` extension is additive (new branch, no removed behavior). SC-4 (MCP `permission_mode` validation) tightens behavior — calls that were *broken* before (silently passing bad strings to the SDK) now fail loudly at the boundary, which is the desired correction. SC-11 (shadow-drop WARN) emits log lines only — no behavior change in the loaded pack itself.
- **Performance implications**: **No.** Two additional optional fields parsed once per pack at load time; one additional set-membership check per `spawn_teammate` call; `~/.claude.json` parsed once at load time for SC-7 (small file, ~5KB typical); shadow-drop comparison runs once during pack merge.
- **Cross-cutting touches**: `_loader.py` (PackFrontmatter, _validate_frontmatter, parse_pack_text); `_user_loader.py` (merge_packs for SC-11, _warn_unknown logic for SC-7); `factories.py` (unchanged unless Phase 2 probe reveals an edge); `sdk_teammate.py:_build_options` (new wiring for `mcpServers`, new WARN for `memory` per SC-6); `server.py:spawn_teammate` (validation per SC-4).
- **Tests touched**: `test_pack_loader.py`, `test_subagents.py`, `test_user_loader.py`, `test_server_sdk_mode.py`, plus new tests for `_build_options` mcpServers wiring, memory-WARN at teammate spawn, shadow-drop WARN in merge_packs, and one live SDK probe under `CLAUDE_CREW_LIVE_TESTS=1`.

**Gate**:
- ✅ Sentinel review complete — H-1, H-2, H-3 addressed; M-1, M-2, M-3, L-1, L-2 routed.
- ✅ Co-architect three-pushback warmup complete — four design pillars surfaced (Pillars 1-3 + D-D folded into SCs; Pillar 4 elevated to Q-6 → SC-11).
- ✅ Q-1, Q-2, Q-3, Q-4, Q-6 answered.
- ✅ Q-5 resolved by SDK code reading — translation strategy locked into SC-5; no live probe needed.
- ⏳ User sign-off on Phase 1 SCs.

---

## Phase 2: Design & Specification

### Architecture Overview

Three integration surfaces, in pack-load → spawn-time → runtime order:

1. **Pack-load surface** (`claude_crew/subagents/_loader.py`): `PackFrontmatter` gains two optional fields (`mcpServers`, `memory`); `_validate_frontmatter` validates them shallowly; `parse_pack_text` forwards them into the constructed `AgentDefinition`. Existing key set in `_user_loader._ACCEPTED_FRONTMATTER_KEYS` (auto-derived from `PackFrontmatter.__dataclass_fields__`) propagates structurally.

2. **Pack-merge surface** (`claude_crew/subagents/_user_loader.py:build_merged_pack` + `__init__.py:merge_packs`): a new `_warn_unknown_mcp_servers` mirrors `_warn_unknown_skills` (called after merge). A new `_warn_shadow_drop` runs alongside the existing shadow-INFO logging, comparing optional-field presence across precedence layers and emitting WARN when a higher-precedence pack drops fields a lower-precedence pack set.

3. **Spawn surface** (`claude_crew/server.py:spawn_teammate` + `claude_crew/sdk_teammate.py:_run`): MCP boundary validates `permission_mode` and raises `ToolError` on bad values. The inline options-builder in `_run()` (lines 841–904 — there is no separate `_build_options` method, despite earlier references) gains two branches: (a) translate `role_def.mcpServers` (resolving string-name entries against `~/.claude.json`) into `ClaudeAgentOptions.mcp_servers`, and (b) detect `role_def.memory` and emit a WARN naming the role and the limitation (subagent-context-only).

No architecture changes. No new modules. All extensions are additive on existing seams.

### Data / API Contracts

**`PackFrontmatter` (`_loader.py:25-45`) — add two fields, append to existing optional set:**
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
    skills: tuple[str, ...] | Literal["all"] | None = None
    permissionMode: str | None = None
    disallowedTools: tuple[str, ...] | None = None
    settingSources: list[str] | None = None
    # New:
    mcpServers: tuple[str | dict[str, Any], ...] | None = None
    memory: Literal["user", "project", "local"] | None = None
```

**`_validate_frontmatter` (`_loader.py:169-256`) — add validation blocks:**
```python
# memory: enum of 3 strings, mirrors permissionMode pattern
if "memory" in raw and raw["memory"] is not None:
    if raw["memory"] not in {"user", "project", "local"}:
        raise PackLoadError(
            f"pack file {path}: memory {raw['memory']!r} is invalid; "
            f"accepted: 'user', 'project', 'local'"
        )

# mcpServers: list of (str | dict-with-known-type), shallow validation only.
# `sdk`-type is REJECTED in pack form per MF-1 — it requires an in-process
# Python callable (`instance` field) that cannot survive YAML serialization.
if "mcpServers" in raw and raw["mcpServers"] is not None:
    if not isinstance(raw["mcpServers"], list):
        raise PackLoadError(f"pack file {path}: mcpServers must be a list")
    valid_types = {"stdio", "sse", "http"}  # sdk excluded — see D-7
    for i, entry in enumerate(raw["mcpServers"]):
        if isinstance(entry, str):
            continue
        if isinstance(entry, dict):
            t = entry.get("type")
            if t == "sdk":
                raise PackLoadError(
                    f"pack file {path}: mcpServers[{i}] type='sdk' is not "
                    f"supported in pack form (requires in-process instance); "
                    f"register the server in ~/.claude.json and reference by name"
                )
            if t not in valid_types:
                raise PackLoadError(
                    f"pack file {path}: mcpServers[{i}] dict has type={t!r}; "
                    f"accepted: {sorted(valid_types)}"
                )
            continue
        raise PackLoadError(
            f"pack file {path}: mcpServers[{i}] must be str or dict, got {type(entry).__name__}"
        )

# In the explicit PackFrontmatter(...) constructor at the bottom of
# _validate_frontmatter (currently around _loader.py:239), append:
#     mcpServers=tuple(raw["mcpServers"]) if raw.get("mcpServers") else None,
#     memory=raw.get("memory"),
```

**`parse_pack_text` (`_loader.py:91-138`) — add forwards to AgentDefinition:**
```python
if fm.mcpServers is not None:
    agent_kwargs["mcpServers"] = list(fm.mcpServers)
if fm.memory is not None:
    agent_kwargs["memory"] = fm.memory
```

**`server.py:spawn_teammate` (lines 51-84) — validate permission_mode at MCP boundary:**
```python
from mcp.server.fastmcp.exceptions import ToolError

_VALID_PERMISSION_MODES = frozenset({
    "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"
})

async def spawn_teammate(..., permission_mode: str | None = None) -> dict[str, Any]:
    if permission_mode is not None and permission_mode not in _VALID_PERMISSION_MODES:
        raise ToolError(
            f"permission_mode {permission_mode!r} is not a valid PermissionMode; "
            f"accepted: {sorted(_VALID_PERMISSION_MODES)}"
        )
    # ... existing body unchanged
```

**`sdk_teammate.py:_run` options-builder (currently around lines 880-904) — add two branches after the existing `role_skills` / `role_disallowed` handling, inside the `if role_def is not None:` block:**
```python
role_mcp = getattr(role_def, "mcpServers", None)
if role_mcp:
    # home_dir=None → _resolve_mcp_servers reads Path.home(); tests inject tmp_path.
    opts_kwargs["mcp_servers"] = _resolve_mcp_servers(
        role_mcp, self.role, self.id, home_dir=None,
    )
role_memory = getattr(role_def, "memory", None)
if role_memory is not None:
    logger.warning(
        "teammate=%s role=%s pack declares memory=%r; ClaudeAgentOptions has no "
        "memory carrier — this field applies only to subagent dispatch contexts",
        self.id, self.role, role_memory,
    )
```

**New helper `sdk_teammate._resolve_mcp_servers` (module-level):**
```python
def _resolve_mcp_servers(
    entries: list[str | dict[str, Any]],
    role: str,
    teammate_id: str,
    home_dir: Path | None = None,  # MF-3: explicit injection for test isolation
) -> dict[str, dict[str, Any]]:
    """Translate pack mcpServers (list of str|dict) → ClaudeAgentOptions dict.
    
    String entries resolve against ~/.claude.json's top-level mcpServers map.
    Dict entries are pack-side-validated (type ∈ {stdio, sse, http}) and assigned
    a key via the entry's `name` field (D-7 convention) or a fallback `<type>_<index>`.
    The `name` field is stripped from the value before insertion into the result dict
    since `McpServerConfig` (stdio/sse/http) does not include `name` as a member field.
    Unresolvable string names are skipped with a WARN; spawn does not fail.
    """
    user_servers = _load_user_mcp_servers(home_dir)  # no module cache — D-11
    resolved: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name") or f"{entry.get('type', 'unnamed')}_{len(resolved)}"
            resolved[name] = {k: v for k, v in entry.items() if k != "name"}
            # D-13 breadcrumb: log inline-dict pass-through so a later CLI-subprocess
            # crash from a malformed dict has operator-visible context.
            logger.info(
                "teammate=%s role=%s mcpServers passing through inline dict "
                "name=%s type=%s",
                teammate_id, role, name, entry.get("type"),
            )
        else:  # str
            cfg = user_servers.get(entry)
            if cfg is None:
                logger.warning(
                    "teammate=%s role=%s mcpServers names %r but server is not "
                    "registered in ~/.claude.json; skipping (load-time WARN already fired)",
                    teammate_id, role, entry,
                )
                continue
            resolved[entry] = cfg
    return resolved
```

**`_user_loader.py:build_merged_pack` — add two new calls after merge:**
```python
# Existing:
merged = merge_packs(merge_packs(default, user), project)
_warn_unknown_skills(merged, home_dir, project_root)
# New:
_warn_unknown_mcp_servers(merged, home_dir)
_warn_shadow_drop(default, user, project)
return merged, role_ss, bodies
```

**Two distinct helpers for `~/.claude.json` parsing — explicit homes per co-architect M1:**

| Helper | Module | Returns | Caller |
|---|---|---|---|
| `_load_user_mcp_server_names(home_dir)` | `_user_loader.py` | `set[str]` of registered server names | `_warn_unknown_mcp_servers` (load time) |
| `_load_user_mcp_servers(home_dir)` | `sdk_teammate.py` | `dict[str, dict[str, Any]]` mapping name → config dict | `_resolve_mcp_servers` (spawn time) |

Both parse `~/.claude.json` (or accept a planted `home_dir`) on every call (no cache, D-11). Both treat missing file / malformed JSON / missing `mcpServers` key as empty (return `set()` or `{}` respectively, no exception). Names-only vs full-config split avoids exposing user MCP server *configs* to the load-time warner (which only needs to check membership) and keeps `_user_loader.py` free of teammate-runtime concerns.

**`_warn_unknown_mcp_servers` (new in `_user_loader.py`):**
```python
def _warn_unknown_mcp_servers(
    merged: dict[str, AgentDefinition],
    home_dir: Path | None = None,
) -> None:
    """Emit a WARN for each string-form mcpServers entry not in ~/.claude.json."""
    home = home_dir if home_dir is not None else Path.home()
    user_servers = _load_user_mcp_server_names(home)
    for role, agent in merged.items():
        entries = getattr(agent, "mcpServers", None) or []
        unresolved = [
            e for e in entries
            if isinstance(e, str) and e not in user_servers
        ]
        if unresolved:
            logger.warning(
                "agent %r declares unknown mcpServers %s — not registered in "
                "~/.claude.json; teammate will skip them at spawn",
                role, unresolved,
            )
```

**`_warn_shadow_drop` (new in `_user_loader.py`):**
```python
# Optional fields only. `tools` is required and is excluded per SF-2 — a higher-precedence
# pack must declare `tools` or pack-load fails; a "drop" cannot occur.
_OPTIONAL_AGENTDEF_FIELDS = (
    "mcpServers", "memory", "skills", "disallowedTools", "permissionMode",
    "maxTurns", "background", "initialPrompt", "effort",
)

def _warn_shadow_drop(
    default: dict[str, AgentDefinition],
    user: dict[str, AgentDefinition] | None,
    project: dict[str, AgentDefinition] | None,
) -> None:
    """WARN when a higher-precedence pack drops an optional field a lower one set."""
    user = user or {}
    project = project or {}
    # user shadows default; project shadows whichever lower layer the role came from
    for role in user.keys() & default.keys():
        _check_drop("user", role, default[role], user[role])
    for role in project.keys():
        if role in user:
            _check_drop("project", role, user[role], project[role])
        elif role in default:
            _check_drop("project", role, default[role], project[role])

def _check_drop(layer: str, role: str, lower: AgentDefinition, higher: AgentDefinition) -> None:
    for field in _OPTIONAL_AGENTDEF_FIELDS:
        lower_val = getattr(lower, field, None)
        higher_val = getattr(higher, field, None)
        if lower_val is not None and higher_val is None:
            logger.warning(
                "%s-level agent %r drops optional field %r set by lower-precedence "
                "pack (value=%r); pack-merge is whole-replacement, not field-level",
                layer, role, field, lower_val,
            )
```

### Design Decisions

- **D-1: `mcpServers` validated shallowly at pack-load (list-of str|dict-with-type).** *Rationale:* Deep validation duplicates SDK logic; SDK fails loudly with better messages. Mirrors how `skills` (str-list referencing externally-validated names) is handled. *Carried into:* `_validate_frontmatter` block in `_loader.py`; `tests/test_pack_loader.py::test_mcpServers_*`.

- **D-2: `memory` validated as 3-string enum at pack-load.** *Rationale:* Mirrors `permissionMode` pattern — small closed set, cheap to enforce. *Carried into:* `_validate_frontmatter`; `PackFrontmatter.memory: Literal[...]` annotation.

- **D-3: `permission_mode` validated at MCP boundary via `ToolError`, not `ValueError`.** *Rationale:* `ValueError` gets wrapped by FastMCP into `"Error executing tool spawn_teammate: ..."` losing signal; `ToolError` is forwarded verbatim to MCP callers. *Carried into:* `server.py:spawn_teammate` body; `tests/test_server_sdk_mode.py::test_spawn_rejects_invalid_permission_mode`.

- **D-4: `mcpServers` translation strategy on teammate path = inline-dicts pass through; string-names resolve against `~/.claude.json` and inline.** *Rationale:* Resolved by Q-5 SDK code reading. ClaudeAgentOptions.mcp_servers serializes to `--mcp-config` CLI flag (subprocess_cli.py:289-314); the dict form works directly. Resolving names ourselves makes the pack declaration self-describing — the resulting teammate's `mcp_servers` dict contains exactly the named server configs, regardless of what `--setting-sources=user` would have auto-loaded. *Carried into:* `sdk_teammate._resolve_mcp_servers`; `tests/test_sdk_teammate.py::test_mcp_servers_*`.

- **D-5: `mcpServers` translation is ADDITIVE, not exclusive.** *Rationale:* The CLI receives both `--mcp-config` (from our translated dict) and `--setting-sources=user,project` (which auto-loads `~/.claude.json` mcpServers). Pack `mcpServers: ["atlassian"]` adds atlassian to the explicit dict; user-settings still loads everything in `~/.claude.json` (including atlassian, which becomes a no-op duplicate the CLI dedupes). Pack authors who want exclusive scope should set `settingSources: []` to disable user auto-load. *Carried into:* docstring on `PackFrontmatter.mcpServers`; `tests/test_sdk_teammate.py::test_mcp_servers_additive_to_user_settings` (live SDK).

- **D-6: Unresolvable string-name `mcpServers` entries WARN at both load-time AND spawn-time, then are skipped (do not crash spawn).** *Rationale:* Load-time WARN (SC-7) catches misconfig before any teammate spawns; spawn-time skip-with-WARN provides a defensive net for race cases (e.g., `~/.claude.json` edited between load and spawn). Skipping rather than failing matches "pack mcpServers is a hint" semantics — the teammate may still function via user-settings auto-load. *Carried into:* `_warn_unknown_mcp_servers` (load); `_resolve_mcp_servers` (spawn); `tests/test_user_loader.py::test_warn_unknown_mcp_servers`.

- **D-7: Inline-dict `mcpServers` entries adopt a `"name"` key as the pack-form convention; `sdk`-type is REJECTED in pack form.** *Rationale:* `ClaudeAgentOptions.mcp_servers` is `dict[str, McpServerConfig]` keyed by name. The SDK's `AgentDefinition.mcpServers: list[str | dict]` doesn't require a name on inline dicts (the CLI names them). For our translation we need a key — adopt `"name"` as the pack convention, fall back to `"<type>_<index>"` if absent, strip `name` from the value before insertion (the value must satisfy `McpServerConfig`, which for stdio/sse/http does NOT include `name`). **`sdk`-type is rejected at pack-load** (per MF-1) because `McpSdkServerConfig` requires an in-process Python `instance` callable that cannot survive YAML serialization; pack authors who want `sdk`-type servers should register them in `~/.claude.json` and reference by name. *Carried into:* `_resolve_mcp_servers`; `_validate_frontmatter` rejection block; `tests/test_pack_loader.py::test_mcpServers_sdk_type_rejected`; `tests/test_sdk_teammate.py::test_mcp_servers_inline_dict_*`.

- **D-8: `memory` on teammate path = WARN at spawn time, not reject at pack-load.** *Rationale:* The same role pack legitimately gets used both ways (subagent dispatch + top-level teammate). Rejecting at load disables a valid subagent use-case to defend a teammate-only constraint. WARN at spawn keeps the pack valid for subagent dispatch and gives operators the signal when the constraint bites. *Carried into:* `sdk_teammate._run` memory-WARN block; `tests/test_sdk_teammate.py::test_memory_warns_on_teammate_spawn`.

- **D-9: Shadow-drop WARN runs in `build_merged_pack` after `_warn_unknown_skills`.** *Rationale:* Same lifecycle phase as existing per-role load-time warnings. Reuses existing logger (`claude_crew.subagents.loader`). *Carried into:* `_user_loader.build_merged_pack:323`; `tests/test_user_loader.py::test_shadow_drop_warns`.

- **D-10: Shadow-drop WARN applies to all 10 optional AgentDefinition fields uniformly, not just the new ones.** *Rationale:* The footgun pre-exists for `skills`/`disallowedTools`/`permissionMode`; #17 fixes it once for all. Required fields (`description`, `model`) are not warned (the higher-precedence pack must declare them or the whole pack fails to load). *Carried into:* `_OPTIONAL_AGENTDEF_FIELDS` constant in `_user_loader.py`; `tests/test_user_loader.py::test_shadow_drop_*`.

- **D-11: `_resolve_mcp_servers` parses `~/.claude.json` lazily on each call (no module-level cache); `home_dir` is an explicit param for test isolation.** *Rationale:* Module-level cache would leak across pytest tests planting fake `~/.claude.json`. Per-spawn parse is one tiny JSON read — negligible. The `home_dir` param mirrors `_warn_unknown_skills`/`_warn_unknown_mcp_servers` and lets spawn-path tests inject `tmp_path` directly without monkeypatching `HOME` (MF-3). The teammate's `_run` passes `home_dir=None`; tests pass `home_dir=tmp_path`. *Carried into:* `_resolve_mcp_servers` signature; `_load_user_mcp_servers` signature; `tests/test_sdk_teammate.py::test_mcp_servers_resolves_against_home_dir`.

- **D-12 (added per MF-2): teammate-path failure mode for malformed inline-dict `mcpServers` is a CLI subprocess crash, surfacing via the existing teammate death path.** *Rationale:* Shallow validation accepts `{"type": "stdio"}` without `command`. The dict serializes via `--mcp-config`; the CLI fails to spawn the MCP server; the teammate dies. This is asymmetric with the subagent path (where AgentDefinition serialization can surface the error via the SDK's initialize response). The asymmetry is acceptable because (a) deep validation duplicates SDK logic, (b) the existing `_handle_teammate_death` path tombstones the teammate cleanly, and (c) `~/.claude.json`-registered servers (the recommended path) are SDK-validated when first loaded. Operators who hit this see a teammate that dies on first turn with a CLI subprocess error in stderr. *Carried into:* note in `PackFrontmatter.mcpServers` docstring; no test required (covered by existing teammate death path).

- **D-13 (added per co-architect Miss-3): `_resolve_mcp_servers` emits an INFO breadcrumb on every inline-dict pass-through.** *Rationale:* D-12's accepted asymmetry (CLI-subprocess crash on malformed dict → teammate death) leaves the operator looking at #19's tool stream / #22's badge / #14's token telemetry on the dashboard, not at stderr. A 1-line INFO at spawn time naming role, teammate id, server name, and server type gives the operator a breadcrumb when the teammate dies seconds later: "the death lined up with this MCP config" is enough to root-cause without rerunning. INFO-level (not WARN) because passing an inline dict is the legitimate happy path; we only want the breadcrumb if death follows. *Carried into:* `_resolve_mcp_servers` body; no test required (observability only, no behavior change).

### Edge Cases

1. **`mcpServers: []` (explicit empty list)** — pack declares the field with no entries. Validation accepts (empty list is valid). `parse_pack_text` forwards empty list to `AgentDefinition`. `_resolve_mcp_servers` returns empty dict. No WARN at any layer. *Test:* `test_mcpServers_empty_list_accepted`.

2. **`mcpServers: null` vs `mcpServers` absent** — both produce `PackFrontmatter.mcpServers = None`. No behavioral difference. *Test:* covered by SC-9 regression set.

3. **Mixed string + dict entries** — `mcpServers: ["atlassian", {"type": "stdio", "name": "local-tool", "command": "..."}]`. Validator accepts both. Resolver inlines atlassian's config and passes through the dict. *Test:* `test_mcpServers_mixed_entries`.

4. **String-name entry refers to a server that exists at pack-load but is removed before teammate spawn** — load-time WARN does not fire; spawn-time WARN fires; entry is skipped. *Test:* `test_mcpServers_runtime_skip_with_warn`.

5. **Inline-dict without `name` key** — fallback naming `<type>_<index>`. Multiple unnamed dicts of same type get distinct keys. *Test:* `test_mcpServers_inline_dict_unnamed_fallback`.

6. **`memory: "local"` at pack-load** — accepted. `_validate_frontmatter` passes; `parse_pack_text` forwards. If role is dispatched as subagent, SDK honors it. If role is spawned as teammate, WARN fires. *Test:* `test_memory_warns_on_teammate_spawn`.

7. **`permission_mode` at MCP boundary with `None` value** — passes through unchanged (no validation triggered for None). Falls back to pack `permissionMode` if set, else SDK default. *Test:* covered by existing `test_spawn_with_cwd_and_permission_mode_reaches_options`.

8. **Shadow-drop where lower pack has the field and higher pack explicitly sets it to empty (`disallowedTools: []`)** — WARN does NOT fire (explicit empty is not None; the higher pack made an explicit choice). *Test:* `test_shadow_drop_no_warn_on_explicit_empty`.

9. **Shadow-drop where multiple fields drop in one role** — separate WARN per field. *Test:* `test_shadow_drop_multiple_fields`.

10. **`~/.claude.json` does not exist or is malformed JSON** — `_load_user_mcp_server_names` returns empty set; load-time WARN fires for every string-form entry. `_resolve_mcp_servers` returns empty dict; spawn-time WARN fires. Spawn does not crash. *Test:* `test_warn_unknown_mcp_servers_no_user_config`.

11. **`~/.claude.json` exists but lacks `mcpServers` top-level key** — same behavior as malformed: empty server-name set, all string entries warn. *Test:* `test_warn_unknown_mcp_servers_no_mcpservers_key`.

12. **Concurrent spawns of the same role** — `_resolve_mcp_servers` is stateless per call; no race. WARNs may interleave in log output (acceptable; each names the teammate id). *Test:* not explicitly required (no shared mutable state).

13. **Malformed inline-dict in pack (e.g., `{"type": "stdio"}` missing `command`)** — passes shallow validation (D-1); resolver inlines the malformed dict; CLI subprocess fails to spawn the MCP server; teammate dies on first turn via existing `_handle_teammate_death` path. Failure mode is asymmetric with subagent path (D-12). Operator sees teammate death + CLI subprocess error in stderr. *Test:* not required — relies on existing teammate death path; documented in `PackFrontmatter.mcpServers` docstring per D-12.

14. **Pack `mcpServers: ["server-a"]` combined with `settingSources: []` (escape hatch from D-5)** — pack disables user-settings auto-load via empty settingSources; resolver still pulls server-a's config from `~/.claude.json` and inlines it via `--mcp-config`. Teammate gets server-a explicitly, no other servers. Matches D-5's documented escape hatch. *Test:* `test_mcp_servers_with_empty_setting_sources` (stub mode — assert constructed ClaudeAgentOptions has both settingSources=[] and mcp_servers={"server-a": {...}}).

15. **Same server name appears in both pack `mcpServers` and `~/.claude.json` (collision case)** — resolver inlines the resolved config under that name; CLI receives the name in `--mcp-config` AND would auto-load it via setting-sources. CLI behavior on collision is the SF-1 question; resolved by the live SDK test in SC-5(d). Whichever wins, the teammate has a working server under that name. *Test:* SC-5(d) live SDK test.

16. **`~/.claude.json` modified between server startup (load-time WARN) and teammate spawn (resolve-time WARN)** — Load-time `_warn_unknown_mcp_servers` reads once at `build_merged_pack`; spawn-time `_resolve_mcp_servers` reads again per spawn. Race is benign: both paths handle missing/unresolvable names with WARN+skip. Logs may report inconsistent state across timestamps (e.g., load WARN says "atlassian unknown" but spawn resolves it cleanly because the operator just edited `~/.claude.json`). The spawn-time resolution is the source of truth for what actually went onto the wire — load-time WARN is a startup convenience, not a contract. *Test:* not required; documented behavior. *Per co-architect Miss-2.*

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| YAML → `_validate_frontmatter` | YAML parses to dict | `PackLoadError` naming field, value, file path | `PackFrontmatter` instance with validated optional fields | Pack file is silently skipped at higher level (`load_user_agents` catches and logs) |
| `_validate_frontmatter` → `parse_pack_text` | `PackFrontmatter` instance valid | N/A — no further validation | `(key, AgentDefinition, PackFrontmatter, body)` tuple | N/A |
| `parse_pack_text` → `merge_packs` | Three sources loaded as `dict[str, AgentDefinition]` | N/A — `merge_packs` cannot fail | Merged dict; shadow-drop WARN emitted for any drops | N/A — WARN is observability only |
| `merge_packs` → `_warn_unknown_mcp_servers` | Merged dict + `~/.claude.json` accessible (or absent) | N/A — missing/malformed `~/.claude.json` is non-fatal | WARN per unresolved string-name entry, per role | N/A |
| MCP arg → `spawn_teammate` body | `permission_mode` is None or str | `ToolError` with field/value/accepted-set | Validated args pass to `broker.spawn_teammate` | None — spawn never happened from broker's view |
| `broker.spawn_teammate` → `factory` → `SdkTeammate.__init__` | All args pre-validated | N/A — type-checked already | `SdkTeammate` instance with `_agents` populated | Existing broker error path |
| `SdkTeammate._run` options-builder → `ClaudeAgentOptions(...)` | `role_def.mcpServers` is None or list | `_resolve_mcp_servers` returns dict, possibly empty + WARN | `ClaudeAgentOptions.mcp_servers` populated | None — partial dict is acceptable |
| `ClaudeAgentOptions` → SDK CLI subprocess | `mcp_servers` is dict, str, or Path | SDK/CLI failure surfaces via existing error path | Teammate has access to declared servers | Existing teammate-death/tombstone path |

### Specification

**Source of truth.** Implementation MUST match this spec; any divergence updates this section before the divergence ships.

The feature ships in three behavioral layers, all additive:

- **Layer A (pack-load):** `PackFrontmatter` accepts `mcpServers` and `memory`; `_validate_frontmatter` rejects malformed values; `parse_pack_text` forwards both to `AgentDefinition`; `_user_loader._ACCEPTED_FRONTMATTER_KEYS` propagates structurally (no manual update — verified by behavioral test).

- **Layer B (pack-merge):** `_warn_unknown_mcp_servers` and `_warn_shadow_drop` run in `build_merged_pack` after the existing merge. Both emit WARN-level logs on the existing `claude_crew.subagents.loader` logger. Neither blocks load.

- **Layer C (spawn):** `spawn_teammate` validates `permission_mode` at the MCP boundary via `ToolError`. `SdkTeammate._run` translates `role_def.mcpServers` via `_resolve_mcp_servers` and emits a WARN for `role_def.memory`.

**Cross-feature integration check** — using knowledge graph + reading consumers:

- `PackFrontmatter` consumers: `_loader.parse_pack_text` (forwarder), `_user_loader.strict_parse` (warns on unknown keys via `__dataclass_fields__` reflection — adding fields propagates structurally). No other consumers read `PackFrontmatter` fields directly.
- `AgentDefinition.mcpServers` / `.memory` consumers: SDK serializes via `asdict()` for subagent dispatch (initialize message). For teammate path, only `SdkTeammate._run` reads — that's where SC-5/SC-6 wiring goes.
- `merge_packs` consumers: only `build_merged_pack`. SC-11 hooks live there.
- `spawn_teammate` consumers: lead sessions via MCP. SC-4 validation is at the public boundary.

No cross-feature surfaces touched. Mission Control dashboard, transcript sink, broker registry, telemetry pipeline (#14, #19, #22), token cost (#14) — all unaffected.

### Assumptions

*Default-accept semantics. Silence = accept. Call out wrong assumptions during refinement.*

- **A-1: `~/.claude.json`'s top-level `mcpServers` key is the authoritative registry for string-name resolution.** *Default:* yes — this is what the SDK CLI loads via `--setting-sources=user`. *Rationale:* CLAUDE.md explicitly documents "MCP servers must be in user-level config"; `~/.claude.json` already contains `atlassian` and `claude-crew` as production servers. Project-level `.claude/` MCP config is documented as not loaded by SDK teammates.

- **A-2: Inline-dict `mcpServers` entries adopting a `"name"` key as a convention does not conflict with `McpServerConfig` schema.** *Default:* `McpServerConfig` is a TypedDict union of stdio/sse/http/sdk — none of which uses a top-level `name` key (servers are named by their dict key). Adding `name` at the entry level for our list-form is a layered convention, stripped before the dict reaches `ClaudeAgentOptions.mcp_servers`. *Rationale:* read of types.py:582-668; no field collision.

- **A-3: `_resolve_mcp_servers` parsing `~/.claude.json` lazily per spawn is fast enough.** *Default:* yes — typical file is <50KB, parsed once per teammate (one-shot at spawn). No runtime hot path. *Rationale:* spawn already does multiple disk + subprocess operations; one JSON parse is negligible.

- **A-4: Test isolation via `monkeypatch.setenv("HOME", tmp_path)` works for pack-merge and resolve tests.** *Default:* yes — `_load_user_mcp_servers` reads `Path.home() / ".claude.json"`. Tests can plant a fake file under `tmp_path / ".claude.json"`. *Rationale:* same pattern used in `test_user_loader.py::TestWarnUnknownSkills`.

- **A-5: `ToolError` import path is `mcp.server.fastmcp.exceptions.ToolError`.** *Default:* verified by Sentinel reading `mcp/server/fastmcp/tools/base.py:117`. *Rationale:* FastMCP catches non-`ToolError` exceptions and re-wraps them in `"Error executing tool ..."`, losing the message structure. Direct `ToolError` raise preserves the message.

### Open Questions

*All resolved during Phase 1 + Phase 2 synthesis + Sentinel review. No must-answers blocking Phase 3.*

- ✅ Q-5 translation strategy — D-4.
- ✅ Additive vs exclusive — D-5.
- ✅ Inline-dict naming + `sdk`-type rejection — D-7 (revised after Sentinel MF-1).
- ✅ Shadow-drop scope — D-10 (excludes `tools` per Sentinel SF-2).
- ✅ Test isolation for `_resolve_mcp_servers` — D-11 (revised with explicit `home_dir` param after Sentinel MF-3).
- ✅ Teammate-path failure asymmetry for malformed inline dicts — D-12 (added per Sentinel MF-2).
- ✅ Operator-ergonomic breadcrumb for inline-dict pass-through — D-13 (added per co-architect Miss-3).
- ✅ Helper home/return-shape split for `_load_user_mcp_server_names` (set) vs `_load_user_mcp_servers` (dict) — explicit contract table in Data/API Contracts (per co-architect Miss-1).
- ✅ Load-time vs spawn-time race on `~/.claude.json` — Edge Case 16 (per co-architect Miss-2).
- ⚠ Name-collision tie-break (pack vs `~/.claude.json`) — empirically settled by SC-5(d) live SDK test, not pre-determined; D-5 documents whichever wins.

**Gate**:
- ✅ Design clear and justifiable — three layers (A/B/C), no architecture changes, all extensions additive
- ✅ Spec comprehensive — 11 SCs, 11 design decisions with carried-into pointers, 12 edge cases enumerated, 8 handoff boundary contracts
- ✅ ALL edge cases listed
- ✅ Error handling specified — `PackLoadError` for pack-load, `ToolError` for MCP boundary, WARN+skip for runtime resolution
- ✅ Validation contracts at every handoff boundary (8-row table)
- ✅ Cross-feature integration check complete — verified by reading `PackFrontmatter`/`AgentDefinition`/`merge_packs` consumers, no cross-feature surface impact
- ✅ No new architecture decisions warranting `.claude/rules/` capture (this feature is parameter parity, not new architecture)
- ✅ Implementable by someone with no additional context

---

## Phase 3: Task Breakdown

Five tasks, three layers (A/B/C) plus a small MCP-boundary fix plus a unified E2E. T1–T3 are independent; T4 depends on T1 (needs `PackFrontmatter.mcpServers` to consume); T5 depends on all.

---

### Task T1: PackFrontmatter extension + shallow validation (Layer A)

**Depends on**: None | **Blocks**: T4, T5

**Scope**: Add `mcpServers` and `memory` to `PackFrontmatter`. Extend `_validate_frontmatter` per D-1, D-2, D-7. Forward to `AgentDefinition` in `parse_pack_text`. Confirm `_ACCEPTED_FRONTMATTER_KEYS` propagates structurally via `__dataclass_fields__`. Touched: `claude_crew/subagents/_loader.py`, `tests/test_pack_loader.py`, `tests/test_subagents.py`, `tests/test_user_loader.py`.

**Acceptance Criteria** (covers SC-1, SC-2, SC-3, SC-8):

```
Scenario: Pack declares mcpServers with both string and inline-dict entries
  Given a pack file with `mcpServers: ["atlassian", {type: "stdio", name: "local", command: "uv"}]`
  When parse_pack_text loads it
  Then PackFrontmatter.mcpServers == ("atlassian", {"type": "stdio", "name": "local", "command": "uv"})
  And the constructed AgentDefinition.mcpServers contains both entries (as list)

Scenario: Pack declares memory with valid value
  Given a pack file with `memory: "project"`
  When parse_pack_text loads it
  Then PackFrontmatter.memory == "project"
  And the constructed AgentDefinition.memory == "project"

Scenario: Pack declares memory with invalid value
  Given a pack file with `memory: "global"`
  When _validate_frontmatter runs
  Then PackLoadError is raised with message naming "memory", "global", and {"user", "project", "local"}

Scenario: Pack declares mcpServers as a non-list
  Given a pack file with `mcpServers: "atlassian"` (string instead of list)
  When _validate_frontmatter runs
  Then PackLoadError is raised naming the field

Scenario: Pack declares mcpServers with sdk-type inline dict
  Given a pack file with `mcpServers: [{type: "sdk", name: "x"}]`
  When _validate_frontmatter runs
  Then PackLoadError is raised naming the index, "sdk", and the not-supported reason

Scenario: Pack declares mcpServers with unknown dict type
  Given a pack file with `mcpServers: [{type: "websocket"}]`
  When _validate_frontmatter runs
  Then PackLoadError is raised naming the index, "websocket", and accepted types

Scenario: User-pack with mcpServers and memory does NOT emit "unsupported key" warnings
  Given a user pack file declaring both new fields validly
  When _user_loader.strict_parse loads it
  Then no logger.warning is emitted on the "unsupported frontmatter key" message
```

**Verification**: `uv run pytest tests/test_pack_loader.py tests/test_subagents.py tests/test_user_loader.py -v` — new tests fail before implementation, pass after.

---

### Task T2: Pack-merge layer warnings — `_warn_unknown_mcp_servers` + `_warn_shadow_drop` (Layer B)

**Depends on**: None (independent of T1; reads `getattr(agent, "mcpServers", None)` which is `None` until T1 ships, so the WARN paths are effectively dormant pre-T1 — but the helpers and tests can land independently) | **Blocks**: T5

**Scope**: Add helper `_load_user_mcp_server_names(home_dir) -> set[str]` to `_user_loader.py`. Add `_warn_unknown_mcp_servers(merged, home_dir)` mirroring `_warn_unknown_skills`. Add `_warn_shadow_drop(default, user, project)` and `_check_drop` and `_OPTIONAL_AGENTDEF_FIELDS` constant per D-9, D-10, SF-2. Wire both into `build_merged_pack` after the existing `_warn_unknown_skills` call. Touched: `claude_crew/subagents/_user_loader.py`, `tests/test_user_loader.py`.

**Acceptance Criteria** (covers SC-7, SC-11):

```
Scenario: A pack declares an mcpServers string-name not in ~/.claude.json
  Given build_merged_pack runs with home_dir containing a .claude.json without "ghost-server"
  And a pack declares `mcpServers: ["ghost-server"]`
  When build_merged_pack completes
  Then a WARN is logged on "claude_crew.subagents.loader" naming the role and "ghost-server"

Scenario: A pack declares an mcpServers string-name THAT IS in ~/.claude.json
  Given .claude.json registers "atlassian"
  And a pack declares `mcpServers: ["atlassian"]`
  When build_merged_pack completes
  Then NO WARN is logged for that entry

Scenario: A pack declares only inline-dict mcpServers entries
  Given a pack with `mcpServers: [{type: "stdio", name: "x", command: "y"}]`
  When build_merged_pack completes
  Then NO WARN is logged for that pack (inline dicts are self-contained)

Scenario: ~/.claude.json missing or malformed
  Given home_dir has no .claude.json
  And a pack declares `mcpServers: ["any-name"]`
  When build_merged_pack completes
  Then a WARN is logged for "any-name"

Scenario: Project pack drops mcpServers that bundled pack set
  Given bundled "explorer" has mcpServers=["atlassian"]
  And a project-level "explorer.md" omits mcpServers
  When build_merged_pack runs
  Then a shadow-drop WARN names role="explorer", field="mcpServers", layer="project"

Scenario: Project pack drops memory that bundled pack set
  Given bundled role has memory="project"
  And a project-level shadow omits memory
  When build_merged_pack runs
  Then a shadow-drop WARN names the field

Scenario: Higher-precedence pack sets explicit empty (not None)
  Given lower has disallowedTools=["Bash"]
  And higher has disallowedTools=[]
  When build_merged_pack runs
  Then NO shadow-drop WARN fires (explicit-empty is not a drop)

Scenario: Multiple optional fields drop in one role
  Given lower has skills=["a"] AND memory="project"
  And higher omits both
  When build_merged_pack runs
  Then TWO shadow-drop WARNs fire, one per field

Scenario: Required fields are not subject to shadow-drop checks
  Given any pack pair where description/model differ in presence
  Then no shadow-drop WARN fires for these fields (they're required, not optional)

Scenario: _OPTIONAL_AGENTDEF_FIELDS covers exactly the 9 D-10 fields
  Given the _OPTIONAL_AGENTDEF_FIELDS constant
  Then it set-equals {"mcpServers", "memory", "skills", "disallowedTools",
                      "permissionMode", "maxTurns", "background", "initialPrompt", "effort"}
  (Guards against drift — if a future SDK upgrade adds a new optional AgentDefinition
   field, this test fails and forces a deliberate inclusion/exclusion decision.
   Mirrors the "stale regression-guard" failure mode caught on #22 retro.)
```

**Verification**: `uv run pytest tests/test_user_loader.py -v` — new test classes pass; existing `TestShadowingObservability` still passes.

---

### Task T3: MCP boundary validation for `permission_mode`

**Depends on**: None | **Blocks**: T5

**Scope**: Import `ToolError` from `mcp.server.fastmcp.exceptions`. Add `_VALID_PERMISSION_MODES` frozenset (the 6 SDK literals). Add validation block at the top of `spawn_teammate` raising `ToolError` per D-3. Touched: `claude_crew/server.py`, `tests/test_server_sdk_mode.py`.

**Acceptance Criteria** (covers SC-4):

```
Scenario: spawn_teammate called with invalid permission_mode via FastMCP dispatch
  Given an MCP client call to spawn_teammate with permission_mode="superuser"
  When the FastMCP dispatch routes the call
  Then a ToolError surfaces at the protocol boundary
  And the error message names "permission_mode", "superuser", and the 6 accepted PermissionMode literals
  And no broker.spawn_teammate / factory invocation occurred

Scenario: spawn_teammate called with valid permission_mode
  Given an MCP client call with permission_mode="plan"
  Then the call succeeds (no ToolError)
  And the existing test_spawn_with_cwd_and_permission_mode_reaches_options assertions still hold

Scenario: spawn_teammate called with permission_mode=None
  Given an MCP client call without permission_mode
  Then no validation triggers
  And the existing fall-back-to-pack behavior works
```

**Verification**: `uv run pytest tests/test_server_sdk_mode.py -v` — new test for invalid PM passes; existing valid-PM test still passes.

---

### Task T4: Teammate-path translation — `_resolve_mcp_servers` + `_run` wiring (Layer C)

**Depends on**: T1 (consumes `role_def.mcpServers` and `role_def.memory`) | **Blocks**: T5

**Scope**: Add helper `_load_user_mcp_servers(home_dir) -> dict[str, dict[str, Any]]` to `sdk_teammate.py`. Add module-level `_resolve_mcp_servers(entries, role, teammate_id, home_dir=None)` per D-4, D-7, D-11, D-13. Extend the inline options-builder in `_run` (currently around `sdk_teammate.py:880-904`) with two branches: mcpServers translation + memory WARN. Touched: `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py` (or extend `test_server_sdk_mode.py` if that's the existing pattern).

**Acceptance Criteria** (covers SC-5(a/b/c), SC-6):

```
Scenario: Teammate spawned with pack declaring inline-dict mcpServers
  Given a pack with `mcpServers: [{type: "stdio", name: "local-x", command: "uv"}]`
  When SdkTeammate._run builds ClaudeAgentOptions
  Then opts_kwargs["mcp_servers"] == {"local-x": {"type": "stdio", "command": "uv"}}
  And the "name" key is stripped from the value
  And an INFO breadcrumb is logged naming role, teammate_id, "local-x", "stdio" (D-13)

Scenario: Teammate spawned with pack declaring string-name mcpServers (resolves)
  Given home_dir has .claude.json registering "atlassian" with config {"type": "http", "url": "..."}
  And a pack with `mcpServers: ["atlassian"]`
  When SdkTeammate._run builds ClaudeAgentOptions with home_dir=tmp_path
  Then opts_kwargs["mcp_servers"] == {"atlassian": {"type": "http", "url": "..."}}
  (The resolved value is the VERBATIM dict from ~/.claude.json's mcpServers["atlassian"];
   no field stripping or transformation — only inline-dict entries strip "name" per D-7.)

Scenario: Teammate spawned with pack declaring string-name mcpServers (unresolvable)
  Given home_dir has .claude.json without "ghost"
  And a pack with `mcpServers: ["ghost"]`
  When SdkTeammate._run builds ClaudeAgentOptions
  Then opts_kwargs["mcp_servers"] == {} (or omitted)
  And a WARN is logged naming role, teammate_id, "ghost"
  And the spawn does not fail

Scenario: Teammate spawned with pack declaring mixed entries
  Given a pack with `mcpServers: ["atlassian", {type: "stdio", name: "local", command: "uv"}]`
  When SdkTeammate._run builds ClaudeAgentOptions
  Then opts_kwargs["mcp_servers"] contains both entries with correct shapes

Scenario: Teammate spawned with pack declaring memory
  Given a pack with `memory: "local"`
  When SdkTeammate._run builds ClaudeAgentOptions
  Then NO `memory` key is added to opts_kwargs
  (because ClaudeAgentOptions has no `memory` field — see D-8; subagent path
   carries the value via AgentDefinition serialization, but teammate path has no carrier.)
  And a WARN is logged naming the role, teammate_id, and the value (SC-6)

Scenario: home_dir injection for test isolation
  Given a test plants ~/.claude.json under tmp_path
  When _resolve_mcp_servers is called with home_dir=tmp_path
  Then resolution reads from tmp_path/.claude.json, not the real ~/.claude.json
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py tests/test_server_sdk_mode.py -v` — new tests fail before T4, pass after.

---

### Task T5: End-to-end integration tests + live SDK probe + PRODUCT-VISION update

**Depends on**: T1, T2, T3, T4 | **Blocks**: documentation/Phase-5

**Scope**: Cohesive E2E tests through the public surface (`spawn_teammate` MCP tool + pack-merge lifecycle). One live SDK test under `CLAUDE_CREW_LIVE_TESTS=1` for SC-5(d) including name-collision verification. Update PRODUCT-VISION row 191 to reflect actual scope (SC-10). Update README/docstrings for new pack fields if user-facing behavior changed (SC-9 discipline). Touched: `tests/test_e2e_pack_parity.py` (new), `tests/test_live_pack_mcp.py` (new), `doc/PRODUCT-VISION.md`, `claude_crew/subagents/_loader.py` (PackFrontmatter docstrings).

**Happy Path Scenarios**:

```
Scenario: Full lifecycle — pack with both new fields → spawn → ClaudeAgentOptions
  Given a project-level pack at .claude/agents/test-role.md declares:
    mcpServers: ["atlassian", {type: "stdio", name: "local-x", command: "uv"}]
    memory: "project"
  And ~/.claude.json registers "atlassian"
  When the MCP server starts (build_merged_pack runs) AND a teammate is spawned for "test-role"
  Then no load-time WARN fires for "atlassian"
  And no shadow-drop WARN fires (project pack doesn't shadow anything)
  And SdkTeammate._run produces ClaudeAgentOptions with:
    mcp_servers == {"atlassian": <atlassian config>, "local-x": {"type": "stdio", "command": "uv"}}
  And a memory-WARN is logged naming the role
  And an INFO breadcrumb is logged for the local-x inline-dict

Scenario: Multi-source merge — bundled + user + project, all valid
  Given bundled has explorer with skills=["a"]
  And user-level shadow has explorer adding mcpServers=["atlassian"]
  And no project shadow exists
  When build_merged_pack runs
  Then merged["explorer"].mcpServers == ["atlassian"]
  And merged["explorer"].skills is NOT inherited (whole-replacement); a shadow-drop WARN fires for skills
```

**Live-probe checklist**:
- [x] Live test (SC-5d) plants no UUID/secret in agent prompt; tests behavioral side effects (server reachable, server invocations succeed) not narrated outcomes.
- [x] Tool-name correctness verified by observable side effect — assert that an MCP tool from the named server is callable, not by agent narration.
- [x] No assertions on token counts or workload-sensitive values.

**Sad Path Scenarios**:

```
Scenario: spawn_teammate rejects invalid permission_mode at MCP boundary
  When MCP client calls spawn_teammate with permission_mode="invalid"
  Then ToolError surfaces at the protocol boundary
  And no broker spawn occurred (broker.list_crew shows no new teammate)
  And no factory invocation occurred (no SdkTeammate constructor call)

Scenario: Pack with malformed inline-dict mcpServers spawns teammate that dies
  Given a pack with `mcpServers: [{type: "stdio", name: "broken"}]` (missing command)
  When the teammate is spawned and its _run begins
  Then ClaudeAgentOptions is constructed with the malformed dict (validation accepted shallow shape)
  And the SDK CLI subprocess crashes
  And the teammate is tombstoned via the existing death path
  And the D-13 INFO breadcrumb appears in the log preceding the death
  (This scenario is covered in stub mode — we don't actually run the failing CLI; we assert the dict reaches opts_kwargs and rely on the existing death-path tests for the rest.)

Scenario: Shadow-drop on optional field surfaces WARN
  Given bundled "explorer" has skills=["a"], mcpServers=["atlassian"]
  And project shadow declares only description+model+tools (drops both)
  When build_merged_pack runs
  Then TWO shadow-drop WARNs fire (one per field), each naming layer="project"
```

**Live SDK Scenarios** (SC-5d, gated by `CLAUDE_CREW_LIVE_TESTS=1`):

```
Scenario: Pack mcpServers string-name produces a reachable server in the teammate
  Given ~/.claude.json registers "atlassian"
  And a test pack declares mcpServers: ["atlassian"]
  When a teammate is spawned and asked to invoke an atlassian MCP tool
  Then the tool invocation succeeds (or fails with an atlassian-specific error, NOT "server not found")

Scenario: Name collision — same name in pack and ~/.claude.json
  Given ~/.claude.json registers "test-server" with config A
  And the spawn passes mcp_servers={"test-server": configA} via _resolve_mcp_servers
  When the teammate session initializes (with --setting-sources=user,project AND --mcp-config inline)
  Then a known tool on test-server is invocable from the teammate session
  And the response succeeds (or fails with a server-specific error, NOT "server not found"
   or "duplicate server")
  (Asserting "exactly one" instance via introspection is not feasible — there is no SDK
   surface that exposes loaded-server count. The behavioral assertion is "the server works
   under that name." Whichever config wins on tie-break is captured by post-hoc inspection
   of the response shape and recorded in D-5 retroactively.)
```

**Verification**: `uv run pytest tests/test_e2e_pack_parity.py -v` (full suite) and `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_pack_mcp.py -v` (live probe).

**PRODUCT-VISION row 191 update** — reword the Notes column from "missing mcpServers, permissionMode, disallowedTools, memory" to "missing mcpServers, memory; tightened MCP-boundary validation for permission_mode; both subagent and teammate paths wired; warn-on-shadow-drop for optional AgentDefinition fields. Reference: doc/features/FEATURE-agent-definition-parity.md."

---

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ T5 is the dedicated E2E task with explicit happy + sad + live-probe coverage
- ✅ Verification commands fail without the feature (new tests use new fields/branches)
- ✅ Each Phase 2 edge case (1-16) traces to at least one Phase 3 scenario or is explicitly marked observability-only (12, 13, 16)
- ✅ Cross-feature integration check: `_warn_shadow_drop` covers all 9 optional AgentDefinition fields uniformly per D-10; `_resolve_mcp_servers` covers both forms per D-7
- ✅ User approves before T1 begins

---

## Phase 4: Implementation

*To be filled after Phase 3.*

---

## Phase 5: Completion

*To be filled at the end.*
