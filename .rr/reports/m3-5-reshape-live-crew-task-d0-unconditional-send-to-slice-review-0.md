# Slice Review: m3-5-reshape-live-crew task=d0-unconditional-send-to

**Task:** `d0-unconditional-send-to` (index 0) — owns AT 1, 2, 3 (D0 unconditional send_to wiring).
**Cycle:** 0. **Verdict:** PASS.

## Summary

The `_has_out_edges` spawn-time gate is cleanly removed from `sdk_teammate.py:_run()` (diff `+17 -19`); the send_to MCP server + `_SEND_TO_TOOL_ID` allowed-tools entry are now wired for every `SdkTeammate`, with the `dict.fromkeys(...)` dedup preserved. The `__init__` comment on `self._neighbors` was correctly updated to drop the stale "None/empty → tool not injected" M2 claim. A new 8-test file covers AT 1/2/3, and 3 old-contract tests in `test_sdk_teammate.py` were flipped to the new contract. All three test commands exit 0 in my re-run.

## Slice adherence (AT 1/2/3)

- **AT 1 (no-out-edge teammate HAS send_to wired).** `_send_to_cfg = self._build_send_to_mcp_server()` runs unconditionally (~1547). Covered by `TestNoOutEdgesSendToWired`. ✓
- **AT 2 (deletion-detector, NAMED LITERAL `_has_out_edges`).** Independently grepped `sdk_teammate.py` → **0 occurrences**. `TestHasOutEdgesLiteralAbsent` encodes the guard correctly. ✓
- **AT 3 (teammates WITH out-edges still get send_to).** Covered by `TestWithOutEdgesSendToWired` (out; mixed in/out with dedup count==1). Delivery clause exercised by unchanged-and-green `test_scoped_send.py`. ✓

**Safety reasoning verified.** `broker.authorize_send` (broker.py:1560) is the real gate and is NOT in this diff — still raises `UnauthorizedEdgeError` when no forward edge authorizes. Tool presence ≠ edge authorization; boundary intact.

## Flipped-contract verification (coordinator-sanctioned scope amendment)

Confirmed the 3 flipped `TestSdkTeammateMcpServersWiring` tests encode the NEW contract rather than being gutted: each now asserts `_SEND_TO_MCP_SERVER_NAME in mcp_servers` AND retains the pack-declared-entry assertion. Correct positive encoding of D0. Per amendment, no `slice.scope.task-touches-violation` filed.

## Scope (Invariant 1)

Diff: `claude_crew/sdk_teammate.py` (declared), `tests/test_d0_send_to_unconditional.py` (new, declared), `tests/test_sdk_teammate.py` (coordinator-amended in-scope, mandatory for suite-green). No out-of-scope files.

## Non-regression

- `uv run pytest tests/test_d0_send_to_unconditional.py tests/test_scoped_send.py` → **30 passed** (exit 0).
- `uv run pytest tests/test_sdk_teammate.py` → **138 passed** (exit 0), including the 3 flipped tests.

## Findings

### Critical / High / Medium
_None identified._

### Low
- [LOW-01] `slice.quality.style` — the 3 flipped tests in `tests/test_sdk_teammate.py` add a function-body inline import `from claude_crew.sdk_teammate import _SEND_TO_MCP_SERVER_NAME`, violating the project's CLAUDE.md "Imports at module top" convention. (The new test file does it right.) Non-blocking. Fix: hoist to module import block.

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — AT 3's "message is delivered" outcome verified transitively (wiring here + unchanged `test_scoped_send.py`), not by a single end-to-end delivery test in this slice. Defensible: D0 changes only wiring; the LIVE ATs + feature-review are the end-to-end gate.

## Code-quality smoke

`sdk_teammate.py`: rationale comment accurate and load-bearing; no secrets, no swallowed exceptions, no dead code, dedup preserved. Test files: clear AT-mapped structure, good happy/sad coverage. Only nit is LOW-01.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| LOW-01 | Low | slice.quality.style | deferred-with-rationale | Cosmetic module-top-import nit; surface at retro for a one-line hoist. |
| INFO-01 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | AT 3 delivery verified transitively; end-to-end is the LIVE-AT/feature-review gate. |

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
