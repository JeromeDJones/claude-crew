# Validation: m3-5-reshape-live-crew

## Verdict
PASS

## Stub suite
`uv run pytest` → **1707 passed, 41 skipped (incl. live AT-17/18), 1 xfailed, 0 failed** (~248s). Coordinator-verified 3×; feature-reviewer independently re-ran green.

## Live regression guards (coordinator-run, real crews, cloud API)
`CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py` → **2 passed**.
- AT 17 (no-respawn add): same-id impl teammate reaches the live-added reviewer via a real send_to turn on a direct edge; reviewer inbox receives the message from impl.
- AT 18 (swap): slot-name send_to resolves to the replacement worker (post-swap topology wins); new worker inbox receives from sender.

## Root-cause note (validation-gate fix)
Initial live run: both timed out. Isolated probe proved send_to is CALLED and succeeds (outcome=ok) — but the tests used gated edges (ShapeEdge default), which per M2 `_send_routed` route the message to LEAD, not the recipient's inbox. Fix (test-setup only, assertions unchanged): direct-mode edges + `general` acting roles + 180s poll headroom. Product is sound; no prior live test had ever driven a real teammate send_to (M2 tested the broker side at stub level only).

## Pass criteria
(1) full stub suite exits 0 ✓  (2) both live guards pass ✓
