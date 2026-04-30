"""MCP server: thin tool handlers that delegate to the Broker.

The server is built around a single ``Broker`` instance. Tests can
inject their own broker; production builds one fresh.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from claude_crew.auth import validate_auth_or_exit
from claude_crew.broker import (
    LEAD_ID,
    Broker,
    TeammateAlreadyDeadError,
    TeammateFactory,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id


# Maximum wait_seconds accepted by the get_messages long-poll tool.
# 10 minutes: enough headroom for any realistic single Opus/Sonnet turn
# with margin, so mid-turn timeouts genuinely don't happen. The lead can
# always cancel; FastMCP over stdio has no transport-level timeout.
MAX_WAIT_SECONDS = 600.0


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def make_server(
    broker: Broker | None = None,
    factory: TeammateFactory | None = None,
) -> FastMCP:
    broker = broker if broker is not None else Broker()
    if factory is None:
        # Lazy import to avoid circular: factories imports server's siblings.
        from claude_crew.factories import default_factory
        factory = default_factory()
    if getattr(factory, "requires_auth", False):
        validate_auth_or_exit()
    mcp = FastMCP("claude-crew")

    @mcp.tool()
    async def spawn_teammate(
        role: str,
        name: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a new teammate with the given role.

        Args:
            role: The teammate's role (e.g., "planner", "builder").
            name: Optional human-friendly name; defaults to role.
            model: Optional model id (e.g., "claude-opus-4-7",
                "claude-sonnet-4-6", "claude-haiku-4-5"). Defaults to
                the SdkTeammate built-in (Sonnet 4.6).
            effort: Optional reasoning effort. One of "low", "medium",
                "high", "max". Higher uses more thinking tokens and costs
                more; "low" is fastest and cheapest.
            cwd: Optional working directory for the teammate subprocess.
                When set, the teammate's project CLAUDE.md is loaded from
                this path automatically.
            permission_mode: Optional permission mode override. One of
                "default", "acceptEdits", "plan", "bypassPermissions",
                "dontAsk", "auto". Overrides the role's pack-declared
                permissionMode when provided.
        """
        tid = await broker.spawn_teammate(
            role=role, name=name, factory=factory,
            model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
        )
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
        except TeammateAlreadyDeadError:
            status = broker.get_teammate_status(teammate_id)
            died_at = status.get("died_at_wallclock")
            exit_code = status.get("exit_code")
            return _err(
                "teammate_dead",
                f"teammate {teammate_id!r} died at {died_at}; exit_code={exit_code}",
            )
        except UnknownTeammateError:
            return _err("unknown_teammate", f"no teammate with id {teammate_id!r}")
        if stamped is None:
            return _err("duplicate", f"message id {env.id!r} was already delivered")
        return {"message_id": stamped.id, "seq": stamped.seq}

    @mcp.tool()
    async def broadcast(payload: Any, id: str | None = None) -> dict[str, Any]:
        """Broadcast a message to every alive teammate. Sender (lead) does not loop back.

        Tombstoned teammates are silently skipped; their ids are reported in
        ``skipped_dead`` so the caller knows the delivery was partial.

        Args:
            payload: Any JSON-serializable value.
            id: Optional root id; per-recipient ids derive from it.
        """
        result = await broker.broadcast(sender=LEAD_ID, payload=payload, id=id)
        ids = result["message_ids"]
        return {
            "message_ids": ids,
            "delivered_to": len(ids),
            "skipped_dead": result["skipped_dead"],
        }

    @mcp.tool()
    async def get_messages(
        since_seq: int = 0,
        limit: int = 100,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Return messages addressed to the lead with seq > since_seq.

        Args:
            since_seq: Cursor; pass the largest seq you've already seen.
            limit: Maximum messages to return (default 100).
            wait_seconds: If > 0 and no messages are waiting, block up to
                this many seconds for one to arrive. Default 0 returns
                immediately (existing behavior). Capped at 600 s server-side;
                negative values treated as 0.
        """
        msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
        if not msgs and wait_seconds > 0:
            capped = min(max(wait_seconds, 0.0), MAX_WAIT_SECONDS)
            await broker.wait_for_lead_message(capped)
            msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
        next_seq = msgs[-1].seq if msgs else since_seq
        return {
            "messages": [m.to_dict() for m in msgs],
            "next_seq": next_seq,
        }

    @mcp.tool()
    async def list_crew() -> dict[str, Any]:
        """List all spawned teammates (alive and tombstoned)."""
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
        """Terminate a teammate. Subsequent send_to calls will return teammate_dead."""
        try:
            await broker.kill_teammate(teammate_id)
        except UnknownTeammateError:
            return _err("unknown_teammate", f"no teammate with id {teammate_id!r}")
        return {"ok": True}

    @mcp.tool()
    async def get_teammate_status(teammate_id: str) -> dict[str, Any]:
        """Return live or post-mortem status for a teammate.

        Returns the same payload shape whether the teammate is alive or
        tombstoned, with death-record fields populated only when alive=False.

        F8 tool-tracking fields (always present):
            current_tools: list of in-flight tool calls, each with
                {tool_name, tool_use_id, started_at_wallclock, args_summary}.
            current_tool: last-started tool name, or null if none in flight.
            current_tool_count: number of tools currently in flight.
            last_tool_completed: most recent fully-finished tool record
                {tool_name, outcome, finished_at_wallclock, duration_seconds,
                error_summary?}, or null if none.
            redaction_version: active redaction schema version ("v1"), or null
                for tombstoned teammates.

        What this tells you mid-execution:
            - Is the teammate running Bash? Check current_tool == "Bash".
            - How long has the current tool been running?
              current_tools[0].started_at_wallclock vs now.
            - What tool last completed, and did it succeed?
              last_tool_completed.tool_name / .outcome.
            - Is args_summary populated? Only for allowlisted tools (Bash,
              Task, WebFetch) with redaction applied.

        Args:
            teammate_id: id from spawn_teammate.
        """
        return broker.get_teammate_status(teammate_id)

    @mcp.tool()
    async def get_transcript_path() -> dict[str, Any]:
        """Return the path of this crew's JSONL transcript file.

        Returns:
            path: Filesystem path to the transcript, or null if disabled.
            crew_id: 8-hex crew identifier (also embedded in path/lines).
            disabled: True if transcripts are turned off via env var.
        """
        sink = broker._sink  # type: ignore[attr-defined]
        return {
            "path": str(sink.path) if sink.path else None,
            "crew_id": broker.crew_id,
            "disabled": sink.disabled,
        }

    # Stash the broker on the server for tests / introspection.
    mcp._broker = broker  # type: ignore[attr-defined]

    # Discoverability: print the transcript path so operators can `tail -f` it.
    sink = broker._sink  # type: ignore[attr-defined]
    if sink.disabled:
        sys.stderr.write("[claude-crew] transcript: disabled\n")
    else:
        sys.stderr.write(f"[claude-crew] transcript -> {sink.path}\n")

    return mcp


def _pick_ui_port(preferred: int) -> int:
    """Return the first free TCP port at or after *preferred*, or an OS-assigned one."""
    import socket

    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # All candidates taken — let the OS pick anything free.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    """Console entrypoint: run the MCP server over stdio."""
    import os

    import anyio

    ui_port_str = os.environ.get("CLAUDE_CREW_UI_PORT", "auto")
    if ui_port_str.lower() == "auto":
        ui_port = _pick_ui_port(7821)
    else:
        try:
            ui_port = int(ui_port_str)
        except ValueError:
            sys.stderr.write(
                f"[claude-crew] CLAUDE_CREW_UI_PORT={ui_port_str!r} is not a valid integer"
                " — UI disabled\n"
            )
            ui_port = 0

    broker = Broker()
    server = make_server(broker=broker)

    if ui_port <= 0:
        server.run()
        return

    from claude_crew.ui_server import UIServer

    ui = UIServer(broker, port=ui_port)
    sys.stderr.write(f"[claude-crew] ui -> http://127.0.0.1:{ui_port}\n")

    async def _run() -> None:
        async def _ui_safe() -> None:
            try:
                await ui.serve()
            except Exception:
                sys.stderr.write("[claude-crew] ui server stopped unexpectedly (MCP still running)\n")

        async with anyio.create_task_group() as tg:
            tg.start_soon(server.run_stdio_async)
            tg.start_soon(_ui_safe)

    anyio.run(_run)
