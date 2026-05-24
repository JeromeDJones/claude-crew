# FEATURE: agent-pack-refresh

**Status:** implemented (validated, on branch `repo-react/agent-pack-refresh`)
**Date:** 2026-05-24
**Built via:** repo-reactor (planner → plan-review ×3 → 3-task implement/slice-review → feature-review → validation)

## Problem

claude-crew freezes its merged agent pack once, at MCP-server startup. `default_factory()` called `build_merged_pack()` exactly once and captured the result as closure-locals shared by `factory()` and `_resolve_agent_def`. Editing or adding a `.claude/agents/*.md` file on a running server had no effect until the whole server was restarted. This feature adds an on-demand MCP tool, `refresh_agents`, that re-reads agent definitions and atomically swaps the in-memory pack so future `spawn_teammate` calls see the new definitions — no restart, no disruption to teammates already running.

Scope was deliberately bounded to "agents in the current repo": the server reuses the `home_dir`/`project_root` captured at startup. Per-cwd / per-target-repo drop-in was explicitly out of scope (per-cwd re-resolution is a documented footgun).

## Design

- **`_PackState` holder** (`claude_crew/factories.py`): a small mutable dataclass owning `pack`, `role_ss`, `bodies`, the frozen `home_dir`/`project_root`, and a `threading.Lock`. `default_factory()` constructs one; `factory()`, `_resolve_role`, and `_resolve_agent_def` read its fields **live** at every call instead of capturing closure-locals. The initial-load path is behaviorally unchanged.
- **`_PackState.refresh()`**: re-invokes `build_merged_pack(home_dir=..., project_root=...)` against the **captured roots** (never `Path.cwd()`), captures refresh-window WARN/INFO via `collect_startup_diagnostics`, computes an added/removed/changed diff vs the prior pack (`dataclasses.asdict` comparison; role keys exactly as `merged_pack` keys them), serialises diagnostics into `{level, logger, message}` dicts, and **atomically swaps** the three fields under the lock — **only on rebuild success**. If `build_merged_pack` raises, the holder is untouched and `ok=False` is returned (no torn pack).
- **`refresh_agents` MCP tool** (`claude_crew/server.py`): calls `getattr(factory, "refresh_pack", <no-op>)()` and returns the structured `RefreshResult`. Stub mode attaches a no-op `refresh_pack`; sdk mode attaches the real one. **Future-spawns-only**: already-running teammates keep the `AgentDefinition` snapshot they were spawned on — stated in both the tool docstring and `RefreshResult.note`.

### RefreshResult contract

```
{ ok, error, counts: {default, plugin, user, project, total}, diff: {added, removed, changed}, warnings: [...], note }
```

- `diff` (added/removed/changed role keys) is the load-bearing operator signal.
- `counts`: **`total` and `plugin` are populated; `default`/`user`/`project` are reserved and always 0 in v1** — per-layer attribution is not recoverable from the merged pack without re-loading each layer. Documented as a deliberate v1 limitation (feature-review ruling); a follow-up could thread per-layer counts from `build_merged_pack`.

## Out of scope (deferred)

- Per-cwd / per-target-repo agent resolution.
- Hot-mutation of already-running teammates' agent set (SDK subprocess constraint).
- File-watching / automatic refresh (on-demand via the tool only).
- Updating `Broker.startup_diagnostics` / the dashboard Startup Notices panel on refresh (warnings surface via `RefreshResult.warnings`).
- Populating per-layer `counts` (reserved; see above).

## Tests

11 acceptance tests in `tests/test_pack_refresh.py` (AT-1..AT-11): holder live-read via direct mutation, diff classification, resolver-reads-holder, atomic-swap-on-failure, captured-roots-not-cwd, four-layer re-merge, malformed-file isolation, future-spawns-only note, and `make_server` integration (happy/stub/sad). Full suite: **1214 passed**, 32 skipped, 1 xfailed (baseline 1200 + 14).

## Notes / follow-ups

- Per-layer `counts` population (reserved fields) — possible follow-up.
- See `doc/BACKLOG.md` [2026-05-24] for an unrelated infra bug surfaced during this run: SDK teammate dies (exit 1) on reuse with stderr discarded.
