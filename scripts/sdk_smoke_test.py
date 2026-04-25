"""End-to-end smoke test of SDK mode over real stdio transport.

Spawns `claude-crew` as a subprocess in production (SDK) mode and drives
the full tool surface against the real Anthropic API. Same registration
path Claude Code uses via `claude mcp add`.

Cost: a few cents per run.
Gate: requires CLAUDE_CREW_LIVE_TESTS=1 in the environment.

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/sdk_smoke_test.py`
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _content_json(result):
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def _wait_for_response(session, since_seq: int, timeout: float = 90.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = _content_json(await session.call_tool(
            "get_messages", {"since_seq": since_seq},
        ))
        if result["messages"]:
            return result
        await asyncio.sleep(0.5)
    raise RuntimeError(f"timed out waiting for response since seq {since_seq}")


async def main() -> int:
    # SDK mode is the default; we explicitly set it to make the intent clear.
    env = dict(os.environ)
    env["CLAUDE_CREW_TEAMMATE_MODE"] = "sdk"
    params = StdioServerParameters(command="claude-crew", args=[], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✓ initialized")

            tools = await session.list_tools()
            tool_names = sorted(t.name for t in tools.tools)
            expected = ["broadcast", "get_messages", "kill_teammate",
                        "list_crew", "send_to", "spawn_teammate"]
            assert tool_names == expected, f"tools mismatch: {tool_names}"
            print(f"✓ tools registered: {len(tool_names)}")

            spawn = _content_json(await session.call_tool(
                "spawn_teammate", {"role": "smoke-test", "name": "smokey"},
            ))
            tid = spawn["teammate_id"]
            print(f"✓ spawned SDK teammate: {tid} ({spawn['name']}/{spawn['role']})")

            since = 0
            for i, prompt in enumerate([
                "In one short sentence, what is 2+2?",
                "In one short sentence, what is 3+3?",
                "In one short sentence, what is 4+4?",
            ], start=1):
                send = _content_json(await session.call_tool(
                    "send_to", {"teammate_id": tid, "payload": prompt},
                ))
                assert "message_id" in send, f"send failed turn {i}: {send}"
                result = await _wait_for_response(session, since_seq=since)
                msg = result["messages"][-1]
                since = msg["seq"]
                assert "text" in msg["payload"], (
                    f"turn {i} got error envelope: {msg['payload']}"
                )
                preview = msg["payload"]["text"].strip().replace("\n", " ")[:80]
                print(f"✓ turn {i}: {preview}")

            kill = _content_json(await session.call_tool(
                "kill_teammate", {"teammate_id": tid},
            ))
            assert kill == {"ok": True}
            print(f"✓ killed teammate")

    print("\n🎉 SDK smoke test passed")
    return 0


if __name__ == "__main__":
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skipped (set CLAUDE_CREW_LIVE_TESTS=1 to run)")
        sys.exit(0)
    sys.exit(asyncio.run(main()))
