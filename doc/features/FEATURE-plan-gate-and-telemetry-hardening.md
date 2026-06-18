# Feature: plan-gate-and-telemetry-hardening

**Status**: Done  
**Created**: 2026-06-17  
**Shipped**: 2026-06-17  
**Spec**: `.rr/specs/plan-gate-and-telemetry-hardening.md`

---

## Problem

Four post-M3 hardening follow-ups closed in one reviewable unit:

1. **Silent safety regression (HIGH)** — A teammate spawned with `permission_mode="plan"` could still WRITE files in headless SDK subprocess sessions. As of claude-agent-sdk 0.1.68 / Claude Code CLI 2.1.177, plan mode presents an approval UI rather than silently blocking; headless, that UI is a no-op, so Writes proceeded. Any coordinator using `permission_mode: plan` as a mutation gate was not actually gated.

2. **F7 TNM telemetry noise** — The `TaskNotificationMessage` `tool_use_id` (SDK 0.1.68) no longer correlates with the PostSubagentUse-hook `tool_use_id` for the same dispatch. This caused false `"no TNM for subagent"` warnings on every successful subagent turn and required two live tests to run with a narrowed warning filter.

3. **Stale backlog entry + missing crash test** — A BACKLOG entry claimed `stderr_tail_at_death` capture was unimplemented (it shipped 2026-06-09). The null observation during the M3 run was a stale-server artifact. One genuine coverage gap remained: no test forcing a real subprocess non-zero exit with real stderr.

4. **Shutdown-signal test flake** — `test_shutdown_signals.py::test_sigterm/sigint_triggers_clean_exit_and_deregister` intermittently timed out at 15s under full-suite load because the spawned `claude_crew.cli` subprocess contended for the default leader port 7821, held by the live MCP session driving the test run.

---

## What Changed

### Deliverable 1 — Plan-mode write gate (`sdk_teammate.py`)

Added a claude-crew-side enforcement layer independent of the SDK's (now-broken) plan gate:

- **`_PLAN_MODE_DENIED_TOOLS: frozenset`** = `{"Write", "Edit", "NotebookEdit", "MultiEdit"}` — module-level constant.
- **`self._effective_permission_mode: str | None`** — new instance attribute, initialized to `None` in `__init__`; stashed from the resolved spawn-arg-wins-then-role-pack value in `_run()` before the SDK client context opens.
- **Deny branch in `_on_pre_tool_use`** — placed AFTER the existing memory-write guard, BEFORE the subagent/main tracking branches: when `_effective_permission_mode == "plan"` and `tool_name in _PLAN_MODE_DENIED_TOOLS`, returns `hookSpecificOutput: {permissionDecision: "deny"}`. Read-only tools (Read, Grep, Glob, Bash, WebFetch, Task) are deliberately NOT denied — a plan-mode teammate is gated, not neutered.
- **`tests/test_live_sdk.py::test_plan_mode_blocks_file_write_and_cwd_works`** — `@pytest.mark.xfail` removed; now a hard PASS (AT9).
- **`CLAUDE.md`** "Known limitations" — new bullet documenting the client-side enforcement under the literal `plan-mode write gate`.

Files changed: `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`, `tests/test_live_sdk.py`, `CLAUDE.md`.

### Deliverable 2 — TNM correlation fix (`sdk_teammate.py`)

A runtime probe confirmed neither `tnm.task_id` nor `tnm.tool_use_id` matches the PostSubagentUse hook's `tool_use_id` in SDK 0.1.68. The spec's sanctioned arrival-order fallback was taken:

- **`_task_notifs_by_tool_use_id: dict`** → **`_task_notifs_ordered: list`** — field renamed and type changed.
- **`_record_task_notif`** — now appends TNMs in stream-arrival order (ignores both `task_id` and `tool_use_id` for keying).
- **`_end_turn`** — correlates the i-th TNM with the i-th closed-scratch entry (arrival-order position match). `finally` block clears `_task_notifs_ordered`.
- **Warning preserved** — `"no TNM for subagent"` still fires for the genuinely-missing case (TNM count < closed-scratch count).
- **Live tests restored** — `test_live_subagents.py::test_pack_end_to_end` and `test_user_loader_live.py::test_user_and_project_agents_invokable` have the narrowing NOTE removed and the full zero-warning filter restored (AT14, AT15).

Files changed: `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`, `tests/test_live_subagents.py`, `tests/test_user_loader_live.py`.

### Deliverable 3a — Backlog correction (`doc/BACKLOG.md`)

The `[2026-06-17]` "Teammate death telemetry" BACKLOG entry is marked ✅ RESOLVED in-place. Status bullet explains the capture path was shipped 2026-06-09, names all existing test coverage, and records the M3 null observation as a **stale-server artifact** (the running MCP server predated the diagnostics ship).

Files changed: `doc/BACKLOG.md`.

### Deliverable 3b — Forced-crash test (`tests/test_live_stderr.py`)

Added `test_forced_subprocess_crash_populates_stderr_tail` (class `TestForcedSubprocessCrash`) to the gated live test file. The test:
- Writes a fake Python-based `claude` binary that unconditionally emits a known marker to stderr and exits non-zero.
- Monkeypatches `ClaudeAgentOptions.cli_path` so the SDK spawns the fake binary without any API call.
- Asserts `stderr_tail_at_death` (via `get_teammate_status`) is non-null and contains the crash marker — exercising the full `_on_stderr_line → _stderr_ring → _tombstone_teammate → TeammateInfo.stderr_tail_at_death` chain.

A green-suite deletion-detector (`tests/test_stderr_crash_guard.py`) asserts the function name is present in the live test file.

Files changed: `tests/test_live_stderr.py`, `tests/test_stderr_crash_guard.py` (new).

### Deliverable 4 — Shutdown-signal flake fix (`tests/test_shutdown_signals.py`)

Root cause (coordinator-verified): `_spawn_claude_crew` did not set `CLAUDE_CREW_UI_PORT`, so the spawned subprocess attempted to bind the default leader port 7821, which was held by the live claude-crew session driving the test run. `UIServer.serve()` never completed; `register()` was never called; `_wait_for_registry_entry` timed out.

Fix: added `_get_free_port()` helper (bind socket to port 0, read assigned ephemeral port, close); `_spawn_claude_crew` now sets `CLAUDE_CREW_UI_PORT` to the dynamically allocated port before subprocess spawn. A `_SIGNAL_TEST_LOCK` threading mutex guards the TOCTOU window. Both tests pass in ~1.36s regardless of whether a live claude-crew session holds 7821. Deregistration assertions preserved.

Files changed: `tests/test_shutdown_signals.py`.

---

## Test Coverage

**Stub suite (full non-regression)**: 1628 passed, 39 skipped, 1 xfailed, 0 failed (211s).

**Live deliverable tests (coordinator validation gate)**:
- AT9 — `test_plan_mode_blocks_file_write_and_cwd_works` → **1 passed (9.44s)**
- AT14 — `test_pack_end_to_end` (full warning filter restored) → **1 passed (40.95s)**
- AT15 — `test_user_and_project_agents_invokable` (full filter restored) → **1 passed (72.58s)**
- AT17 — `test_forced_subprocess_crash_populates_stderr_tail` → **1 passed (64.10s)**

---

## Design Decisions (key)

- **PreToolUse deny-hook over `disallowed_tools`** — surgical (blocks only mutation; read-only tools preserved), reuses the proven memory-guard deny path, testable at implementation level by calling `_on_pre_tool_use` directly.
- **`_effective_permission_mode` stashed at options-build time** — prevents drift between the resolved value the SDK received and the value the hook reads; `None` init allows unit tests to set it directly.
- **Arrival-order TNM correlation** — runtime probe confirmed neither `task_id` nor `tool_use_id` matches in 0.1.68; arrival-order is stable for sequential subagent dispatches (TNMs and PostSubagentUse hooks both arrive in completion order).
- **`doc/BACKLOG.md` single-writer during parallel build** — D1/D2/D4 backlog entries resolved by the post-build documenter/doc-sync phase (not build tasks) to avoid same-file collision across the parallel dispatch group.

---

## Acceptance Tests Summary

| AT | Description | Status |
|----|-------------|--------|
| 1–4 | Plan mode denies Write/Edit/NotebookEdit/MultiEdit (stub) | ✅ PASS |
| 5–6 | Plan mode allows Read/Bash (not neutered) (stub) | ✅ PASS |
| 7 | Non-plan mode allows Write (stub) | ✅ PASS |
| 8 | Role-pack `permissionMode="plan"` feeds gate (stub) | ✅ PASS |
| 9 | Live: plan teammate Write blocked; control Write succeeds | ✅ PASS (live) |
| 10 | Structural: live test has no `xfail` marker | ✅ PASS |
| 11 | Structural: CLAUDE.md contains `plan-mode write gate` | ✅ PASS |
| 12 | TNM correlated by arrival-order, no warning (stub) | ✅ PASS |
| 13 | Genuinely missing TNM: warning fires + fallback (stub) | ✅ PASS |
| 14 | Live: `test_pack_end_to_end` warning-free (full filter) | ✅ PASS (live) |
| 15 | Live: `test_user_and_project_agents_invokable` warning-free | ✅ PASS (live) |
| 16 | Structural: narrowing NOTE literal absent from live tests | ✅ PASS |
| 17 | Live: forced-crash → `stderr_tail_at_death` non-null + marker | ✅ PASS (live) |
| 18 | Structural: forced-crash function exists in `test_live_stderr.py` | ✅ PASS |
| 19 | `doc/BACKLOG.md` stderr entry RESOLVED with `stale-server artifact` | ✅ PASS |
| 20 | `test_sigterm` passes under load, deregistration asserted | ✅ PASS |
| 21 | `test_sigint` passes under load, deregistration asserted | ✅ PASS |

---

## Out of Scope (confirmed deferred)

- Propagating the plan-mode write gate into Task subagents
- Widening the `adaptation_diff` gate channel from `str` to structured `AdaptationDiff`
- Persisting `AdaptationChain` provenance to broker/transcript
- Investigating the persistent-teammate second-turn subprocess death item
