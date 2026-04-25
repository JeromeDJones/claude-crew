# MCP Resource-Subscription Spike

**Question:** When an MCP server emits `notifications/resources/updated`, does Claude Code surface the change to the model mid-session — or is the notification silently absorbed by the client?

If yes, MCP resource subscriptions are a viable push channel for the planned multi-Claude messaging product, and we can drop the Claude-Code-hooks dependency.

If no, hooks remain the inbound channel and MCP stays as the action surface only.

## What's here

- `main.py` — FastMCP server with two resources and two background mutators
  - `spike://counter` — incremented every 10 seconds by a ticker
  - `spike://inbox` — updated when `/tmp/spike-poke` is written to
- `poke.sh` — CLI helper to write to `/tmp/spike-poke` from another terminal
- `/tmp/spike-server.log` — server-side log of reads and notifications (created on first run)

Both mutators call `session.send_resource_updated(uri)`, which emits the standard `notifications/resources/updated` JSON-RPC notification.

## Register with Claude Code

From a directory where you want to run the test session (recommend a fresh scratch dir, not this one):

```bash
claude mcp add pubsub-spike -- /home/jerome/.local/bin/uv --directory /home/jerome/dev/mcp-pubsub-spike run python main.py
```

Verify:

```bash
claude mcp list
```

## Test protocol

1. **Start a fresh Claude Code session** in a scratch directory with the server registered.
2. **Have Claude call `start_ticker`** (the tool kicks off the background tasks and captures the session reference needed for notifications).
3. **Have Claude subscribe** — ask it to subscribe to `spike://counter` and `spike://inbox`, or check whether the client auto-subscribes when resources are listed. The Python SDK doesn't expose subscription as a tool, so this is really a client-behavior question: does Claude Code subscribe by default? Watch the server log.
4. **Sit and wait.** Make small talk with Claude or ask it to do unrelated work. Don't mention the counter.
5. **Observe** — after 10–20 seconds, does Claude spontaneously mention the counter changed? Does the resource content show up in the conversation context? Or is the notification swallowed silently?
6. **External trigger:** in a separate terminal, run `./poke.sh "hello kael"`. Same observation: does Claude notice unprompted?
7. **Sanity check:** ask Claude to read `spike://counter` directly. This should succeed regardless — proves the resource plumbing works end-to-end. Compare with the implicit-update behavior.
8. **Tail the server log** (`tail -f /tmp/spike-server.log`) to confirm notifications were actually sent server-side. This separates "server didn't emit" from "client ignored emission."

## Outcomes and what they mean

| Observation | Conclusion |
|---|---|
| Claude mentions updates without being prompted, content appears in context | **Push works.** Resource subscriptions are the channel. Drop hooks from the design. |
| Claude doesn't mention updates, but if asked "any changes?" it knows | Client cached the update silently. Partial win — could work with a periodic Claude self-check. |
| Server log shows notifications sent, but Claude is fully unaware until it re-reads | **Push doesn't reach the model.** Hooks remain the inbound channel. |
| Server log shows no notifications sent | Subscription never happened client-side. Investigate Claude Code subscription behavior. |

## Cleanup

```bash
claude mcp remove pubsub-spike
rm /tmp/spike-poke /tmp/spike-server.log
```
