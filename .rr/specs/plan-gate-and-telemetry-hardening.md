# Spec: plan-gate-and-telemetry-hardening

## Problem

Four post-M3 hardening follow-ups (all recorded under `[2026-06-17]` in `doc/BACKLOG.md`) need to be closed in one reviewable unit. The centerpiece is a **silent safety regression**: a teammate spawned with `permission_mode="plan"` can still WRITE files in headless SDK subprocess sessions. As of claude-agent-sdk 0.1.68 / Claude Code CLI 2.1.177, plan mode presents an approval UI instead of silently blocking; headless, that UI is a no-op, so the Write goes through. Any coordinator that uses `permission_mode: plan` as a mutation gate is **not actually gated**. Alongside it: F7 subagent outcome telemetry is dead because the `TaskNotificationMessage` (TNM) `tool_use_id` no longer correlates with the PostSubagentUse-hook `tool_use_id` in 0.1.68 (benign, but emits noise warnings and two live tests were narrowed to tolerate it); a stale backlog entry wrongly claims teammate-death stderr capture is unimplemented (it shipped 2026-06-09, the null observation was a stale-server artifact) and the one genuinely-missing forced-real-crash test is absent; and two `test_shutdown_signals.py` tests flake under full-suite load. The outcome: plan-mode teammates cannot mutate files regardless of SDK behavior; subagent TNM telemetry correlates again with no noise warning; the backlog tells the truth and a real subprocess-crash test proves stderr survives onto the death record; and the shutdown-signal tests stop flaking — without weakening any assertion.

## Architecture Overview

Two of the four deliverables touch the same file — `claude_crew/sdk_teammate.py` (the SDK-teammate per-turn loop, PreToolUse hook, and telemetry path) — so they are batched as one planned unit and **serialized** (D2 depends on D1) so they never edit that file concurrently. The other two are test/doc-only and disjoint, dispatching in parallel.

- **Deliverable 1 (plan-mode write gate)** — extends the existing `SdkTeammate._on_pre_tool_use` PreToolUse hook callback (`claude_crew/sdk_teammate.py:766`), which already returns a `permissionDecision: "deny"` for the memory-write guard (`:781-797`). A new branch denies the standard mutating tools when the teammate's resolved `permission_mode` is `"plan"`. The resolved mode is computed today at `:1447-1452` (spawn-arg wins, then role-pack `permissionMode`); it must be stashed on the instance so the hook can read it. This is a **claude-crew-side** enforcement layer that does not depend on the SDK's (now-broken) plan gate.
- **Deliverable 2 (TNM correlation)** — fixes `_record_task_notif`/`_end_turn` (`claude_crew/sdk_teammate.py:1100-1158`) which key TNMs by `tool_use_id` (`:1102`, `:1111`). In 0.1.68 the TNM `tool_use_id` (`:364`) differs from the hook's. Correlation moves to a key that matches in 0.1.68 (`TaskNotificationMessage.task_id` — `:370` shows it is available — or arrival-order per active Task call).
- **Deliverable 3 (stderr telemetry)** — the capture path is ALREADY shipped and tested: `_on_stderr_line` + `_stderr_ring` + `_stderr_tail_redacted()` (`sdk_teammate.py:745-759`), registered as the SDK stderr sink (`:1515`), surfaced as `snap["stderr_tail"]`, read by the broker into `stderr_tail_at_death` (`broker.py:565`, dataclass field `:98`, constructor `:618`) and exposed on `get_teammate_status` (`broker.py:1313`). Work here is doc-correction + the missing forced-real-crash test only.
- **Deliverable 4 (shutdown flake)** — pure test-infra resilience in `tests/test_shutdown_signals.py`.

## Data / API Contracts

```python
# claude_crew/sdk_teammate.py — module level (new)
_PLAN_MODE_DENIED_TOOLS: frozenset[str] = frozenset(
    {"Write", "Edit", "NotebookEdit", "MultiEdit"}
)

# SdkTeammate.__init__ (new instance attr; init to None so direct hook calls work)
self._effective_permission_mode: str | None = None

# In _run() options-building, where effective_pm is resolved (~:1448), AFTER the
# spawn-arg / role-pack fallback resolution, BEFORE the client context opens:
self._effective_permission_mode = effective_pm

# _on_pre_tool_use (new branch, AFTER the existing memory-write guard, BEFORE
# the subagent/main tracking branches):
#   if self._effective_permission_mode == "plan" and tool_name in _PLAN_MODE_DENIED_TOOLS:
#       return {
#           "hookSpecificOutput": {
#               "hookEventName": "PreToolUse",
#               "permissionDecision": "deny",
#               "permissionDecisionReason": <claude-crew plan-mode mutation block message>,
#           }
#       }

# Deliverable 2 — correlation key change (illustrative; implementor confirms the
# real 0.1.68 fields by runtime probe before choosing):
#   _record_task_notif keys by tnm.task_id (or arrival-order index per active
#   Task dispatch) instead of tnm.tool_use_id; _end_turn looks up by the same key.
#   The closed-subagent-scratch entry must carry the matching key.
```

## Design Decisions

- **Enforce plan-mode write-blocking via the PreToolUse deny-hook, not `disallowed_tools`** — *Rationale:* the deny-hook is surgical (blocks only MUTATION while leaving read-only tools — Read, Grep, Bash inspection — available, so a plan-mode teammate is gated, not neutered), it reuses the already-proven memory-guard deny path in the same callback, and it is testable at the implementation layer by calling `_on_pre_tool_use` directly. `disallowed_tools` is coarser (removes the tool from the catalog entirely, can't condition on mode at hook time cleanly) and would silently change the wire prompt. — *Carried into:* `_on_pre_tool_use` plan-mode branch; ATs 1-8; `_PLAN_MODE_DENIED_TOOLS`.
- **Denied mutator set is `{Write, Edit, NotebookEdit, MultiEdit}`** — *Rationale:* these are the standard file/state mutators in the default toolset; read-only and inspection tools (Read, Grep, Glob, Bash, WebFetch, Task) are deliberately NOT denied so plan-mode inspection still works. — *Carried into:* `_PLAN_MODE_DENIED_TOOLS`; ATs 1-7.
- **Resolved permission mode is stashed on `self._effective_permission_mode` at options-build time** — *Rationale:* the hook fires during the turn and must see the same resolved value (spawn-arg-wins-then-role-pack) the SDK got; recomputing in the hook would risk drift. Init to `None` in `__init__` so unit tests can set it directly. — *Carried into:* `self._effective_permission_mode`; AT 8.
- **Flip `test_plan_mode_blocks_file_write_and_cwd_works` to a hard pass (remove `xfail`)** — *Rationale:* once the claude-crew gate exists, the live behavior is a real pass; leaving `xfail(strict=False)` would hide a future regression. — *Carried into:* AT 9, AT 10.
- **TNM↔subagent correlation uses a key that matches in 0.1.68; the missing-TNM warning is preserved for the genuinely-missing case** — *Rationale:* fixing correlation kills the noise warning for normal dispatches, but the `"no TNM for subagent"` warning is still a real diagnostic when a subagent truly produced no TNM, so it must keep firing in that case. — *Carried into:* `_record_task_notif`/`_end_turn`; ATs 12-13.
- **Restore the full warning filter in the two narrowed live tests** — *Rationale:* they were narrowed to `"subagent failure: status="` only, to tolerate the benign `"no TNM"` warning; once correlation is fixed they must again assert a clean, warning-free run, and the narrowing NOTE comment must be removed. — *Carried into:* ATs 14-16.
- **Deliverable 3 adds ONLY the forced-real-crash test + the backlog correction; the capture machinery is not re-implemented** — *Rationale:* the capture path shipped 2026-06-09 with tests (AT2/AT3/AT7 stub + `test_live_stderr.py` live); the M3 null observation was a stale-server artifact. — *Carried into:* ATs 17-19; Out of Scope.
- **Backlog stderr entry is marked RESOLVED in-place, not deleted** — *Rationale:* the audit trail matters; resolved entries elsewhere in `doc/BACKLOG.md` follow this convention. — *Carried into:* AT 19; named literal `stale-server artifact`.
- **Shutdown-signal tests are made load-resilient by raising/parameterizing the registration timeout (and/or poll-with-backoff), keeping the deregistration assertions intact** — *Rationale:* the flake is host-load sensitivity in `_wait_for_registry_entry` at 15s, not a product bug; the tests must still prove clean SIGTERM/SIGINT deregistration. — *Carried into:* ATs 20-21.
- **`doc/BACKLOG.md` is touched by exactly one task during the parallel build (D3a)** — *Rationale:* multiple parallel tasks editing the same file collide at merge-back; the D1/D2/D4 backlog entries are resolved by the post-build documenter/doc-sync phase, not by the build tasks. — *Carried into:* breakout `taskTouches`; Assumptions.

### Branch Coverage

Inverse-coverage map — every conditional/optional branch this spec introduces owes ≥1 AT, including the negative / gate-fires / path-taken case:

#### Deliverable 1 — plan-mode gate branches
- plan-mode + mutating tool (`Write`) → **DENY** → AT 1
- plan-mode + mutating tool (`Edit`) → **DENY** → AT 2
- plan-mode + mutating tool (`NotebookEdit`) → **DENY** → AT 3
- plan-mode + mutating tool (`MultiEdit`) → **DENY** → AT 4
- plan-mode + read-only tool (`Read`) → **ALLOW** (not neutered) → AT 5
- plan-mode + read-only tool (`Bash`) → **ALLOW** (read-only inspection) → AT 6
- non-plan-mode + mutating tool (`Write`) → **ALLOW** (gate only fires under plan) → AT 7
- effective mode resolved from role-pack (no spawn arg) → gate fires → AT 8
- live end-to-end (real teammate, real Write blocked; control writes) → AT 9
- structural: live test no longer `xfail` → AT 10
- doc: CLAUDE.md documents the enforcement → AT 11

#### Deliverable 2 — TNM correlation branches
- TNM present, key matches despite differing `tool_use_id` → found, no warning → AT 12
- TNM genuinely missing → fallback outcome from hook, `"no TNM"` warning still fires → AT 13
- live restored full-filter (warning-free) for both tests → ATs 14-15
- structural: narrowing NOTE removed → AT 16

#### Deliverable 3 — stderr
- real subprocess non-zero exit → `stderr_tail_at_death` non-null + contains captured stderr → AT 17
- structural: forced-crash test exists (green-suite deletion detector for the gated test) → AT 18
- doc: backlog entry RESOLVED with stale-server explanation → AT 19

#### Deliverable 4 — shutdown flake
- SIGTERM clean deregistration under load → AT 20
- SIGINT clean deregistration under load → AT 21

## Edge Cases

- **MultiEdit absent from the active toolset** — including it in `_PLAN_MODE_DENIED_TOOLS` is harmless; the gate never sees it. The set is a denylist, not a requirement that all members exist.
- **`_on_pre_tool_use` called before `_run` sets the resolved mode** (direct unit-test invocation, or an early hook) — `self._effective_permission_mode` initialised to `None` in `__init__` → gate does not fire (fails open to existing behavior; the live SDK path always sets it before the turn).
- **Memory-write guard interaction** — the memory guard runs FIRST in the callback and already denies Write/Edit into the lead's memory path; the plan-mode branch is independent and runs after it. A plan-mode teammate writing to memory is denied by the memory guard regardless.
- **Plan-mode teammate dispatches a Task subagent that writes** — the per-teammate gate denies the teammate's own direct mutators; a spawned subagent runs in its own permission context. This slice does NOT propagate the gate into subagents (see Out of Scope).
- **TNM correlation with concurrent/interleaved Task dispatches** — arrival-order correlation is ambiguous if two Tasks are in flight; prefer a stable `task_id` key; fall back to arrival-order only when `task_id` is unavailable. The regression test (AT 12) must exercise at least one dispatch with a differing `tool_use_id`.
- **Genuinely-missing TNM** — a subagent that emits no TNM must still log `"no TNM for subagent"` and fall back to the hook outcome (AT 13); the fix narrows the *false-negative* (TNM present but mis-keyed), not the *true-missing* case.
- **claude CLI emits nothing to stderr on a normal turn** — per the documented caveat in `tests/test_live_stderr.py`, all normal output is stdout JSON. The forced-crash test (AT 17) must induce a REAL stderr-producing crash (e.g. an invalid spawn argument / unusable model / corrupted session) and assert the captured stderr survives onto the death record — it must NOT rely on normal-turn stderr nor manually inject the probe line.
- **Redaction failure during stderr capture** — `_stderr_tail_redacted()` returns the sentinel `"[stderr-redaction-failed]"` rather than raising; the death path stays diagnosable. The forced-crash assertion tolerates a non-empty redacted tail (it asserts non-null + contains a marker from the induced crash, not exact bytes).
- **Shutdown tests under load** — registration may legitimately take longer than 15s under full-suite CPU contention; the fix raises/parameterizes the wait without making the test pass when deregistration genuinely fails (the deregistration assertion is preserved).

## Acceptance Tests

1. Given an `SdkTeammate` with `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="Write"`, then it returns a `hookSpecificOutput` with `permissionDecision == "deny"` (implementation-level; stub).
2. Given `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="Edit"`, then it returns `permissionDecision == "deny"`.
3. Given `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="NotebookEdit"`, then it returns `permissionDecision == "deny"`.
4. Given `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="MultiEdit"`, then it returns `permissionDecision == "deny"`.
5. Given `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="Read"`, then it does NOT return a deny decision (returns `{}` or a non-deny result) — read-only tools are not neutered.
6. Given `_effective_permission_mode = "plan"`, when `_on_pre_tool_use` is invoked with `tool_name="Bash"`, then it does NOT return a deny decision — read-only inspection is allowed.
7. Given `_effective_permission_mode = None` (non-plan), when `_on_pre_tool_use` is invoked with `tool_name="Write"`, then it does NOT return a deny decision — the plan-mode gate fires only under plan mode.
8. Given a role-pack whose `permissionMode == "plan"` and NO spawn-time `permission_mode` override, when the teammate builds its options, then `self._effective_permission_mode` resolves to `"plan"` and a subsequent `_on_pre_tool_use(tool_name="Write")` returns `permissionDecision == "deny"` (proves the role-pack resolution branch feeds the gate).
9. (LIVE, gated) Given a real teammate spawned with `permission_mode="plan"` asked to Write `probe.txt`, and a control teammate (no plan mode) asked the same, when both turns complete, then the plan teammate's `probe.txt` does NOT exist and the control teammate's `probe.txt` exists and contains `probe` — i.e. `tests/test_live_sdk.py::test_plan_mode_blocks_file_write_and_cwd_works` passes as a hard pass.
10. Given the test file `tests/test_live_sdk.py`, when a green-suite structural guard inspects `test_plan_mode_blocks_file_write_and_cwd_works`, then that test carries no `@pytest.mark.xfail` marker (deletion-detector for the flip; exits non-zero if `xfail` is reintroduced).
11. Given `CLAUDE.md`, when a green-suite structural guard greps the "Known limitations" section, then it contains the named literal `plan-mode write gate` describing the claude-crew-side enforcement (deletion-detector; exits non-zero if the note is absent).
12. Given a set of closed subagent-scratch entries and a list of `TaskNotificationMessage`s whose `tool_use_id` differs from the hook's `tool_use_id` but whose correlation key (task_id / arrival-order) matches, when `_end_turn` runs, then each TNM is found (`tnm_missing == False`) and NO `"no TNM for subagent"` warning is logged (implementation-level; stub).
13. Given a closed subagent-scratch entry with NO corresponding TNM at all, when `_end_turn` runs, then `tnm_missing == True`, the outcome falls back to the hook outcome, and the `"no TNM for subagent"` warning IS logged (preserves the true-missing diagnostic).
14. (LIVE, gated) Given `tests/test_live_subagents.py::test_pack_end_to_end` with the FULL subagent-warning filter restored, when it runs, then it passes with zero subagent warnings (no `"subagent failure: status="` AND no `"no TNM for subagent"`).
15. (LIVE, gated) Given `tests/test_user_loader_live.py::test_user_and_project_agents_invokable` with the FULL subagent-warning filter restored, when it runs, then it passes with zero subagent warnings.
16. Given the two live test files, when a green-suite structural guard greps them, then the narrowing NOTE literal `intentionally excluded here` is ABSENT from both (deletion-detector; exits non-zero if the narrowing is reintroduced).
17. (LIVE, gated) Given a teammate whose subprocess is forced to exit non-zero with real stderr output (NOT a manually-injected probe line, NOT a mocked `ProcessError`), when the broker records its death, then `stderr_tail_at_death` (via `get_teammate_status` / the tombstone) is non-null and contains a marker from the induced crash's stderr.
18. Given `tests/test_live_stderr.py`, when a green-suite structural guard inspects it, then the forced-crash test function `test_forced_subprocess_crash_populates_stderr_tail` is present (deletion-detector for the gated test; exits non-zero if it is removed).
19. Given `doc/BACKLOG.md`, when greped, then the `[2026-06-17]` stderr-telemetry entry is marked RESOLVED and contains the named literal `stale-server artifact` explaining the M3 null observation, and the false `"declared but never populated; null in practice"` claim no longer reads as an open gap.
20. Given `tests/test_shutdown_signals.py::test_sigterm_triggers_clean_exit_and_deregister` run repeatedly under concurrent full-suite load, when it executes, then it passes every run (no `_wait_for_registry_entry` timeout) AND still asserts the subprocess deregisters cleanly on SIGTERM.
21. Given `tests/test_shutdown_signals.py::test_sigint_triggers_clean_exit_and_deregister` run repeatedly under concurrent load, when it executes, then it passes every run AND still asserts clean deregistration on SIGINT.

## Test Command

Prerequisites: all test files import only `pytest` and `claude_agent_sdk`, both already in `pyproject.toml` — no new packages. The stub suite requires no manual setup. The LIVE tests (ATs 9, 14, 15, 17) require `CLAUDE_CREW_LIVE_TESTS=1` and a logged-in Claude CLI (`~/.claude/.credentials.json` + `~/.claude.json` present); they spawn real `claude` subprocesses that cost tokens and take minutes. Run from the worktree root.

Stub suite (full — required because Deliverables 1 and 2 change behavior other suites assert on):

```bash
uv run pytest
```

Live suite covering the touched surface (run before merge, per project policy):

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py tests/test_live_subagents.py tests/test_user_loader_live.py tests/test_live_stderr.py
```

## Out of Scope

- Re-implementing the teammate-death stderr-capture machinery (`_on_stderr_line`, `_stderr_ring`, `_stderr_tail_redacted`, broker `stderr_tail_at_death`) — it shipped 2026-06-09 and is tested; only the forced-real-crash test and the backlog correction are in scope.
- Propagating the plan-mode write gate into spawned Task subagents — the gate blocks the teammate's own direct mutators only; subagent permission contexts are not modified.
- Widening the `adaptation_diff` gate channel from `str` to a structured `AdaptationDiff` (deferred M3 follow-up; leave in backlog).
- Persisting `AdaptationChain` provenance to broker/transcript (deferred M3 follow-up; leave in backlog).
- Redesigning the SDK-teammate turn loop, the broker death machinery, or the redaction allowlist beyond what each deliverable strictly needs.
- Resolving/removing the D1, D2, and D4 backlog entries during the build — left to the post-build documenter/doc-sync phase so `doc/BACKLOG.md` has a single writer during the parallel build.
- Investigating the separate "persistent-teammate second-turn subprocess death" item (the stderr capture is its prerequisite diagnostic, but the investigation is not this slice).

## Assumptions

- **[Denied mutator set = `{Write, Edit, NotebookEdit, MultiEdit}`]** — *Default:* deny exactly these four; allow everything else (Read, Grep, Glob, Bash, WebFetch, Task, MCP tools). — *Rationale:* these are the standard file/state mutators; denying more would neuter legitimate plan-mode inspection, denying fewer would leave a write path open.
- **[TNM correlation key = `task_id` with arrival-order fallback]** — *Default:* key on `TaskNotificationMessage.task_id` (shown available at `sdk_teammate.py:370`); fall back to per-Task arrival order only if a runtime probe shows `task_id` is unstable/absent in 0.1.68. — *Rationale:* the idea names both; `task_id` is the stabler stable key, and the implementor is directed to probe real 0.1.68 fields before finalizing.
- **[Forced-crash test lives in `tests/test_live_stderr.py` (live, gated)]** — *Default:* add `test_forced_subprocess_crash_populates_stderr_tail` to the existing gated live file; if the crash can be driven through the real death path without a live API call, an integration variant may be added instead, but a green-suite structural guard (AT 18) covers its existence either way. — *Rationale:* forcing a real non-zero subprocess exit with real stderr is inherently a live/subprocess concern per the documented CLI-stderr caveat.
- **[`doc/BACKLOG.md` edited only by the D3a task during the build]** — *Default:* D1/D2/D4 backlog entries are resolved by the documenter phase post-merge, not by build tasks. — *Rationale:* avoids same-file collision across the parallel dispatch group.
- **[CLAUDE.md note added under existing "Known limitations" section]** — *Default:* add a new bullet titled with the literal `plan-mode write gate` stating claude-crew enforces write-blocking client-side regardless of SDK plan-gate behavior. — *Rationale:* the section currently has no plan-mode entry; the idea expects the doc to reflect the enforcement so it no longer reads as "you are not gated."
- **[Shutdown flake fixed by raising/parameterizing the 15s registration timeout]** — *Default:* raise the `_wait_for_registry_entry` budget (e.g. to 45-60s) and/or poll with backoff, keeping the deregistration assertions. — *Rationale:* lowest-risk fix that removes load-sensitivity without altering what the test proves; serializing the two tests is a fallback if a timeout bump proves insufficient.

## Open Questions

- (none)

## Validation

After feature-review PASS, the coordinator runs the full stub suite plus the live suite covering the touched surface, and confirms the plan-mode gate behaves end-to-end (the live test is now a hard pass, not xfail). Live tests require `CLAUDE_CREW_LIVE_TESTS=1` and a logged-in Claude CLI (token cost; minutes).

```bash
uv run pytest && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py tests/test_live_subagents.py tests/test_user_loader_live.py tests/test_live_stderr.py
```

## Task Breakout

```yaml
tasks:
  - name: plan-mode-write-gate
    description: |
      Deliverable 1 (centerpiece). Add a claude-crew-side plan-mode write gate.
      In claude_crew/sdk_teammate.py: add module-level _PLAN_MODE_DENIED_TOOLS =
      frozenset({"Write","Edit","NotebookEdit","MultiEdit"}); init
      self._effective_permission_mode = None in __init__; stash the resolved
      permission mode (spawn-arg-wins-then-role-pack, computed ~:1448) onto that
      attr before the client context opens; in _on_pre_tool_use, AFTER the
      existing memory-write guard, deny mutating tools (permissionDecision:deny)
      when _effective_permission_mode == "plan". Add stub implementation tests
      for every gate branch (deny Write/Edit/NotebookEdit/MultiEdit under plan;
      allow Read/Bash under plan; allow Write when non-plan; role-pack resolution
      feeds the gate). Flip tests/test_live_sdk.py::test_plan_mode_blocks_file_
      write_and_cwd_works to a hard pass (remove xfail). Add green-suite
      structural guards: the live test has no xfail marker (AT10), and CLAUDE.md
      "Known limitations" contains the literal `plan-mode write gate` (AT11).
      Add that CLAUDE.md note. This task edits sdk_teammate.py and test_
      sdk_teammate.py and is serialized with tnm-correlation via its dependsOn.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_sdk_teammate.py"
      - "tests/test_live_sdk.py"
      - "CLAUDE.md"
    implementationKind: behavior-change
    implementationTier: reasoning
    testCommand: |
      uv run pytest && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py -k plan_mode
  - name: tnm-correlation
    description: |
      Deliverable 2. Fix F7 subagent TNM<->hook correlation for SDK 0.1.68. In
      claude_crew/sdk_teammate.py: probe the real 0.1.68 TNM/hook fields, then
      re-key _record_task_notif / _end_turn correlation on a key that matches in
      0.1.68 (TaskNotificationMessage.task_id, or arrival-order per active Task
      call) instead of tool_use_id; carry the matching key on the closed-subagent
      scratch entry. Add stub tests: a normal dispatch with differing tool_use_id
      is correlated (no "no TNM for subagent" warning, AT12) and a genuinely-
      missing TNM still logs the warning + falls back to the hook outcome (AT13).
      Restore the FULL warning filter (remove the `intentionally excluded here`
      NOTE and the narrowed `subagent failure: status=`-only filter) in
      tests/test_live_subagents.py::test_pack_end_to_end and
      tests/test_user_loader_live.py::test_user_and_project_agents_invokable so
      both assert a warning-free run again (ATs 14-15), plus a green-suite
      structural guard that the NOTE literal is absent from both (AT16). Depends
      on plan-mode-write-gate because both edit sdk_teammate.py and
      test_sdk_teammate.py — serialized to avoid a same-file collision.
    dependsOn: [plan-mode-write-gate]
    acceptanceTests: [12, 13, 14, 15, 16]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_sdk_teammate.py"
      - "tests/test_live_subagents.py"
      - "tests/test_user_loader_live.py"
    implementationKind: behavior-change
    implementationTier: reasoning
    testCommand: |
      uv run pytest && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_subagents.py tests/test_user_loader_live.py
  - name: stderr-forced-crash-test
    description: |
      Deliverable 3b. Add the one genuinely-missing test: force a teammate
      subprocess to exit non-zero with REAL stderr (not a manually-injected
      probe line, not a mocked ProcessError — induce a real stderr-producing
      crash per the test_live_stderr.py CLI-stderr caveat) and assert
      stderr_tail_at_death (on the tombstone / get_teammate_status) is non-null
      and contains a marker from the induced crash. Name it
      test_forced_subprocess_crash_populates_stderr_tail in
      tests/test_live_stderr.py (live, gated). Add a green-suite structural guard
      in tests/test_stderr_crash_guard.py asserting that function exists (AT18).
      Disjoint from the sdk_teammate.py pair and from shutdown-flake-fix —
      dispatches in parallel.
    dependsOn: []
    acceptanceTests: [17, 18]
    taskTouches:
      - "tests/test_live_stderr.py"
      - "tests/test_stderr_crash_guard.py"
    implementationKind: behavior-change
    implementationTier: reasoning
    testCommand: |
      uv run pytest tests/test_stderr_crash_guard.py && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py
  - name: stderr-backlog-correction
    description: |
      Deliverable 3a. Correct the stale [2026-06-17] "Teammate death telemetry:
      capture & surface subprocess stderr" entry in doc/BACKLOG.md: mark it
      RESOLVED in-place (do NOT delete — the audit trail matters), record that
      the capture path is implemented and tested (shipped 2026-06-09), and that
      the M3 "null in practice" observation was a `stale-server artifact` (the
      running MCP server predated the ship). Must contain the literal `stale-
      server artifact`. Trivial prose edit; only file touched is doc/BACKLOG.md,
      so it is the single backlog writer during the parallel build.
    dependsOn: []
    acceptanceTests: [19]
    taskTouches:
      - "doc/BACKLOG.md"
    implementationKind: documentation
    implementationTier: trivial
    testCommand: |
      grep -q "stale-server artifact" doc/BACKLOG.md && grep -q "RESOLVED" doc/BACKLOG.md
  - name: shutdown-flake-fix
    description: |
      Deliverable 4. Make tests/test_shutdown_signals.py::test_sigterm_triggers_
      clean_exit_and_deregister and ::test_sigint_triggers_clean_exit_and_
      deregister resilient to full-suite load: raise/parameterize the 15s
      _wait_for_registry_entry timeout (and/or poll with backoff; serialize the
      two tests if a timeout bump is insufficient), keeping the clean-
      deregistration assertions intact. Validate by running them repeatedly
      under concurrent load. Disjoint file — dispatches in parallel.
    dependsOn: []
    acceptanceTests: [20, 21]
    taskTouches:
      - "tests/test_shutdown_signals.py"
    implementationKind: behavior-change
    implementationTier: mechanical
    testCommand: |
      uv run pytest tests/test_shutdown_signals.py -k "sigterm or sigint"
```

## Design Notes

- The PreToolUse hook already proves the deny-decision shape works (memory-write guard, `sdk_teammate.py:791-797`); the plan-mode branch mirrors it exactly — same `hookSpecificOutput` / `permissionDecision: "deny"` envelope — so the SDK contract for denial is already verified live.
- Implementor: before finalizing the D2 correlation key, run a runtime probe to confirm which `TaskNotificationMessage` field is stable in 0.1.68 (the backlog notes `task_id` differs from `tool_use_id` only on the latter; `task_id` is the candidate stable key). Do not guess from field names alone.
- The two `sdk_teammate.py`-touching tasks (D1, D2) MUST NOT run concurrently — the `dependsOn: [plan-mode-write-gate]` edge on `tnm-correlation` enforces serialization. Their `taskTouches` overlap on `claude_crew/sdk_teammate.py` and `tests/test_sdk_teammate.py` by design; the edge is what prevents the collision.
- The three parallel-eligible tasks (`stderr-forced-crash-test`, `stderr-backlog-correction`, `shutdown-flake-fix`) have provably disjoint `taskTouches` from each other and from the sdk_teammate.py pair.
- No `bin/spec-schema-check.sh` is present in this worktree (`.rr/` contains only `idea.txt` and `skew-warning.md`); this artifact was hand-validated against the ten required `##` headers and the Task Breakout schema invariants documented in the spec template.
```
