# Slice Review: multi-scope-agent-memory task=sdk-teammate-multi-scope-injection

## Scope

Task index 3, cycle 0. Acceptance tests owned: **10, 11**. Files touched: `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`.

## Check 1 — Slice adherence

- **AT-10 (project scope @ `cwd=tmp_root`)**: `test_memory_project_injects_in_system_prompt` constructs an `SdkTeammate` with `memory="project"`, `tools=[]`, `cwd=str(tmp_path)`, and asserts `_system_prompt` contains "project-scoped memory" + the resolved `<tmp>/.claude/agent-memory/<role>` path + `SENTINEL_MEMORY`, AND `self._agents[role].tools` contains `"Write"`. ✅
- **AT-11 (local scope @ `cwd=tmp_root`)**: `test_memory_local_injects_in_system_prompt` asserts the local-scope guidance phrase, the resolved `<tmp>/.claude/agent-memory.local/<role>` path, and `SENTINEL_MEMORY`. ✅

Implementation matches the spec sketch lines 73-96:
- `if role_memory in ("user","project","local")` replaces the WARN-only branch.
- `_memory_project_root = Path(cwd).resolve() if cwd else Path.cwd()` for project/local — matches the "resolve before constructing memory paths" edge case.
- `ensure_write_tool(role_def)` → conditional `self._agents = {**self._agents, role: patched_def}` — fresh dict, no in-place mutation (F3b invariant preserved).
- `build_memory_section(..., scope=role_memory, project_root=_memory_project_root)` passes correct args. ValueError handler retained for unsafe role names.

## Check 2 — Non-regression

- Slice command (`tests/test_sdk_teammate.py -k memory -x`): **15 passed**.
- All other-task commands subsumed by `tests/test_teammate_memory.py` full module: **65 passed** — path-resolution, per-scope-guidance, and write-tool slices remain green.
- The prior WARN-emitting test (`test_memory_warns_and_no_options_key`) was updated to assert the *absence* of the now-incorrect "only 'user' is supported" WARN — a deliberate semantic flip aligned with the new contract.

## Check 3 — Code-quality smoke

- `ensure_write_tool` runs unconditionally inside the scope guard, even when a `system_prompt` override blocks `build_memory_section` from firing. Inline comment explicitly justifies this — `self._agents[role].tools` must reflect Write capability regardless of prompt path. Correct.
- `role_def = patched_def` rebinds the local so the subsequent `getattr(role_def, "tools", None)` for prompt construction sees the auto-attached Write. Subtle but correct.
- Inline import `from claude_crew.teammate_memory import build_memory_section, ensure_write_tool` inside the method body. Matches the spec sketch and likely guards a circular import with `teammate_memory`. **Info**: consider hoisting once the circular-risk is verified absent; not blocking.
- `Path(cwd).resolve()` with the `Path.cwd()` fallback matches the "Project root is a relative path" edge case in the spec (`resolve()` produces absolute paths in the rendered guidance).
- Logger message updated to include `scope=%s` — small observability win.
- Tests use inline imports inside method bodies (`from claude_agent_sdk.types import AgentDefinition`, `from claude_crew.teammate_prompt import SENTINEL_MEMORY`). **Info**: project CLAUDE.md flags inline test imports as a smell; these are localized inside the new test methods and consistent with prior style nearby in the same file. Not blocking.

No Critical / High findings.

## Verdict

Both AT-10 and AT-11 are covered by direct assertions on `_system_prompt` content and `self._agents[role].tools`. Slice tests and all upstream slice tests pass. The WARN-test update reflects the intentional contract change. Implementation cleanly threads scope, project_root, and Write auto-attach through the existing memory-injection seam without disturbing the system_prompt override path or the F3b shared-pack invariant.

**Verdict:** PASS
