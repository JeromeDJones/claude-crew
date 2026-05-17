# Auto-Memory Disable — SDK Spike Findings

Empirical results from `scripts/auto_memory_disable_spike.py`.
Sentinel phrase: `coordinator-in-the-loop is the moat`.
Re-run to refresh; do not edit by hand.

## Summary

| Config | setting_sources | DISABLE_AUTO_MEMORY | Memory loaded? | Verdict |
|---|---|---|---|---|
| A | defaults (control) | — | True | memory LOADED |
| B | defaults + DISABLE_AUTO_MEMORY | — | False | memory NOT loaded |
| C | setting_sources=[] | — | True | memory LOADED |
| D | setting_sources=[] + DISABLE | — | False | memory NOT loaded |

## Replies

**A — defaults (control)**:

> Yes.

`[Coordinator-in-the-loop is the moat](feedback_coordinator_in_the_loop_is_the_moat.md) — Reject features that route around the coordinator's judgement (e.g., #20 peer messaging, backlogged 2026-05-17). Substrate's edge is human-supervised observability, not autonomous chatter.`

**B — defaults + DISABLE_AUTO_MEMORY**:

> not in context

**C — setting_sources=[]**:

> Yes.

"[Coordinator-in-the-loop is the moat](feedback_coordinator_in_the_loop_is_the_moat.md) — Reject features that route around the coordinator's judgement (e.g., #20 peer messaging, backlogged 2026-05-17). Substrate's edge is human-supervised observability, not autonomous chatter."

**D — setting_sources=[] + DISABLE**:

> not in context
