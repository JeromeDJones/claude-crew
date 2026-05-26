"""MCP server: thin tool handlers that delegate to the Broker.

The server is built around a single ``Broker`` instance. Tests can
inject their own broker; production builds one fresh.
"""

from __future__ import annotations

import asyncio
import stat
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from claude_crew.auth import validate_auth_or_exit
from claude_crew.local_backend import local_backend_env
from claude_crew.subagents._loader import _VALID_PERMISSION_MODES
from claude_crew.broker import (
    LEAD_ID,
    Broker,
    TeammateAlreadyDeadError,
    TeammateFactory,
    UnknownTeammateError,
)
from claude_crew.artifact_registry import (
    ArtifactNotText,
    ArtifactRegistry,
    ArtifactTooLarge,
    MAX_BODY_BYTES,
)

# Upper bound on the surface_document `title` metadata field (display-only;
# bounds the un-capped metadata surface independent of the body cap).
_MAX_TITLE_LEN = 512
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
    ui_port: int | None = None,
    artifact_registry: ArtifactRegistry | None = None,
) -> FastMCP:
    # Capture project_root once at server creation time so list_available_tools
    # returns a stable value for the process lifetime.
    _project_root: Path = project_root if project_root is not None else Path.cwd()
    _home_dir: Path | None = home_dir  # None → discovery functions use Path.home()
    # Resolved UI port for the non-blocking message-wait endpoint. None or <= 0
    # means the UI server is disabled, so get_wait_endpoint reports no URL.
    _ui_port: int | None = ui_port
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
            "helpers). In-session subagents for fire-and-forget specialist "
            "work.\n\n"
            "Staying productive while waiting: get_messages long-polls and "
            "blocks your turn. To keep working instead of waiting idle, call "
            "get_wait_endpoint for a localhost URL, then background a "
            "`curl` long-poll against it via the Bash tool "
            "(run_in_background=true) — it returns the instant a teammate "
            "replies, and you drain the content via get_messages."
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
        env: dict[str, str] | None = None,
        local_backend: bool | dict | None = None,
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
            env: Optional dict of environment variables to set for the
                teammate subprocess. Keys and values must both be strings.
                Empty keys are rejected. Caller keys win on conflict with
                crew defaults; use with care.
            local_backend: Optional preset for routing the teammate through a
                local model backend (e.g. ccr / llama.cpp). Pass True to use
                default settings (base_url=http://127.0.0.1:3456), or a dict
                with optional "base_url" and/or "api_key" overrides. Explicit
                env keys win over preset values.
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

        # Validate env shape: reject non-string values and empty keys.
        if env is not None:
            for key, value in env.items():
                if not isinstance(key, str) or key == "":
                    raise ToolError(
                        f"env key {key!r} is invalid: keys must be non-empty strings"
                    )
                if not isinstance(value, str):
                    raise ToolError(
                        f"env[{key!r}] has a non-string value ({type(value).__name__}); "
                        "all env values must be strings"
                    )

        # Resolve local_backend preset → base env dict (empty if no preset).
        resolved_env: dict[str, str] | None = None
        if local_backend:
            preset_kwargs: dict[str, str] = {}
            if isinstance(local_backend, dict):
                if "base_url" in local_backend:
                    preset_kwargs["base_url"] = local_backend["base_url"]
                if "api_key" in local_backend:
                    preset_kwargs["api_key"] = local_backend["api_key"]
            preset = local_backend_env(**preset_kwargs)
            # Merge: preset as base, explicit env wins on conflict.
            resolved_env = {**preset, **(env or {})}
        elif env:
            resolved_env = env

        tid = await broker.spawn_teammate(
            role=role, name=name, factory=factory,
            model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
            extra_tools=extra_tools, extra_skills=extra_skills,
            env=resolved_env,
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

        Staying productive while waiting: this call blocks the lead's turn.
        When you have other work to do instead of waiting idle, you have two
        options. (1) Cheapest: pass a SMALL wait_seconds (e.g. 5) and the
        next-poll cursor (next_seq) — drain what's queued, do a chunk of work,
        poll again next turn. (2) True push-on-arrival without freezing: call
        get_wait_endpoint to get a localhost URL, then run, via the Bash tool
        with run_in_background=true, a blocking long-poll against it, e.g.
        `curl -s "<url>?since_seq=<next_seq>&timeout=300"`. That curl returns
        the instant a message lands; the harness notifies you, and you then
        call this tool to drain. The endpoint is content-free (signals arrival
        only); actual message content always comes back through THIS tool.

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
    async def get_wait_endpoint() -> dict[str, Any]:
        """Return the localhost URL for the non-blocking message-wait long-poll.

        Use this to stay productive instead of blocking on get_messages. The
        returned URL is an HTTP long-poll that BLOCKS until the lead has a new
        message, then returns a content-free arrival signal (no payloads).

        Pattern:
          1. Call this once; cache the URL.
          2. Run, via the Bash tool with run_in_background=true:
             `curl -s "<url>?since_seq=<next_seq>&timeout=300"`
             (since_seq = the next_seq from your last get_messages call).
          3. Keep working. When a teammate replies the curl returns and the
             harness notifies you.
          4. Call get_messages to drain the actual content, then relaunch the
             curl with the new next_seq.

        Query params on the URL: ``since_seq`` (cursor; default 0) and
        ``timeout`` (seconds to block; default 300, capped at 600 server-side).
        Response JSON: ``{waiting, count, next_seq}``.

        Returns ``{enabled: false, url: null, ...}`` when the UI/HTTP server is
        disabled (CLAUDE_CREW_UI_PORT=0) — fall back to a small-wait_seconds
        get_messages poll in that case.
        """
        # Invariant: production threads in the *resolved* port (main() reads it
        # via getsockname() before calling make_server), so a live UI is always
        # > 0 here. _ui_port of None (tests / make_server default) or 0 (UI
        # disabled via CLAUDE_CREW_UI_PORT=0) both mean "no endpoint".
        if _ui_port is None or _ui_port <= 0:
            return {
                "enabled": False,
                "url": None,
                "reason": (
                    "HTTP server disabled (CLAUDE_CREW_UI_PORT=0); use "
                    "get_messages with a small wait_seconds to poll instead."
                ),
            }
        return {
            "enabled": True,
            "url": f"http://127.0.0.1:{_ui_port}/wait-messages",
            "usage": (
                'background a Bash call: '
                f'curl -s "http://127.0.0.1:{_ui_port}/wait-messages'
                '?since_seq=<next_seq>&timeout=300" — it returns when a '
                "message arrives; then call get_messages to drain."
            ),
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

    @mcp.tool()
    async def surface_document(path: str, title: str) -> dict[str, Any]:
        """Surface a markdown document to Mission Control for operator review.

        Reads ``path`` ONCE at call time (snapshot semantics — the stored copy
        is immutable; later edits to the file are not reflected). The original
        path is stored as a display label only; the returned ``artifact_id`` is
        the sole fetch key.

        Path resolution: resolved to an absolute path against the server
        process cwd. The lead is trusted — no traversal restriction is
        imposed — but the path must point to an existing regular file.

        Args:
            path: Filesystem path to the markdown document to surface.
            title: Human-readable title shown in the Artifacts tray.

        Returns:
            artifact_id: Opaque UUID4 hex id (never path-derived). Use to
                reference the artifact in follow-up messages.
            crew_id: The crew this artifact belongs to.
        """
        if artifact_registry is None:
            raise ToolError(
                "surface_document is not available: no artifact registry is configured"
            )

        if len(title) > _MAX_TITLE_LEN:
            raise ToolError(
                f"title is {len(title):,} chars; limit is {_MAX_TITLE_LEN}"
            )

        # Resolve to absolute against cwd (trusted lead; document the contract).
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        resolved = resolved.resolve()

        # File-access sad paths — each returns a clean error, never a traceback.
        try:
            file_stat = resolved.stat()
        except FileNotFoundError:
            raise ToolError(f"path not found: {path!r}")
        except PermissionError:
            raise ToolError(f"permission denied reading: {path!r}")
        except OSError as exc:
            raise ToolError(f"OS error accessing {path!r}: {exc}")

        # Symlinks are followed by design (resolve() above dereferenced them);
        # after resolution a non-regular target is a directory, device, FIFO, etc.
        if not stat.S_ISREG(file_stat.st_mode):
            kind = "directory" if resolved.is_dir() else "non-regular file"
            raise ToolError(f"{path!r} is a {kind}, not a regular file")

        # Reject oversize BEFORE reading the whole file into memory — st_size is
        # already available from stat(). The post-read cap in store() still runs
        # as the authoritative check (covers a file that grows between stat and read).
        if file_stat.st_size > MAX_BODY_BYTES:
            raise ToolError(
                f"{path!r} is {file_stat.st_size:,} bytes; "
                f"limit is {MAX_BODY_BYTES:,} bytes (1 MiB)"
            )

        try:
            body_bytes = resolved.read_bytes()
        except PermissionError:
            raise ToolError(f"permission denied reading: {path!r}")
        except OSError as exc:
            raise ToolError(f"IO error reading {path!r}: {exc}")

        try:
            artifact_id = artifact_registry.store(
                path_label=str(resolved),
                title=title,
                surfacing_teammate=LEAD_ID,
                body_bytes=body_bytes,
            )
        except ArtifactTooLarge as exc:
            raise ToolError(str(exc))
        except ArtifactNotText as exc:
            raise ToolError(str(exc))

        return {
            "artifact_id": artifact_id,
            "crew_id": artifact_registry.crew_id,
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


def build_runtime(ui_port: int, ui_sock: "socket.socket | None" = None):
    """Construct the production runtime components with all wiring in place.

    Factored out of ``main()`` so the production wiring — notably the artifact
    registry threading through BOTH the MCP server and the UI server — is
    covered by tests. ``main()`` itself is not unit-testable (it binds sockets,
    runs servers, and installs signal handlers); this is the part that can be.

    Returns ``(broker, artifact_registry, server, registry, ui)``. ``registry``
    and ``ui`` are ``None`` when the UI is disabled (``ui_port <= 0``).
    """
    broker = Broker()
    artifact_registry = ArtifactRegistry(crew_id=broker.crew_id)
    server = make_server(
        broker=broker, ui_port=ui_port, artifact_registry=artifact_registry
    )

    if ui_port <= 0:
        return broker, artifact_registry, server, None, None

    from claude_crew.instance_registry import InstanceRegistry
    from claude_crew.ui_server import UIServer

    registry = InstanceRegistry(crew_id=broker.crew_id, port=ui_port)
    ui = UIServer(
        broker, port=ui_port, registry=registry, sock=ui_sock,
        artifact_registry=artifact_registry,
    )
    return broker, artifact_registry, server, registry, ui


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

    broker, artifact_registry, server, registry, ui = build_runtime(ui_port, ui_sock)

    if ui_port <= 0:
        if ui_sock:
            ui_sock.close()
        server.run()
        return

    from claude_crew.ui_server import UIServer  # for the promoted-leader construction below

    _LEADER_PORT = 7821
    is_leader = ui_port == _LEADER_PORT

    if is_leader:
        sys.stderr.write(f"[claude-crew] ui -> http://127.0.0.1:{ui_port}\n")

    async def _run() -> None:
        # Install SIGTERM handler inside the running event loop so it targets
        # the correct loop (anyio creates its own; loop.add_signal_handler must
        # be called from within it, not from main() before anyio.run()).
        loop = asyncio.get_running_loop()

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
                        promoted = UIServer(
                            broker, port=_LEADER_PORT, registry=registry,
                            sock=leader_sock, artifact_registry=artifact_registry,
                        )
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
            # Full clean shutdown on SIGTERM/SIGINT: deregister, then cancel the
            # task group so _ui_safe / _leader_watcher exit and the process can
            # terminate. Previously SIGTERM only deregistered, leaving the UI
            # server running and the process alive — orphan claude-crew processes
            # required SIGKILL to clear. Idempotent on repeated signals via
            # tg.cancel_scope (anyio collapses redundant cancels).
            def _shutdown_signal(sig_name: str) -> None:
                """Signal-driven full shutdown.

                Why we use os._exit rather than relying on tg.cancel_scope.cancel()
                alone: FastMCP's `run_stdio_async` blocks in a worker thread that
                anyio cannot cancel. tg.cancel_scope.cancel() marks tasks for
                cancellation, but the stdin-read thread keeps running, so
                `async with create_task_group()` never exits and the process
                never returns from anyio.run(). Pre-fix that's exactly the
                orphan-claude-crew bug. We do the user-visible cleanup
                synchronously (deregister the registry file so other instances
                see us go) and then force-exit; the OS reclaims sockets, threads,
                and the asyncio loop.
                """
                sys.stderr.write(f"[claude-crew] received {sig_name}, shutting down\n")
                sys.stderr.flush()
                try:
                    registry.deregister()
                except Exception as exc:
                    # Surface so the operator knows the registry may be stale —
                    # logger calls may not flush before os._exit. PermissionError
                    # is the most likely culprit (file owned by another uid).
                    sys.stderr.write(f"[claude-crew] deregister failed during shutdown: {exc}\n")
                    sys.stderr.flush()
                os._exit(0)

            for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")):
                try:
                    loop.add_signal_handler(sig, _shutdown_signal, name)
                except (NotImplementedError, RuntimeError):
                    # add_signal_handler is unsupported on some platforms / when
                    # not in the main thread. Fall back silently — the existing
                    # EOF-based shutdown path still works when Claude closes stdin.
                    pass

            tg.start_soon(_mcp_then_cancel)
            tg.start_soon(_ui_safe)
            tg.start_soon(_leader_watcher)

    anyio.run(_run)
