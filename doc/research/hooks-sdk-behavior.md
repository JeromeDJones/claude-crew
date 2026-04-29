# Hooks SDK Behavior — Spike Findings

Empirical results from `scripts/hooks_spike.py`.
Re-run to refresh; do not edit by hand.

## Summary

| Q | Finding |
|---|---------|
| Q1 — PreToolUse fires in SDK mode? | HOOKS FIRED — 2 hook invocation(s) confirmed |
| Q2 — PostToolUse fires in SDK mode? | HOOKS FIRED — 2 hook invocation(s) confirmed |
| Q3 — hooks fire for subagent tool calls? | HOOKS FIRED — 8 hook invocation(s) confirmed |

## Detail

### Scenario A: Q1/Q2 — Pre/PostToolUse hooks fire in top-level SDK session?

**Finding:** HOOKS FIRED — 2 hook invocation(s) confirmed

Tools called by model: `['Bash']`

New log lines from hook probe:
```
---HOOK-98280
---HOOK-98342
```

### Scenario B: Q3 — hooks fire for subagent tool calls inside SDK session?

**Finding:** HOOKS FIRED — 8 hook invocation(s) confirmed

Tools called by model: `['Agent', 'Bash', 'SendMessage', 'Read']`

New log lines from hook probe:
```
---HOOK-98430
---HOOK-98432
---HOOK-98494
---HOOK-98496
---HOOK-98498
---HOOK-98500
---HOOK-98502
---HOOK-98504
```
