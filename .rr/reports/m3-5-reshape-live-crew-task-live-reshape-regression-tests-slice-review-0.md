# Slice Review: m3-5-reshape-live-crew task=live-reshape-regression-tests

**Task:** `live-reshape-regression-tests` (index 4) — owns AT 17, 18. **Cycle:** 0. **Verdict:** PASS.

## Summary

Two gated live regression guards (`tests/test_live_reshape.py`) for AT 17 (no-respawn add) and AT 18 (swap routing). Structural gate clean (2 collect + skip, zero errors). On code review both genuinely pin the live reshape path — capture teammate ids before reshape, assert identity/tombstone invariants against real broker state, and require a REAL SDK turn (running teammate calls `send_to`, recipient inbox shows delivery from the expected sender). Neither is stub-satisfiable. No Critical/High.

## Slice adherence — AT 17/18 test quality

### Structural gate — PASS
`uv run pytest tests/test_live_reshape.py` (no env var) → `2 skipped`, zero errors. Module `pytestmark = skipif(CLAUDE_CREW_LIVE_TESTS != "1")` mirrors the existing live-test convention.

### AT 17 — `test_no_respawn_add` — genuinely pins (NOT hollow)
- Captures impl id BEFORE (`impl_tid = slot_to_tm["impl"]`) and asserts UNCHANGED after (`new_impl_tid == impl_tid`) — the no-respawn D4 invariant.
- Asserts `impl_tid in reshape_result["actions"]["informed"]` (real `_actions["informed"]` key, server.py:1561).
- Real SDK turn + delivery proof: sends impl a lead prompt to call `send_to('reviewer', {...})`, then polls the REVIEWER's inbox for a message whose sender is impl_tid. The lead's prompt is sender=LEAD_ID so it can't satisfy the filter — only impl running a live turn and invoking send_to (authorized by the live impl→reviewer edge) produces it. Not stub-satisfiable. **Headline guard.**

### AT 18 — `test_swap_routing` — genuinely pins (NOT hollow)
- Old teammate tombstoned: `broker.list_crew()` shows old worker present but `not alive`.
- New teammate holds slot: `worker_new_tid != worker_old_tid`, new alive.
- Slot-NAME send_to resolves to replacement: sends `sender` a prompt to `send_to('worker', {...})`, polls the NEW worker's inbox for a message from sender_tid. Exercises `_resolve_scoped_recipient` scanning `reversed(_topologies)` so post-swap topology wins; a stale-topology regression routes to the dead worker → timeout → fail. Correct D5 pin.

### Gating / fixture / auth / setup — correct
Skip gate mirrors convention; `monkeypatch.setenv(CLAUDE_CREW_TEAMMATE_MODE, sdk)` overrides conftest stub pin; `default_factory()` real merged pack; `known_roles()` fail-fast sanity check. In-process MCP session + real `Broker()` — no UI server so no free-port machinery needed (correctly omitted); no HOME monkeypatch so real `~/.claude` creds used (auth-preservation helper correctly not required). Gate-approval drives `broker.resolve_proposal` directly while the reshape Task blocks on `await_proposal` — sound half-duplex workaround.

## Scope

Exactly one new file: `tests/test_live_reshape.py`. No source mutation, no other edits. Clean.

## Non-regression

Per SPECIAL REVIEW MODE, the live suite was NOT run with the env var (coordinator runs it at validation). Structural gate PASS. Test-only slice, no behavioral surface.

## Code-quality smoke

Module-top imports; `asyncio.get_running_loop()` (not deprecated get_event_loop); bounded `asyncio.wait_for` waits (240s); `try/finally` with `broker.shutdown_all()`; delivery assertions check arrival-from-sender + non-empty payload (relay-safe, not exact content); small tokens (`reshapeok`/`swapok`).

## Findings

### Critical / High / Medium / Low
_None identified._

### Info
- [INFO-01] `slice.test.live-inherent-flake` — both tests depend on the live LLM actually invoking `send_to`; if the model declines/forgets, the inbox poll times out. Inherent to live behavioral guards, well-mitigated (imperative prompt, arrival-not-content assertion, small tokens, 120s poll). **Note for the coordinator's live gate: a timeout may be a live-LLM miss, not a routing regression — re-run to distinguish.**

## Findings Disposition

| ID | Severity | Tag | Disposition |
|----|----------|-----|-------------|
| INFO-01 | Info | slice.test.live-inherent-flake | Acknowledged — inherent to live tests, well-mitigated; coordinator re-runs on timeout |

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
