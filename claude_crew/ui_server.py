"""HTTP + WebSocket UI server for the Mission Control dashboard.

Runs alongside the MCP stdio server in the same anyio event loop.
Port controlled by CLAUDE_CREW_UI_PORT env var (default auto, 0 = disabled).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
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

from claude_crew.artifact_registry import ArtifactRegistry
from claude_crew.broker import LEAD_ID, Broker, BrokerSnapshot
from claude_crew.ctx_window import resolve_ctx_window
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.local_model_metrics import fetch_local_slot_metrics
from claude_crew.redaction import REDACTION_VERSION, _TOOL_OUTPUT_BYTE_CAP
from claude_crew.teammate import ToolEvent

_logger = logging.getLogger(__name__)

_DASHBOARD_PATH = Path(__file__).parent / "ui" / "dashboard.html"
_POLL_INTERVAL = 1.5
# Cadence of the background local-model /slots probe. Decoupled from the state
# build / push loop so a slow-or-down backend can never stall _build_state.
_LOCAL_PROBE_INTERVAL = 5.0
_BRANCH_TTL_SECONDS = 30
_BRANCH_DETECT_TIMEOUT = 2.0
# Server-side cap on the /wait-messages long-poll, mirroring the MCP
# get_messages tool's MAX_WAIT_SECONDS. A caller-supplied timeout above this
# is clamped before it reaches the broker.
_WAIT_MESSAGES_MAX_TIMEOUT = 600.0
# Path-param allowlist for /tool-output route — rejects traversal chars, spaces, etc.
_PATH_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
# Ceiling on concurrent in-flight /wait-messages long-polls per UIServer.
# The realistic caller is a single lead backgrounding one curl at a time;
# this is a backstop so a buggy/looping caller can't pile up parked tasks.
# Above the ceiling the endpoint returns 429 rather than parking another waiter.
_WAIT_MESSAGES_MAX_INFLIGHT = 32

# Threat-model note for /wait-messages (v1): the endpoint is bound to localhost
# only (see serve()) and is content-free — it returns {waiting, count, next_seq},
# never message payloads. No auth token is required on the rationale that a
# single-user dev machine's localhost is trusted. `count`/`next_seq` do disclose
# message *volume/arrival cadence* (not content) to any local process; on a
# shared/CI host that metadata leak would warrant an auth token. Revisit the
# no-auth decision if this is ever deployed beyond a single-user workstation.


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
        artifact_registry: ArtifactRegistry | None = None,
    ) -> None:
        self._broker = broker
        self._port = port
        self._registry = registry
        self._sock = sock  # pre-bound socket; closed in serve() finally block
        self._cwd = cwd if cwd is not None else os.getcwd()
        self._artifact_registry = artifact_registry
        self._branch_cache: tuple[str, float] = ("main", 0.0)
        # Count of concurrently parked /wait-messages long-polls (M-2 backstop).
        # Safe as a plain int: asyncio is single-threaded and the check→increment
        # in _handle_wait_messages has no await between them.
        self._wait_inflight: int = 0
        # Cached local crew_id for /tool-output routing. Derived from a snapshot
        # so this module never reads the crew id off the broker directly (SC-2
        # decoupling), and cached because crew_id is immutable per broker.
        self._cached_crew_id: str | None = None
        # Long-lived client: connection pooling across push cycles.
        # Closed in serve()'s finally block.
        self._http_client = httpx.AsyncClient(timeout=2.0)
        # Optional local-model metrics probe URL (e.g. "http://127.0.0.1:8080").
        # When set, _build_state fetches the local llama.cpp /slots gauge and the
        # ctx-window resolver uses it for local-backed teammates. Unset = disabled
        # (Anthropic strategy for all). Opt-in so non-local crews never probe.
        self._local_model_url = os.environ.get("CLAUDE_CREW_LOCAL_MODEL_URL")
        # The /slots probe powers the live local ctx-window gauge. It runs on a
        # background task (_local_probe_loop) on its own cadence and writes the
        # latest result into _local_metrics_cache; _build_state reads that cache
        # synchronously and NEVER awaits the network. This keeps a slow-or-down
        # backend from stalling state builds and flapping crews to "unreachable"
        # (a down localhost port drops SYNs here rather than refusing, so an
        # inline probe would block the full timeout every build). Default ON;
        # set CLAUDE_CREW_LOCAL_MODEL_PROBE=0 to disable entirely (no task,
        # no gauge).
        self._local_model_probe = os.environ.get("CLAUDE_CREW_LOCAL_MODEL_PROBE", "1") != "0"
        # Latest /slots metrics (or None when the backend is down/slow/absent),
        # refreshed by _local_probe_loop. Read synchronously by _build_state.
        self._local_metrics_cache: dict[str, Any] | None = None
        self._local_metrics_task: asyncio.Task | None = None

    def _own_crew_id(self) -> str:
        """Local broker's crew_id, sourced from a snapshot (not a direct attr
        read — SC-2) and cached (crew_id never changes for a broker)."""
        if self._cached_crew_id is None:
            self._cached_crew_id = self._broker.snapshot(log_limit=0).crew_id
        return self._cached_crew_id

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
        self, snapshot: BrokerSnapshot, local_metrics: "dict[str, Any] | None" = None
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
                    # Canonical context-window metric — one sink, strategy chosen
                    # per teammate: local /slots for local-backed teammates, else
                    # Anthropic usage. Rendered source-agnostically by the dashboard.
                    "ctx_window": resolve_ctx_window(
                        is_local=(live_entry.is_local if live_entry is not None else False),
                        peak_in=agent_last_peak,
                        local_metrics=(
                            local_metrics
                            if (live_entry is not None and live_entry.is_local)
                            else None
                        ),
                    ),
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
                        # Dead teammates: backend unknown post-death → Anthropic strategy.
                        "ctx_window": resolve_ctx_window(
                            is_local=False, peak_in=agent_last_peak, local_metrics=None
                        ),
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
                "tool_use_id": ev.tool_use_id,
                # Owning crew so the dashboard can route the /tool-output fetch to
                # the right instance. For remote instances this record is built by
                # the follower's own _build_local_instance, so it carries the
                # follower's crew_id; the leader proxies the fetch there.
                "crew_id": snapshot.crew_id,
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
            # Artifact metadata (no body). crew_id is load-bearing for the
            # multi-instance proxy: the leader must route /artifact fetches to
            # the owning follower when crew_id != self._own_crew_id().
            "artifacts": (
                self._artifact_registry.metadata_list()
                if self._artifact_registry is not None
                else []
            ),
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

    def _ensure_local_probe(self) -> None:
        """Start the background /slots probe once, if enabled. Idempotent and
        cheap (no await, single attribute check). Called from both serve() and
        the WS handler so the probe runs regardless of how the server was
        launched (direct uvicorn, leader, promoted leader). Requires a running
        event loop. serve()'s finally cancels the task on teardown."""
        if self._local_metrics_task is not None:
            return
        if not (self._local_model_url and self._local_model_probe):
            return
        self._local_metrics_task = asyncio.create_task(self._local_probe_loop())

    async def _refresh_local_metrics(self) -> None:
        """One /slots probe → cache write. Metrics on success, None on any
        failure/timeout (a down backend self-hides the gauge). Re-raises only
        CancelledError so the owning task can be torn down cleanly."""
        try:
            self._local_metrics_cache = await fetch_local_slot_metrics(
                self._local_model_url, client=self._http_client
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._local_metrics_cache = None

    async def _local_probe_loop(self) -> None:
        """Refresh the local-model /slots cache on a fixed cadence.

        Runs off the state-build path so a slow-or-down backend can never stall
        _build_state. Each cycle refreshes the cache and sleeps
        _LOCAL_PROBE_INTERVAL. A swallowed-exception refresh keeps the loop
        alive; it only exits via cancellation (serve()'s finally).
        """
        while True:
            await self._refresh_local_metrics()
            await asyncio.sleep(_LOCAL_PROBE_INTERVAL)

    async def _build_state(self, local_only: bool = False) -> dict[str, Any]:
        snapshot = self._broker.snapshot(log_limit=200)
        # Local-model gauge: read the latest cached /slots metrics WITHOUT any
        # network I/O. _local_probe_loop refreshes the cache on its own cadence;
        # reading it here keeps _build_state instant even when the backend hangs.
        # The ctx-window resolver inside _build_local_instance applies it to
        # local-backed teammates. Each instance caches its own local model; the
        # value rides _fetch_remote_state's wholesale copy, so no proxy endpoint
        # is needed.
        local_metrics = None
        if self._local_model_url and self._local_model_probe:
            local_metrics = self._local_metrics_cache
        local_instance, local_messages = self._build_local_instance(snapshot, local_metrics)
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
        self._ensure_local_probe()
        try:
            while True:
                state = await self._build_state()
                await ws.send_json({"type": "state", "data": state})
                await asyncio.sleep(_POLL_INTERVAL)
        except WebSocketDisconnect:
            pass
        except Exception:
            _logger.exception("UI WebSocket error; connection closed")

    async def _wait_messages(self, since_seq: int, timeout: float) -> dict[str, Any]:
        """Content-free long-poll: block until the lead has mail past since_seq.

        Composes the broker's existing primitives — get_messages (the level
        check) and wait_for_lead_message (the block) — and returns ONLY a signal:
        ``{waiting, count, next_seq}``. No message payloads cross this boundary;
        the lead drains actual content via the get_messages MCP tool.

        Level-triggered by construction (D1): the leading get_messages check
        returns immediately when mail already sits past since_seq, closing the
        lost-wakeup race where a message lands after the lead drains but before
        the next waiter parks on the Condition. The post-wait re-check mirrors
        the MCP get_messages tool exactly.

        The caller's timeout is clamped to _WAIT_MESSAGES_MAX_TIMEOUT so an
        unbounded value cannot pin a connection open indefinitely.
        """
        msgs = self._broker.get_messages(recipient=LEAD_ID, since_seq=since_seq)
        if not msgs:
            capped = min(timeout, _WAIT_MESSAGES_MAX_TIMEOUT)
            await self._broker.wait_for_lead_message(capped)
            msgs = self._broker.get_messages(recipient=LEAD_ID, since_seq=since_seq)
        next_seq = msgs[-1].seq if msgs else since_seq
        return {"waiting": bool(msgs), "count": len(msgs), "next_seq": next_seq}

    async def _handle_wait_messages(self, request: Request) -> JSONResponse:
        """HTTP boundary for the message-wait long-poll.

        Parses ``since_seq`` (default 0) and ``timeout`` (default 300s) query
        params, returning 400 on malformed/out-of-range values, then delegates
        to _wait_messages. Localhost-only by binding (see serve()); the response
        is content-free so no auth token is required in v1 (see threat-model
        note at module top).

        Validation hardening:
        - since_seq must parse as int and be >= 0 (a negative cursor would make
          get_messages return the entire log).
        - timeout must parse as a finite number >= 0. ``float("nan")`` and
          ``float("inf")`` parse successfully but would poison the clamp /
          detonate inside asyncio.timeout(); reject them up front. timeout == 0
          is allowed and means a non-blocking peek (pure level-check).
        - Above _WAIT_MESSAGES_MAX_INFLIGHT concurrent waiters → 429.
        - Any unexpected error → structured 500 (mirrors _handle_state) so a
          backgrounded curl always gets parseable JSON, never a bare stack page.
        """
        try:
            raw_since = request.query_params.get("since_seq", "0")
            try:
                since_seq = int(raw_since)
            except ValueError:
                return JSONResponse(
                    {"error": f"since_seq must be an integer, got {raw_since!r}"},
                    status_code=400,
                )
            if since_seq < 0:
                return JSONResponse(
                    {"error": f"since_seq must be >= 0, got {since_seq}"},
                    status_code=400,
                )

            raw_timeout = request.query_params.get("timeout", "300")
            try:
                timeout = float(raw_timeout)
            except ValueError:
                return JSONResponse(
                    {"error": f"timeout must be a number, got {raw_timeout!r}"},
                    status_code=400,
                )
            if not math.isfinite(timeout) or timeout < 0:
                return JSONResponse(
                    {"error": f"timeout must be a finite number >= 0, got {raw_timeout!r}"},
                    status_code=400,
                )

            if self._wait_inflight >= _WAIT_MESSAGES_MAX_INFLIGHT:
                return JSONResponse(
                    {
                        "error": "too_many_waiters",
                        "message": (
                            f"at most {_WAIT_MESSAGES_MAX_INFLIGHT} concurrent "
                            "/wait-messages long-polls are allowed"
                        ),
                    },
                    status_code=429,
                )

            self._wait_inflight += 1
            try:
                return JSONResponse(await self._wait_messages(since_seq, timeout))
            finally:
                self._wait_inflight -= 1
        except Exception:
            _logger.exception("wait-messages handler error")
            return JSONResponse({"error": "internal_error"}, status_code=500)

    async def _handle_tool_output(self, request: Request) -> JSONResponse:
        """HTTP endpoint for lazy-fetching stored tool output bodies.

        Multi-instance aware. The dashboard is a leader that AGGREGATES remote
        follower instances (see _fetch_remote_state / InstanceRegistry), and the
        modal fetches this endpoint same-origin against the leader. Tool output,
        however, lives in the OWNING instance's broker — so the path carries the
        row's ``crew_id`` and this handler routes:
          - crew_id == our own broker → serve locally;
          - otherwise → look the crew up in the registry and proxy the request
            to that instance's port (mirrors how /api/state is aggregated).
        Without this, a click on any remote instance's row hits the leader's
        broker, which never has that teammate → 404 for every remote row.

        Mirrors the /wait-messages security posture: localhost-only bind,
        structured-500 try/except, no auth token in v1. Path params are
        validated against ^[A-Za-z0-9_\\-]+$ to block traversal.

        Returns:
            200  {body, truncated, redaction_version}   — hit
            400  {error: "invalid_param"}               — bad path param
            404  {error: "not_found"}                   — miss / unknown crew
            500  {error: "internal_error"}              — unexpected exception
            502  {error: "bad_gateway"}                 — proxy to owner failed
        """
        try:
            crew_id = request.path_params["crew_id"]
            teammate_id = request.path_params["teammate_id"]
            tool_use_id = request.path_params["tool_use_id"]

            for name, value in (
                ("crew_id", crew_id),
                ("teammate_id", teammate_id),
                ("tool_use_id", tool_use_id),
            ):
                if not _PATH_PARAM_RE.match(value):
                    return JSONResponse(
                        {"error": "invalid_param", "param": name},
                        status_code=400,
                    )

            # Own crew → serve from the local broker.
            if crew_id == self._own_crew_id():
                return self._local_tool_output_response(teammate_id, tool_use_id)

            # Remote crew → proxy to the owning instance via the registry.
            return await self._proxy_tool_output(crew_id, teammate_id, tool_use_id)
        except Exception:
            _logger.exception("tool-output handler error")
            return JSONResponse({"error": "internal_error"}, status_code=500)

    def _local_tool_output_response(
        self, teammate_id: str, tool_use_id: str
    ) -> JSONResponse:
        """Serve a tool output body from THIS instance's broker."""
        body = self._broker.get_tool_output(teammate_id, tool_use_id)
        if body is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        # `>=` (not `>`) is deliberate: the store caps over-limit bodies to
        # exactly the cap (with a `…` marker), so a served body AT the cap is
        # most likely truncated. Over-reporting truncation is the fail-safe
        # direction (operator may re-check) vs. claiming a truncated body is
        # complete. A store-time flag would be exact; tracked in BACKLOG.
        truncated = len(body.encode("utf-8")) >= _TOOL_OUTPUT_BYTE_CAP
        return JSONResponse({
            "body": body,
            "truncated": truncated,
            "redaction_version": REDACTION_VERSION,
        })

    async def _proxy_tool_output(
        self, crew_id: str, teammate_id: str, tool_use_id: str
    ) -> JSONResponse:
        """Proxy a tool-output fetch to the instance that owns ``crew_id``.

        The follower receives crew_id == its own broker's crew, so it serves
        locally (no re-proxy loop). 404 when the crew is unknown / not in the
        registry; 502 when the owner is registered but unreachable / malformed.
        """
        if self._registry is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        target = next(
            (e for e in self._registry.read_all() if e.get("crew_id") == crew_id),
            None,
        )
        if target is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        # Validate the port as a real TCP port before interpolating it into the
        # URL. The registry is a plain JSON file (`_read_entry` does a bare
        # json.loads); a corrupt/hand-edited entry could carry a non-int or
        # out-of-range "port". `bool` is an int subclass, so exclude it.
        port = target.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            return JSONResponse({"error": "not_found"}, status_code=404)
        url = (
            f"http://127.0.0.1:{port}/tool-output/"
            f"{crew_id}/{teammate_id}/{tool_use_id}"
        )
        try:
            resp = await self._http_client.get(url)
        except Exception:
            _logger.warning(
                "tool-output proxy to crew %s (port %s) failed",
                crew_id, port, exc_info=True,
            )
            return JSONResponse({"error": "bad_gateway"}, status_code=502)
        try:
            return JSONResponse(resp.json(), status_code=resp.status_code)
        except Exception:
            return JSONResponse({"error": "bad_gateway"}, status_code=502)

    async def _handle_artifact(self, request: Request) -> JSONResponse:
        """HTTP endpoint for lazy-fetching stored artifact bodies.

        Multi-instance aware: mirrors _handle_tool_output. The dashboard leader
        aggregates remote followers; artifact bodies live in the OWNING instance's
        registry. The path carries crew_id so the leader can proxy to the right
        follower when crew_id != the local broker's crew.

          - crew_id == local broker → serve from local artifact registry;
          - otherwise → proxy to the owning instance via InstanceRegistry.
        """
        try:
            crew_id = request.path_params["crew_id"]
            artifact_id = request.path_params["artifact_id"]

            if not _PATH_PARAM_RE.match(crew_id) or not _PATH_PARAM_RE.match(artifact_id):
                return JSONResponse({"error": "bad_request"}, status_code=400)

            if crew_id == self._own_crew_id():
                return self._local_artifact_response(artifact_id)

            return await self._proxy_artifact(crew_id, artifact_id)
        except Exception:
            _logger.exception("artifact handler error")
            return JSONResponse({"error": "internal_error"}, status_code=500)

    def _local_artifact_response(self, artifact_id: str) -> JSONResponse:
        if self._artifact_registry is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        rec = self._artifact_registry.get(artifact_id)
        if rec is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({
            "artifact_id": rec.artifact_id,
            "crew_id": rec.crew_id,
            "title": rec.title,
            "path_label": rec.path_label,
            "surfacing_teammate": rec.surfacing_teammate,
            "timestamp_utc": rec.timestamp_utc,
            "body": rec.body,
        })

    async def _proxy_artifact(self, crew_id: str, artifact_id: str) -> JSONResponse:
        """Proxy an artifact fetch to the instance that owns crew_id.

        Mirror of _proxy_tool_output. The follower receives crew_id == its own
        broker's crew and serves locally (no re-proxy loop). 404 when unknown /
        not in registry; 502 when registered but unreachable.
        """
        if self._registry is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        target = next(
            (e for e in self._registry.read_all() if e.get("crew_id") == crew_id),
            None,
        )
        if target is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        port = target.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            return JSONResponse({"error": "not_found"}, status_code=404)
        url = f"http://127.0.0.1:{port}/artifact/{crew_id}/{artifact_id}"
        try:
            resp = await self._http_client.get(url)
        except Exception:
            _logger.warning(
                "artifact proxy to crew %s (port %s) failed",
                crew_id, port, exc_info=True,
            )
            return JSONResponse({"error": "bad_gateway"}, status_code=502)
        try:
            return JSONResponse(resp.json(), status_code=resp.status_code)
        except Exception:
            return JSONResponse({"error": "bad_gateway"}, status_code=502)

    def _make_app(self) -> Starlette:
        return Starlette(routes=[
            Route("/", self._handle_root),
            Route("/api/state", self._handle_state),
            Route("/wait-messages", self._handle_wait_messages),
            Route("/tool-output/{crew_id}/{teammate_id}/{tool_use_id}", self._handle_tool_output),
            Route("/artifact/{crew_id}/{artifact_id}", self._handle_artifact),
            WebSocketRoute("/ws", self._handle_ws),
        ])

    async def serve(self) -> None:
        if self._registry is not None:
            self._registry.register()
        self._ensure_local_probe()
        try:
            if self._sock is not None:
                # The fd= path inherits whatever address the pre-bound socket
                # holds. _bind_ui_socket (server.py) binds 127.0.0.1, so the
                # effective host stays localhost — load-bearing for the
                # /wait-messages localhost-only guarantee. If that bind ever
                # changes to 0.0.0.0, this path would silently follow; add an
                # explicit guard there before doing so.
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
            if self._local_metrics_task is not None:
                self._local_metrics_task.cancel()
                try:
                    await self._local_metrics_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
            if self._registry is not None:
                self._registry.deregister()
            await self._http_client.aclose()
