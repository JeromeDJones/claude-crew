# MCP SDK Behavior — Spike Findings

Empirical results from `scripts/mcp_spike.py`.
Re-run to refresh; do not edit by hand.

## Summary

| Q | Finding |
|---|---------|
| Q2 — global MCP loads in SDK mode? | YES — global MCP loads |
| Q1 — mcp_servers merge or replace? | REPLACE — claude-crew tools absent when explicit mcp_servers is set |
| Q3a — parent tools allowlist blocks MCP? | NO — MCP tools accessible despite tools allowlist |
| Q3b — subagent tools allowlist blocks MCP? | YES — AgentDefinition.tools BLOCKS MCP tools in subagent |
| Q3c — wildcard works in AgentDefinition.tools? | NO — wildcard does NOT work; must enumerate MCP tools by name |

## Detail

### Scenario A: Q2 — global MCP loads in SDK mode?

**Finding:** YES — global MCP loads

Tools called: `['mcp__atlassian__atlassianUserInfo']`

Model text (truncated to 500 chars):
```
```json
{
  "account_id": "70121:d4f339bb-9de3-43b7-ae35-f682bd67fbf9",
  "account_type": "atlassian",
  "account_status": "active",
  "name": "Jerome Jones",
  "picture": "https://secure.gravatar.com/avatar/4c0a79ea1b397e5f635bd0619893dfdb?d=https%3A%2F%2Favatar-management--avatars.us-west-2.prod.public.atl-paas.net%2Finitials%2FJJ-6.png",
  "email": "jeromedjones@outlook.com",
  "characteristics": {
    "not_mentionable": false
  },
  "nickname": "Jerome Jones",
  "zoneinfo": "America/Denver",
```

### Scenario B: Q1 — mcp_servers merges with or replaces global config?

**Finding:** REPLACE — claude-crew tools absent when explicit mcp_servers is set

Tools called: `['mcp__atlassian__atlassianUserInfo']`

Model text (truncated to 500 chars):
```
**Result 1: ✓ Confirmed**

`mcp__atlassian__atlassianUserInfo` succeeded. User details:
- **Name:** Jerome Jones
- **Email:** jeromedjones@outlook.com
- **Account ID:** 70121:d4f339bb-9de3-43b7-ae35-f682bd67fbf9
- **Status:** Active
- **Timezone:** America/Denver

---

**Result 2:**

TOOL_MISSING: mcp__claude_crew__list_crew
```

### Scenario C: Q3a — ClaudeAgentOptions.tools blocks MCP tools?

**Finding:** NO — MCP tools accessible despite tools allowlist

Tools called: `['mcp__atlassian__atlassianUserInfo']`

Model text (truncated to 500 chars):
```
```json
{
  "account_id": "70121:d4f339bb-9de3-43b7-ae35-f682bd67fbf9",
  "account_type": "atlassian",
  "account_status": "active",
  "name": "Jerome Jones",
  "picture": "https://secure.gravatar.com/avatar/4c0a79ea1b397e5f635bd0619893dfdb?d=https%3A%2F%2Favatar-management--avatars.us-west-2.prod.public.atl-paas.net%2Finitials%2FJJ-6.png",
  "email": "jeromedjones@outlook.com",
  "characteristics": {
    "not_mentionable": false
  },
  "nickname": "Jerome Jones",
  "zoneinfo": "America/Denver",
```

### Scenario D: Q3b — AgentDefinition.tools blocks subagent MCP access?

**Finding:** YES — AgentDefinition.tools BLOCKS MCP tools in subagent

Tools called: `['Agent']`

Model text (truncated to 500 chars):
```
TOOL_MISSING: mcp__atlassian__atlassianUserInfo
```

### Scenario E: Q3c — wildcard 'mcp__atlassian__*' works in AgentDefinition.tools?

**Finding:** NO — wildcard does NOT work; must enumerate MCP tools by name

Tools called: `['Agent']`

Model text (truncated to 500 chars):
```
TOOL_MISSING: mcp__atlassian__atlassianUserInfo
```
