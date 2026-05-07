"""MCP server: thin tool handlers that delegate to the Broker.

The server is built around a single ``Broker`` instance. Tests can
inject their own broker; production builds one fresh.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from claude_crew.auth import validate_auth_or_exit
from claude_crew.subagents._loader import _VALID_PERMISSION_MODES
from claude_crew.broker import (
    LEAD_ID,
    Broker,
    TeammateAlreadyDeadError,
    TeammateFactory,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.subagents._user_loader import (
    _discover_skill_names,
    _load_user_mcp_server_names,
    _read_installed_plugins,
    discover_dir,
)

# SDK built-ins available to grant via extra_tools (Task excluded — leaf-node invariant).
_SDK_BUILTIN_TOOLS: list[str] = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch", "WebSearch", "Agent",
]


# Maximum wait_seconds accepted by the get_messages long-poll tool.
# 10 minutes: enough headroom for any realistic single Opus/Sonnet turn
# with margin, so mid-turn timeouts genuinely don't happen. The lead can
# always cancel; FastMCP over stdio has no transport-level timeout.
MAX_WAIT_SECONDS = 600.0

# Single source of truth for the valid PermissionMode set lives in
# `claude_crew.subagents._loader` (used by both pack-load validation and
# spawn_teammate MCP-boundary validation per Feature #17 SC-4). Importing
# from there prevents the dual-constant drift sentinel M-1 flagged at merge.


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def make_server(
    broker: Broker | None = None,
    factory: TeammateFactory | None = None,
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> FastMCP:
    # Capture project_root once at server creation time so list_available_tools
    # returns a stable value for the process lifetime.
    _project_root: Path = project_root if project_root is not None else Path.cwd()
    _home_dir: Path | None = home_dir  # None → discovery functions use Path.home()
    if factory is None:
        # Lazy import to avoid circular: factories imports server's siblings.
        from claude_crew.factories import default_factory
        # default_factory uses Path.home()/Path.cwd() internally for pack
        # discovery; tests inject via monkeypatch on those primitives.
        factory = default_factory()
    # Thread the factory's captured startup diagnostics (sdk mode) into the
    # default Broker. Stub mode and externally-supplied factories that do not
    # set the attribute fall through to the empty-tuple default.
    if broker is None:
        broker = Broker(
            startup_diagnostics=getattr(factory, "startup_diagnostics", ()),
        )
    if getattr(factory, "requires_auth", False):
        validate_auth_or_exit()
    mcp = FastMCP(
        "claude-crew",
        instructions=(
            "claude-crew spawns long-lived agents (\"teammates\") as top-level "
            "Claude processes that persist across multiple turns of the lead "
            "session and can be messaged mid-task.\n\n"
            "Use claude-crew when:\n"
            "- You need an agent that lives across multiple lead-session turns, "
            "receiving and responding to messages over time.\n"
            "- The agent itself needs to spawn subagents to do its work. "
            "In-session subagents (the Task/Agent tool) run as isolated workers "
            "and cannot recursively delegate; claude-crew teammates run as "
            "top-level Claude processes and inherit the full toolkit, including "
            "spawning their own subagents.\n"
            "- You're running concurrent work and want to message agents "
            "mid-task (send_to, broadcast) rather than waiting for one return "
            "value.\n"
            "- The work benefits from agent learning across runs — teammates "
            "can persist project-scoped memory that future invocations inherit.\n\n"
            "Do NOT use claude-crew for:\n"
            "- One-shot work where the agent runs, returns, and is done. Use "
            "the in-session Agent/Task tool — lighter, faster, no spawn or "
            "teardown overhead.\n"
            "- Pure parallelism without dialog or recursive delegation. "
            "In-session subagents already run in parallel.\n\n"
            "Pattern: claude-crew for *relationship* (persistence + dialog + "
            "memory) and *recursive delegation* (teammate spawning its own "
            "helpers). In-session subagents for fire-and-forget specialist work."
        ),
    )

    @mcp.tool()
    async def spawn_teammate(
        role: str,
        name: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
        extra_tools: list[str] | None = None,
        extra_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """Spawn a new teammate with the given role.

        Use this when you need a persistent agent that lives across multiple
        lead-session turns, can be messaged mid-task (send_to / broadcast),
        can itself spawn subagents (unlike in-session Agent/Task subagents,
        which cannot recursively delegate), or accumulates project memory
        across runs.

        Do NOT use for one-shot work — prefer the in-session Agent/Task tool
        for fire-and-forget delegation (lighter, faster, no teardown).

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
            extra_tools: Optional list of additional tool IDs to grant beyond
                the pack's declared tools. Additive only — pack tools are
                never removed. "Task" is explicitly disallowed. MCP tool IDs
                (mcp__<server>__<tool>) automatically wire the server
                connection — no separate mcpServers configuration needed.
            extra_skills: Optional list of additional skill names to grant
                beyond the pack's declared skills. Additive only.
        """
        if permission_mode is not None and permission_mode not in _VALID_PERMISSION_MODES:
            raise ToolError(
                f"permission_mode {permission_mode!r} is not a valid PermissionMode; "
                f"accepted: {sorted(_VALID_PERMISSION_MODES)}"
            )
        # Task is a Claude Code built-in that only exists inside a Claude Code
        # session. SDK-spawned teammates run as standalone processes and the
        # tool is simply absent at runtime (verified live: teammate reports
        # TASK_TOOL_UNAVAILABLE). Block early with a clear message rather than
        # silently granting a tool that will never fire.
        if "Task" in (extra_tools or []):
            raise ToolError(
                "Task tool is not available in SDK subprocess context — "
                "teammates run as standalone processes outside Claude Code. "
                "Use Agent instead to spawn subagents."
            )
        tid = await broker.spawn_teammate(
            role=role, name=name, factory=factory,
            model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
            extra_tools=extra_tools, extra_skills=extra_skills,
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
        wait_seconds: float,
        since_seq: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return messages addressed to the lead with seq > since_seq.

        wait_seconds is REQUIRED and must be > 0. If the inbox is empty,
        the call blocks up to this many seconds for a message to arrive.
        This long-poll behavior is the whole point of the tool: one call
        that returns as soon as a teammate replies, instead of repeated
        immediate-return polls that waste lead-session turns and context.

        Choosing wait_seconds:
        - Awaiting a reply to something you just sent: 300 (5 min) is
          a sensible default. Bump higher (up to 600, the server cap)
          for slow Opus turns or long-running teammate tool calls.
        - Doing other work in parallel and want to drain the inbox
          before continuing: a short wait (5-30 s) is enough — you're
          not waiting *for* anything, just giving in-flight messages a
          moment to land.
        - Never call this in a tight loop hoping to "check quickly" —
          long-poll exists exactly so you don't have to. Set the wait
          to however long you'd otherwise have spent calling repeatedly.

        Args:
            wait_seconds: Required. Seconds to block when the inbox is
                empty. Must be > 0. Capped at 600 s server-side.
            since_seq: Cursor; pass the largest seq you've already seen.
            limit: Maximum messages to return (default 100).
        """
        if wait_seconds <= 0:
            raise ToolError(
                "wait_seconds must be > 0; long-poll is required. "
                "Pass 300 if you're awaiting a reply, or a short value "
                "(e.g. 5-30) if you're just draining the inbox between "
                "other work."
            )
        msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
        if not msgs:
            capped = min(wait_seconds, MAX_WAIT_SECONDS)
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
        """Terminate a teammate. Subsequent send_to calls will return teammate_dead.

        Use this for genuine teardown — the work is finished, the role is no
        longer needed, or the teammate is wedged and unrecoverable.

        Killing fully resets context. The teammate is a subprocess; when it
        dies, its conversation history and all in-process accumulated state
        die with it. A respawned teammate is a fresh process — it gets the
        role's pack body and re-reads any project-level memory files in the
        cwd, but it has no memory of the prior teammate's exchanges with you.

        Do NOT kill-and-respawn as a way to redirect or correct a teammate's
        work. claude-crew exists to support multi-turn conversation: if the
        teammate went the wrong direction, send_to it with the correction.
        Respawning loses everything you've built up together and burns the
        spawn cost again. The whole point of a persistent teammate is that
        you can talk to it like a colleague who remembers the last thing
        you said. Use that.

        Reach for kill_teammate when:
        - The feature/task is genuinely done and the teammate is being torn
          down (often paired with a final debrief send_to first).
        - The teammate is in an unrecoverable state (looping, deadlocked,
          repeatedly producing malformed output despite correction).
        - You need to free resources and the teammate has no more work.
        """
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

    @mcp.tool()
    async def list_available_tools() -> dict[str, Any]:
        """Return a grouped discovery payload of what the lead can grant via extra_tools / extra_skills.

        TOOL ID CONVENTION (load-bearing):
        MCP tool IDs follow ``mcp__<server>__<tool>``. This payload returns server NAMES
        only — enumerating individual tool IDs requires spawning every MCP server at startup
        and querying it; rejected as too costly. The lead matches server names against its
        own tool surface (system-reminder enumeration) to construct the full MCP tool ID.

        Returns:
            builtins: SDK built-in tools (no Task).
            mcp_servers: Registered MCP server names from ~/.claude.json; running=null.
            skills: Skill names from user and project skill dirs.
            plugins: Installed plugins, each with key, agents list, and skills list.
            project_root: Working directory captured at server startup.
        """
        # MCP servers: names only, never command/args/env
        mcp_server_names = _load_user_mcp_server_names(_home_dir)
        mcp_servers = [
            {"name": name, "running": None}
            for name in sorted(mcp_server_names)
        ]

        # Skills: union of user + project skill dirs
        skill_names = _discover_skill_names(_home_dir, _project_root)
        skills = sorted(skill_names)

        # Plugins: each entry has key, agents list, skills list
        plugin_pairs = _read_installed_plugins(_home_dir, _project_root)
        plugins_out = []
        for plugin_key, agents_dir in plugin_pairs:
            # Agent role names
            pack, _, _ = discover_dir(agents_dir)
            agent_names = sorted(pack.keys())

            # Plugin skills: by convention live at <installPath>/skills/
            plugin_skills_dir = agents_dir.parent / "skills"
            plugin_skill_names: list[str] = []
            if plugin_skills_dir.is_dir():
                for child in plugin_skills_dir.iterdir():
                    if child.is_dir() and (child / "SKILL.md").is_file():
                        plugin_skill_names.append(child.name)
            plugin_skill_names.sort()

            plugins_out.append({
                "key": plugin_key,
                "agents": agent_names,
                "skills": plugin_skill_names,
            })

        return {
            "builtins": _SDK_BUILTIN_TOOLS,
            "mcp_servers": mcp_servers,
            "skills": skills,
            "plugins": plugins_out,
            "project_root": str(_project_root),
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


def _bind_ui_socket(preferred: int) -> "socket.socket | None":
    """Bind *preferred* port (or OS ephemeral if busy) and return the open socket.

    The socket stays open so the caller can pass it to uvicorn via fd=, eliminating
    the probe-release-rebind race where two processes both see the port as free.
    Caller is responsible for closing the socket (UIServer.serve() does this).
    """
    import socket

    for port in [preferred, 0]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            # listen() is the serialization point: with SO_REUSEADDR, two
            # sockets can both bind() the same port (needed for TIME_WAIT),
            # but only one can listen() — so this is where exclusivity is
            # established atomically.
            s.listen(socket.SOMAXCONN)
            return s
        except OSError:
            s.close()
    return None


def main() -> None:
    """Console entrypoint: run the MCP server over stdio."""
    import os
    import signal

    import anyio

    ui_port_str = os.environ.get("CLAUDE_CREW_UI_PORT", "auto")
    ui_sock = None
    if ui_port_str.lower() == "auto":
        ui_sock = _bind_ui_socket(7821)
        ui_port = ui_sock.getsockname()[1] if ui_sock else 0
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
        if ui_sock:
            ui_sock.close()
        server.run()
        return

    from claude_crew.instance_registry import InstanceRegistry
    from claude_crew.ui_server import UIServer

    _LEADER_PORT = 7821
    is_leader = ui_port == _LEADER_PORT

    registry = InstanceRegistry(crew_id=broker.crew_id, port=ui_port)
    ui = UIServer(broker, port=ui_port, registry=registry, sock=ui_sock)

    if is_leader:
        sys.stderr.write(f"[claude-crew] ui -> http://127.0.0.1:{ui_port}\n")

    async def _run() -> None:
        # Install SIGTERM handler inside the running event loop so it targets
        # the correct loop (anyio creates its own; loop.add_signal_handler must
        # be called from within it, not from main() before anyio.run()).
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, registry.deregister)

        async def _ui_safe() -> None:
            try:
                await ui.serve()
            except Exception:
                sys.stderr.write("[claude-crew] ui server stopped unexpectedly (MCP still running)\n")

        async def _leader_watcher() -> None:
            """Follower instances: poll for the leader port and promote when free."""
            if is_leader:
                return
            while True:
                await asyncio.sleep(20)
                # Attempt atomic bind — if it succeeds, we hold the socket and
                # pass it directly to the promoted UIServer (no re-bind race).
                leader_sock = _bind_ui_socket(_LEADER_PORT)
                if leader_sock is not None and leader_sock.getsockname()[1] == _LEADER_PORT:
                    try:
                        registry.update_port(_LEADER_PORT)
                        promoted = UIServer(broker, port=_LEADER_PORT, registry=registry, sock=leader_sock)
                        sys.stderr.write(f"[claude-crew] promoted to leader: http://127.0.0.1:{_LEADER_PORT}\n")
                        await promoted.serve()
                    except Exception:
                        if leader_sock:
                            leader_sock.close()
                        registry.update_port(ui_port)  # revert if promotion failed
                elif leader_sock is not None:
                    # Got an ephemeral port instead — 7821 still busy, discard
                    leader_sock.close()

        async def _mcp_then_cancel() -> None:
            try:
                await server.run_stdio_async()
            finally:
                # stdin closed (Claude exited) — deregister and cancel the UI server
                registry.deregister()
                tg.cancel_scope.cancel()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_mcp_then_cancel)
            tg.start_soon(_ui_safe)
            tg.start_soon(_leader_watcher)

    anyio.run(_run)
