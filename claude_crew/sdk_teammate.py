"""SdkTeammate: a Teammate backed by claude-agent-sdk's ClaudeSDKClient.

Drives the SDK as documented:
  async with ClaudeSDKClient(options) as client:
      await client.query(prompt, session_id="default")
      async for msg in client.receive_response():
          ...

Each turn:
  1. Pull an envelope from the inbox.
  2. Translate payload → prompt string.
  3. client.query(prompt) and drain receive_response() within a per-turn backstop.
  4. Send a result envelope (success or error) back to the original sender.

Errors and backstop fires produce a structured error envelope and the loop continues.
The teammate dies (worker task exits) only on shutdown signal, catastrophic
failure outside the per-turn handler, or SDK process death detected by the
liveness poll task.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    RateLimitEvent,
    ResultMessage,
    TaskNotificationMessage,
    TextBlock,
)

logger = logging.getLogger(__name__)

from claude_crew.broker import LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.redaction import REDACTION_VERSION, redact_error, summarize_args
from pathlib import Path

from claude_crew.subagents import load_default_pack
from claude_crew.teammate import Teammate, ToolEvent, _ToolUseEntry, _tool_events_maxlen
from claude_crew.teammate_prompt import build_teammate_prompt

if TYPE_CHECKING:
    from claude_crew.broker import Broker

# Bounded wait for graceful shutdown of the worker task.
SHUTDOWN_TIMEOUT_SECONDS: float = 5.0

# D4: Backstop sequence timing constants.
INTERRUPT_GRACE_SECONDS: float = 30.0
POST_INTERRUPT_DRAIN_SECONDS: float = 5.0

# D8: Liveness poll defaults. Both are env-overridable at __init__ time.
POLL_INTERVAL_SECONDS_DEFAULT: float = 5.0
TURN_BACKSTOP_SECONDS_DEFAULT: float = 3600.0

# D8: Max concurrent tools before soft overflow guard (logged but accepted).
MAX_CONCURRENT_TOOLS: int = 64

_SHUTDOWN_SENTINEL: object = object()


# F7: Subagent-activity envelope dataclasses.
@dataclasses.dataclass(frozen=True)
class _SubagentUseEntry:
    agent_id: str
    tool_use_id: str
    spawned_at_wallclock: float


@dataclasses.dataclass(frozen=True)
class _ClosedSubagentEntry:
    agent_id: str
    tool_use_id: str
    spawned_at_wallclock: float
    finished_at_wallclock: float
    hook_outcome: str


class RateLimitedError(Exception):
    """Raised by _collect_response_text when a RateLimitEvent is observed."""


def _payload_to_prompt(payload: Any) -> str:
    """Translate an inbound envelope payload into an SDK prompt string."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict) and "prompt" in payload:
        prompt = payload["prompt"]
        return prompt if isinstance(prompt, str) else json.dumps(prompt)
    return json.dumps(payload)


def _classify_error(exc: BaseException) -> str:
    """Map an exception into one of the error-envelope code values."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if isinstance(exc, RateLimitedError) or "rate" in msg and "limit" in msg:
        return "rate_limited"
    if "api" in name.lower() or "anthropic" in name.lower():
        return "api_error"
    if "cli" in name.lower() or "connection" in name.lower():
        return "api_error"
    return "internal"


@dataclass(frozen=True)
class TurnDrainResult:
    """What we observed during one drain of client.receive_response().

    text: concatenated TextBlock content from AssistantMessages.
    failed_task_notifs: all TaskNotificationMessages with status in
        {"failed","stopped"} observed this turn, in arrival order. Used by
        SC-8(a) to synthesize an envelope when the parent didn't narrate
        over the failure. Empty list means no failures observed.
    turn_input_tokens: per-turn input tokens from ResultMessage.usage
        (includes cache tokens per D-3). Accumulate this across turns to get
        session total. None if no valid ResultMessage was observed
        (interrupted, malformed, etc.).
    turn_output_tokens: per-turn output tokens from ResultMessage.usage.
        Accumulate this across turns to get session total. None under same
        conditions as turn_input_tokens.
    cumulative_cost_usd: session-cumulative cost from ResultMessage.total_cost_usd.
        Overwrite (not accumulate) — the SDK already maintains the running total.
        None if ResultMessage was absent or total_cost_usd was None/malformed.
    """

    text: str
    failed_task_notifs: list[TaskNotificationMessage]
    # D-8: None = no valid ResultMessage observed; caller skips assignment.
    turn_input_tokens: int | None = None
    turn_output_tokens: int | None = None
    cumulative_cost_usd: float | None = None


async def _collect_response_text(
    client: Any,
    stamp_activity: Callable[[], None] | None = None,
    record_task_notif: Callable[[str, TaskNotificationMessage], None] | None = None,
) -> TurnDrainResult:
    """Drain client.receive_response() and accumulate text + subagent failures.

    - D1: invokes stamp_activity at loop top, BEFORE any continue branch, so
      RateLimitEvent and TaskNotificationMessage events also stamp activity.
    - Ignores tool-use, thinking, and other non-text blocks (Assumption A2).
    - On RateLimitEvent (status=rejected), raises RateLimitedError.
    - Calls record_task_notif(tool_use_id, tnm) for ALL TNM statuses when
      tnm.tool_use_id is not None (enables correlation in _handle_one_turn).
    - Tracks all TaskNotificationMessages with a failure-shaped status in
      failed_task_notifs; logs a WARNING for *every* such notification.
    - Terminates when the SDK iterator terminates (typically at ResultMessage).
    - Returns TurnDrainResult(text="", failed_task_notifs=[]) if nothing of
      substance was observed.

    The caller must wrap this in asyncio.wait_for to bound non-termination.
    """
    def _extract_token_cost_from_rm(
        rm: ResultMessage,
    ) -> tuple[int | None, int | None, float | None]:
        """Extract (per_turn_input, per_turn_output, cumulative_cost_usd) from a ResultMessage.

        D-1: ResultMessage is the single source; AssistantMessage.usage is never read.
        D-3: per_turn_input includes cache_read_input_tokens + cache_creation_input_tokens
             (all billed context). Accumulate per-turn values across turns to get session total.
        D-2: cumulative_cost_usd is session-total; overwrite (not accumulate) this value per turn.
        D-8: returns (None, None, None) on malformed/absent data; logs WARNING
             with the offending dict's KEYS (not values) to avoid leaking content.
        """
        usage = rm.usage
        cost = rm.total_cost_usd

        # Both absent: nothing to extract.
        if usage is None and cost is None:
            return (None, None, None)

        per_turn_input: int | None = None
        per_turn_output: int | None = None
        cost_usd: float | None = None

        if usage is not None:
            if not isinstance(usage, dict):
                logger.warning(
                    "_collect_response_text: ResultMessage.usage is not a dict "
                    "(type=%s); leaving token totals unchanged",
                    type(usage).__name__,
                )
                # cost may still be valid — fall through
            else:
                try:
                    per_turn_input = int(
                        usage.get("input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                    )
                    per_turn_output = int(usage.get("output_tokens", 0))
                except (TypeError, ValueError):
                    logger.warning(
                        "_collect_response_text: malformed ResultMessage.usage values "
                        "(keys=%s); leaving token totals unchanged",
                        list(usage.keys()),
                    )
                    per_turn_input = None
                    per_turn_output = None

        if cost is not None:
            try:
                cost_usd = float(cost)
            except (TypeError, ValueError):
                logger.warning(
                    "_collect_response_text: ResultMessage.total_cost_usd is not "
                    "convertible to float (type=%s); leaving cost unchanged",
                    type(cost).__name__,
                )

        return (per_turn_input, per_turn_output, cost_usd)

    text_parts: list[str] = []
    failed_task_notifs: list[TaskNotificationMessage] = []
    turn_input_tokens: int | None = None
    turn_output_tokens: int | None = None
    cumulative_cost_usd: float | None = None
    async for msg in client.receive_response():
        # D1 stamping order: invoke before any continue branch so every
        # event type (including RateLimitEvent, TaskNotificationMessage)
        # advances the activity timestamp.
        if stamp_activity is not None:
            stamp_activity()
        if isinstance(msg, RateLimitEvent):
            # status: 'allowed' (normal), 'allowed_warning' (near limit),
            # 'rejected' (over limit). Only the last is a real failure;
            # the rest are informational and the model still responded.
            info = getattr(msg, "rate_limit_info", None)
            status = getattr(info, "status", None)
            if status == "rejected":
                raise RateLimitedError(f"rate limit hit: {info}")
            continue
        if isinstance(msg, ResultMessage):
            # D-1: single source for cost AND tokens — read from ResultMessage only.
            # D-2: per-turn tokens accumulate; cumulative cost overwrites (SDK-maintained total).
            # D-6: multiple ResultMessages → last wins (overwrite semantics for cost only).
            ri, ro, rc = _extract_token_cost_from_rm(msg)
            if ri is not None:
                turn_input_tokens = ri
            if ro is not None:
                turn_output_tokens = ro
            if rc is not None:
                cumulative_cost_usd = rc
            continue
        if isinstance(msg, TaskNotificationMessage):
            # Fire callback for ALL statuses (completed/failed/stopped) so
            # _handle_one_turn can correlate TNMs with tool_use_ids. Skip if
            # tool_use_id is absent (can't correlate).
            if record_task_notif is not None and msg.tool_use_id is not None:
                record_task_notif(msg.tool_use_id, msg)
            if msg.status in ("failed", "stopped"):
                failed_task_notifs.append(msg)
                logger.warning(
                    "subagent failure: status=%s task_id=%s summary=%r",
                    msg.status, msg.task_id, msg.summary,
                )
            continue
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return TurnDrainResult(
        text="".join(text_parts),
        failed_task_notifs=failed_task_notifs,
        turn_input_tokens=turn_input_tokens,
        turn_output_tokens=turn_output_tokens,
        cumulative_cost_usd=cumulative_cost_usd,
    )


def _default_system_prompt(role: str) -> str:
    return f"You are a {role}. Help the lead with {role}-level work."


def _load_user_mcp_servers(home_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the full ``{name: config}`` map from ``~/.claude.json``'s mcpServers.

    Distinct from ``_user_loader._load_user_mcp_server_names`` (set of names only):
    the spawn-time path needs the full config dicts to inline into
    ``ClaudeAgentOptions.mcp_servers``. Best-effort: missing file, malformed
    JSON, or absent ``mcpServers`` key all return the empty dict (no exception).
    No module-level cache (Feature #17 D-11) — pytest tests planting fake
    ``~/.claude.json`` would otherwise leak across tests.
    """
    home = home_dir if home_dir is not None else Path.home()
    cfg_path = home / ".claude.json"
    try:
        text = cfg_path.read_text()
    except (OSError, FileNotFoundError):
        return {}
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(cfg, dict):
        return {}
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    return {name: cfg for name, cfg in servers.items() if isinstance(cfg, dict)}


def _resolve_mcp_servers(
    entries: list[str | dict[str, Any]] | tuple[str | dict[str, Any], ...],
    role: str,
    teammate_id: str,
    home_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Translate pack ``mcpServers`` (list of str|dict) → ClaudeAgentOptions dict.

    Per Feature #17 D-4, D-7, D-11, D-13:

    - String entries resolve against ``~/.claude.json``'s ``mcpServers`` map.
      Unresolvable names are skipped with a WARN; spawn does not fail.
    - Dict entries (already validated at pack-load to have ``type`` ∈
      ``{stdio, sse, http}`` — ``sdk`` is rejected) are assigned a key via
      the entry's ``name`` field, falling back to ``<type>_<index>``. The
      ``name`` field is stripped from the value before insertion since
      ``McpServerConfig`` does not include it.
    - Every inline-dict pass-through emits an INFO breadcrumb naming role,
      teammate id, server name, and type — connective tissue between D-12's
      accepted teammate-death-on-malformed-dict and #19/#22 dashboards.

    ``home_dir`` is an explicit param for test isolation: production callers
    pass ``None`` (defaults to ``Path.home()``); tests inject ``tmp_path``.
    """
    user_servers = _load_user_mcp_servers(home_dir)
    resolved: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name") or f"{entry.get('type', 'unnamed')}_{len(resolved)}"
            resolved[name] = {k: v for k, v in entry.items() if k != "name"}
            logger.info(
                "teammate=%s role=%s mcpServers passing through inline dict "
                "name=%s type=%s",
                teammate_id, role, name, entry.get("type"),
            )
        else:  # str
            cfg = user_servers.get(entry)
            if cfg is None:
                logger.warning(
                    "teammate=%s role=%s mcpServers names %r but server is not "
                    "registered in ~/.claude.json; skipping (load-time WARN may "
                    "have already fired)",
                    teammate_id, role, entry,
                )
                continue
            resolved[entry] = cfg
    return resolved


class SdkTeammate(Teammate):
    """Teammate driven by a ClaudeSDKClient over a persistent CLI subprocess."""

    def __init__(
        self,
        id: str,
        name: str,
        role: str,
        *,
        model: str = "claude-sonnet-4-6",
        effort: str | None = None,
        system_prompt: str | None = None,
        setting_sources: list[str] | None = None,
        agents: "dict[str, Any] | None" = None,
        pack_bodies: "dict[str, str] | None" = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
        allowed_tools: "list[str] | None" = None,
    ) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._model = model
        self._effort = effort
        # `agents=None` → load the bundled default pack. `agents={}` → explicit
        # empty (this teammate cannot delegate). `agents={...}` → custom pack
        # (Feature #3b's seam ride-along). load_default_pack() returns a
        # (pack, role_ss, bodies) tuple; we need both pack and bodies.
        if agents is None:
            _pack, _role_ss, _bodies = load_default_pack()
            self._agents = _pack
            # pack_bodies kwarg wins if provided; otherwise use bodies from default pack.
            self._pack_bodies: dict[str, str] = pack_bodies if pack_bodies is not None else _bodies
        else:
            self._agents = agents
            self._pack_bodies = pack_bodies if pack_bodies is not None else {}
        # Assign _system_prompt AFTER _agents and _pack_bodies are populated so
        # build_teammate_prompt has access to the full agents dict (A-3).
        role_def = self._agents.get(role)
        role_memory = getattr(role_def, "memory", None)

        # Warn once for unsupported memory values; suppress for "user" (injection handles it).
        if role_memory in ("project", "local"):
            logger.warning(
                "teammate=%s role=%s pack declares memory=%r; only 'user' is "
                "supported in v1 — no injection performed",
                self.id, role, role_memory,
            )

        if system_prompt is not None:
            self._system_prompt = system_prompt  # explicit override wins (edge case 2)
        else:
            _body = self._pack_bodies.get(role)
            if _body is not None:
                # Memory section computed inside else — skip I/O when override is active.
                _memory_section = None
                if role_memory == "user":
                    from claude_crew.teammate_memory import build_memory_section
                    try:
                        _memory_section = build_memory_section(
                            role, getattr(role_def, "tools", None)
                        )
                        logger.debug(
                            "teammate=%s role=%s memory section injected", self.id, role
                        )
                    except ValueError:
                        logger.warning(
                            "teammate=%s role=%s memory injection skipped: "
                            "role name contains unsafe characters",
                            self.id, role,
                        )
                self._system_prompt = build_teammate_prompt(
                    role, _body, self._agents, memory_section=_memory_section
                )
            else:
                # Fallback: role not in any loaded pack (D-7 legacy path)
                self._system_prompt = _default_system_prompt(role)
        self._setting_sources = (
            setting_sources if setting_sources is not None else ["user", "project"]
        )
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._allowed_tools = allowed_tools
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None

        # Base-class telemetry fields (Q5/D1). Mirror what StubTeammate does.
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None
        # F8: tool-tracking state (base class fields — T3 hooks populate these).
        # Mirror what StubTeammate.__init__ does; T3 will consume these.
        self._tool_uses: dict[str, Any] = {}
        self._recently_closed_tool_use_ids: collections.deque[str] = collections.deque(maxlen=64)
        self._last_tool_completed: dict[str, Any] | None = None
        # F19: completed tool-event deque (D-2). Populated by T2 hook append sites
        # (_on_post_common, _close_open_tools); read by Broker.snapshot (T3).
        self._completed_tool_events: collections.deque[ToolEvent] = collections.deque(
            maxlen=_tool_events_maxlen()
        )

        # F14: token/cost accumulation. Overwritten (not accumulated) per-turn-drain
        # from ResultMessage (D-1, D-2). Initialized to zero; reset on respawn (D-11).
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0
        # Last-turn deltas — context-window pressure signal. Overwrite each turn.
        self._last_turn_input_tokens: int = 0
        self._last_turn_output_tokens: int = 0

        # F7 subagent-tracking namespace (completely separate from F8 tool-tracking)
        self._subagent_uses: dict[str, _SubagentUseEntry] = {}
        self._closed_subagent_scratch: dict[str, _ClosedSubagentEntry] = {}
        self._recently_closed_subagent_use_ids: collections.deque[str] = collections.deque(maxlen=64)
        self._last_subagent_completed: dict[str, Any] | None = None
        self._task_notifs_by_tool_use_id: dict[str, TaskNotificationMessage] = {}

        # Liveness state (T4/D2/D4).
        self._death_suspected: bool = False
        self._death_in_flight_envelope: Envelope | None = None
        self._poll_task: asyncio.Task[None] | None = None
        # D2 start-ordering invariant: worker waits for poll task to signal
        # readiness before entering the inbox loop.
        self._poll_started: asyncio.Event = asyncio.Event()

        # D8: env-overridable timing for poll interval and per-turn backstop.
        self._poll_interval_seconds = float(
            os.environ.get("CLAUDE_CREW_LIVENESS_POLL_SECONDS", POLL_INTERVAL_SECONDS_DEFAULT)
        )
        self._backstop_seconds = float(
            os.environ.get("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", TURN_BACKSTOP_SECONDS_DEFAULT)
        )

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._run(), name=f"sdk-{self.id}")

    async def _on_pre_tool_use(self, inp: dict, tool_use_id: str, ctx: dict) -> dict:
        """Hook callback for PreToolUse event (D8, SC-1, SC-4).

        Wraps in try/except; stamps activity; branches on subagent vs main-agent;
        updates _tool_uses dict; writes transcript line.

        Also enforces the memory write guard: blocks Write/Edit calls whose
        file_path resolves into ~/.claude/projects/*/memory/** (the lead's
        project-scoped memory). See FEATURE-teammate-memory-write-guard.md.
        """
        try:
            self._stamp_activity()
            # Memory write guard — runs first, before any tracking, since a
            # denied call doesn't proceed to the tool dispatch path anyway.
            tool_name = inp.get("tool_name")
            if tool_name in ("Write", "Edit"):
                tool_input = inp.get("tool_input") or {}
                file_path = tool_input.get("file_path")
                if isinstance(file_path, str) and file_path:
                    from claude_crew.teammate_memory import (
                        is_lead_project_memory_path,
                        write_guard_deny_message,
                    )
                    if is_lead_project_memory_path(file_path):
                        reason = write_guard_deny_message(self.role, file_path)
                        logger.warning(
                            "memory_write_guard: blocked %s to %r for teammate=%s role=%s",
                            tool_name, file_path, self.id, self.role,
                        )
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": reason,
                            }
                        }
            # D3: subagent branch — activity stamped; spawn tracking path.
            if inp.get("agent_id") is not None:
                agent_id = inp["agent_id"]
                # Null tool_use_id guard.
                if not tool_use_id:
                    logger.warning(
                        "pre_tool_use: subagent with empty tool_use_id for teammate=%s, skipping",
                        self.id,
                    )
                    return {}
                # Duplicate guard (last-write-wins).
                if tool_use_id in self._subagent_uses:
                    logger.warning(
                        "pre_tool_use: duplicate subagent tool_use_id=%s for teammate=%s, last-write-wins",
                        tool_use_id,
                        self.id,
                    )
                # Soft cap guard.
                if len(self._subagent_uses) >= MAX_CONCURRENT_TOOLS:
                    logger.warning(
                        "pre_tool_use: concurrent subagent cap (%d) reached for teammate=%s",
                        MAX_CONCURRENT_TOOLS,
                        self.id,
                    )
                spawned_at_wallclock = time.time()
                # F2: emit JSONL FIRST, then store in dict.
                # If write fails, outer try/except swallows it and dict is never populated.
                # This guarantees subagent_result cannot appear without a preceding subagent_spawn.
                broker = self._broker
                if broker is not None:
                    broker._sink.write_tool_event(
                        "subagent_spawn",
                        {
                            "teammate_id": self.id,
                            "agent_id": agent_id,
                            "tool_use_id": tool_use_id,
                            "spawned_at_wallclock": spawned_at_wallclock,
                        },
                    )
                self._subagent_uses[tool_use_id] = _SubagentUseEntry(
                    agent_id=agent_id,
                    tool_use_id=tool_use_id,
                    spawned_at_wallclock=spawned_at_wallclock,
                )
                return {}
            # D8 defensive: null tool_use_id guard.
            if not tool_use_id:
                logger.warning(
                    "pre_tool_use: empty tool_use_id for teammate=%s, skipping",
                    self.id,
                )
                return {}
            # D8 pre-twice guard: last-write-wins + WARNING.
            if tool_use_id in self._tool_uses:
                logger.warning(
                    "pre_tool_use: duplicate tool_use_id=%s for teammate=%s, last-write-wins",
                    tool_use_id,
                    self.id,
                )
            # D8 soft overflow guard: cap check (accept anyway).
            if len(self._tool_uses) >= MAX_CONCURRENT_TOOLS:
                logger.warning(
                    "pre_tool_use: concurrent tool cap (%d) reached for teammate=%s",
                    MAX_CONCURRENT_TOOLS,
                    self.id,
                )
            # Build entry.
            args_summary = summarize_args(inp["tool_name"], inp["tool_input"])
            entry = _ToolUseEntry(
                tool_name=inp["tool_name"],
                tool_use_id=tool_use_id,
                started_at_wallclock=time.time(),
                args_summary=args_summary,
            )
            self._tool_uses[tool_use_id] = entry
            # Emit transcript.
            try:
                broker = self._broker
                if broker is not None:
                    broker._sink.write_tool_event(
                        "tool_start",
                        {
                            "teammate_id": self.id,
                            "tool_name": entry.tool_name,
                            "tool_use_id": tool_use_id,
                            "started_at_wallclock": entry.started_at_wallclock,
                            "args_summary": args_summary,
                            "redaction_version": REDACTION_VERSION,
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "pre_tool_use: write_tool_event failed for teammate=%s tool_use_id=%s: %s",
                    self.id,
                    tool_use_id,
                    exc,
                )
            return {}
        except Exception as exc:
            logger.warning(
                "pre_tool_use: internal exception for teammate=%s: %s",
                self.id,
                exc,
            )
            return {}

    async def _on_post_common(
        self,
        inp: dict,
        tool_use_id: str,
        *,
        outcome: str,
        error_text: str | None,
    ) -> dict:
        """Helper for PostToolUse and PostToolUseFailure (D8, SC-2, SC-3).

        Common logic: activity stamp, subagent branch, dedup guard, entry lookup,
        last_tool_completed update, transcript emit.
        """
        try:
            self._stamp_activity()
            # D3: subagent branch.
            if inp.get("agent_id") is not None:
                agent_id = inp["agent_id"]
                # Dedup guard.
                if tool_use_id in self._recently_closed_subagent_use_ids:
                    logger.warning(
                        "post_tool_use: duplicate subagent close for tool_use_id=%s teammate=%s",
                        tool_use_id,
                        self.id,
                    )
                    return {}
                # Pop from in-flight dict.
                entry = self._subagent_uses.pop(tool_use_id, None)
                if entry is None:
                    logger.warning(
                        "post_tool_use: orphan subagent Post for tool_use_id=%s teammate=%s (no Pre seen)",
                        tool_use_id,
                        self.id,
                    )
                    return {}
                self._recently_closed_subagent_use_ids.append(tool_use_id)
                # Move to scratch — _end_turn will emit the JSONL after stream drains.
                self._closed_subagent_scratch[tool_use_id] = _ClosedSubagentEntry(
                    agent_id=entry.agent_id,
                    tool_use_id=tool_use_id,
                    spawned_at_wallclock=entry.spawned_at_wallclock,
                    finished_at_wallclock=time.time(),
                    hook_outcome=outcome,
                )
                return {}
            # D8 defensive: null tool_use_id guard.
            if not tool_use_id:
                logger.warning(
                    "post_tool_use: empty tool_use_id for teammate=%s, skipping",
                    self.id,
                )
                return {}
            # D8 fifth guard: recently-closed dedup.
            if tool_use_id in self._recently_closed_tool_use_ids:
                logger.info(
                    "post_tool_use: late post for closed tool_use_id=%s (teammate=%s), suppressing duplicate",
                    tool_use_id,
                    self.id,
                )
                return {}
            # Lookup entry.
            entry = self._tool_uses.pop(tool_use_id, None)
            # D8 post-without-pre: emit audit line + WARNING.
            if entry is None:
                logger.warning(
                    "post_tool_use: post fired without matching pre for tool_use_id=%s (teammate=%s)",
                    tool_use_id,
                    self.id,
                )
                # D11 schema-honesty fix: orphan_post records carry the same
                # field set as normal tool_end records (sentinel inner-4 review).
                try:
                    broker = self._broker
                    if broker is not None:
                        broker._sink.write_tool_event(
                            "tool_end",
                            {
                                "teammate_id": self.id,
                                "tool_name": inp.get("tool_name", "<unknown>"),
                                "tool_use_id": tool_use_id,
                                "outcome": "orphan_post",
                                "finished_at_wallclock": time.time(),
                                "duration_seconds": None,
                                "error_summary": redact_error(
                                    "post fired without matching pre"
                                ),
                                "redaction_version": REDACTION_VERSION,
                            },
                        )
                except Exception as exc:
                    logger.warning(
                        "post_tool_use: write_tool_event (no-pre case) failed for teammate=%s: %s",
                        self.id,
                        exc,
                    )
                return {}
            # Normal path: compute duration, update last_tool_completed, emit transcript.
            finished_at_wallclock = time.time()
            duration_seconds = finished_at_wallclock - entry.started_at_wallclock
            error_summary = redact_error(error_text) if error_text else None
            self._last_tool_completed = {
                "tool_name": entry.tool_name,
                "outcome": outcome,
                "finished_at_wallclock": finished_at_wallclock,
                "duration_seconds": duration_seconds,
                "error_summary": error_summary,
            }
            # F19 D-3 / D-4: append to in-memory deque BEFORE transcript write so
            # the dashboard sees the event even if the JSONL sink is disabled or
            # raises. orphan_post path above intentionally skips this (D-3).
            self._completed_tool_events.append(
                ToolEvent(
                    teammate_id=self.id,
                    tool_name=entry.tool_name,
                    tool_use_id=tool_use_id,
                    started_at_wallclock=entry.started_at_wallclock,
                    finished_at_wallclock=finished_at_wallclock,
                    duration_seconds=duration_seconds,
                    outcome=outcome,
                    args_summary=entry.args_summary,
                    error_summary=error_summary,
                    redaction_version=REDACTION_VERSION,
                )
            )
            try:
                broker = self._broker
                if broker is not None:
                    broker._sink.write_tool_event(
                        "tool_end",
                        {
                            "teammate_id": self.id,
                            "tool_name": entry.tool_name,
                            "tool_use_id": tool_use_id,
                            "outcome": outcome,
                            "finished_at_wallclock": finished_at_wallclock,
                            "duration_seconds": duration_seconds,
                            "error_summary": error_summary,
                            "redaction_version": REDACTION_VERSION,
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "post_tool_use: write_tool_event failed for teammate=%s tool_use_id=%s: %s",
                    self.id,
                    tool_use_id,
                    exc,
                )
            return {}
        except Exception as exc:
            logger.warning(
                "post_tool_use: internal exception for teammate=%s: %s",
                self.id,
                exc,
            )
            return {}

    async def _on_post_tool_use(self, inp: dict, tool_use_id: str, ctx: dict) -> dict:
        """Hook callback for PostToolUse event (SC-2)."""
        return await self._on_post_common(
            inp, tool_use_id, outcome="ok", error_text=None
        )

    async def _on_post_tool_use_failure(
        self, inp: dict, tool_use_id: str, ctx: dict
    ) -> dict:
        """Hook callback for PostToolUseFailure event (SC-3)."""
        outcome = "interrupted" if inp.get("is_interrupt") else "failed"
        error_text = inp.get("error", "")
        return await self._on_post_common(inp, tool_use_id, outcome=outcome, error_text=error_text)

    def _record_task_notif(self, tool_use_id: str, tnm: TaskNotificationMessage) -> None:
        """Store a TaskNotificationMessage keyed by tool_use_id (F7)."""
        self._task_notifs_by_tool_use_id[tool_use_id] = tnm

    def _end_turn(self, *, close_tools: bool = True) -> None:
        """Extend base _end_turn with F7 subagent-result JSONL emit."""
        super()._end_turn(close_tools=close_tools)
        # Emit subagent_result for each entry that PostToolUse closed into scratch.
        entries = list(self._closed_subagent_scratch.values())
        try:
            for closed in entries:
                tnm = self._task_notifs_by_tool_use_id.get(closed.tool_use_id)
                tnm_missing = tnm is None
                if tnm_missing:
                    logger.warning(
                        "_end_turn: no TNM for subagent tool_use_id=%s teammate=%s, defaulting outcome from hook",
                        closed.tool_use_id,
                        self.id,
                    )
                # Map outcome: TNM status wins when present; fall back to hook_outcome.
                if tnm is not None:
                    outcome = "ok" if tnm.status == "completed" else "failed"
                else:
                    outcome = "ok" if closed.hook_outcome == "ok" else "failed"
                duration_seconds = closed.finished_at_wallclock - closed.spawned_at_wallclock
                result_record = {
                    "teammate_id": self.id,
                    "agent_id": closed.agent_id,
                    "tool_use_id": closed.tool_use_id,
                    "outcome": outcome,
                    "duration_seconds": duration_seconds,
                    "summary": tnm.summary if tnm is not None else None,
                    "tnm_missing": tnm_missing,
                    "finished_at_wallclock": closed.finished_at_wallclock,
                }
                try:
                    broker = self._broker
                    if broker is not None:
                        broker._sink.write_tool_event("subagent_result", result_record)
                except Exception as exc:
                    logger.warning(
                        "_end_turn: write_tool_event subagent_result failed for %s/%s: %s",
                        self.id,
                        closed.tool_use_id,
                        exc,
                    )
                # Update last_subagent_completed regardless of write success.
                self._last_subagent_completed = {
                    "agent_id": closed.agent_id,
                    "tool_use_id": closed.tool_use_id,
                    "outcome": outcome,
                    "duration_seconds": duration_seconds,
                    "summary": tnm.summary if tnm is not None else None,
                    "finished_at_wallclock": closed.finished_at_wallclock,
                }
        finally:
            self._closed_subagent_scratch.clear()
            self._task_notifs_by_tool_use_id.clear()

    def _close_open_subagents(self, reason: Literal["death", "kill"]) -> None:
        """Emit subagent_abandoned_batch and clear in-flight subagent state on death/kill.

        Drains BOTH _subagent_uses (Pre fired, Post not yet) AND _closed_subagent_scratch
        (Post fired, _end_turn not yet) to catch all windows. If both are empty, no-op.
        """
        in_flight = list(self._subagent_uses.values())
        scratch = list(self._closed_subagent_scratch.values())
        all_entries = in_flight + scratch
        if not all_entries:
            return
        batch = [
            {"agent_id": e.agent_id, "tool_use_id": e.tool_use_id, "spawned_at_wallclock": e.spawned_at_wallclock}
            for e in all_entries
        ]
        try:
            broker = self._broker
            if broker is not None:
                broker._sink.write_tool_event(
                    "subagent_abandoned_batch",
                    {
                        "teammate_id": self.id,
                        "reason": reason,
                        "subagents": batch,
                        "count": len(batch),
                    },
                )
        except Exception as exc:
            logger.warning(
                "_close_open_subagents: write_tool_event failed for %s: %s",
                self.id,
                exc,
            )
        finally:
            self._subagent_uses.clear()
            self._closed_subagent_scratch.clear()

    def status_snapshot(self) -> dict[str, Any]:
        """Extend base status_snapshot with F7 subagent-activity and F14 token/cost fields."""
        snap = super().status_snapshot()
        # F14: overwrite the base zero values with live accumulated totals (D-5).
        # D-2: overwrite semantics — ResultMessage values are session-cumulative.
        # D-4: atomic co-assignment — _run() is single-threaded; no await between
        #      the three assignments in _handle_one_turn, so readers cannot observe
        #      a torn state (tokens from turn N paired with cost from turn N-1).
        snap["total_input_tokens"] = self._total_input_tokens
        snap["total_output_tokens"] = self._total_output_tokens
        snap["total_cost_usd"] = self._total_cost_usd
        snap["last_turn_input_tokens"] = self._last_turn_input_tokens
        snap["last_turn_output_tokens"] = self._last_turn_output_tokens
        # Build current_subagents from BOTH in-flight and limbo-state scratch entries (D10)
        subagent_entries = [
            {
                "agent_id": e.agent_id,
                "tool_use_id": e.tool_use_id,
                "spawned_at_wallclock": e.spawned_at_wallclock,
            }
            for e in itertools.chain(
                self._subagent_uses.values(),
                self._closed_subagent_scratch.values(),
            )
        ]
        subagent_entries.sort(key=lambda x: x["spawned_at_wallclock"])
        snap["current_subagents"] = subagent_entries
        snap["last_subagent_completed"] = self._last_subagent_completed
        snap["in_flight_subagents_at_death"] = None
        return snap

    async def _liveness_poll_loop(self, client: Any) -> None:
        """Poll the SDK subprocess for unexpected death (D5/D8).

        - Sets _poll_started to gate the worker's inbox entry (D2).
        - Reads _transport._process.returncode broadly; probe errors degrade
          open (D5): log WARNING and continue to next tick.
        - On returncode != None OR _death_suspected: call _handle_teammate_death
          and exit the loop.
        """
        self._poll_started.set()  # D2: signal worker may enter inbox loop
        while True:
            try:
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                return
            # D5: broad probe — any exception → degrade open (log, continue).
            try:
                transport = getattr(client, "_transport", None)
                process = getattr(transport, "_process", None)
                returncode = getattr(process, "returncode", None)
            except Exception as exc:
                logger.warning(
                    "liveness probe failed for teammate=%s: %s", self.id, exc
                )
                continue
            if returncode is not None or self._death_suspected:
                try:
                    assert self._broker is not None
                    await self._broker._handle_teammate_death(
                        self.id, exit_code=returncode
                    )
                except Exception as exc:
                    logger.warning(
                        "death handler failed for teammate=%s: %s", self.id, exc
                    )
                return  # poll task exits after triggering death handler

    async def _run(self) -> None:
        # D6: Log env override for CLAUDE_CREW_TOOL_ARGS_FULL if set.
        if os.environ.get("CLAUDE_CREW_TOOL_ARGS_FULL") == "1":
            logger.info(
                "CLAUDE_CREW_TOOL_ARGS_FULL=1: full tool args will be written to transcripts (teammate %s)",
                self.id,
            )

        opts_kwargs: dict = {
            "model": self._model,
            "system_prompt": self._system_prompt,
            "setting_sources": self._setting_sources,
            "agents": self._agents,
            # Suppress the UI server and instance registry inside SDK teammates.
            # They inherit global MCP config (including claude-crew), so without
            # this they'd each register themselves as a crew instance in the dashboard.
            #
            # CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 suppresses Claude Code's project
            # auto-memory injection (~/.claude/projects/<sanitized-cwd>/memory/MEMORY.md)
            # for the spawned teammate. Auto-memory is a lead-session continuity
            # mechanism — it's the operator's project memory, not the teammate's.
            # Leaking it into every teammate's context wastes ~550 tokens per
            # LLM invocation and pollutes role-scoped agents with lead-only
            # entries. The operator's interactive `claude` session is unaffected
            # (this env scoping applies only to subprocesses spawned via the SDK).
            # Spike: doc/research/auto-memory-disable-sdk-behavior.md.
            "env": {
                "CLAUDE_CREW_UI_PORT": "0",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            },
        }
        if self._effort is not None:
            opts_kwargs["effort"] = self._effort

        # D8: Register hook callbacks with timeout=1.0 (SC-8.3, D1).
        opts_kwargs["hooks"] = {
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=[self._on_pre_tool_use], timeout=1.0)
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=None, hooks=[self._on_post_tool_use], timeout=1.0
                )
            ],
            "PostToolUseFailure": [
                HookMatcher(
                    matcher=None,
                    hooks=[self._on_post_tool_use_failure],
                    timeout=1.0,
                )
            ],
        }

        # Extract role-level fields from the agents pack.
        role_def = self._agents.get(self.role)

        # allowed_tools: pre-approve specific tool IDs (e.g. MCP tools granted via extra_tools).
        if self._allowed_tools:
            opts_kwargs["allowed_tools"] = self._allowed_tools

        # permissionMode: spawn-time arg wins; falls back to role-pack; None → SDK default.
        effective_pm = self._permission_mode
        if effective_pm is None and role_def is not None:
            effective_pm = getattr(role_def, "permissionMode", None)
        if effective_pm is not None:
            opts_kwargs["permission_mode"] = effective_pm

        # skills and disallowedTools: role-pack only (spawn-time override deferred).
        if role_def is not None:
            role_skills = getattr(role_def, "skills", None)
            role_disallowed = getattr(role_def, "disallowedTools", None)
            if role_skills is not None:
                opts_kwargs["skills"] = role_skills
            if role_disallowed is not None:
                opts_kwargs["disallowed_tools"] = role_disallowed

            # Feature #17 D-4: mcpServers translates list[str|dict] → dict
            # via name resolution against ~/.claude.json (string entries) and
            # name-stripped inline pass-through (dict entries).
            role_mcp = getattr(role_def, "mcpServers", None)
            if role_mcp:
                opts_kwargs["mcp_servers"] = _resolve_mcp_servers(
                    role_mcp, self.role, self.id, home_dir=None,
                )


        # cwd: spawn-time only.
        if self._cwd is not None:
            opts_kwargs["cwd"] = self._cwd

        options = ClaudeAgentOptions(**opts_kwargs)
        try:
            async with ClaudeSDKClient(options=options) as client:
                # Spawn poll task inside the client context so it has a valid
                # client reference for the transport probe.
                self._poll_task = asyncio.create_task(
                    self._liveness_poll_loop(client), name=f"poll-{self.id}"
                )
                # D2 start-ordering invariant: do not enter inbox loop until
                # the poll task is live and ready to observe _death_suspected.
                await self._poll_started.wait()
                while True:
                    assert self._inbox is not None
                    msg = await self._inbox.get()
                    if msg is _SHUTDOWN_SENTINEL:
                        return
                    assert isinstance(msg, Envelope)
                    await self._handle_one_turn(client, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # construction or context-mgr failure
            if self._poll_task is not None and not self._poll_task.done():
                self._poll_task.cancel()
            await self._send_error_envelope(
                to=LEAD_ID,
                code=_classify_error(exc),
                message=f"SdkTeammate {self.id} crashed: {exc}",
            )

    async def _handle_one_turn(self, client: Any, env: Envelope) -> None:
        self._begin_turn()  # D1: set current_turn_started_at + stamp activity
        try:
            prompt = _payload_to_prompt(env.payload)
            if not prompt:
                await self._send_error_envelope(
                    to=env.sender,
                    code="invalid_response",
                    message="empty prompt — nothing to send to model",
                )
                return
            try:
                # SC-16: use crew-teammate session format instead of "default" (D5).
                assert self._broker is not None
                session_id = f"{self._broker.crew_id}-{self.id}"
                await client.query(prompt, session_id=session_id)
                result = await asyncio.wait_for(
                    _collect_response_text(client, self._stamp_activity, self._record_task_notif),
                    timeout=self._backstop_seconds,
                )
            except asyncio.TimeoutError:
                # D4: backstop sequence — interrupt → bounded grace → drain → error.
                interrupt_succeeded = False
                try:
                    await asyncio.wait_for(
                        client.interrupt(), timeout=INTERRUPT_GRACE_SECONDS
                    )
                    interrupt_succeeded = True
                except asyncio.TimeoutError:
                    logger.warning(
                        "interrupt hung past %ss for teammate=%s",
                        INTERRUPT_GRACE_SECONDS, self.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "interrupt raised for teammate=%s: %s", self.id, exc
                    )
                if not interrupt_succeeded:
                    # Co-architect escalation: hung/raising interrupt is a
                    # wedge signal — set death_suspected; poll task tombstones.
                    self._death_suspected = True
                else:
                    try:
                        await asyncio.wait_for(
                            _collect_response_text(client, self._stamp_activity),
                            timeout=POST_INTERRUPT_DRAIN_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        pass
                await self._send_error_envelope(
                    to=env.sender,
                    code="backstop_timeout",
                    message=(
                        f"backstop fired at {self._backstop_seconds:.0f}s; "
                        f"interrupt {'sent' if interrupt_succeeded else 'failed (death-suspected)'}"
                    ),
                )
                return
            except RateLimitedError as exc:
                await self._send_error_envelope(
                    to=env.sender, code="rate_limited", message=str(exc),
                )
                return
            except Exception as exc:
                # D2: SDK-death exceptions hand the in-flight envelope to the
                # death handler via _death_in_flight_envelope. Match by class
                # name to avoid importing SDK internals directly.
                exc_name = type(exc).__name__
                if (
                    "ProcessError" in exc_name
                    or "CLIConnectionError" in exc_name
                    or "BrokenPipe" in exc_name
                ):
                    self._death_in_flight_envelope = env
                    self._death_suspected = True
                    return  # poll task tombstones; no envelope sent here
                logger.warning(
                    "subagent stream-level failure: teammate=%s role=%s exc=%s",
                    self.id, self.role, exc,
                )
                await self._send_error_envelope(
                    to=env.sender, code=_classify_error(exc), message=str(exc),
                )
                return
            # F14: apply token/cost logic from this turn's ResultMessage.
            # D-4: atomic co-assignment under single-threaded _run() — no awaits
            #      between these three statements; readers cannot see a torn state.
            # D-2: per-turn tokens ACCUMULATE (+=) across turns; cumulative cost OVERWRITES
            #      (=) because it's already a running total from the SDK.
            # D-8: only assign when not None (malformed/missing → unchanged).
            if result.turn_input_tokens is not None:
                self._total_input_tokens += result.turn_input_tokens  # accumulate
                self._last_turn_input_tokens = result.turn_input_tokens  # overwrite (per-turn delta)
            if result.turn_output_tokens is not None:
                self._total_output_tokens += result.turn_output_tokens  # accumulate
                self._last_turn_output_tokens = result.turn_output_tokens  # overwrite (per-turn delta)
            if result.cumulative_cost_usd is not None:
                self._total_cost_usd = result.cumulative_cost_usd  # overwrite (cumulative)
            # Success path: text/no-text/SC-8(a) subagent failure synthesis.
            text = result.text
            if not text:
                # SC-8(a): empty parent text. If a subagent failed within the
                # turn, synthesize the error from its summary so the lead gets
                # a useful message. Otherwise fall through to the existing
                # generic invalid_response.
                if result.failed_task_notifs:
                    notif = result.failed_task_notifs[-1]
                    summary = notif.summary or "subagent run did not complete"
                    await self._send_error_envelope(
                        to=env.sender,
                        code="invalid_response",
                        message=f"subagent failed: {summary}",
                    )
                    return
                await self._send_error_envelope(
                    to=env.sender,
                    code="invalid_response",
                    message="model returned no text content",
                )
                return
            assert self._broker is not None
            await self._broker.send(Envelope(
                id=new_message_id(),
                seq=0,
                sender=self.id,
                recipient=env.sender,
                timestamp=time.time(),
                payload={"text": text, "from": self.role},
            ))
        finally:
            self._end_turn()  # D1: clear current_turn_started_at

    async def _send_error_envelope(
        self, *, to: str, code: str, message: str,
    ) -> None:
        assert self._broker is not None
        try:
            await self._broker.send(Envelope(
                id=new_message_id(),
                seq=0,
                sender=self.id,
                recipient=to,
                timestamp=time.time(),
                payload={"error": code, "message": message, "from": self.role},
            ))
        except Exception:
            # If the recipient is gone (killed concurrently), drop. The
            # broker is the source of truth on liveness.
            pass

    async def shutdown(self) -> None:
        # Cancel the liveness poll task first — it may be sleeping or mid-probe.
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        self._poll_task = None

        # Signal worker to stop, then wait (or hard-cancel on timeout).
        if self._inbox is not None:
            await self._inbox.put(_SHUTDOWN_SENTINEL)
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    self._task, timeout=SHUTDOWN_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._task = None
