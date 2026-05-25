# Spec: per-teammate-model-routing

## Problem

A claude-crew lead today cannot run a mixed crew where some teammates route
through a local-model backend (via `claude-code-router` / `ccr`) while the lead
and other teammates continue to hit the Anthropic API. SDK teammates inherit
the parent process `os.environ`, so routing is all-or-nothing per crew process:
either `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` / `CLAUDE_CODE_ATTRIBUTION_HEADER`
are set for the whole process and every teammate hits the local backend, or
they aren't set and nobody does. The user-visible outcome is a lead that can
spawn one teammate routed to a local backend (cheap, parallelizable mechanical
work) and another routed to Anthropic (judgment-heavy reasoning) in the **same**
crew, without restarting the MCP server.

## Architecture Overview

The change is a thread-through of one optional parameter — a per-spawn `env`
dict — from the MCP boundary down to the SDK options. Three call sites carry
the parameter:

1. `claude_crew/server.py::spawn_teammate` (MCP tool surface). New optional
   `env: dict[str, str] | None` arg and a new optional `local_backend: bool`
   convenience flag (or named-bundle shape — see Design Decisions). Forwards
   `env` to `broker.spawn_teammate`.
2. `claude_crew/broker.py::Broker.spawn_teammate` (factory dispatch). New
   optional `env` kwarg appended to the existing signature
   (`role, name, factory, model=, effort=, cwd=, permission_mode=, ...`).
   Forwards `env` to the factory.
3. `claude_crew/factories.py::sdk_factory` + the inner `factory()` produced
   by `default_factory()`. New optional `env` kwarg. Forwards into
   `SdkTeammate(..., env=...)`.

`SdkTeammate.__init__` gains an optional `env: dict[str, str] | None` kwarg
stored on `self._env`. Inside `_run()`, the existing hardcoded
`opts_kwargs["env"] = {"CLAUDE_CREW_UI_PORT": "0",
"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}` is merged with `self._env` (caller
keys win on conflict — never silently drop a caller's override; do log a
warning if caller overrides either of the two crew-defaults since both exist
for a reason). The merged dict is passed to `ClaudeAgentOptions(env=...)`.

The "local backend" preset is a small, pure helper in a new module
`claude_crew/local_backend.py` (or as a private helper in `server.py` — see
Open Questions) that returns the canonical three-var env dict for ccr routing:

```python
def local_backend_env(
    base_url: str = "http://127.0.0.1:3456",
    api_key: str = "sk-local-no-key-required",
) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    }
```

The MCP `spawn_teammate` tool accepts `env: dict[str, str] | None` AND
`local_backend: bool | dict | None`. When `local_backend` is truthy the tool
expands it via `local_backend_env()` (optionally with `base_url` / `api_key`
overrides when passed as a dict) and merges with any explicit `env` (explicit
`env` keys win, so a caller can override one var of the preset). The generic
`env` override remains the underlying primitive — the preset is a named
bundle on top.

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| MCP tool | `claude_crew/server.py::spawn_teammate` | `async def(... env=None, local_backend=None)` | Operator-facing; accepts preset + explicit env |
| Broker | `claude_crew/broker.py::Broker.spawn_teammate` | `async def(... env: dict[str,str]|None = None)` | Pure pass-through; no merging, no preset awareness |
| Factory | `claude_crew/factories.py::sdk_factory` and `factory()` inner | `def(... env: dict[str,str]|None = None)` | Pure pass-through to SdkTeammate ctor |
| SdkTeammate ctor | `claude_crew/sdk_teammate.py::SdkTeammate.__init__` | stores on `self._env` | Stub teammate gets the kwarg too (factory uniformity) — but ignores it |
| SdkTeammate `_run` | merges `self._env` into the hardcoded `opts_kwargs["env"]` | merge with caller-wins | Single place that actually reaches the SDK |

All call sites are structurally identical (sync/async, kwarg style) — single
polymorphic helper not needed; the parameter just rides through.

## Data / API Contracts

```python
# claude_crew/server.py — MCP tool signature
@mcp.tool()
async def spawn_teammate(
    role: str,
    name: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    cwd: str | None = None,
    permission_mode: str | None = None,
    extra_tools: list[str] | None = None,
    extra_skills: list[str] | None = None,
    env: dict[str, str] | None = None,             # NEW — raw env override
    local_backend: bool | dict | None = None,      # NEW — named preset
) -> dict[str, Any]: ...

# claude_crew/broker.py
async def spawn_teammate(
    self,
    role: str,
    name: str | None,
    factory: TeammateFactory,
    model: str | None = None,
    effort: str | None = None,
    cwd: str | None = None,
    permission_mode: str | None = None,
    agent_def_resolver: AgentDefResolver | None = None,
    extra_tools: list[str] | None = None,
    extra_skills: list[str] | None = None,
    env: dict[str, str] | None = None,             # NEW
) -> str: ...

# claude_crew/factories.py
def sdk_factory(
    id, name, role, *,
    model=None, effort=None, agents=None, pack_bodies=None,
    cwd=None, permission_mode=None, setting_sources=None,
    allowed_tools=None, extra_tools=None, extra_skills=None,
    env: dict[str, str] | None = None,             # NEW
) -> Teammate: ...

# claude_crew/sdk_teammate.py
class SdkTeammate(Teammate):
    def __init__(
        self, id, name, role, *,
        model="claude-sonnet-4-6",
        effort=None,
        system_prompt=None,
        setting_sources=None,
        agents=None, pack_bodies=None,
        cwd=None, permission_mode=None, allowed_tools=None,
        env: dict[str, str] | None = None,         # NEW — stored on self._env
    ) -> None: ...

# claude_crew/local_backend.py (NEW module)
def local_backend_env(
    base_url: str = "http://127.0.0.1:3456",
    api_key: str = "sk-local-no-key-required",
) -> dict[str, str]:
    """Return the three env vars required to route a teammate through ccr."""
```

Env-merge semantics inside `SdkTeammate._run()`:
```
merged = {**CREW_DEFAULTS, **(self._env or {})}
# CREW_DEFAULTS = {"CLAUDE_CREW_UI_PORT": "0", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# Caller keys win. If caller overrides either default key, emit a WARN log
# naming the key and the new value (operator visibility, not a hard error).
opts_kwargs["env"] = merged
```

## Design Decisions

- **Caller env wins on conflict with crew defaults** — *Rationale:* the user
  may legitimately want to enable auto-memory or set the UI port for a single
  teammate; silently dropping their override would be worse than warning them.
  Defaults exist for sound reasons (the docstring at lines 1144-1156 of
  `sdk_teammate.py` explains both), so a WARN log accompanies the override.
  — *Carried into:* `SdkTeammate._run` env-merge block; test
  `test_env_caller_override_wins_with_warn`.
- **`env=None` (default) preserves today's exact behavior** — *Rationale:* this
  is the non-regression contract. A teammate spawned without `env` must pass
  the identical `opts_kwargs["env"]` dict that ships today (the two-key
  crew-defaults dict), unchanged in keys, values, or order. — *Carried into:*
  test `test_no_env_override_preserves_crew_defaults`.
- **Preset is a named bundle, not a parallel path** — *Rationale:* the idea
  brief is explicit ("the preset is a named bundle, not a parallel code path").
  `local_backend` at the MCP boundary expands via `local_backend_env()` to a
  dict, then merges with explicit `env` (explicit wins), then takes the same
  path as raw `env` through broker/factory/teammate. No code below the MCP
  boundary knows the preset exists. — *Carried into:* `local_backend.py`
  helper + `server.py` expansion site; tests
  `test_local_backend_preset_expands_to_three_vars` and
  `test_local_backend_with_explicit_env_explicit_wins`.
- **Broker and factory layers are pure pass-through** — *Rationale:* SRP. The
  merge logic lives in exactly one place (`SdkTeammate._run`) so there's one
  source of truth for "what env reaches the SDK." Broker/factory don't read or
  mutate the dict — they hand it through. — *Carried into:* signature
  additions only in `broker.py` / `factories.py`; tests
  `test_broker_spawn_teammate_threads_env_to_factory` and
  `test_sdk_factory_threads_env_to_sdk_teammate`.
- **Stub teammate ignores `env`** — *Rationale:* factory signature uniformity
  (matches how stub ignores `model`/`effort`/`cwd` today). Stub mode tests
  exercise the broker→factory thread-through without depending on the SDK.
  — *Carried into:* `stub_factory` signature gains `env=None`; tested by
  `test_broker_spawn_teammate_threads_env_to_factory` (stub-mode factory spy).
- **No live local-backend exercise in this slice** — *Rationale:* hard gate
  from the idea brief. All validation runs against stub mode and against
  `ClaudeAgentOptions` construction; no llama.cpp/ccr boot. — *Carried into:*
  `## Test Command` (stub-only), `## Validation` (assertions, not network).
- **`env` value type enforced as `dict[str, str]`** — *Rationale:* SDK
  `ClaudeAgentOptions.env` requires string values; non-string values would
  fail at subprocess spawn with a confusing error. Validate at the MCP
  boundary (raise `ToolError`) and at the `SdkTeammate.__init__` boundary
  (raise `TypeError`). — *Carried into:* `server.py` validation block;
  test `test_env_non_string_value_rejected_at_mcp_boundary`.

## Edge Cases

- `env=None` (no override) — must be byte-identical to today's behavior.
- `env={}` (empty dict) — same as `None` semantically (no override). Treat as
  no-op; do NOT clobber crew-defaults to an empty dict.
- `env={"FOO": "bar"}` — merged in addition to crew defaults; both reach SDK.
- `env={"CLAUDE_CREW_UI_PORT": "9000"}` — caller wins, WARN logged.
- `env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"}` — caller wins, WARN logged.
- `env={"FOO": 123}` (non-string value) — rejected at MCP boundary with
  `ToolError`; at SDK-teammate ctor with `TypeError`.
- `env={"": "x"}` (empty-string key) — rejected at MCP boundary.
- `local_backend=True` — expands to default three-var preset.
- `local_backend={"base_url": "http://192.168.1.5:3456"}` — expands with
  override.
- `local_backend=True` AND `env={"ANTHROPIC_API_KEY": "sk-real-thing"}` —
  explicit `env` key wins over preset value; final dict has the explicit key.
- `local_backend=False` or `None` — preset path not taken; identical to
  omitting the arg.
- Stub mode + `env=...` — the env arg propagates through broker→factory→
  StubTeammate (which ignores it). No crash.

UI/data display surface — not applicable (this feature ships no UI changes;
the dashboard does not surface per-teammate env today and that surfacing is
explicitly Out of Scope).

Data retirement/expiration — not applicable.

## Acceptance Tests

1. **Given** a `SdkTeammate` constructed with `env=None`, **when** `_run()`
   builds `opts_kwargs`, **then** `opts_kwargs["env"]` equals
   `{"CLAUDE_CREW_UI_PORT": "0", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}`
   exactly — same keys, same values, no extras (non-regression of the
   inherited-env default).

2. **Given** a `SdkTeammate` constructed with
   `env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
   "ANTHROPIC_API_KEY": "sk-local", "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}`,
   **when** `_run()` builds `opts_kwargs`, **then** `opts_kwargs["env"]`
   contains all three caller keys with the caller-supplied values AND the two
   crew-default keys, and the resulting `ClaudeAgentOptions(env=...)` reflects
   the merged dict.

3. **Given** a `SdkTeammate` constructed with
   `env={"CLAUDE_CREW_UI_PORT": "9000"}`, **when** `_run()` builds `opts_kwargs`,
   **then** `opts_kwargs["env"]["CLAUDE_CREW_UI_PORT"] == "9000"` (caller wins)
   AND a WARN log is emitted naming the overridden key.

4. **Given** the broker's `spawn_teammate` is called with `env={"FOO": "bar"}`
   and a spy factory, **when** the factory is invoked, **then** the factory
   receives `env={"FOO": "bar"}` as a kwarg (broker is pure pass-through). The
   spy returns a stub teammate so the call completes without the SDK.

5. **Given** the broker's `spawn_teammate` is called with `env=None`
   and a spy factory, **when** the factory is invoked, **then** the factory
   either is NOT passed an `env` kwarg or is passed `env=None` (default
   propagation; no spurious empty-dict).

6. **Given** `sdk_factory(..., env={"X": "y"})` is called directly with a
   monkey-patched `SdkTeammate` constructor, **when** the constructor runs,
   **then** it receives `env={"X": "y"}` as a kwarg and stores it on
   `self._env`.

7. **Given** the MCP `spawn_teammate` tool is called with `env={"FOO": 123}`
   (non-string value), **when** the tool body validates, **then** it raises
   `ToolError` naming the offending key, AND the broker is not called.

8. **Given** the MCP `spawn_teammate` tool is called with `local_backend=True`
   and no explicit `env`, **when** the tool resolves the preset, **then**
   `broker.spawn_teammate` is called with
   `env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
   "ANTHROPIC_API_KEY": "sk-local-no-key-required",
   "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}`.

9. **Given** the MCP `spawn_teammate` tool is called with `local_backend=True`
   AND `env={"ANTHROPIC_API_KEY": "sk-override"}`, **when** the tool resolves
   the preset and merges, **then** `broker.spawn_teammate` is called with an
   `env` dict where `ANTHROPIC_API_KEY == "sk-override"` and the other two
   preset keys retain their preset values.

10. **Given** `local_backend_env(base_url="http://1.2.3.4:9999",
    api_key="sk-custom")` is called directly, **when** it returns, **then** the
    dict equals
    `{"ANTHROPIC_BASE_URL": "http://1.2.3.4:9999",
    "ANTHROPIC_API_KEY": "sk-custom",
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}`.

## Test Command

No new third-party dependencies are introduced; all new tests import from
`claude_crew`, `pytest`, and the stdlib only — all present in `pyproject.toml`.
No system-level prerequisites: stub mode is the default (set by
`tests/conftest.py`) and no SDK, network, or local-backend services are
required.

```bash
uv run pytest tests/test_per_teammate_env.py tests/test_broker.py tests/test_factories.py tests/test_local_backend.py -q
```

## Out of Scope

- Live exercise against a running ccr / llama.cpp instance. **Hard gate:** if
  anyone believes this is needed, escalate to the operator (Jerome) before
  running — he controls when the local model is up.
- Surfacing per-teammate env (or local-backend status) on the Mission Control
  dashboard.
- ccr config management, ccr provider switching, multiple concurrent local
  servers, or any llama.cpp/ccr operator infrastructure.
- Per-teammate model-NAME routing through ccr (the model string is cosmetic
  under ccr today; this feature is about `env`/`base_url`, not model selection).
- Crew-size enforcement against local-backend slot count (a planning concern
  noted in the idea brief but not enforced in this slice).
- Allowing the caller to fully replace the crew-defaults dict (caller may
  override individual keys; caller cannot remove crew-default keys).

## Assumptions

- **Module location for the preset** — *Default:* new module
  `claude_crew/local_backend.py` exporting `local_backend_env()`. *Rationale:*
  one-function module keeps `server.py` from growing; matches the existing
  pattern (`auth.py`, `redaction.py`, `envelope.py` are similarly small).
- **Preset default base_url** — *Default:* `http://127.0.0.1:3456` (ccr's
  documented default port). *Rationale:* matches the validated foundation in
  the idea brief.
- **Preset default api_key** — *Default:* `sk-local-no-key-required`.
  *Rationale:* matches the brief; any non-empty value satisfies
  `claude_crew/auth.py`.
- **Validation strictness for `env` keys/values** — *Default:* reject
  non-string values and empty-string keys at the MCP boundary with `ToolError`;
  do NOT validate variable names against a known-good list (operator may set
  arbitrary env vars). *Rationale:* fail-fast on shape errors; trust the
  operator on content.
- **WARN log when caller overrides a crew-default key** — *Default:* emit a
  `logger.warning(...)` naming the key, but do NOT block the override.
  *Rationale:* the brief and the existing docstring both treat these defaults
  as load-bearing; visibility is the right level of friction.
- **`env={}` semantics** — *Default:* treat as `None` (no-op). *Rationale:*
  caller passing an empty dict almost certainly didn't mean "wipe my
  crew-defaults"; explicit override would name a key.
- **MCP-boundary shape of `local_backend`** — *Default:* `bool | dict | None`.
  `True` uses preset defaults; a dict with optional `base_url` / `api_key`
  customizes; `False`/`None` is no-op. *Rationale:* one tool arg covers both
  the zero-config common case and the customized case; avoids a second
  parameter.

## Open Questions

- (none)

## Validation

The implementor's slice tests double as the validation gate for this feature:
every promised behavior (non-regression of the inherited-env default,
thread-through at each layer, preset expansion, merge precedence) has a
deterministic stub-mode test. The full-suite invocation below proves no
cross-cutting regression in adjacent suites (notably `test_sdk_teammate.py`,
`test_factories.py`, `test_broker.py`, which all touch the modified
signatures).

```bash
uv run pytest -q
```

## Task Breakout

```yaml
tasks:
  - name: local-backend-preset-helper
    description: |
      Create `claude_crew/local_backend.py` exporting `local_backend_env(base_url, api_key)`
      that returns the three-var dict (ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY,
      CLAUDE_CODE_ATTRIBUTION_HEADER="0"). Add `tests/test_local_backend.py` covering
      AT 10 (default args + custom args).
    dependsOn: []
    acceptanceTests: [10]
    taskTouches:
      - "claude_crew/local_backend.py"
      - "tests/test_local_backend.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_local_backend.py -q

  - name: sdk-teammate-env-merge
    description: |
      Add `env: dict[str, str] | None = None` kwarg to `SdkTeammate.__init__`;
      store on `self._env`. In `_run()`, replace the hardcoded `opts_kwargs["env"]`
      assignment with a merge: `{**CREW_DEFAULTS, **(self._env or {})}` where
      caller keys win and a WARN log fires if caller overrides a CREW_DEFAULTS
      key. Validate that non-string values raise `TypeError` in `__init__`.
      Add tests covering AT 1 (no-override regression), AT 2 (three-key merge),
      AT 3 (caller-override + WARN).
    dependsOn: []
    acceptanceTests: [1, 2, 3]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_per_teammate_env.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_per_teammate_env.py -q

  - name: factory-env-passthrough
    description: |
      Thread `env: dict[str, str] | None = None` through `sdk_factory` and the
      inner `factory()` produced by `default_factory()` in `claude_crew/factories.py`.
      Also add the kwarg to `stub_factory` for signature uniformity (stub ignores
      the value). Pass-through only — no merging at this layer. Append tests to
      `tests/test_factories.py` covering AT 6 (sdk_factory forwards to SdkTeammate).
    dependsOn: [sdk-teammate-env-merge]
    acceptanceTests: [6]
    taskTouches:
      - "claude_crew/factories.py"
      - "tests/test_factories.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_factories.py -q -k "env"

  - name: broker-env-passthrough
    description: |
      Add `env: dict[str, str] | None = None` kwarg to `Broker.spawn_teammate`.
      Forward to the factory call unchanged. No merging, no preset awareness.
      Append tests to `tests/test_broker.py` covering AT 4 (factory receives
      env when passed) and AT 5 (no env kwarg or env=None when omitted).
    dependsOn: [factory-env-passthrough]
    acceptanceTests: [4, 5]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_broker.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_broker.py -q -k "env"

  - name: mcp-tool-env-and-preset
    description: |
      Extend `claude_crew/server.py::spawn_teammate` MCP tool with
      `env: dict[str, str] | None = None` and `local_backend: bool | dict | None = None`
      args. Validate `env` shape at the boundary (non-string values, empty keys
      → ToolError). When `local_backend` is truthy, expand via
      `local_backend_env(...)` and merge with explicit `env` (explicit wins).
      Forward final merged env to `broker.spawn_teammate(env=...)`. Append tests
      to `tests/test_per_teammate_env.py` covering AT 7 (non-string rejection),
      AT 8 (preset expansion), AT 9 (preset + explicit override).
    dependsOn: [broker-env-passthrough, local-backend-preset-helper]
    acceptanceTests: [7, 8, 9]
    taskTouches:
      - "claude_crew/server.py"
      - "tests/test_per_teammate_env.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_per_teammate_env.py tests/test_local_backend.py -q
```

## Design Notes

- **No live model exercise in this slice.** Per the idea brief's hard gate,
  zero tests boot llama.cpp or ccr. AT 2 asserts the merged env dict reaches
  `ClaudeAgentOptions(env=...)` by inspecting `opts_kwargs` *before* the
  `ClaudeSDKClient` context manager is entered — no subprocess spawned.
- **The `ClaudeAgentOptions.env` invariant** (verified in the idea brief,
  `claude_agent_sdk` `types.py:1475`, `subprocess_cli.py` lines 406-456) is
  what makes this safe: the SDK builds
  `process_env = {**inherited_os_environ_minus_CLAUDECODE, **options.env}`
  and passes it to the subprocess, so caller's `options.env` wins on conflict
  with the parent process env. We rely on this — if a future SDK release
  changes the precedence, AT 2 fails (a future live integration test would
  also fail, but that's outside this slice).
- **Stub-mode test fixtures.** `tests/test_per_teammate_env.py` is a new file;
  it uses spy factories for the broker-layer tests (AT 4/5/7/8/9) and
  monkey-patches `ClaudeSDKClient` to avoid spawning the SDK subprocess for
  the sdk-teammate-layer tests (AT 1/2/3) — capture `opts_kwargs` at the
  `ClaudeAgentOptions(**opts_kwargs)` construction site and assert against
  the captured dict.
- **CLAUDE.md hygiene.** New code follows the conventions in the worktree's
  `CLAUDE.md`: imports at module top, `asyncio.get_running_loop()` (no
  occurrences needed here), no Windows CRLF in any new file, no inline imports
  in tests except for guarded optional deps.
- **Schema check note.** The repo-reactor `bin/spec-schema-check.sh` is
  not present in this worktree (no `bin/` directory at the worktree root); the
  schema invariants enforced by that script are satisfied by construction —
  all ten required headers present, `## Test Command` carries a runnable
  `bash` fence, `## Task Breakout` carries a `tasks:` yaml block, every AT
  (1-10) is claimed by exactly one task, every `dependsOn` reference resolves,
  no cycles, every task declares the required keys, every
  `implementationKind` is `behavior-change`.
