# BACKLOG

Out-of-scope observations from feature work. Surfaced during implementation, logged here, addressed when prioritized.

Format per workflow.md: `## [YYYY-MM-DD] Feature: <name>` then bulleted entries (What / Where / Why / Suggested action).

---

## [2026-04-27] Feature: #7 subagent-activity envelopes (T5 + sentinel chain follow-ups)

### Phase 3 Scenario 4 BDD comment misleads — `abandoned_batch` vs `subagent_result`
- **What**: The Phase 3 BDD for Scenario 4 says expected output is `subagent_abandoned_batch` with `in_flight_subagents_at_death == 1`. Actual: `subagent_result(tnm_missing=True)` and `in_flight_subagents_at_death == 0` — because `_tombstone_teammate` calls `_end_turn(close_tools=False)` at step 2, draining `_closed_subagent_scratch` before `_close_open_subagents` runs at step 8b. Behavior is semantically correct; the BDD text is wrong.
- **Where**: `doc/features/FEATURE-subagent-activity-envelopes.md` Phase 3 Scenario 4; `tests/test_e2e_subagent_telemetry.py::test_kill_with_scratch_entry_emits_result_from_end_turn`
- **Why it matters**: Future readers using the FEATURE doc as a tombstone-behavior reference get a wrong mental model.
- **Suggested action**: Update the Scenario 4 BDD block to match actual behavior. Add prose: "`_end_turn(close_tools=False)` at tombstone step 2 drains scratch entries before `_close_open_subagents` runs." Trivial doc fix.

### `broker is None` guard in D3 branch skips write but still populates dict
- **What**: In `_on_pre_tool_use` D3 branch, `write_tool_event("subagent_spawn", ...)` is gated on `if broker is not None`. If broker is None, write is skipped but `self._subagent_uses[tool_use_id] = ...` still runs — technically violating F2 (write before store). In practice, hooks only fire when broker is set; None branch is unreachable in production.
- **Where**: `claude_crew/sdk_teammate.py` D3 branch in `_on_pre_tool_use`
- **Why it matters**: Subtle inconsistency if the path ever becomes reachable in tests or future refactors. Inner-4 sentinel flagged as non-blocking.
- **Suggested action**: Either (a) move dict store inside the `broker is not None` block, or (b) add a comment documenting the None branch is unreachable in production. Prefer (b) — skipping dict store would silently break `status_snapshot` in-flight visibility.

### `TaskStartedMessage` / `TaskProgressMessage` not consumed in v1
- **What**: Both explicitly deferred in Phase 2 (co-architect). `TaskStartedMessage` adds spawn→running timing gap; `TaskProgressMessage` is the streaming-activity firehose. Neither has a current consumer.
- **Where**: `claude_crew/sdk_teammate.py` `_collect_response_text` (only `TaskNotificationMessage` handled)
- **Why it matters**: Future feature candidate — streaming subagent activity, richer timing analytics.
- **Suggested action**: Route as a separate feature when a consumer surfaces. `TaskStartedMessage` is S-size; `TaskProgressMessage` re-opens push semantics question and is M-size.

---

## [2026-04-27] Feature: #8 tool-execution telemetry via SDK hooks (sentinel final-review follow-ups + session observations)

### `outcome="orphan_post"` is a sixth value beyond D11's documented five
- **What**: D11 in the F8 spec enumerates five `tool_end.outcome` values: `ok`/`failed`/`interrupted`/`abandoned`/`killed`. The inner-4 fix introduced a sixth — `orphan_post` — for the post-without-pre audit case. Replay tooling consumers reading D11 won't know about it.
- **Where**: `claude_crew/sdk_teammate.py` (orphan-Post writer), `doc/features/FEATURE-tool-execution-telemetry.md` D11 spec
- **Why it matters**: Replay tooling joining `tool_start`/`tool_end` by `tool_use_id` could mis-classify orphan records or fail enum validation.
- **Suggested action**: Update D11 in the FEATURE spec to document the sixth value AND its semantics (audit-only, `duration_seconds: None`, no matching `tool_start`). Alternatively, formalize via an enum in `redaction.py` or a constants module so the source of truth is code, not prose. Trivial doc fix.

### Stale `_get_redaction_version()` ImportError fallback in teammate.py
- **What**: `claude_crew/teammate.py:50-62` has a try-import + `"v1"` string fallback for `REDACTION_VERSION`, with a TODO saying "remove once T1 merged." T1 is merged.
- **Where**: `claude_crew/teammate.py` lines ~50-62
- **Why it matters**: Dead code, confusing to future readers (why is there a fallback?), TODO debt.
- **Suggested action**: Replace with direct `from claude_crew.redaction import REDACTION_VERSION` at module top. Verify no circular-import issue (shouldn't be — redaction has no claude_crew imports). 5-minute change.

### Live-probe `"echo"`-in-args_summary content assertion is model-behavior-dependent
- **What**: `tests/test_e2e_tool_telemetry.py::test_live_a2_probe_real_bash_observed` asserts `"echo"` appears in the `args_summary` of the captured tool_start line. If a future model uses `printf` or `cat <<EOF` to satisfy the prompt, the assertion fails despite the substrate working correctly.
- **Where**: `tests/test_e2e_tool_telemetry.py` live probe assertions
- **Why it matters**: Live probe should test substrate facts (tool_name, tool_use_id pairing, redaction_version, transcript order), not model output choices. Today's assertion is fine but flaky-shaped.
- **Suggested action**: Convert content assertions to "soft/informational" (log but don't fail), keep structural assertions (tool_name=Bash, tool_use_id pairs, redaction_version="v1") as hard assertions. Pattern worth formalizing as a project convention: live-probe assertions check the substrate, not the model. Sentinel-flagged in final review.

### Process pattern: parallel sentinel + co-architect review at gates produced a second convergent catch
- **What**: Sentinel-f8-p1 and co-architect-f8 independently flagged the duplicate-`tool_end` gap (D9 abandon → late Post → second tool_end via Post-without-Pre path) at Phase 2 review. F6's similar convergence was on the in-flight envelope handoff. Two features in a row, two production-impact catches that neither track alone produced.
- **Where**: SDD workflow, Phase 1 + Phase 2 gates
- **Why it matters**: Two-track parallel review is currently a "thing we do" — formalizing it would surface the convergence pattern as a "this would have bit us" indicator and bake the cost (two reviewer teammates) into the process explicitly.
- **Suggested action**: Update `~/.claude/skills/sdd-workflow/SKILL.md` to make parallel sentinel + co-architect review at Phase 1 + Phase 2 a standing requirement, with explicit attention to convergent findings as a high-confidence catch signal. Or, more conservatively, add to the project journal as a confirmed pattern to apply to the next feature, then formalize after one more confirmation.

### Process pattern: lead polling discipline gap
- **What**: Three times this session, lead dispatched teammates and didn't poll for replies until prompted. One reply (sentinel-f8-p1 Phase 2 review) sat in the inbox for ~17 minutes before the lead noticed. The notification mechanism is pull-only; cursor-based `get_messages` requires the lead to actively poll.
- **Where**: lead orchestration during multi-teammate dispatch
- **Why it matters**: Creates visible session-pacing friction. Jerome had to ask "did we check back in?" three times.
- **Suggested action**: Either (a) implement the deferred "Hook-based ambient inbound delivery to lead" feature in PRODUCT-VISION (structural fix), or (b) bake "poll within N minutes of any `send_to` expecting a reply" into lead workflow guidance (process band-aid until (a) ships). Probably (b) first; (a) when MMM-4b real-task validation surfaces enough pain to justify the effort.

---

## [2026-04-27] Feature: #6 telemetry-based teammate liveness (sentinel inner-4 + final review follow-ups)

### MCP-tool-surface coverage gap in T5 e2e
- **What**: T5's `tests/test_telemetry_e2e.py` calls `broker.send()` / `broker.broadcast()` / `broker.get_teammate_status()` directly rather than going through the FastMCP tool surface in `make_server()`.
- **Where**: `tests/test_telemetry_e2e.py` (all 6 scenarios)
- **Why it matters**: Server tools are thin pass-throughs; `test_server.py` covers the MCP layer separately. But for "real wire test" coverage of `_err("teammate_dead", ...)` JSON shape and the `skipped_dead` field's serialization back through FastMCP, an end-to-end through `make_server()` would close the small remaining gap.
- **Suggested action**: When Feature #7 ships, add ≥1 e2e scenario per substrate feature that exercises the full MCP wire (server.py → broker → teammate). Could refactor T5's scenarios to share a common server-fixture, or add a single multi-scenario "wire test" alongside.

### `POST_INTERRUPT_DRAIN_SECONDS` monkeypatch in 2 T4 tests is a fake-fidelity issue
- **What**: Two tests in `test_sdk_teammate.py` (`test_backstop_fires_interrupt_succeeds` and one other) monkeypatch `POST_INTERRUPT_DRAIN_SECONDS = 0.05` because `ProgrammableSDKClient._hang` stays True after `interrupt()` is called.
- **Where**: `tests/test_sdk_teammate.py`, plus the underlying fake at `tests/fakes/programmable_sdk_client.py`
- **Why it matters**: The A2 live probe (Feature #6 T5) confirmed the real SDK terminates `receive_response` on `interrupt()` — so production has no hang here. The monkeypatch is purely a fake-shape band-aid, not papering over a real bug. Cosmetic test smell only.
- **Suggested action**: Enrich `ProgrammableSDKClient` to flip `_hang=False` (or terminate the receive_response generator) when `interrupt()` is called. Removes the monkeypatch. ~10-line change.

### SDK exception name-matching is brittle to SDK refactor
- **What**: `sdk_teammate.py:358-363` matches `"ProcessError"`, `"CLIConnectionError"`, `"BrokenPipe"` substrings against `type(exc).__name__` to decide whether to set `_death_in_flight_envelope` and `_death_suspected`.
- **Where**: `claude_crew/sdk_teammate.py` lines ~358-363 (exception handling in `_handle_one_turn`)
- **Why it matters**: An SDK class rename or a wrapping exception silently bypasses the in-flight handoff path. Worker would then send a generic `api_error` envelope and `_death_in_flight_envelope` would never be set — SC-5b clause 1 silently fails.
- **Suggested action**: Replace substring match with `isinstance` against the actual SDK exception types from `claude_agent_sdk.types`. The earlier SDK spike showed the import surface is opaque from `sdk_teammate.py` today, so this requires a small import re-arch. Pin the SDK version in `pyproject.toml` simultaneously to bound upgrade risk.

### Probe failure inside `_handle_teammate_death` exits the poll task without retombstoning
- **What**: If `teammate.status_snapshot()` raises an exception other than `AttributeError` (only that one is caught at `broker.py:148`), the death handler propagates up to `_liveness_poll_loop`, gets logged, and the loop returns — leaving the teammate alive in `_info` forever.
- **Where**: `claude_crew/broker.py:148` (narrow except clause) interacting with `claude_crew/sdk_teammate.py:_liveness_poll_loop`
- **Why it matters**: Edge case (probe-inside-handler is rare). But the failure mode is silent and unrecoverable without operator intervention.
- **Suggested action**: Either (a) catch broader inside `_tombstone_teammate` and continue with degraded death record, or (b) make `_liveness_poll_loop` retry the death handler on next tick rather than exiting on first handler failure. Prefer (b) — failure is observable and retried, no silent leak.

---

<!-- Add new entries above. Keep this file ordered newest-first. -->
