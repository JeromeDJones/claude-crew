# Spec: agent-pack-refresh

## Problem

Operators editing or adding `.claude/agents/*.md` files (project- or user-level) on a running claude-crew MCP server cannot see those changes take effect: `default_factory()` calls `build_merged_pack()` exactly once at startup and captures the result as closure-locals (`merged_pack`, `role_ss`, `merged_bodies`) shared by `factory()` and `_resolve_agent_def`. The only remediation today is restarting the server. We need an on-demand MCP tool — `refresh_agents` — that re-reads agent definitions against the same `home_dir` / `project_root` captured at startup and atomically swaps the in-memory pack so future `spawn_teammate` calls see the new definitions, without disturbing teammates already running.

## Architecture Overview

The refactor introduces a small mutable holder, `_PackState`, owning the three closure-locals (`pack`, `role_ss`, `bodies`). `default_factory()` constructs one holder, and both `factory()` and `_resolve_agent_def` (and the new refresh entry point) read fields *off the holder live* at call time rather than capturing them as immutable locals.

A new method on the holder — `refresh()` — invokes `build_merged_pack(home_dir=..., project_root=...)` using the **exact same roots that were captured at startup** (never re-resolved from `Path.cwd()`), captures any WARN/INFO records emitted during the load via a `StartupDiagCollector`, computes a diff vs the previous pack, and (only if the rebuild succeeds) atomically swaps all three fields in a single critical section. If `build_merged_pack` raises, the holder is left untouched; the prior pack continues to serve spawns.

`make_server()` exposes a new `@mcp.tool` `refresh_agents` that calls the holder's refresh and returns the structured result. The refresh function is attached to the factory (mirroring how `agent_def_resolver` and `startup_diagnostics` are surfaced today) so `make_server` can wire it without reaching into closures.

**Out of v1:** the broker's `startup_diagnostics` tuple is *not* mutated by refresh. Rationale: it is captured-once at `Broker.__init__`, exposed via `BrokerSnapshot`, and consumed by the dashboard's Startup Notices panel — three additional moving parts. The refresh's WARN/INFO surface is fully returned in the tool response, which is the operator's immediate feedback channel. Dashboard panel resync is deliberately deferred (recorded in Out of Scope) to keep the blast radius small per the idea's explicit guidance.

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| `factory()` spawn path | `claude_crew/factories.py` | reads `merged_pack`, `role_ss`, `merged_bodies` (closure-local today) | will read holder fields |
| `_resolve_agent_def` (broker snapshot) | `claude_crew/factories.py` | reads `merged_pack` (closure-local today) | will read holder fields |
| `_resolve_role` (auto-promotion) | `claude_crew/factories.py` | reads `merged_pack` (closure-local today) | will read holder fields |
| `refresh_agents` MCP tool | `claude_crew/server.py` (new) | calls `factory.refresh_pack()` | new call-site |

Resolution: one shared mutable holder; every read-site reaches through `holder.pack` / `holder.role_ss` / `holder.bodies`. No polymorphism, no second helper.

## Data / API Contracts

```python
# Holder owned by default_factory()'s closure.
@dataclasses.dataclass
class _PackState:
    pack: dict[str, AgentDefinition]
    role_ss: dict[str, list[str] | None]
    bodies: dict[str, str]
    home_dir: Path | None       # frozen at startup; refresh reuses
    project_root: Path | None   # frozen at startup; refresh reuses
    _lock: threading.Lock       # guards atomic swap

# Attached to the sdk-mode factory; called by the MCP tool.
def refresh_pack() -> RefreshResult: ...

# RefreshResult: returned by the MCP tool, JSON-serialisable.
{
  "ok": True,                        # False iff rebuild raised; pack unchanged
  "error": None | "<error str>",     # exception repr when ok=False
  "counts": {                        # post-refresh effective pack
    "default": int, "plugin": int, "user": int, "project": int, "total": int
  },
  "diff": {
    "added":   ["role-a", "plugin:role-b"],
    "removed": ["role-c"],
    "changed": ["role-d"]            # role key present in both, AgentDefinition differs
  },
  "warnings": [                      # WARN/INFO captured this refresh pass
    {"level": "WARNING", "logger": "...", "message": "..."}
  ],
  "note": "future-spawns-only: running teammates keep their original AgentDefinition snapshot."
}
```

## Design Decisions

- **Mutable `_PackState` holder; read fields live at every call-site** — *Rationale:* lets `factory()` / `_resolve_agent_def` / `_resolve_role` pick up refreshed state without re-wiring closures. — *Carried into:* `_PackState` dataclass in `claude_crew/factories.py`; AT-1, AT-3.
- **Atomic swap under a `threading.Lock`; rebuild precedes assign** — *Rationale:* a partial-failure rebuild (`build_merged_pack` raises) must not leave a torn pack visible. — *Carried into:* `_PackState.refresh()`; AT-4.
- **Refresh reuses captured `home_dir` / `project_root`; never `Path.cwd()`** — *Rationale:* per-cwd resolution is the deliberately-rejected footgun documented in `load_project_agents`. — *Carried into:* `_PackState.home_dir` / `_PackState.project_root` fields; AT-6.
- **Future-spawns-only semantics; running teammates unaffected** — *Rationale:* SDK subprocesses materialised their `AgentDefinition` at spawn; the host cannot retro-mutate them. — *Carried into:* `refresh_agents` tool docstring AND `RefreshResult.note`; AT-5.
- **Diff computed by role-key with `dataclasses.asdict` comparison** — *Rationale:* deterministic "changed" classification independent of dict iteration order. — *Carried into:* diff helper; AT-2.
- **Diff role keys are exactly the keys `merged_pack` is keyed on** — *Rationale:* bare for default/user/project, `<plugin>:<role>` for plugin agents, matching the existing four-layer keying. No re-namespacing inside diff. — *Carried into:* diff helper; AT-2, AT-7.
- **`StartupDiagnostic` records are serialised into the warning dict as `{"level": <levelname>, "logger": <logger_name>, "message": <rendered_message>}`** — *Rationale:* matches the `warnings[]` shape in Data/API Contracts and the AT-8 assertion that the `message` substring references `bad.md`. The `category` classification is dropped from the v1 tool response (kept internal). — *Carried into:* refresh result serialiser; AT-8.
- **Full re-merge of all four layers (default + plugin + user + project)** — *Rationale:* `build_merged_pack` already composes them; refresh calls it whole-cloth. — *Carried into:* `_PackState.refresh()` calls `build_merged_pack`; AT-7.
- **Refresh failures preserve the prior pack** — *Rationale:* one bad file should not zero out a working server. — *Carried into:* `try/except` around rebuild before swap; AT-4, AT-8.
- **Broker `startup_diagnostics` NOT updated by refresh in v1** — *Rationale:* keep blast radius small; warnings are surfaced via the tool response. — *Carried into:* Out of Scope; AT-9.
- **Stub mode exposes a no-op refresh that returns `ok=True` with empty diff** — *Rationale:* `make_server` must register the tool unconditionally; stub tests must not require sdk mode. — *Carried into:* `stub_factory.refresh_pack`; AT-10.

## Edge Cases

- A newly-added project agent file appears after refresh: `diff.added` contains its role; subsequent `spawn_teammate` resolves to it.
- A removed project agent file: `diff.removed` contains its role; a user/default fallback (if any) becomes effective; spawn does not error.
- An edited agent file (changed prompt, tools, or model): `diff.changed` contains its role; bodies reflect the new prompt.
- A newly-malformed agent file (invalid YAML / oversized): `build_merged_pack` per-file isolation logs a WARN and skips it; refresh succeeds; `RefreshResult.warnings` carries the WARN.
- A refresh during which `build_merged_pack` raises (e.g. directory deleted, IO error): `ok=False`, prior pack remains; subsequent spawns continue to work with the previous AgentDefinition set.
- Concurrent `refresh_agents` calls: serialised by `_PackState._lock`; final state is the result of the last to acquire.
- A teammate spawned mid-refresh: reads the holder fields *after* the lock releases, so either fully-pre or fully-post; never a torn mix.
- Already-running teammates: unchanged — their SDK subprocess holds its spawn-time `AgentDefinition`; refresh result `note` field states this.
- Refresh with no file changes: `diff.added=[]`, `diff.removed=[]`, `diff.changed=[]`; counts unchanged; `ok=True`.
- Stub-mode server: `refresh_agents` still callable; returns `ok=True` with empty diff and zero counts.

## Acceptance Tests

1. **Holder swap — newly-added project agent visible** — Given an sdk-mode factory built with a temp `project_root` containing no agents, when a new `.claude/agents/foo.md` is written and `factory.refresh_pack()` is called, then `factory(role="foo", …)` resolves to the new `AgentDefinition` and `RefreshResult.diff.added` contains `"foo"`.
2. **Holder swap — diff classification** — Given an initial pack with role `a`, when refresh runs after `a` is edited (prompt body changes), `b` is added, and `c` is removed (from the prior set), then `RefreshResult.diff` reports `{added:["b"], removed:["c"], changed:["a"]}` (any deterministic order).
3. **Resolver reads holder live** — Given an sdk-mode factory, `factory.agent_def_resolver("role-x")` returns `None` before any mutation; when the holder's `pack` is then mutated directly (`holder.pack["role-x"] = <AgentDefinition>`), a subsequent `factory.agent_def_resolver("role-x")` returns that `AgentDefinition` — proving read-sites read holder fields live rather than captured locals. No `refresh_pack()` involved.
4. **Atomic-swap on rebuild failure** — Given an sdk-mode factory with a working pack, when `build_merged_pack` is monkeypatched to raise on the next call and `refresh_pack()` runs, then `RefreshResult.ok is False`, `error` is populated, and `factory.agent_def_resolver` continues to return the *prior* `AgentDefinition` for every role.
5. **Future-spawns-only note present** — `RefreshResult["note"]` contains the substring `"future-spawns-only"` and the `refresh_agents` MCP tool's docstring contains the same substring.
6. **Refresh uses captured roots, not cwd** — Given an sdk-mode factory built with `project_root=/tmp/A`, when `Path.cwd()` is monkeypatched to `/tmp/B` (which contains a different agent file) and `refresh_pack()` is called, then the refreshed pack reflects `/tmp/A`, not `/tmp/B`.
7. **Full four-layer re-merge** — Given an sdk-mode factory, when refresh runs after a *user-level* (`home_dir/.claude/agents/`) file is added, then `RefreshResult.diff.added` contains that role (proving user-layer was re-read, not just project).
8. **Sad-path malformed file isolated** — Given an sdk-mode factory, when a malformed `.claude/agents/bad.md` exists in `project_root` and refresh runs, then `ok=True`, `RefreshResult.warnings` contains a record whose `message` references `bad.md`, and the prior valid roles remain spawnable.
9. **Integration through `make_server` — happy path** — Given `make_server(factory=sdk_factory_with_temp_roots)`, when the `refresh_agents` MCP tool is invoked, then it returns a dict matching the `RefreshResult` shape with `ok=True`.
10. **Stub-mode no-op refresh** — Given `make_server()` in stub mode, when the `refresh_agents` MCP tool is invoked, then it returns `{ok: True, counts: {...total: 0}, diff: {added:[], removed:[], changed:[]}, warnings: [], note: "<future-spawns-only…>"}` without raising.
11. **Integration sad path through `make_server`** — Given `make_server(factory=sdk_factory_with_temp_roots)` where the project agents dir contains one malformed file, when `refresh_agents` is invoked, then `ok=True`, `warnings` is non-empty and references the bad file, and the server process does not crash (a subsequent `list_crew` call still responds).

## Test Command

All test dependencies (pytest, pyyaml, claude-agent-sdk, mcp) are already in `pyproject.toml`. The full suite is the gate per the repo standard ("Validate the whole suite when changing widely-consumed behavior").

```bash
uv run pytest
```

## Out of Scope

- Per-cwd / per-target-repo agent resolution. Refresh reuses the startup-captured `home_dir` / `project_root`.
- Hot-mutation of already-running teammates' agent set. SDK subprocess constraint; refresh is future-spawns-only.
- File-watching / automatic refresh. On-demand via the tool only.
- Updating `Broker.startup_diagnostics` (and therefore the dashboard's Startup Notices panel) on refresh. Warnings surface via `RefreshResult.warnings` instead. Deferred deliberately.
- Changes to the four-layer precedence or merge semantics.
- A "dry-run / preview" mode for refresh.

## Assumptions

- **`build_merged_pack(home_dir=..., project_root=...)` is the correct re-entry point** — *Default:* yes, per the loader's docstring and current call shape. — *Rationale:* it is already parameterized on those roots and used at startup.
- **`threading.Lock` is sufficient (vs `asyncio.Lock`)** — *Default:* `threading.Lock`. — *Rationale:* the holder is shared with sync read-sites (`factory()`, `_resolve_agent_def`) called from FastMCP's threadpool, and `build_merged_pack` is synchronous IO; `asyncio.Lock` would not protect the sync readers.
- **`RefreshResult.diff.changed` is computed via `dataclasses.asdict` comparison of `AgentDefinition`s** — *Default:* yes. — *Rationale:* `AgentDefinition` is an SDK dataclass; field-wise comparison is deterministic and JSON-trivial.
- **Stub-mode `refresh_agents` is registered and returns a stub `RefreshResult`** — *Default:* yes. — *Rationale:* tool registration must not depend on mode; stub tests need a callable surface.
- **Warning capture uses the existing `StartupDiagCollector`** — *Default:* yes, scoped only for the refresh window (mirroring `default_factory`'s `collect_startup_diagnostics` block). — *Rationale:* reuses validated capture code including direct-attach fallbacks.
- **`refresh_agents` requires no auth-token-style permission** — *Default:* identical permission posture to other MCP tools (no extra gate). — *Rationale:* the operator already has stdio control of the server.

## Open Questions

(none)

## Validation

End-to-end exercise of the promised user-visible outcome: an operator edits a project agent file on a running server, calls `refresh_agents`, and observes that a subsequently-spawned teammate runs with the edited definition. Captured as the full-suite gate:

```bash
uv run pytest
```

## Task Breakout

```yaml
tasks:
  - name: pack-state-holder
    description: |
      Introduce `_PackState` dataclass in `claude_crew/factories.py` carrying
      `pack`, `role_ss`, `bodies`, `home_dir`, `project_root`, and a
      `threading.Lock`. Refactor `default_factory()` so the three closure-
      locals are replaced by a single holder instance; rewrite `factory()`,
      `_resolve_role`, and `_resolve_agent_def` to read fields LIVE off the
      holder at every call rather than capturing them. No `refresh()` yet
      and no new MCP tool — just the indirection. The startup initial-load
      path must remain behaviorally identical (same diagnostics flow, same
      `merged_pack`/`role_ss`/`bodies` semantics for the first build).
      AT-3 exercises the live-read indirection via DIRECT holder mutation
      (no `refresh_pack()` involved — that arrives in task 2). The
      refactor's behavior-preservation guarantee is additionally gated by
      the full existing test suite, which this task's `testCommand` runs.
    dependsOn: []
    acceptanceTests: [3]
    taskTouches:
      - "claude_crew/factories.py"
      - "tests/test_factories*.py"
      - "tests/test_pack_refresh*.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest

  - name: refresh-method-and-diff
    description: |
      Implement `_PackState.refresh()`: re-invoke
      `build_merged_pack(home_dir=self.home_dir, project_root=self.project_root)`
      against the captured roots (never `Path.cwd()`), capture refresh-window
      WARN/INFO via `collect_startup_diagnostics`, compute the
      added/removed/changed diff against the prior pack (using
      `dataclasses.asdict` field comparison; role keys exactly as
      `merged_pack` keys on them), serialise `StartupDiagnostic` records
      into `{level, logger, message}` warning dicts, and atomically swap
      `pack`/`role_ss`/`bodies` under the holder's lock ONLY on rebuild
      success. On `build_merged_pack` raising, leave state untouched and
      return `ok=False` with the error string. Attach a callable
      `factory.refresh_pack` returning the `RefreshResult` dict. Attach a
      no-op `stub_factory.refresh_pack` returning the empty-diff/zero-counts
      `RefreshResult` (still includes the `note` string).
    dependsOn: [pack-state-holder]
    acceptanceTests: [1, 2, 4, 6, 7, 8]
    taskTouches:
      - "claude_crew/factories.py"
      - "tests/test_factories*.py"
      - "tests/test_pack_refresh*.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest

  - name: mcp-refresh-agents-tool
    description: |
      Register a `@mcp.tool` `refresh_agents` on the FastMCP server in
      `claude_crew/server.py`. The tool calls
      `getattr(factory, "refresh_pack", <no-op>)()` and returns the
      structured `RefreshResult` dict documented in Data/API Contracts.
      The tool docstring MUST contain the substring `"future-spawns-only"`
      and explain that already-running teammates keep the AgentDefinition
      snapshot they spawned on (refresh affects subsequent spawns only).
      Wire so stub-mode `make_server()` exposes the tool and returns
      `ok=True` with empty diff, and sdk-mode surfaces real refresh
      output including warnings for a malformed project agent file
      without crashing the server. This task's gate is the full suite —
      it is the final task, the cross-cutting whole-suite check per the
      repo's "validate the whole suite" standard.
    dependsOn: [refresh-method-and-diff]
    acceptanceTests: [5, 9, 10, 11]
    taskTouches:
      - "claude_crew/server.py"
      - "tests/test_server*.py"
      - "tests/test_pack_refresh*.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest
```
