# Slice Review: click-to-view-tool-output task=broker-lookup

## Inputs verified

- Spec/breakout: `broker-lookup` description — `Broker.get_tool_output(teammate_id, tool_use_id)` walks live + tombstoned, delegates to `Teammate.get_tool_output`.
- Build report: PASS, 5/5.
- Changed files: `claude_crew/broker.py` (+24/-1), new `tests/test_broker_tool_output.py`.

## Check 1 — Slice adherence

- `Broker.__init__` adds `_dead_teammates: dict[str, Teammate]`.
- `_tombstone_teammate` step 6: stashes the live `teammate` ref into `_dead_teammates` immediately before `_teammates.pop(...)`. Guarded by `if teammate is not None`.
- `Broker.get_tool_output` checks `_teammates` first, falls back to `_dead_teammates`, returns `None` for unknown id, else delegates to `tm.get_tool_output(tool_use_id)` (already returns `None` on evicted entries).
- All five test cases (unknown miss, evicted miss, live hit, tombstoned hit, tombstoned-evicted miss) pass.

Matches breakout description and design decision "Broker exposes a lookup, not direct dict access".

## Check 2 — Non-regression

Sensitive-path scrutiny of `_tombstone_teammate`:

- Step 5 (alive=False frozen tombstone before pop) — unchanged.
- Step 6 — only addition is stash-before-pop; the `pop(..., None)` line itself unchanged. No reordering vs. steps 5/7/8/8b.
- Step 7 (in-flight bounce), step 8 (inbox drain via `_inboxes.pop`), step 8b (`_close_open_tools(reason=...)` using the still-held `teammate` local) all intact and reachable — they read the `teammate` local captured before pop, so stashing the same ref in `_dead_teammates` is side-effect-free.

Test results:
```
tests/test_broker_tool_output.py + test_tool_outputs.py + test_redaction_output.py + test_broker.py
→ 130 passed in 2.25s
```

Tombstone-suite (`test_broker.py`) including death/kill ordering and bounce tests all green.

## Check 3 — Code-quality smoke

- **Info** [slice.memory]: `_dead_teammates` grows unbounded over the broker's lifetime — every tombstoned teammate retains its `Teammate` object (and through it, `_tool_outputs` up to ~200KB plus deques) forever. Matches existing `_info`-tombstone pattern (which also retains the frozen record indefinitely), so not a new class of leak, but worth a future eviction policy if crews churn heavily. Does not affect this verdict.
- **Info** [slice.naming]: `_dead_teammates` is dict-typed and lives alongside `_teammates`; clear and consistent.
- Forward-reference quoted return type `"str | None"` is fine under `from __future__ import annotations` (no harm, just stylistically inconsistent — surrounding code doesn't quote).

No High/Critical findings.

## Verdict

PASS — slice adherent, non-regressive (tombstone path verified), code-quality clean.
