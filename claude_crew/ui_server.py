"""HTTP + WebSocket UI server for the Mission Control dashboard.

Runs alongside the MCP stdio server in the same anyio event loop.
Port controlled by CLAUDE_CREW_UI_PORT env var (default auto, 0 = disabled).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from claude_crew.broker import Broker, BrokerSnapshot
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.teammate import ToolEvent

_logger = logging.getLogger(__name__)

_DASHBOARD_PATH = Path(__file__).parent / "ui" / "dashboard.html"
_POLL_INTERVAL = 1.5
_BRANCH_TTL_SECONDS = 30
_BRANCH_DETECT_TIMEOUT = 2.0


def _normalize_model(model_id: str | None) -> str:
    if not model_id:
        return "sonnet"
    m = model_id.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


def _derive_status(snap: dict[str, Any]) -> str:
    if snap.get("current_tool_count", 0) > 0:
        return "tool-use"
    if snap.get("current_turn_started_at_wallclock") is not None:
        return "thinking"
    return "idle"


def _ts(wallclock: float | None) -> str:
    if wallclock is None:
        wallclock = time.time()
    return datetime.fromtimestamp(wallclock, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _detect_branch(cwd: str) -> str | None:
    """Detect the current git branch in `cwd` via `git branch --show-current`.

    Returns the branch name on success, or None on any failure (not a git
    repo, git missing, subprocess error, timeout, detached HEAD producing
    empty output). Callers should fall back to "main" on None.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=_BRANCH_DETECT_TIMEOUT,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None  # empty = detached HEAD → fail


def _format_tool_event_body(ev: ToolEvent) -> str:
    """Render a ToolEvent for the dashboard stream (F19 D-9).

    Format: ``"<tool_name> (<outcome>, <duration>s)"`` with optional
    ``" — <args_summary>"`` and ``" [<error_summary>]"`` suffixes when populated.
    The typed ToolEvent fields stay available on the broker snapshot for any
    future structured view; this string is purely for the operator-readable
    dashboard column.
    """
    base = f"{ev.tool_name} ({ev.outcome}, {ev.duration_seconds:.2f}s)"
    if ev.args_summary:
        base += f" — {ev.args_summary}"
    if ev.error_summary:
        base += f" [{ev.error_summary}]"
    return base


def _unreachable_instance(crew_id: str) -> dict[str, Any]:
    return {
        "id": crew_id,
        "is_local": False,
        "label": f"crew-{crew_id}",
        "cwd": "~",
        "branch": "main",
        "uptime": 0,
        "status": "unreachable",
        "cost": 0.0,
        "tokens": {"in": 0, "out": 0},
        "agents": [],
    }


class UIServer:
    def __init__(
        self,
        broker: Broker,
        port: int = 7821,
        registry: InstanceRegistry | None = None,
        sock: "Any | None" = None,
        cwd: str | None = None,
    ) -> None:
        self._broker = broker
        self._port = port
        self._registry = registry
        self._sock = sock  # pre-bound socket; closed in serve() finally block
        self._cwd = cwd if cwd is not None else os.getcwd()
        self._branch_cache: tuple[str, float] = ("main", 0.0)
        # Long-lived client: connection pooling across push cycles.
        # Closed in serve()'s finally block.
        self._http_client = httpx.AsyncClient(timeout=2.0)

    def _get_html(self) -> str:
        try:
            return _DASHBOARD_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return (
                "<html><body style='font-family:monospace;padding:2rem'>"
                "<p>claude-crew dashboard not found.</p>"
                f"<p>Expected: {_DASHBOARD_PATH}</p>"
                "</body></html>"
            )

    def _get_branch(self) -> str:
        """Return the cached git branch name; refresh every _BRANCH_TTL_SECONDS.

        Falls back to "main" on any detection failure. Never raises.
        """
        now = time.time()
        if now < self._branch_cache[1]:
            return self._branch_cache[0]
        detected = _detect_branch(self._cwd)
        branch = detected if detected is not None else "main"
        self._branch_cache = (branch, now + _BRANCH_TTL_SECONDS)
        return branch

    def _build_local_instance(
        self, snapshot: BrokerSnapshot
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build the local broker's instance dict and transcript list FROM A SNAPSHOT.

        Production-path SC-2: reads zero broker or teammate private attrs.
        Function-input decoupling (D-11 / SC-10): accepts a BrokerSnapshot so tests
        can call this with synthetic data and no live Broker.
        """
        now = time.time()

        # Build a lookup from teammate id → LiveTeammateInfo for alive entries.
        live_by_id = {entry.info.id: entry for entry in snapshot.live}

        agents: list[dict[str, Any]] = []
        total_cost = 0.0
        total_in = 0
        total_out = 0
        for info in snapshot.teammates:
            if info.alive:
                live_entry = live_by_id.get(info.id)
                snap: dict[str, Any] = live_entry.status if live_entry is not None else {}
                model_raw = live_entry.model if live_entry is not None else None

                status = _derive_status(snap)
                last_activity = snap.get("last_activity_at_wallclock")

                current_tools = snap.get("current_tools", [])
                current_tool_names = [t["tool_name"] for t in current_tools]

                # F22 D-3: oldest_in_flight is the badge field. Explicit key allowlist
                # (NOT a copy or pop on the source dict) — args_summary MUST NOT ship
                # on the wire even if a future redactor regression leaves it non-blank.
                if current_tools:
                    t0 = current_tools[0]
                    oldest_in_flight = {
                        "tool_name": t0["tool_name"],
                        "tool_use_id": t0["tool_use_id"],
                        "started_at_wallclock": t0["started_at_wallclock"],
                    }
                else:
                    oldest_in_flight = None

                agent_cost = float(snap.get("total_cost_usd", 0.0))
                agent_in = int(snap.get("total_input_tokens", 0))
                agent_out = int(snap.get("total_output_tokens", 0))
                agent_last_in = int(snap.get("last_turn_input_tokens", 0))
                agent_last_out = int(snap.get("last_turn_output_tokens", 0))
                agent_last_peak = int(snap.get("last_turn_peak_invocation_input_tokens", 0))

                # F22 D-8: API surface freeze. tools[] = full set of names (frozen
                # as-of-#22, no new consumers). current_tool = last-started scalar
                # (legacy, retained for SC-9). current_tools[] = canonical structured
                # list. oldest_in_flight = badge field; pair with instance.now_wallclock.
                agent_entry: dict[str, Any] = {
                    "id": info.id,
                    "role": info.role,
                    "name": info.name,
                    "model": _normalize_model(model_raw),
                    # API-authoritative model id from the most recent
                    # AssistantMessage. None until the first assistant
                    # turn completes. Raw id (not normalized) so the UI
                    # can show the exact value Anthropic returned.
                    "active_model": snap.get("active_model"),
                    "status": status,
                    "uptime": int(now - info.spawned_at),
                    "lastMsg": _ts(last_activity),
                    "cost": agent_cost,
                    "tokens": {"in": agent_in, "out": agent_out},
                    "last_turn": {
                        "in": agent_last_in,
                        "out": agent_last_out,
                        "peak_in": agent_last_peak,
                    },
                    "tools": current_tool_names,
                    "current_tool": snap.get("current_tool"),
                    "oldest_in_flight": oldest_in_flight,
                    "in_flight_count": len(current_tools),
                    "last_tool_completed": snap.get("last_tool_completed"),
                }
                # ui-agent-transparency: embed config snapshot when present.
                # Omit the key entirely (not null) when no AgentDef was resolved.
                # live_entry can be None when snapshot.live does not include every alive
                # teammate (e.g., test fixtures that construct BrokerSnapshot with live=()
                # directly). Guard is load-bearing for those paths.
                agent_config = live_entry.config if live_entry is not None else None
                if agent_config is not None:
                    agent_entry["config"] = agent_config
                agents.append(agent_entry)

                total_cost += agent_cost
                total_in += agent_in
                total_out += agent_out
            else:
                # Dead teammates: contribute to instance-level aggregate (D-6)
                # and — if a config snapshot was retained — appear in the agents
                # list as dimmed rows so the dashboard can still show chips/panel.
                agent_cost = float(info.total_cost_usd_at_death or 0.0)
                agent_in = int(info.total_input_tokens_at_death or 0)
                agent_out = int(info.total_output_tokens_at_death or 0)
                agent_last_in = int(info.last_turn_input_tokens_at_death or 0)
                agent_last_out = int(info.last_turn_output_tokens_at_death or 0)
                agent_last_peak = int(info.last_turn_peak_invocation_input_tokens_at_death or 0)
                total_cost += agent_cost
                total_in += agent_in
                total_out += agent_out

                dead_config = snapshot.dead_configs.get(info.id)
                if dead_config is not None:
                    dead_entry: dict[str, Any] = {
                        "id": info.id,
                        "role": info.role,
                        "name": info.name,
                        "model": "sonnet",  # model unknown post-death; normalized default
                        # API-authoritative model preserved from tombstone (None if
                        # no AssistantMessage was observed before death).
                        "active_model": info.active_model_at_death,
                        "status": "dead",
                        "uptime": int((info.died_at_wallclock or now) - info.spawned_at),
                        "lastMsg": _ts(info.last_activity_at_wallclock_at_death),
                        "cost": agent_cost,
                        "tokens": {"in": agent_in, "out": agent_out},
                        "last_turn": {
                            "in": agent_last_in,
                            "out": agent_last_out,
                            "peak_in": agent_last_peak,
                        },
                        "tools": [],
                        "current_tool": None,
                        "oldest_in_flight": None,
                        "in_flight_count": 0,
                        "last_tool_completed": info.last_tool_completed_at_death,
                        "dead": True,
                        "config": dead_config,
                    }
                    agents.append(dead_entry)

        # F19 D-8 + sentinel D1: build messages as (float_ts, record) tuples so we
        # sort on the RAW wallclock — _ts() truncates to whole seconds, sorting on
        # the formatted string would be lossy within the same second.
        merged: list[tuple[float, dict[str, Any]]] = []
        for env in snapshot.log:
            payload = env.payload
            if isinstance(payload, dict) and payload.get("error"):
                continue
            if isinstance(payload, str):
                body = payload
            elif isinstance(payload, dict) and "text" in payload:
                body = payload["text"]
            else:
                body = json.dumps(payload)
            merged.append((env.timestamp, {
                "t": _ts(env.timestamp),
                "from": env.sender,
                "to": env.recipient,
                "kind": "msg",
                "body": str(body)[:10000],
            }))

        # F19 D-8: merge tool events as kind="tool" entries. Filter Task at this
        # render step (Q2 revised) — preserve in snapshot.tool_events for any
        # future view, hide from operator dashboard to avoid double-rendering #7.
        for ev in snapshot.tool_events:
            if ev.tool_name == "Task":
                continue
            merged.append((ev.finished_at_wallclock, {
                "t": _ts(ev.finished_at_wallclock),
                "from": ev.teammate_id,
                "to": None,
                "kind": "tool",
                "body": _format_tool_event_body(ev),
            }))

        merged.sort(key=lambda pair: pair[0])  # stable, raw-float ordering
        messages: list[dict[str, Any]] = [rec for _, rec in merged]

        spawn_times = [info.spawned_at for info in snapshot.teammates]
        crew_uptime = int(now - min(spawn_times)) if spawn_times else 0

        instance: dict[str, Any] = {
            "id": snapshot.crew_id,
            "is_local": True,
            "label": f"crew-{snapshot.crew_id}",
            "cwd": "~",
            "branch": self._get_branch(),
            "uptime": crew_uptime,
            "status": "active" if agents else "idle",
            "cost": total_cost,
            "tokens": {"in": total_in, "out": total_out},
            # F22 D-4: server-stamped wall-clock for clock-skew-safe elapsed display.
            # Single time.time() per _build_local_instance call (the `now` local
            # above), paired with each agent's oldest_in_flight.started_at_wallclock
            # — both produced on the same producer's clock. Per-instance, not
            # per-agent: _build_state runs synchronously, one stamp covers all.
            "now_wallclock": now,
            "agents": agents,
            # startup-diagnostics-dashboard: surface frozen startup-time
            # log records (pack-shadow trail, unknown skills, frontmatter
            # rejections, etc.) on the instance payload. Always present —
            # empty list when the collector captured nothing or capture
            # was skipped (stub mode). Each entry is a flat dict with
            # keys {level, message, source, timestamp, category}.
            "startup_diagnostics": [
                {
                    "level": diag.level,
                    "message": diag.message,
                    "source": diag.source,
                    "timestamp": diag.timestamp,
                    "category": diag.category,
                }
                for diag in snapshot.startup_diagnostics
            ],
        }
        return instance, messages

    async def _fetch_remote_state(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch /api/state from a remote instance. Returns None on any failure."""
        crew_id = entry.get("crew_id", "")
        port = entry.get("port")
        if not port:
            return None
        try:
            # ?local=1 tells the remote to skip its own registry fanout, breaking
            # the circular dependency where A fetches B which fetches A.
            resp = await self._http_client.get(f"http://127.0.0.1:{port}/api/state?local=1")
            resp.raise_for_status()
            data = resp.json()
            # Find the remote's own local instance (is_local=True); fall back to [0].
            instances = data["instances"]
            remote_instance = next(
                (i for i in instances if i.get("is_local")),
                instances[0] if instances else None,
            )
            if remote_instance is None:
                return None
            remote_instance = dict(remote_instance)  # don't mutate the parsed dict
            remote_instance["is_local"] = False
            return {
                "instance": remote_instance,
                "transcript": data.get("transcripts", {}).get(crew_id, []),
                "crew_id": crew_id,
            }
        except (IndexError, KeyError):
            # Remote is running but has no crew yet (startup race) or malformed
            return None
        except Exception:
            return None

    async def _build_state(self, local_only: bool = False) -> dict[str, Any]:
        snapshot = self._broker.snapshot(log_limit=200)
        local_instance, local_messages = self._build_local_instance(snapshot)
        instances: list[dict[str, Any]] = [local_instance]
        transcripts: dict[str, list] = {snapshot.crew_id: local_messages}

        if local_only or self._registry is None:
            return {"instances": instances, "transcripts": transcripts}

        remote_entries = [
            e for e in self._registry.read_all()
            if e.get("crew_id") != snapshot.crew_id
        ]
        if remote_entries:
            results = await asyncio.gather(
                *[self._fetch_remote_state(e) for e in remote_entries],
                return_exceptions=True,
            )
            for entry, result in zip(remote_entries, results):
                crew_id = entry.get("crew_id", "unknown")
                if isinstance(result, dict):
                    instances.append(result["instance"])
                    transcripts[result["crew_id"]] = result["transcript"]
                else:
                    instances.append(_unreachable_instance(crew_id))

        return {"instances": instances, "transcripts": transcripts}

    async def _handle_root(self, request: Request) -> HTMLResponse:
        return HTMLResponse(self._get_html())

    async def _handle_state(self, request: Request) -> JSONResponse:
        try:
            local_only = request.query_params.get("local") == "1"
            return JSONResponse(await self._build_state(local_only=local_only))
        except Exception:
            _logger.exception("UI state build error")
            return JSONResponse({"error": "internal_error"}, status_code=500)

    async def _handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                state = await self._build_state()
                await ws.send_json({"type": "state", "data": state})
                await asyncio.sleep(_POLL_INTERVAL)
        except WebSocketDisconnect:
            pass
        except Exception:
            _logger.exception("UI WebSocket error; connection closed")

    def _make_app(self) -> Starlette:
        return Starlette(routes=[
            Route("/", self._handle_root),
            Route("/api/state", self._handle_state),
            WebSocketRoute("/ws", self._handle_ws),
        ])

    async def serve(self) -> None:
        if self._registry is not None:
            self._registry.register()
        try:
            if self._sock is not None:
                config = uvicorn.Config(
                    self._make_app(),
                    fd=self._sock.fileno(),
                    log_level="error",
                    lifespan="off",
                )
            else:
                config = uvicorn.Config(
                    self._make_app(),
                    host="127.0.0.1",
                    port=self._port,
                    log_level="error",
                    lifespan="off",
                )
            server = uvicorn.Server(config)
            await server.serve()
        finally:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
            if self._registry is not None:
                self._registry.deregister()
            await self._http_client.aclose()
