# MCP Cold-Start Behavior — Spike Findings

Empirical results from `scripts/mcp_cold_start_spike.py`.
Re-run to refresh; do not edit by hand.

## Summary

| Scenario | Finding |
|---|---------|
| A — Atlassian × 3 cold starts | CONSISTENT PASS — Atlassian MCP loads on all 3 cold starts |
| B — claude-crew stdio × 3 cold starts | CONSISTENT FAIL — claude-crew stdio MCP unavailable in SDK mode |
| C — Atlassian + 5s warm-up delay | DELAY CLOSES GAP — 5s warm-up before first call succeeds |

## Detail

### Scenario A: Atlassian HTTP MCP × 3 cold starts

**Finding:** CONSISTENT PASS — Atlassian MCP loads on all 3 cold starts

**Per-run results:**

- Run 1: `PASS` — tools called: `['mcp__atlassian__atlassianUserInfo']`
- Run 2: `PASS` — tools called: `['mcp__atlassian__atlassianUserInfo']`
- Run 3: `PASS` — tools called: `['mcp__atlassian__atlassianUserInfo']`

### Scenario B: claude-crew stdio MCP × 3 cold starts

**Finding:** CONSISTENT FAIL — claude-crew stdio MCP unavailable in SDK mode

**Per-run results:**

- Run 1: `FAIL — TOOL_MISSING` — tools called: `[]`
- Run 2: `FAIL — TOOL_MISSING` — tools called: `[]`
- Run 3: `FAIL — TOOL_MISSING` — tools called: `[]`

### Scenario C: Atlassian with 5s warm-up delay

**Finding:** DELAY CLOSES GAP — 5s warm-up before first call succeeds

**Per-run results:**

- Run 1: `PASS` — tools called: `['mcp__atlassian__atlassianUserInfo']`
