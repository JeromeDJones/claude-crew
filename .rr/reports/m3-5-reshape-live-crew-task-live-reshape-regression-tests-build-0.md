# Build Report — live-reshape-regression-tests — cycle 0

**Task:** `live-reshape-regression-tests` (index 4)
**Spec:** `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/specs/m3-5-reshape-live-crew.md`
**Acceptance Tests covered:** AT 17, AT 18
**Working directory:** `.rr-worktrees/live-reshape-regression-tests`

---

## What was done

Authored `tests/test_live_reshape.py` with two gated live regression guards
covering AT 17 (no-respawn add) and AT 18 (swap routing), mirroring the
structure of `tests/test_live_shape_adaptation.py`.

Both tests are gated by `pytestmark = pytest.mark.skipif(CLAUDE_CREW_LIVE_TESTS != "1")`.

---

## Structural gate result (no env var)

```
uv run pytest tests/test_live_reshape.py -v
============================= test session starts ==============================
collected 2 items

tests/test_live_reshape.py::TestLiveReshapeNoRespawnAdd::test_no_respawn_add SKIPPED [ 50%]
tests/test_live_reshape.py::TestLiveReshapeSwap::test_swap_routing            SKIPPED [100%]

============================== 2 skipped in 0.90s ==============================
```

**Zero collection errors. Zero import errors. Both tests skip cleanly. ✓**

---

## Git diff

```
?? tests/test_live_reshape.py   (new untracked file — not staged, not committed)
```

No other files modified. Hard constraint satisfied: no `git add/commit/push`.

---

## Test design summary

### AT 17 — `TestLiveReshapeNoRespawnAdd.test_no_respawn_add`

1. Overrides conftest stub-mode pin: `CLAUDE_CREW_TEAMMATE_MODE=sdk`.
2. Uses `default_factory()` (real merged pack with `known_roles/resolve_role`).
3. Instantiates a 1-node base crew (`impl/explorer`) via the real SDK.
4. Runs `reshape_crew("add_node")` as an `asyncio.Task` — it blocks on `await_proposal`.
5. Drives gate approval via `broker.resolve_proposal()` directly (avoids MCP half-duplex
   concurrency issue — no second MCP call needed while reshape is blocking).
6. Asserts:
   - `result["topology"]["slot_to_teammate"]["impl"] == impl_tid` — **not respawned** (D0/D4)
   - `impl_tid in result["actions"]["informed"]` — inform message was sent
   - Sends impl an explicit prompt to call `send_to("reviewer", {...})`
   - Polls `broker.get_messages(recipient=reviewer_tid, from_sender=impl_tid)` — a
     message in the reviewer's inbox from impl proves a **real SDK turn** fired and
     live topology authorization worked end-to-end. A stub would never produce this.

### AT 18 — `TestLiveReshapeSwap.test_swap_routing`

1. Same sdk-mode + real-factory setup.
2. Instantiates a 2-node base crew (`sender/explorer → worker/general`).
3. Runs `reshape_crew("swap", {"slot": "worker", "role": "planner"})` as asyncio Task;
   approves gate via `broker.resolve_proposal()`.
4. Asserts:
   - Old `worker` teammate is tombstoned (`info.alive == False` in `broker.list_crew()`)
   - `worker_new_tid != worker_old_tid` — a new teammate occupies the slot
   - Sends `sender` a prompt to call `send_to("worker", {...})`
   - Polls `broker.get_messages(recipient=worker_new_tid, from_sender=sender_tid)` —
     message in the **new** worker's inbox proves `_resolve_scoped_recipient` scanned
     `reversed(_topologies)` and resolved the slot via the latest (post-swap) topology.
     A regression (old topology winning) would route to the dead worker and time out.

Both tests require real cloud API calls — they cannot be satisfied by stub structure.

---

## Why live execution is deferred

Per project policy (CLAUDE.md § "Run live tests before merging"):

> Live SDK tests... spawn real `claude` subprocesses against the cloud Anthropic API —
> they cost tokens and take minutes.

Running nested real SDK crews from inside a teammate subprocess is unreliable (context
window pressure, 600s turn cap). The coordinator runs them live at the validation gate
in a direct session with full API access:

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py
```

Pass criteria (from the spec):
1. Both tests execute (not skipped).
2. `TestLiveReshapeNoRespawnAdd::test_no_respawn_add` — PASS: same-id impl reaches
   the live-added reviewer via a real send_to turn.
3. `TestLiveReshapeSwap::test_swap_routing` — PASS: slot-name send_to resolves to the
   replacement worker after a live swap.

---

## Verdict

Structural gate: **PASS** (2 skipped, 0 errors, 0 failures).
Live execution: deferred to coordinator's validation gate per project policy.
