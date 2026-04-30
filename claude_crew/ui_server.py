"""HTTP + WebSocket UI server for the Mission Control dashboard.

Runs alongside the MCP stdio server in the same anyio event loop.
Port controlled by CLAUDE_CREW_UI_PORT env var (default 7821, 0 = disabled).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from claude_crew.broker import Broker

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


class UIServer:
    def __init__(self, broker: Broker, port: int = 7821) -> None:
        self._broker = broker
        self._port = port
        self._html: str | None = None

    def _get_html(self) -> str:
        if self._html is None:
            try:
                self._html = _DASHBOARD_PATH.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._html = (
                    "<html><body style='font-family:monospace;padding:2rem'>"
                    "<p>claude-crew dashboard not found.</p>"
                    f"<p>Expected: {_DASHBOARD_PATH}</p>"
                    "</body></html>"
                )
        return self._html

    def _build_state(self) -> dict[str, Any]:
        broker = self._broker
        now = time.time()

        agents: list[dict[str, Any]] = []
        for info in broker._info.values():
            if not info.alive:
                continue

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

            agents.append({
                "id": info.id,
                "role": info.role,
                "model": _normalize_model(model_raw),
                "status": status,
                "uptime": int(now - info.spawned_at),
                "lastMsg": _ts(last_activity),
                "cost": 0.0,
                "tokens": {"in": 0, "out": 0},
                "tools": current_tool_names,
                "current_tool": snap.get("current_tool"),
            })

        messages: list[dict[str, Any]] = []
        for env in broker._log[-200:]:
            payload = env.payload
            if isinstance(payload, dict) and payload.get("error"):
                continue
            body = payload if isinstance(payload, str) else json.dumps(payload)
            messages.append({
                "t": _ts(env.timestamp),
                "from": env.sender,
                "to": env.recipient,
                "kind": "msg",
                "body": str(body)[:500],
            })

        # Include all info entries (alive and dead) — crew age = first spawn ever,
        # regardless of whether that teammate is still alive.
        spawn_times = [info.spawned_at for info in broker._info.values()]
        crew_uptime = int(now - min(spawn_times)) if spawn_times else 0

        instance: dict[str, Any] = {
            "id": broker.crew_id,
            "label": f"crew-{broker.crew_id}",
            "cwd": "~",
            "branch": "main",
            "uptime": crew_uptime,
            "status": "active" if agents else "idle",
            "cost": 0.0,
            "tokens": {"in": 0, "out": 0},
            "agents": agents,
        }

        return {
            "instances": [instance],
            "transcripts": {broker.crew_id: messages},
        }

    async def _handle_root(self, request: Request) -> HTMLResponse:
        return HTMLResponse(self._get_html())

    async def _handle_state(self, request: Request) -> JSONResponse:
        try:
            return JSONResponse(self._build_state())
        except Exception:
            _logger.exception("UI state build error")
            return JSONResponse({"error": "internal_error"}, status_code=500)

    async def _handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                state = self._build_state()
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
        config = uvicorn.Config(
            self._make_app(),
            host="127.0.0.1",
            port=self._port,
            log_level="error",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        await server.serve()
