# Hooks SDK Behavior — Spike Findings

Empirical results from `scripts/hooks_spike.py`.
Re-run to refresh; do not edit by hand.

## Summary

| Q | Finding |
|---|---------|
| Q1 — PreToolUse fires in SDK mode? | HOOKS FIRED — 2 hook invocation(s) confirmed |
| Q2 — PostToolUse fires in SDK mode? | HOOKS FIRED — 2 hook invocation(s) confirmed |
| Q3 — hooks fire for subagent tool calls? | HOOKS FIRED — 4 hook invocation(s) confirmed |
| Q4 — matchers filter correctly in SDK mode? | MATCHER WORKS — 1 hook fired (Bash only, Read correctly excluded) |

## Detail

### Scenario A: Q1/Q2 — Pre/PostToolUse hooks fire in top-level SDK session?

**Finding:** HOOKS FIRED — 2 hook invocation(s) confirmed

Tools called by model: `['Bash']`

New log lines from hook probe:
```
---HOOK-2482
---HOOK-2544
```

### Scenario B: Q3 — hooks fire for subagent tool calls inside SDK session?

**Finding:** HOOKS FIRED — 4 hook invocation(s) confirmed

Tools called by model: `['Agent', 'Bash']`

New log lines from hook probe:
```
---HOOK-2634
---HOOK-2636
---HOOK-2698
---HOOK-2700
```

### Scenario E: Q4 — do matchers filter correctly in SDK mode?

**Finding:** MATCHER WORKS — 1 hook fired (Bash only, Read correctly excluded)

Tools called by model: `['Bash', 'Read']`

New log lines from hook probe:
```
---HOOK-2849
```
