"""End-to-end smoke test over real stdio transport.

Spawns `claude-crew` as a subprocess (the console script) and exercises
the tool surface through the MCP stdio client — same path Claude Code
takes when registered via `claude mcp add`.

Run: `uv run python scripts/smoke_test.py`
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


async def main() -> int:
    # Force stub mode: this script validates the bus protocol end-to-end,
    # not the SDK. Stub mode is fast and free; SDK mode is for sdk_smoke_test.py.
    env = dict(os.environ)
    env["CLAUDE_CREW_TEAMMATE_MODE"] = "stub"
    params = StdioServerParameters(command="claude-crew", args=[], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✓ initialized")

            tools = await session.list_tools()
            tool_names = sorted(t.name for t in tools.tools)
            expected = ["broadcast", "get_messages", "get_transcript_path",
                        "kill_teammate", "list_crew", "send_to",
                        "spawn_teammate"]
            assert tool_names == expected, f"tools mismatch: {tool_names}"
            print(f"✓ tools registered: {', '.join(tool_names)}")

            spawn = _content_json(await session.call_tool(
                "spawn_teammate", {"role": "parrot", "name": "polly"},
            ))
            tid = spawn["teammate_id"]
            print(f"✓ spawned teammate: {tid} ({spawn['name']}/{spawn['role']})")

            send = _content_json(await session.call_tool(
                "send_to", {"teammate_id": tid, "payload": {"hello": "world"}},
            ))
            assert "message_id" in send, f"send failed: {send}"
            print(f"✓ sent message: seq={send['seq']}")

            for _ in range(50):
                msgs = _content_json(await session.call_tool("get_messages", {}))
                if msgs["messages"]:
                    break
                await asyncio.sleep(0.02)
            assert msgs["messages"], "no echo received within timeout"
            echo = msgs["messages"][0]
            assert echo["payload"] == {"echo": {"hello": "world"}, "from": "parrot"}, echo
            print(f"✓ echo received: payload={echo['payload']}")

            crew = _content_json(await session.call_tool("list_crew", {}))
            assert len(crew["teammates"]) == 1
            print(f"✓ list_crew shows 1 teammate")

            kill = _content_json(await session.call_tool(
                "kill_teammate", {"teammate_id": tid},
            ))
            assert kill == {"ok": True}
            print(f"✓ killed teammate")

            crew = _content_json(await session.call_tool("list_crew", {}))
            assert crew["teammates"] == []
            print(f"✓ crew is empty after kill")

    print("\n🎉 smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
