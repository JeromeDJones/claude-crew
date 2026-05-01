"""HTTP + WebSocket UI server for the Mission Control dashboard.

Runs alongside the MCP stdio server in the same anyio event loop.
Port controlled by CLAUDE_CREW_UI_PORT env var (default auto, 0 = disabled).
"""

from __future__ import annotations

import asyncio
import json
import logging
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

from claude_crew.broker import Broker
from claude_crew.instance_registry import InstanceRegistry

_logger = logging.getLogger(__name__)

_DASHBOARD_PATH = Path(__file__).parent / "ui" / "dashboard.html"
_POLL_INTERVAL = 1.5


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
    ) -> None:
        self._broker = broker
        self._port = port
        self._registry = registry
        self._sock = sock  # pre-bound socket; closed in serve() finally block
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

    def _build_local_instance(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build the local broker's instance dict and transcript list."""
        broker = self._broker
        now = time.time()

        agents: list[dict[str, Any]] = []
        total_cost = 0.0
        total_in = 0
        total_out = 0
        for info in broker._info.values():
            if info.alive:
                teammate = broker._teammates.get(info.id)
                snap: dict[str, Any] = {}
                if teammate is not None:
                    try:
                        snap = teammate.status_snapshot()
                    except Exception:
                        pass

                model_raw = getattr(teammate, "_model", None) if teammate else None
                status = _derive_status(snap)
                last_activity = snap.get("last_activity_at_wallclock")

                current_tools = snap.get("current_tools", [])
                current_tool_names = [t["tool_name"] for t in current_tools]

                agent_cost = float(snap.get("total_cost_usd", 0.0))
                agent_in = int(snap.get("total_input_tokens", 0))
                agent_out = int(snap.get("total_output_tokens", 0))

                agents.append({
                    "id": info.id,
                    "role": info.role,
                    "name": info.name,
                    "model": _normalize_model(model_raw),
                    "status": status,
                    "uptime": int(now - info.spawned_at),
                    "lastMsg": _ts(last_activity),
                    "cost": agent_cost,
                    "tokens": {"in": agent_in, "out": agent_out},
                    "tools": current_tool_names,
                    "current_tool": snap.get("current_tool"),
                })

                total_cost += agent_cost
                total_in += agent_in
                total_out += agent_out
            else:
                # Dead teammates: excluded from agents (D-10) but contribute
                # to the instance-level aggregate (D-6).
                total_cost += float(info.total_cost_usd_at_death or 0.0)
                total_in += int(info.total_input_tokens_at_death or 0)
                total_out += int(info.total_output_tokens_at_death or 0)

        messages: list[dict[str, Any]] = []
        for env in broker._log[-200:]:
            payload = env.payload
            if isinstance(payload, dict) and payload.get("error"):
                continue
            if isinstance(payload, str):
                body = payload
            elif isinstance(payload, dict) and "text" in payload:
                body = payload["text"]
            else:
                body = json.dumps(payload)
            messages.append({
                "t": _ts(env.timestamp),
                "from": env.sender,
                "to": env.recipient,
                "kind": "msg",
                "body": str(body)[:2000],
            })

        spawn_times = [info.spawned_at for info in broker._info.values()]
        crew_uptime = int(now - min(spawn_times)) if spawn_times else 0

        instance: dict[str, Any] = {
            "id": broker.crew_id,
            "is_local": True,
            "label": f"crew-{broker.crew_id}",
            "cwd": "~",
            "branch": "main",
            "uptime": crew_uptime,
            "status": "active" if agents else "idle",
            "cost": total_cost,
            "tokens": {"in": total_in, "out": total_out},
            "agents": agents,
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
        local_instance, local_messages = self._build_local_instance()
        instances: list[dict[str, Any]] = [local_instance]
        transcripts: dict[str, list] = {self._broker.crew_id: local_messages}

        if local_only or self._registry is None:
            return {"instances": instances, "transcripts": transcripts}

        remote_entries = [
            e for e in self._registry.read_all()
            if e.get("crew_id") != self._broker.crew_id
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
