"""MCP server: thin tool handlers that delegate to the Broker.

The server is built around a single ``Broker`` instance. Tests can
inject their own broker; production builds one fresh.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from claude_crew.broker import (
    LEAD_ID,
    Broker,
    TeammateFactory,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate


def _default_factory(id: str, name: str, role: str) -> StubTeammate:
    return StubTeammate(id=id, name=name, role=role)


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def make_server(
    broker: Broker | None = None,
    factory: TeammateFactory | None = None,
) -> FastMCP:
    broker = broker if broker is not None else Broker()
    factory = factory if factory is not None else _default_factory
    mcp = FastMCP("claude-crew")

    @mcp.tool()
    async def spawn_teammate(role: str, name: str | None = None) -> dict[str, Any]:
        """Spawn a new teammate with the given role.

        Args:
            role: The teammate's role (e.g., "planner", "builder").
            name: Optional human-friendly name; defaults to role.
        """
        tid = await broker.spawn_teammate(role=role, name=name, factory=factory)
        info = next(t for t in broker.list_crew() if t.id == tid)
        return {"teammate_id": info.id, "name": info.name, "role": info.role}

    @mcp.tool()
    async def send_to(
        teammate_id: str,
        payload: Any,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to a specific teammate.

        Args:
            teammate_id: The id returned from spawn_teammate.
            payload: Any JSON-serializable value.
            id: Optional message id for retry-safe delivery (broker dedups).
        """
        env = Envelope(
            id=id if id is not None else new_message_id(),
            seq=0,
            sender=LEAD_ID,
            recipient=teammate_id,
            timestamp=time.time(),
            payload=payload,
        )
        try:
            stamped = await broker.send(env)
        except UnknownTeammateError:
            return _err("unknown_teammate", f"no teammate with id {teammate_id!r}")
        if stamped is None:
            return _err("duplicate", f"message id {env.id!r} was already delivered")
        return {"message_id": stamped.id, "seq": stamped.seq}

    @mcp.tool()
    async def broadcast(payload: Any, id: str | None = None) -> dict[str, Any]:
        """Broadcast a message to every teammate. Sender (lead) does not loop back.

        Args:
            payload: Any JSON-serializable value.
            id: Optional root id; per-recipient ids derive from it.
        """
        ids = await broker.broadcast(sender=LEAD_ID, payload=payload, id=id)
        return {"message_ids": ids, "delivered_to": len(ids)}

    @mcp.tool()
    async def get_messages(
        since_seq: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return messages addressed to the lead with seq > since_seq.

        Args:
            since_seq: Cursor; pass the largest seq you've already seen.
            limit: Maximum messages to return (default 100).
        """
        msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
        next_seq = msgs[-1].seq if msgs else since_seq
        return {
            "messages": [m.to_dict() for m in msgs],
            "next_seq": next_seq,
        }

    @mcp.tool()
    async def list_crew() -> dict[str, Any]:
        """List all currently spawned teammates."""
        return {
            "teammates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "role": t.role,
                    "spawned_at": t.spawned_at,
                    "alive": t.alive,
                }
                for t in broker.list_crew()
            ]
        }

    @mcp.tool()
    async def kill_teammate(teammate_id: str) -> dict[str, Any]:
        """Terminate a teammate. Subsequent send_to calls will fail."""
        try:
            await broker.kill_teammate(teammate_id)
        except UnknownTeammateError:
            return _err("unknown_teammate", f"no teammate with id {teammate_id!r}")
        return {"ok": True}

    # Stash the broker on the server for tests / introspection.
    mcp._broker = broker  # type: ignore[attr-defined]
    return mcp


def main() -> None:
    """Console entrypoint: run the MCP server over stdio."""
    server = make_server()
    server.run()
