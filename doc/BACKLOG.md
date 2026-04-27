# BACKLOG

Out-of-scope observations from feature work. Surfaced during implementation, logged here, addressed when prioritized.

Format per workflow.md: `## [YYYY-MM-DD] Feature: <name>` then bulleted entries (What / Where / Why / Suggested action).

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
