---
name: crew-showcase
description: Live tour of claude-crew's shipped capabilities. Spawns 3 read-only teammates and runs them in parallel to light up the Mission Control dashboard — tool-event stream (#19), in-flight badge prominence (#22), token/cost telemetry (#14), parallel agent columns (#13/#14), subagent delegation (#21). Doesn't modify the project. Use when the operator wants to *see* what the substrate does, not just read about it.
---

# Crew Showcase — A Live Tour

Showcase the shipped surface of claude-crew by running it. Spawn three read-only teammates, give them tasks that exercise each capability, and walk the operator through what they're seeing on the dashboard.

This skill makes **no changes to the project**. All teammate tasks are read-only (file reads, `find`, `wc`, etc.) — no edits, no commits, no installs.

---

## Pre-flight

1. **Confirm the dashboard is open.** Ask the operator to open `http://localhost:<port>/` in a browser (the port is whatever their `CLAUDE_CREW_UI_PORT` is set to, typically auto-allocated and visible on `mcp__claude-crew__list_crew` output, or via `~/.local/state/claude-crew/instances/`). If the dashboard isn't open, don't proceed — the showcase exists to be *seen*.

2. **Check the crew is empty (or close to empty).** Call `mcp__claude-crew__list_crew`. If 5+ live teammates are already running, ask the operator whether to proceed (the showcase will add 3 more) or to clean up first.

3. **State what you're about to do.** One short sentence: "Spawning three read-only teammates that will run for ~30-60 seconds total. Watch the dashboard — you'll see tool badges with elapsed counters, the tool-event stream filling in, and token/cost growing on each agent header."

---

## The three showcase teammates

Spawn these three in parallel via three `mcp__claude-crew__spawn_teammate` calls in a single message:

| Slot | Role | Name | Purpose |
|---|---|---|---|
| 1 | `general-purpose` | `tour-delegator` | Demonstrates subagent delegation (#21) and the `Agent` tool badge surfacing on a long-running parent tool. Shows operator-silence closure (#22). |
| 2 | `explorer` | `tour-explorer` | Read-heavy work — multiple tool calls flow into the dashboard stream (#19). Token counter advances visibly. |
| 3 | `planner` | `tour-planner` | Lightweight reasoning task — minimal tool churn, contrasts with explorer's tool-heavy column. Demonstrates idle/thinking states alongside tool-use. |

After each spawn, capture the returned `teammate_id`. You'll need them to send tasks and to poll status.

---

## The tasks (read-only, designed to exercise specific surfaces)

Send these three tasks via `mcp__claude-crew__send_to`, **in parallel** (single message, three tool calls), so the dashboard shows three columns lighting up simultaneously:

### Task 1 → `tour-delegator` (exercises #22 + #21)

```
Dispatch a Task subagent to run this Bash and report the output:

    find /home/jerome/dev/claude-crew/claude_crew -name "*.py" -exec wc -l {} + | sort -rn | head -10

Don't run any tools yourself other than dispatching the subagent. The point is the long-running Agent dispatch — I'm verifying that my dashboard surfaces it as an in-flight badge with a growing elapsed counter and pulses past 5s.
```

This produces a parent `Agent` tool that runs for ~5-15 seconds (subagent's Bash + tool-call overhead), which is the sweet spot for SC-3's 5s pulse threshold.

### Task 2 → `tour-explorer` (exercises #19 + #14)

```
Read these three files and give me a one-paragraph summary of what each does:

    1. /home/jerome/dev/claude-crew/claude_crew/broker.py
    2. /home/jerome/dev/claude-crew/claude_crew/teammate.py
    3. /home/jerome/dev/claude-crew/claude_crew/ui_server.py

One paragraph per file. No code quotes. Don't write anything.
```

This generates 3 sequential `Read` tool calls — each appears in the dashboard's tool-event stream as `kind: "tool"` entries. Token counter on the explorer column climbs visibly.

### Task 3 → `tour-planner` (exercises lightweight columns)

```
Without reading any files, propose three small features that could improve claude-crew's developer ergonomics, ranked by estimated effort. One sentence per feature. Don't actually do any of them — this is a thinking-only task.
```

No tool calls; just a thinking turn. The planner column shows `thinking` status with pulsing dot but no tool badge — contrast with the other two columns.

---

## What the operator should see (call this out as it happens)

While the tasks run, narrate what's appearing on the dashboard. Use `mcp__claude-crew__get_teammate_status` once or twice to confirm what's in flight, but don't poll aggressively (1-2 status calls is enough).

- **Three agent columns** appear in the StreamColumns grid (capability #4 / #13 multi-column rendering).
- **Accent bars** at column-tops light up purple-tinted on `tour-delegator` and `tour-explorer` as their tools start.
- **Tool chips** show `Agent · Ns` on `tour-delegator` and `Read · Ns` on `tour-explorer`, with elapsed counters advancing in real time (#22 SC-2).
- After ~5s on a tool, the chip's elapsed counter starts pulsing (#22 SC-3 stuck threshold).
- `tour-planner` column shows `thinking` status with the existing yellow status-dot pulse but no tool badge — the negative case that proves the badge surface only fires for tools, not generic activity.
- Each tool completion produces a brief settle-frame chip (`Read · 0.1s ✓`) before clearing (#22 SC-4).
- Each completion also drops a `kind: "tool"` row into the corresponding agent's transcript stream (#19 — `Read (ok, 0.05s) — file_path=...`).
- Token counters and cost on each agent header advance with each turn (#14 / F14 — input tokens accumulate per turn, cost is cumulative).
- The MCTopBar at the very top shows aggregate cost / tokens / per-status counts (idle / thinking / tool-use) update in real time.

---

## Cleanup

By default, **leave the teammates alive** after the showcase finishes — operator may want to inspect the results, look at the transcripts, scroll the streams. Tell them: "Three teammates are still alive. To clean up, run `mcp__claude-crew__kill_teammate` on each id, or restart the claude-crew MCP server."

If the operator explicitly asks to clean up: call `mcp__claude-crew__kill_teammate` on each of the three captured ids in parallel.

---

## Reporting

After all three tasks complete (poll briefly via `get_messages` until each teammate has replied), give a short report:

- Which capabilities were exercised live (list features by number).
- Anything anomalous you noticed (e.g., a tool error, an unexpected delay, a missing field in `get_teammate_status` output).
- A one-line invitation to inspect the dashboard's three columns side-by-side.

That's it. The operator does the looking; you make the substrate run.

---

## Failure modes

- **MCP server isn't connected** → `mcp__claude-crew__list_crew` fails or returns "no server." Tell the operator to `/mcp` reconnect, then re-invoke the skill.
- **Teammate spawn fails with "no such role"** → the default subagent pack isn't loaded. Confirm `claude_crew/subagents/{general_purpose,explorer,planner}.md` exist on the running server's branch. If not, the operator's local pack is out of sync — `git pull` and restart.
- **A teammate doesn't have Bash and refuses Task 1** → expected. The task explicitly asks for subagent dispatch; if the teammate refuses to dispatch a subagent, send a follow-up: "Dispatch any subagent role that has Bash access. The point is to demonstrate delegation — pick whichever subagent works."
- **Dashboard doesn't reflect what `get_teammate_status` shows** → the dashboard is connected to a stale server (old code). Have the operator hard-refresh (`Ctrl+Shift+R`) and verify `/mcp` is connected to the right instance.
