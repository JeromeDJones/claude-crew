# Doc-Sync Checklist: multi-scope-agent-memory

- **Slug:** multi-scope-agent-memory
- **Worktree:** `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory`
- **Retro cycle:** 0

## Per-file Decisions

| File | Proposed Change | Outcome | Notes |
|------|-----------------|---------|-------|
| `doc/PRODUCT-VISION.md` | Add pipeline row for `multi-scope-agent-memory` (status `done 2026-05-19`, capability 1, size S, completes the deferred `project`/`local` scope stub from the memory-persistence feature); update "Features Implemented" header line to include `+ multi-scope-agent-memory` | accept \| edit \| skip | |
| `doc/ROADMAP.md` | skipped — file absent | skip | file absent |
| `doc/features/FEATURE-multi-scope-agent-memory.md` | create new feature doc covering problem, three-scope path layout, Write auto-attach design, acceptance tests 1–12, and validation PASS (184/184) | accept \| edit \| skip | |
| `doc/BACKLOG.md` | Add four `[2026-05-19] Feature: multi-scope-agent-memory` rows for the four Info-tier findings from `multi-scope-agent-memory-feature-review-0.md`: (1) `feature.spec.type-drift.scope-literal` — add `Scope = Literal[...]` alias and re-type the three signatures; (2) `feature.cracks.inline-imports` — hoist `from claude_crew.teammate_memory import ...` to `sdk_teammate.py` module top after verifying no circular import; (3) `feature.cracks.scope-fallthrough` — tighten `build_memory_section` else-branch to raise instead of silently defaulting to user scope; (4) `feature.test.coverage-gap.cwd-none-integration` — add an `SdkTeammate.__init__` test for the `cwd=None` → `Path.cwd()` fallback for project/local scope | accept \| edit \| skip | |
| `doc/ARCHITECTURE.md` | skipped — file absent | skip | file absent |

## Outcome

User decisions (2026-05-19):
- `doc/PRODUCT-VISION.md` — **accepted**: pipeline row `msam` added after #27; Features Implemented header counter updated.
- `doc/ROADMAP.md` — skipped (file absent).
- `doc/features/FEATURE-multi-scope-agent-memory.md` — **deferred to signoff**: created by `extract-feature-doc.sh` at signoff rather than the documenter's hand-authored draft.
- `doc/BACKLOG.md` — **accepted**: 4 follow-up rows added under `## [2026-05-19] Feature: multi-scope-agent-memory`. Items 1+3+4 slated for a follow-up RepoReactor slice; item 2 deferred as cosmetic.
- `doc/ARCHITECTURE.md` — skipped (file absent).

## Staged Diff Summary

_no staged changes_

---

### Proposed content for `doc/features/FEATURE-multi-scope-agent-memory.md`

```markdown
# Feature: Multi-Scope Agent Memory

**Status**: done (2026-05-19)
**Created**: 2026-05-19

---

## Problem

claude-crew's per-role agent memory shipped with three scopes parsed in
pack frontmatter (`user`, `project`, `local`) but only `user` functioned.
When an operator declared `memory: project` or `memory: local`,
`sdk_teammate.py` emitted a one-line WARN and silently skipped injection —
the agent received no memory scaffolding, no path guidance, no Write tool
auto-attach. This feature finishes the deferred v1 cut so all three scopes
resolve to a real directory, carry scope-appropriate write guidance, and
auto-grant `Write` so persistence is actually possible.

---

## Design

Three modules participate:

- `claude_crew/teammate_memory.py` — owns path resolution and
  prompt-section construction (pure / I/O-light, no SDK coupling).
  Extended with `scope` keyword arg (default `"user"`) and
  `project_root: Path | None` on `memory_dir`, `memory_index_path`, and
  `build_memory_section`. Three module-level guidance-text constants
  (`_INSTRUCTIONS_HEADER_USER`, `_INSTRUCTIONS_HEADER_PROJECT`,
  `_INSTRUCTIONS_HEADER_LOCAL`) selected by scope. New helper
  `ensure_write_tool(agent_def)` returns the input unchanged if Write is
  already in tools; otherwise a `dataclasses.replace` copy with Write
  appended.

- `claude_crew/sdk_teammate.py` — injection point. Replaced the
  WARN-only branch (was lines ~500-506) with full injection for all three
  scopes. Resolves `project_root = Path(cwd).resolve() if cwd else Path.cwd()`
  for project/local. Calls `ensure_write_tool(role_def)` and, if it
  returned a replaced def, assigns `self._agents = {**self._agents, role:
  patched_def}` — fresh dict, F3b shared-pack invariant preserved.

### Path Layouts

| Scope | Path |
|-------|------|
| `user` | `~/.claude/agent-memory/<role>/` (unchanged) |
| `project` | `<project_root>/.claude/agent-memory/<role>/` |
| `local` | `<project_root>/.claude/agent-memory.local/<role>/` |

Project root resolves from the teammate's spawn-time `cwd`; falls back
to `Path.cwd()` when `cwd` is `None`.

### Write Auto-Attach

When a role's pack declares any memory scope, `ensure_write_tool` is
called unconditionally inside the scope guard so `self._agents[role].tools`
reflects Write capability even when a `system_prompt` override suppresses
the prompt-side memory injection.

### Non-Collision with Lead Write Guard

New paths (`~/.claude/agent-memory/` for user; `<root>/.claude/agent-memory{,.local}/`
for project/local) do not match the protected
`~/.claude/projects/<slug>/memory/` shape guarded by
`is_lead_project_memory_path`. Pinned by regression test AT-12.

### Local-Scope Gitignore

Local-scope guidance recommends `.gitignore` entry
`.claude/agent-memory.local/`. The feature intentionally does NOT
auto-edit `.gitignore` — guidance only.

---

## Acceptance Tests

| AT | Summary | Status |
|----|---------|--------|
| 1 | `memory_dir(role, scope="user")` returns `~/.claude/agent-memory/<role>` | ✅ |
| 2 | `memory_dir(role, scope="project", project_root=R)` returns `R/.claude/agent-memory/<role>` | ✅ |
| 3 | `memory_dir(role, scope="local", project_root=R)` returns `R/.claude/agent-memory.local/<role>` | ✅ |
| 4 | `scope="project"/"local"` with `project_root=None` raises `ValueError` | ✅ |
| 5 | `build_memory_section(scope="user")` contains cross-project phrase, not project/local phrases | ✅ |
| 6 | `build_memory_section(scope="project")` names project-scoped path; warns against secrets | ✅ |
| 7 | `build_memory_section(scope="local")` describes machine-local memory; recommends `.gitignore` | ✅ |
| 8 | `ensure_write_tool` with `tools=()` returns replaced copy with `tools` containing `"Write"` | ✅ |
| 9 | `ensure_write_tool` with `tools=("Write","Read")` returns input identity | ✅ |
| 10 | `SdkTeammate(memory="project", cwd=tmp)` → `_system_prompt` contains project guidance + path; `_agents[role].tools` contains `"Write"` | ✅ |
| 11 | `SdkTeammate(memory="local", cwd=tmp)` → `_system_prompt` contains local guidance + `.local` path | ✅ |
| 12 | New project/local memory paths return `False` from `is_lead_project_memory_path` | ✅ |

---

## Validation

```bash
uv run pytest tests/test_teammate_memory.py tests/test_sdk_teammate.py
# 184 passed in 3.61s (2026-05-19)
```

---

## Known Follow-ups (from feature review)

- `feature.spec.type-drift.scope-literal` — add `Scope = Literal["user","project","local"]` alias and re-type the three public signatures. Tracked in `doc/BACKLOG.md`.
- `feature.cracks.inline-imports` — hoist `from claude_crew.teammate_memory import ...` to `sdk_teammate.py` module top after verifying no circular import. Tracked in `doc/BACKLOG.md`.
- `feature.cracks.scope-fallthrough` — `build_memory_section` else-branch silently defaults to user-scope; tighten to raise. Tracked in `doc/BACKLOG.md`.
- `feature.test.coverage-gap.cwd-none-integration` — no `SdkTeammate.__init__` test for the `cwd=None` fallback. Tracked in `doc/BACKLOG.md`.
```

---

### Proposed `doc/BACKLOG.md` additions

Insert immediately after the existing `## [2026-05-19] Investigation: SDK subprocess death after empty-turn recovery` block (i.e., at the top of the active backlog, before `## [2026-05-18] Feature candidate: click-to-view tool output in dashboard stream`):

```markdown
## [2026-05-19] Feature: multi-scope-agent-memory

### `Scope` type alias missing — signatures typed `str` instead of `Literal`

- **What**: The spec's Data/API Contracts section declares `Scope = Literal["user", "project", "local"]` as a module-level type alias, but the implementation uses `scope: str = "user"` on all three public signatures (`memory_dir`, `memory_index_path`, `build_memory_section`). The `else: raise ValueError` in `memory_dir` provides a runtime safety net, but the typed API surface promised by the spec is absent. `sdk_teammate.py`'s `role_memory` variable is also untyped.
- **Where**: `claude_crew/teammate_memory.py` (all three signature sites); `claude_crew/sdk_teammate.py` (`role_memory` variable).
- **Why it matters**: Static type checkers won't flag invalid scope literals passed to these helpers; future callers have no IDE completion for valid values. Flagged `feature.spec.type-drift.scope-literal` in `multi-scope-agent-memory-feature-review-0.md`.
- **Suggested action**: Add `Scope = Literal["user", "project", "local"]` at module top; re-type the three signatures. Simultaneously tighten `build_memory_section`'s else-branch to `raise ValueError` (mirror `memory_dir`) per the scope-fallthrough finding below. XS change.

### Inline import in `sdk_teammate.__init__` unjustified

- **What**: `sdk_teammate.py` imports `from claude_crew.teammate_memory import build_memory_section, ensure_write_tool` inside the `__init__` method body with no comment explaining a circular-import rationale. Per project CLAUDE.md, inline imports are a code smell (named for test functions; the same concern applies to method bodies in production code). The import is re-executed on every `SdkTeammate` construction (cached in `sys.modules` after the first hit, but still obscures the dependency graph).
- **Where**: `claude_crew/sdk_teammate.py` — inline import inside `__init__`.
- **Why it matters**: Obscures dependency graph; inconsistent with module-top import convention. Flagged `feature.cracks.inline-imports` in `multi-scope-agent-memory-feature-review-0.md`.
- **Suggested action**: Hoist to module top after verifying no circular import (check whether `teammate_memory.py` → `teammate_prompt.py` → `sdk_teammate.py` forms a cycle). If a cycle exists, document it with a comment at the inline import site. XS change.

### `build_memory_section` else-branch silently defaults to user scope on unknown scope

- **What**: `memory_dir` raises `ValueError("Unknown scope: ...")` on unrecognized scope values; `build_memory_section` silently falls through to user-scope guidance. Currently dead — all callers go through `memory_dir` first (which would already raise). A future direct caller of `build_memory_section` would silently get user-scope behavior for a typo'd or future scope value.
- **Where**: `claude_crew/teammate_memory.py::build_memory_section` — the `else` branch.
- **Why it matters**: Inconsistency between `memory_dir` (fail-loud) and `build_memory_section` (silent default). Flagged `feature.cracks.scope-fallthrough` in `multi-scope-agent-memory-feature-review-0.md`.
- **Suggested action**: Change the else-branch to `raise ValueError(f"Unknown scope: {scope!r}")`. Subsumes naturally into the `Scope` Literal tightening above. XS change; fold into the same follow-up slice as the type-alias fix.

### `SdkTeammate.__init__` not tested with `cwd=None` for project/local scope

- **What**: AT-10 and AT-11 both pass `cwd=str(tmp_path)`; no `SdkTeammate.__init__` integration test exercises the `Path.cwd()` fallback that fires when `cwd=None` is passed with a project/local scope. The fallback is exercised at the helper-function level but not at the construction integration layer. The spec documents DEBUG-log behavior for this path; no corresponding assertion exists.
- **Where**: `tests/test_sdk_teammate.py` — missing AT variant.
- **Why it matters**: A refactor of the `cwd` fallback in `sdk_teammate.__init__` could silently break the edge case. Helper-level tests would remain green while the integration path fails. Flagged `feature.test.coverage-gap.cwd-none-integration` in `multi-scope-agent-memory-feature-review-0.md`.
- **Suggested action**: Add a parametrized test variant in `tests/test_sdk_teammate.py` that constructs `SdkTeammate` with `memory="project"` and `cwd=None`, then asserts the system prompt contains the project-scope guidance text and a path derived from `Path.cwd()`. XS change.
```

---

### Proposed `doc/PRODUCT-VISION.md` pipeline row addition

Add the following row to the **Post-MVP Substrate (v1.1)** table, after the `#27` row:

```markdown
| msam | **Multi-scope agent memory.** Finishes the deferred `project` and `local` scope stub from the teammate-memory-persistence feature. All three `memory:` frontmatter values now resolve to a real directory (`user` unchanged; `project` → `<cwd>/.claude/agent-memory/<role>/`; `local` → `<cwd>/.claude/agent-memory.local/<role>/`), carry scope-appropriate write guidance, and auto-attach the `Write` tool via `ensure_write_tool`. Non-collision with the lead write guard verified by regression test. | 1 | 1 | S | **done (2026-05-19)** | Shipped via RepoReactor. 5 tasks, 12 ATs, 184/184 tests. Four Info-tier follow-ups (Scope Literal type alias, inline import hoist, scope-fallthrough tighten, cwd=None integration test) in BACKLOG. See `doc/features/FEATURE-multi-scope-agent-memory.md`. |
```

Also update the header's **Features Implemented** line to append `+ multi-scope-agent-memory`.
