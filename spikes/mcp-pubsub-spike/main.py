"""MCP resource-subscription spike.

Two resources, two ways to mutate them, both emit notifications/resources/updated.
The question we are answering: does Claude Code surface those updates to the model
mid-session without a tool call?
"""

import asyncio
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl

mcp = FastMCP("pubsub-spike")

COUNTER_URI = "spike://counter"
INBOX_URI = "spike://inbox"
POKE_FILE = Path("/tmp/spike-poke")
LOG_FILE = Path("/tmp/spike-server.log")

state: dict = {
    "counter": 0,
    "inbox": "no messages yet",
    "session": None,
    "ticker_started": False,
}


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat()}] {msg}\n"
    with LOG_FILE.open("a") as f:
        f.write(line)


@mcp.resource(COUNTER_URI)
def counter_resource() -> str:
    log(f"counter resource READ -> {state['counter']}")
    return f"counter={state['counter']} at={datetime.now().isoformat()}"


@mcp.resource(INBOX_URI)
def inbox_resource() -> str:
    log(f"inbox resource READ -> {state['inbox'][:60]!r}")
    return state["inbox"]


@mcp.tool()
async def read_counter() -> str:
    """Sanity-check: read the counter directly via a tool call."""
    return f"counter={state['counter']}"


@mcp.tool()
async def start_ticker() -> str:
    """Start background tasks that mutate resources and emit resources/updated notifications.

    Tasks:
    - ticker: increments spike://counter every 10 seconds
    - poke watcher: when /tmp/spike-poke is written to, updates spike://inbox

    Call this once at the start of a session.
    """
    if state["ticker_started"]:
        return "ticker already running"
    ctx = mcp.get_context()
    state["session"] = ctx.request_context.session
    state["ticker_started"] = True
    asyncio.create_task(_ticker_loop())
    asyncio.create_task(_poke_watcher())
    log("ticker + poke watcher started")
    return (
        "Started. spike://counter increments every 10s. "
        "Write to /tmp/spike-poke to update spike://inbox. "
        "Both emit notifications/resources/updated."
    )


async def _ticker_loop() -> None:
    while True:
        await asyncio.sleep(10)
        state["counter"] += 1
        session = state["session"]
        if session is None:
            continue
        try:
            await session.send_resource_updated(AnyUrl(COUNTER_URI))
            log(f"sent resources/updated for counter (={state['counter']})")
        except Exception as e:
            log(f"ticker notify failed: {e}")


async def _poke_watcher() -> None:
    last_mtime = 0.0
    while True:
        await asyncio.sleep(1)
        try:
            mtime = POKE_FILE.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime == last_mtime:
            continue
        last_mtime = mtime
        try:
            text = POKE_FILE.read_text().strip()
        except OSError:
            continue
        state["inbox"] = f"[{datetime.now().isoformat()}] {text}"
        session = state["session"]
        if session is None:
            continue
        try:
            await session.send_resource_updated(AnyUrl(INBOX_URI))
            log(f"sent resources/updated for inbox: {text!r}")
        except Exception as e:
            log(f"poke notify failed: {e}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
