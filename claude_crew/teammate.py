"""Teammate ABC and built-in implementations.

The Teammate ABC is the seam where Feature #2 plugs in: ``SdkTeammate``
will implement this same interface around a ``ClaudeSDKClient`` so the
broker and MCP server stay constant.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from claude_crew.envelope import Envelope, new_message_id

if TYPE_CHECKING:
    from claude_crew.broker import Broker

log = logging.getLogger(__name__)

_MAX_CONCURRENT_TOOLS = 64


@dataclass(frozen=True)
class _ToolUseEntry:
    """Immutable record of one in-flight tool call.

    Lives in ``Teammate._tool_uses`` keyed by ``tool_use_id``.
    Populated by ``PreToolUse`` hook (T3); read by ``_close_open_tools``
    and ``status_snapshot``.
    """

    tool_name: str
    """e.g. "Bash", "Task", "WebFetch" — the SDK tool name."""

    tool_use_id: str
    """SDK's ``toolu_xxx`` identifier — also the dict key (denormalized)."""

    started_at_wallclock: float
    """``time.time()`` at PreToolUse hook fire (~5ms after ToolUseBlock yield)."""

    args_summary: str | None
    """Redacted+capped summary; null unless tool is on the v1 allowlist (SC-15)."""


def _get_redaction_version() -> str:
    """Return the active redaction version string.

    Tries to import from ``claude_crew.redaction`` (added by T1).  Falls back
    to the hard-coded ``"v1"`` while T1 is landing in parallel.

    TODO: remove the fallback once T1 is merged and ``redaction.py`` is stable.
    """
    try:
        from claude_crew.redaction import REDACTION_VERSION  # type: ignore[import]
        return REDACTION_VERSION
    except ImportError:
        return "v1"


class Teammate(ABC):
    id: str
    name: str
    role: str
    _broker: "Broker | None"
    _last_activity_monotonic: float
    _last_activity_wallclock: float
    _current_turn_started_at_wallclock: float | None
    # F8: per-tool_use_id in-flight dict (D2). Populated by PreToolUse hook (T3);
    # cleared unconditionally by _end_turn / _close_open_tools (D9).
    _tool_uses: dict[str, _ToolUseEntry]
    # F8: bounded deque of recently-closed tool_use_ids for late-Post dedup (D8).
    _recently_closed_tool_use_ids: collections.deque[str]
    # F8: last fully-bracketed tool result (Pre→Post pair). Populated by T3
    # Post/PostFailure hooks; never updated by _close_open_tools (D9 / SC-14).
    _last_tool_completed: dict[str, Any] | None

    @abstractmethod
    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        """Begin consuming from ``inbox``. Must spawn a background task and return."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Stop consuming and release resources. Must be idempotent."""

    def _stamp_activity(self) -> None:
        """Update activity timestamps to now."""
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()

    def _begin_turn(self) -> None:
        """Mark the start of a turn: set current_turn_started_at and stamp activity."""
        self._current_turn_started_at_wallclock = time.time()
        self._stamp_activity()

    def _end_turn(self) -> None:
        """Mark the end of a turn: clear current_turn_started_at."""
        self._current_turn_started_at_wallclock = None

    def _close_open_tools(self, reason: Literal["turn_end", "death", "kill"]) -> None:
        """Close all in-flight tool uses and emit ``tool_end`` transcript records.

        Called by ``_end_turn``, broker ``_tombstone_teammate``, and
        ``kill_teammate``.  Unconditionally clears ``_tool_uses`` in a
        ``finally`` block (D9 cleanup discipline).

        Does NOT update ``_last_tool_completed`` — only fully-bracketed
        Pre→Post pairs roll into that field (SC-14 / D9).

        Args:
            reason: Why the tools are being closed. Drives ``outcome`` value:
                ``"turn_end"`` and ``"death"`` → ``"abandoned"``; ``"kill"``
                → ``"killed"``.
        """
        # Snapshot first (sentinel A2): protects against any dict mutation that
        # could occur if a stray Pre hook fires between iterations (post-death
        # window — see edge-cases in Phase 2).
        entries = list(self._tool_uses.items())
        try:
            outcome = "abandoned" if reason in ("turn_end", "death") else "killed"
            for tool_use_id, entry in entries:
                finished_at_wallclock = time.time()
                duration_seconds = finished_at_wallclock - entry.started_at_wallclock
                error_summary = f"tool was in flight when {reason} closed it"
                try:
                    broker = self._broker
                    if broker is not None:
                        broker._sink.write_tool_event("tool_end", {
                            "teammate_id": self.id,
                            "tool_name": entry.tool_name,
                            "tool_use_id": tool_use_id,
                            "finished_at_wallclock": finished_at_wallclock,
                            "duration_seconds": duration_seconds,
                            "outcome": outcome,
                            "error_summary": error_summary,
                            "redaction_version": _get_redaction_version(),
                        })
                except Exception as exc:
                    log.warning(
                        "close_open_tools: write_tool_event failed for %s/%s: %s",
                        self.id,
                        tool_use_id,
                        exc,
                    )
                # Always mark as recently-closed so late Post hooks don't emit
                # a duplicate tool_end (D8 fifth guard).
                self._recently_closed_tool_use_ids.append(tool_use_id)
        finally:
            # Unconditional clear — even if a write raised mid-iteration (D9).
            self._tool_uses.clear()

    def status_snapshot(self) -> dict[str, Any]:
        """Read-only snapshot of activity telemetry for get_teammate_status.

        Returns:
            dict with F6 fields (``last_activity_at_wallclock``,
            ``current_turn_started_at_wallclock``, ``idle_seconds``) plus
            F8 additions (``current_tools``, ``current_tool``,
            ``current_tool_count``, ``last_tool_completed``,
            ``redaction_version``).
        """
        # Build current_tools list sorted by started_at_wallclock (ascending),
        # so the last element is the most-recently-started tool (SC-9).
        current_tools: list[dict[str, Any]] = sorted(
            [
                {
                    "tool_name": e.tool_name,
                    "tool_use_id": e.tool_use_id,
                    "started_at_wallclock": e.started_at_wallclock,
                    "args_summary": e.args_summary,
                }
                for e in self._tool_uses.values()
            ],
            key=lambda d: d["started_at_wallclock"],
        )
        return {
            # F6 fields — preserved verbatim.
            "last_activity_at_wallclock": self._last_activity_wallclock,
            "current_turn_started_at_wallclock": self._current_turn_started_at_wallclock,
            "idle_seconds": time.monotonic() - self._last_activity_monotonic,
            # F8 additions (D11 — additive, null-safe).
            "current_tools": current_tools,
            # SC-9 last-started semantics: current_tools[-1] when non-empty.
            "current_tool": current_tools[-1]["tool_name"] if current_tools else None,
            "current_tool_count": len(current_tools),
            "last_tool_completed": self._last_tool_completed,
            "redaction_version": _get_redaction_version(),
        }


# Sentinel placed in an inbox queue to signal shutdown to the consumer task.
_SHUTDOWN_SENTINEL: object = object()


class StubTeammate(Teammate):
    """Echoes inbound messages back to the sender.

    For each received envelope, sends a response of the form
    ``{"echo": <original_payload>, "from": <role>}`` back to the original
    sender. This is enough to validate the bus protocol end-to-end before
    real Agent-SDK teammates exist.
    """

    def __init__(self, id: str, name: str, role: str, slow_echo_delay: float = 0.0) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None
        self._slow_echo_delay = slow_echo_delay
        # Initialize activity telemetry (base class fields)
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None
        # F8: tool-tracking state (base class fields — mirrors SdkTeammate pattern)
        self._tool_uses: dict[str, _ToolUseEntry] = {}
        self._recently_closed_tool_use_ids: collections.deque[str] = collections.deque(maxlen=64)
        self._last_tool_completed: dict[str, Any] | None = None

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._run(), name=f"stub-{self.id}")

    async def _run(self) -> None:
        assert self._broker is not None and self._inbox is not None
        while True:
            msg = await self._inbox.get()
            if msg is _SHUTDOWN_SENTINEL:
                return
            assert isinstance(msg, Envelope)
            # Begin turn: set current_turn_started_at and stamp activity
            self._begin_turn()
            # Simulate optional delay before responding
            if self._slow_echo_delay > 0.0:
                await asyncio.sleep(self._slow_echo_delay)
            response = Envelope(
                id=new_message_id(),
                seq=0,  # broker assigns
                sender=self.id,
                recipient=msg.sender,
                timestamp=time.time(),
                payload={"echo": msg.payload, "from": self.role},
            )
            try:
                await self._broker.send(response)
            except Exception:
                # If the recipient is gone (e.g., killed mid-flight),
                # drop silently. The broker is the source of truth.
                pass
            finally:
                # End turn: clear current_turn_started_at
                self._end_turn()

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SHUTDOWN_SENTINEL)
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
