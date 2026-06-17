# Build Report — m3-adaptation-algebra (live tests) — cycle 0

## Verdict: PASS

**Test command:** `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_shape_adaptation.py -v`
**Result:** 3 passed in 3.01s — no warnings

---

## Tests Written

**New file:** `tests/test_live_shape_adaptation.py`

### Test inventory

| Class | Test | Covers |
|---|---|---|
| `TestSwapRoleResolvesAgainstRealFactory` | `test_swap_role_resolves_against_real_factory` | AT-LIVE-1: `adapt_shape(swap general→planner)` resolves against real `factory.known_roles()`; proposal registered in broker snapshot |
| `TestSwapUnresolvableRoleRejectedLive` | `test_swap_unresolvable_role_rejected_live` | AT-LIVE-2: bogus role → `ok:False, stage:"adapt"`, `unresolved_roles` populated, NO proposal registered |
| `TestAdaptApproveInstantiateRealCrew` | `test_adapt_approve_instantiate_real_crew` | AT-LIVE-3: full adapt→approve→instantiate flow; real crew spawned; real model turn proven; topology recorded in broker |

---

## Live Run Output (final)

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
asyncio: mode=Mode.AUTO
collected 3 items

tests/test_live_shape_adaptation.py::TestSwapRoleResolvesAgainstRealFactory::test_swap_role_resolves_against_real_factory PASSED [ 33%]
tests/test_live_shape_adaptation.py::TestSwapUnresolvableRoleRejectedLive::test_swap_unresolvable_role_rejected_live PASSED [ 66%]
tests/test_live_shape_adaptation.py::TestAdaptApproveInstantiateRealCrew::test_adapt_approve_instantiate_real_crew PASSED [100%]

============================== 3 passed in 3.01s ===============================
```

No "Task was destroyed but it is pending!" warnings — all SdkTeammate background tasks shut down cleanly.

---

## Implementation Notes

### Harness pattern
- **Gate:** `pytestmark = pytest.mark.skipif(os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1", ...)` — mirrors `test_live_sdk.py`.
- **Real factory:** each test overrides the conftest autouse stub-mode pin via `monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")` before calling `default_factory()`, so the factory carries `known_roles` and `resolve_role` from the real merged pack.
- **MCP session:** `_client(broker=broker, factory=real_factory)` → `make_server` + `create_connected_server_and_client_session`, then `await s.initialize()` — mirrors `test_shape_gate.py`.
- **Result unwrap:** `_content_json()` handles both `structuredContent` and `content[0].text` paths.
- **Teardown:** `finally: await broker.shutdown_all()` in every test — no subprocess leaks. The clean teardown was verified: zero "Task destroyed" warnings.

### Key fix: sender-targeted reply wait (`_await_reply_from`)
The generic count-based `_wait_for_lead` helper could be fooled by broker system notifications (e.g. `shape_resolved`, `shape_instantiated`) that arrive in the lead inbox before the teammate's real response. The fix: `_await_reply_from(broker, tid, timeout)` scans all lead messages and returns the first one with `msg.sender == tid`, ignoring system notifications.

**Probe confirmed the response is real:** a manual probe showed the planner teammate responded with `{'text': 'READY.', 'from': 'planner'}` from sender `t-c911e3cc1031` — a real model turn. The ~3s timing is legitimate: the `planner` agent is a lightweight leaf node with no Bash/Task tools and a trivial prompt, so its subprocess starts and responds quickly.

### Why "Task destroyed" warnings disappeared
In the original cycle-0 test, `shutdown_all()` was called before the SdkTeammate background tasks (`_liveness_poll_loop`) had started executing. The tasks existed but hadn't run yet; cancellation left them in a pending-but-never-cancelled state. After sending a real prompt and awaiting a real response (proving the subprocess fully launched and processed a turn), `shutdown_all()` has a properly-started task to cancel, and the cleanup completes cleanly.

### Roles used
`explorer`, `general`, `planner` — all three bundled subagents (`claude_crew/subagents/*.md`), always present on any checkout, no Bash/Task tools.

### Topology assertion (AT-LIVE-3)
The `swap` verb changes the ROLE of a slot, not the slot name. Slot `"general"` keeps its name but its role becomes `"planner"`. The topology's `slot_to_teammate` maps both original slot names (`explorer`, `general`) to their real `teammate_id`s (e.g. `t-c911e3cc1031`).

---

## Files Changed

| File | Operation |
|---|---|
| `tests/test_live_shape_adaptation.py` | Created (new file) |
