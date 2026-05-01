"""The crew message broker.

Single source of truth for the bus: teammate registry, append-only message
log, per-recipient inbox queues, monotonic ``seq`` counter, and id-based
dedup. All state mutations happen on the asyncio event loop; no threads.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4

from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate, ToolEvent
from claude_crew.transcript import TranscriptSink

LEAD_ID = "lead"

logger = logging.getLogger(__name__)


class UnknownTeammateError(KeyError):
    """Raised when a teammate id is not registered."""


class TeammateAlreadyDeadError(RuntimeError):
    """Raised when attempting to send to a tombstoned (killed/dead) teammate."""


@dataclass(frozen=True)
class TeammateInfo:
    id: str
    name: str
    role: str
    spawned_at: float
    alive: bool
    # Death-record fields (all None for alive teammates)
    died_at_wallclock: float | None = None
    exit_code: int | None = None
    last_activity_at_wallclock_at_death: float | None = None
    idle_seconds_at_death: float | None = None
    # F8: last cleanly-bracketed tool (Pre→Post pair) observed before death/kill.
    # Populated by _tombstone_teammate from status_snapshot() BEFORE _close_open_tools
    # runs, so abandoned tools (outcome="abandoned"/"killed") never update this field
    # (SC-14 / D9). None if no tool completed cleanly before death.
    last_tool_completed_at_death: dict[str, Any] | None = None
    # F7: subagent-activity snapshot at death.
    in_flight_subagents_at_death: int | None = None
    last_subagent_completed_at_death: dict[str, Any] | None = None
    # F14: token/cost snapshot at death (numeric zero when snap available but no turns ran;
    # None only if status_snapshot() raised — callers coerce None → 0 on the wire).
    total_input_tokens_at_death: int | None = None
    total_output_tokens_at_death: int | None = None
    total_cost_usd_at_death: float | None = None
    # F19 D-7: per-teammate completed-tool-events snapshot at tombstone time.
    # Captured AFTER _close_open_tools runs (step 8c) so abandoned/killed events
    # land in this tuple. None during the brief window between tombstone (step 5,
    # alive=False) and step 8c — snapshot contributes zero events from this
    # teammate during that window (E-3, intentional). Empty tuple is a possible
    # final value (teammate ran no tools before death).
    tool_events_at_death: "tuple[ToolEvent, ...] | None" = None


@dataclass(frozen=True)
class LiveTeammateInfo:
    """Pairs a TeammateInfo with the alive-teammate-only fields the UI needs.

    ``status`` is value-copied at snapshot build time (D-2: deepcopy) so callers
    cannot reach back into teammate internals via the snapshot.
    ``model`` is captured from ``teammate._model`` (D-3 — workaround until a future
    feature promotes ``Teammate.model`` to a public attribute).
    """

    info: TeammateInfo
    status: dict[str, Any]
    model: str | None


@dataclass(frozen=True)
class BrokerSnapshot:
    """Frozen, value-copied view of broker state for downstream consumers.

    Produced by ``Broker.snapshot()``. UIServer is the canonical consumer (#18).
    Synchronous to build (D-1) — no I/O, no awaits.
    """

    crew_id: str
    teammates: tuple[TeammateInfo, ...]
    live: tuple[LiveTeammateInfo, ...]
    log: tuple[Envelope, ...]
    tool_events: "tuple[ToolEvent, ...]" = ()  # F19 D-5: tightened from tuple[Any, ...]


# A factory takes (id, name, role, model=None) and returns an unstarted
# Teammate. The model kwarg is optional — factories that don't care about
# model (e.g., stub) accept and ignore it.
TeammateFactory = Callable[..., Teammate]


class Broker:
    def __init__(self) -> None:
        self.crew_id: str = uuid4().hex[:8]
        self._teammates: dict[str, Teammate] = {}
        self._info: dict[str, TeammateInfo] = {}
        self._inboxes: dict[str, asyncio.Queue] = {}
        self._lead_message_condition: asyncio.Condition = asyncio.Condition()
        self._log: list[Envelope] = []
        self._seen_ids: set[str] = set()
        self._next_seq: int = 1
        self._sink = TranscriptSink(crew_id=self.crew_id)
        self._sink.write_lifecycle("started", {})

    # ---------- spawn / kill ----------

    async def spawn_teammate(
        self,
        role: str,
        name: str | None,
        factory: TeammateFactory,
        model: str | None = None,
        effort: str | None = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
    ) -> str:
        teammate_id = f"t-{uuid4().hex[:12]}"
        resolved_name = name if name is not None else role
        inbox: asyncio.Queue = asyncio.Queue()
        teammate = factory(
            teammate_id, resolved_name, role,
            model=model, effort=effort, cwd=cwd, permission_mode=permission_mode,
        )
        await teammate.start(self, inbox)

        self._teammates[teammate_id] = teammate
        self._inboxes[teammate_id] = inbox
        self._info[teammate_id] = TeammateInfo(
            id=teammate_id,
            name=resolved_name,
            role=role,
            spawned_at=time.time(),
            alive=True,
        )
        self._sink.write_lifecycle("spawn", {
            "teammate_id": teammate_id,
            "name": resolved_name,
            "role": role,
            "model": model,
        })
        return teammate_id

    async def _tombstone_teammate(
        self,
        teammate_id: str,
        exit_code: int | None,
        lifecycle_event_name: str,
        **lifecycle_extra: Any,
    ) -> None:
        """Shared tombstone code path for kill and death detection.

        Idempotent — silently returns if already tombstoned or unknown.

        Execution order (D2):
        1. Idempotency check
        2. End turn on teammate
        3. Capture in-flight envelope (SdkTeammate only)
        4. Snapshot activity at death
        5. Write frozen tombstone to _info (BEFORE pop — ensures concurrent
           send sees alive=False → TeammateAlreadyDeadError, not UnknownTeammateError)
        6. Pop from _teammates active set
        7. Bounce in-flight envelope (if any)
        8. Drain inbox and bounce each pending envelope
        9. Emit lifecycle event
        10. Detached shutdown (fire-and-forget, must NOT await own task)
        """
        # 1. Idempotency check
        info = self._info.get(teammate_id)
        if info is None or not info.alive:
            return

        teammate = self._teammates.get(teammate_id)

        # 2. End turn (clears current_turn_started_at_wallclock)
        if teammate is not None:
            # close_tools=False: broker owns the tool-closing call at step 8b
            # with the correct death/kill reason. Calling _end_turn() with the
            # default (close_tools=True) here would abandon the tools as
            # "turn_end" before step 8b can emit them as "death"/"killed".
            teammate._end_turn(close_tools=False)

        # 3. Capture in-flight envelope (SdkTeammate sets this; others don't)
        in_flight = (
            getattr(teammate, "_death_in_flight_envelope", None)
            if teammate is not None
            else None
        )

        # 4. Snapshot activity at death.
        # Called BEFORE _close_open_tools (between steps 8-9) so that
        # last_tool_completed reflects the last *clean* Pre→Post pair — not
        # the abandoned/killed tools that _close_open_tools is about to emit.
        # (SC-14 / D9: abandoned tools go to the transcript, not status payload.)
        if teammate is not None:
            try:
                snap = teammate.status_snapshot()
                last_activity = snap.get("last_activity_at_wallclock")
                idle_at_death = snap.get("idle_seconds", 0.0)
                last_tool_completed_at_death: dict[str, Any] | None = snap.get(
                    "last_tool_completed"
                )
                last_subagent_completed_at_death: dict[str, Any] | None = snap.get(
                    "last_subagent_completed"
                )
                in_flight_subagents_at_death: int = (
                    len(getattr(teammate, "_subagent_uses", {})) +
                    len(getattr(teammate, "_closed_subagent_scratch", {}))
                )
                # F14: capture last cumulative token/cost values (D-7: numeric zero
                # when no turns ran; overwrite semantics mean final value == cumulative).
                total_input_tokens_at_death: int = snap.get("total_input_tokens", 0)
                total_output_tokens_at_death: int = snap.get("total_output_tokens", 0)
                total_cost_usd_at_death: float = snap.get("total_cost_usd", 0.0)
            except AttributeError:
                last_activity = None
                idle_at_death = None
                last_tool_completed_at_death = None
                last_subagent_completed_at_death = None
                in_flight_subagents_at_death = 0
                total_input_tokens_at_death = None
                total_output_tokens_at_death = None
                total_cost_usd_at_death = None
        else:
            last_activity = None
            idle_at_death = None
            last_tool_completed_at_death = None
            last_subagent_completed_at_death = None
            in_flight_subagents_at_death = 0
            total_input_tokens_at_death = None
            total_output_tokens_at_death = None
            total_cost_usd_at_death = None

        # 5. Write frozen tombstone BEFORE pop (D2 tombstone-before-pop ordering)
        self._info[teammate_id] = dataclasses.replace(
            info,
            alive=False,
            died_at_wallclock=time.time(),
            exit_code=exit_code,
            last_activity_at_wallclock_at_death=last_activity,
            idle_seconds_at_death=idle_at_death,
            last_tool_completed_at_death=last_tool_completed_at_death,
            in_flight_subagents_at_death=in_flight_subagents_at_death,
            last_subagent_completed_at_death=last_subagent_completed_at_death,
            total_input_tokens_at_death=total_input_tokens_at_death,
            total_output_tokens_at_death=total_output_tokens_at_death,
            total_cost_usd_at_death=total_cost_usd_at_death,
        )

        # 6. Pop from active set
        self._teammates.pop(teammate_id, None)

        # 7. Bounce in-flight envelope (if any)
        if in_flight is not None and isinstance(in_flight, Envelope):
            await self._bounce_dead(in_flight, teammate_id, exit_code)

        # 8. Drain inbox; bounce each pending envelope
        inbox = self._inboxes.pop(teammate_id, None)
        while inbox is not None:
            try:
                pending = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(pending, Envelope):
                await self._bounce_dead(pending, teammate_id, exit_code)

        # 8b. Close open tools before lifecycle event (SC-14 / D9).
        # Emits one tool_end transcript record (outcome="abandoned" for death,
        # "killed" for kill) per still-open tool_use_id.  Must run BEFORE the
        # lifecycle line so transcript replay sees tool_end < lifecycle:died/kill.
        if teammate is not None:
            close_reason: Literal["death", "kill"] = (
                "death" if lifecycle_event_name == "died" else "kill"
            )
            teammate._close_open_tools(reason=close_reason)
            if hasattr(teammate, "_close_open_subagents"):
                teammate._close_open_subagents(reason=close_reason)

        # 8c. F19 D-7: capture per-teammate completed_tool_events into TeammateInfo.
        # Must run AFTER 8b so abandoned/killed events appended by _close_open_tools
        # are included in the captured tuple. dataclasses.replace because TeammateInfo
        # is frozen (sentinel F1 — direct mutation would raise FrozenInstanceError).
        # Defensive on missing attribute: a teammate that never initialized the deque
        # (older test fixtures, bare-bones mocks) contributes None instead of crashing.
        if teammate is not None:
            captured = getattr(teammate, "_completed_tool_events", None)
            if captured is not None:
                self._info[teammate_id] = dataclasses.replace(
                    self._info[teammate_id],
                    tool_events_at_death=tuple(captured),
                )

        # 9. Emit lifecycle event
        lifecycle_fields: dict[str, Any] = {"teammate_id": teammate_id}
        if lifecycle_event_name == "died":
            lifecycle_fields.update({
                "exit_code": exit_code,
                "idle_seconds_at_death": idle_at_death,
                "last_activity_at_wallclock": last_activity,
            })
        else:
            # "kill" and others preserve event-specific fields (e.g., reason)
            lifecycle_fields.update(lifecycle_extra)
        self._sink.write_lifecycle(lifecycle_event_name, lifecycle_fields)

        # 10. Detached shutdown — do NOT await (handler must not cancel its own task)
        if teammate is not None:
            loop = asyncio.get_running_loop()
            _t = teammate  # capture for closure

            async def _safe_shutdown() -> None:
                try:
                    await _t.shutdown()
                except Exception as exc:
                    logger.warning(
                        "shutdown error for teammate %s: %s", teammate_id, exc
                    )

            loop.create_task(_safe_shutdown())

    async def _bounce_dead(
        self,
        env: Envelope,
        dead_id: str,
        exit_code: int | None,
    ) -> None:
        """Send a teammate_dead error envelope back to env's sender."""
        info = self._info.get(dead_id)
        died_at = info.died_at_wallclock if info else None
        msg = f"teammate {dead_id!r} is dead"
        if died_at is not None:
            msg += f"; died_at={died_at:.3f}"
        if exit_code is not None:
            msg += f"; exit_code={exit_code}"
        bounce = Envelope(
            id=new_message_id(),
            seq=0,
            sender=dead_id,
            recipient=env.sender,
            timestamp=time.time(),
            payload={"error": "teammate_dead", "message": msg},
        )
        try:
            await self.send(bounce)
        except (UnknownTeammateError, TeammateAlreadyDeadError):
            # Sender is also dead or unknown — log at INFO and drop.
            logger.info(
                "dead-bounce to %r dropped: sender is dead or unknown", env.sender
            )

    async def _handle_teammate_death(
        self,
        teammate_id: str,
        exit_code: int | None,
    ) -> None:
        """Single-writer death handler for unexpected subprocess death. Idempotent."""
        await self._tombstone_teammate(teammate_id, exit_code, "died")

    async def kill_teammate(
        self, teammate_id: str, reason: str = "explicit",
    ) -> None:
        if teammate_id not in self._teammates:
            # Already tombstoned → distinct error from never-existed
            if teammate_id in self._info:
                raise TeammateAlreadyDeadError(teammate_id)
            raise UnknownTeammateError(teammate_id)
        await self._tombstone_teammate(teammate_id, None, "kill", reason=reason)

    async def shutdown_all(self) -> None:
        teammate_ids = list(self._teammates.keys())
        for tid in teammate_ids:
            try:
                await self.kill_teammate(tid, reason="shutdown")
            except Exception:
                pass
        self._sink.write_lifecycle("shutdown", {
            "teammate_count": len(teammate_ids),
        })
        # Wake any pending long-polls before closing the sink so they return
        # cleanly rather than hanging until their wait_seconds cap expires.
        async with self._lead_message_condition:
            self._lead_message_condition.notify_all()
        self._sink.close()

    # ---------- send / broadcast ----------

    async def send(self, env: Envelope) -> Envelope | None:
        """Enqueue ``env`` for delivery.

        Returns the envelope as enqueued (with broker-assigned seq), or
        ``None`` if it was dropped as a duplicate.
        """
        if env.id in self._seen_ids:
            return None

        # D6: check tombstone BEFORE _teammates (tombstoned = in _info but NOT in _teammates)
        info = self._info.get(env.recipient)
        if info is not None and not info.alive:
            raise TeammateAlreadyDeadError(env.recipient)

        if env.recipient != LEAD_ID and env.recipient not in self._teammates:
            raise UnknownTeammateError(env.recipient)

        seq = self._next_seq
        self._next_seq += 1
        stamped = Envelope(
            id=env.id,
            seq=seq,
            sender=env.sender,
            recipient=env.recipient,
            timestamp=env.timestamp if env.timestamp else time.time(),
            payload=env.payload,
        )
        self._seen_ids.add(stamped.id)
        self._log.append(stamped)
        self._sink.write_envelope(stamped.to_dict())
        if stamped.recipient == LEAD_ID:
            # LEAD has no inbox queue; readers consume via get_messages(_log).
            # Notify the long-poll Condition so any waiting get_messages call wakes.
            # CRITICAL: notify comes AFTER _log.append so every waiter that wakes
            # is guaranteed to find the new envelope when it re-reads _log.
            async with self._lead_message_condition:
                self._lead_message_condition.notify_all()
        else:
            await self._inboxes[stamped.recipient].put(stamped)
        return stamped

    async def broadcast(
        self,
        sender: str,
        payload: Any,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Fan-out one envelope per alive teammate (sender excluded).

        Returns dict with ``message_ids`` (delivered ids) and
        ``skipped_dead`` (list of tombstoned teammate ids skipped).
        """
        # D12: filter to alive recipients only
        alive_recipients = [tid for tid in self._teammates if tid != sender]
        dead_recipients = [
            tid for tid, info in self._info.items()
            if not info.alive and tid != sender
        ]

        out_ids: list[str] = []
        for rid in alive_recipients:
            mid = id if id is not None else new_message_id()
            if id is not None:
                mid = f"{id}:{rid}"
            env = Envelope(
                id=mid,
                seq=0,
                sender=sender,
                recipient=rid,
                timestamp=time.time(),
                payload=payload,
            )
            stamped = await self.send(env)
            if stamped is not None:
                out_ids.append(stamped.id)
        return {"message_ids": out_ids, "skipped_dead": dead_recipients}

    # ---------- reads ----------

    def get_messages(
        self,
        recipient: str,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[Envelope]:
        result = [m for m in self._log if m.recipient == recipient and m.seq > since_seq]
        if limit is not None:
            result = result[:limit]
        return result

    async def wait_for_lead_message(self, timeout: float) -> None:
        """Block until any send to LEAD fires the Condition, or timeout elapses.

        No-op when timeout <= 0. Returns silently on timeout (no exception).
        The caller is responsible for re-reading get_messages() after this
        returns — this helper does not inspect or modify _log.

        Uses asyncio.timeout() (Python 3.11+, project requires 3.12) rather than
        asyncio.wait_for() to avoid the extra Task-wrapping semantics of the latter
        for asyncio.Condition.wait() coroutines.
        """
        if timeout <= 0:
            return
        async with self._lead_message_condition:
            try:
                async with asyncio.timeout(timeout):
                    await self._lead_message_condition.wait()
            except TimeoutError:
                pass

    def list_crew(self) -> list[TeammateInfo]:
        """Return all TeammateInfo entries, including tombstoned (alive=False) teammates."""
        return list(self._info.values())

    def snapshot(self, log_limit: int | None = None) -> BrokerSnapshot:
        """Return a frozen, value-copied view of broker state.

        Synchronous and in-memory only (D-1). Status dicts deep-copied (D-2)
        so callers can't reach back into live teammate state.

        Args:
            log_limit: If int, return the last N envelopes; if None (default),
                return the full log.
        """
        teammates_tuple = tuple(self._info.values())

        live_entries: list[LiveTeammateInfo] = []
        for info in teammates_tuple:
            if not info.alive:
                continue
            teammate = self._teammates.get(info.id)
            status: dict[str, Any] = {}
            if teammate is not None:
                try:
                    raw = teammate.status_snapshot()
                except Exception:
                    raw = {}
                status = copy.deepcopy(raw)
            model = getattr(teammate, "_model", None) if teammate is not None else None
            live_entries.append(LiveTeammateInfo(info=info, status=status, model=model))

        if log_limit is None:
            log_tuple = tuple(self._log)
        else:
            log_tuple = tuple(self._log[-log_limit:])

        # F19 D-6: flatten per-teammate completed-tool-events. Live teammates
        # contribute their current deque; tombstoned teammates contribute their
        # frozen tool_events_at_death tuple (D-7). Stable sort by
        # finished_at_wallclock asc gives deterministic chronological order.
        # Mid-tombstone window where info.alive=False but tool_events_at_death=None
        # contributes zero events from that teammate (E-3, intentional).
        all_tool_events: list[ToolEvent] = []
        for info in teammates_tuple:
            if info.alive:
                tm = self._teammates.get(info.id)
                if tm is not None:
                    captured = getattr(tm, "_completed_tool_events", None)
                    if captured is not None:
                        all_tool_events.extend(captured)
            elif info.tool_events_at_death is not None:
                all_tool_events.extend(info.tool_events_at_death)
        all_tool_events.sort(key=lambda e: e.finished_at_wallclock)

        return BrokerSnapshot(
            crew_id=self.crew_id,
            teammates=teammates_tuple,
            live=tuple(live_entries),
            log=log_tuple,
            tool_events=tuple(all_tool_events),
        )

    def get_teammate_status(self, teammate_id: str) -> dict[str, Any]:
        """Read-only status for alive or tombstoned teammates.

        Returns a uniform payload shape regardless of alive/dead status.
        Unknown id returns an error dict matching the existing unknown_teammate shape.

        F8 additions — always present on alive and tombstoned payloads:
            current_tools: list of in-flight tool dicts, each with
                {tool_name, tool_use_id, started_at_wallclock, args_summary}.
                Empty list for tombstoned teammates.
            current_tool: convenience accessor — last-started tool name, or
                null if no tool is in flight (SC-9 last-started semantics).
            current_tool_count: len(current_tools).
            last_tool_completed: dict from the most recent fully-bracketed
                Pre→Post pair, or null if none completed cleanly.  Tombstoned
                teammates preserve the last clean value captured before death.
            redaction_version: active redaction schema version, or null for
                tombstoned teammates.
        """
        info = self._info.get(teammate_id)
        if info is None:
            return {
                "error": "unknown_teammate",
                "message": f"no teammate with id {teammate_id!r}",
            }

        if not info.alive:
            # Short-circuit: all data comes from the frozen tombstone (D3).
            # Do NOT call status_snapshot() — the teammate was popped from _teammates.
            # Force current_turn_started_at_wallclock=None (D3 defense-in-depth on top of
            # _end_turn() in the death handler).
            return {
                "teammate_id": teammate_id,
                "name": info.name,
                "role": info.role,
                "alive": False,
                "spawned_at": info.spawned_at,
                "last_activity_at_wallclock": info.last_activity_at_wallclock_at_death,
                "current_turn_started_at_wallclock": None,
                "idle_seconds": info.idle_seconds_at_death,
                "died_at_wallclock": info.died_at_wallclock,
                "exit_code": info.exit_code,
                "last_activity_at_wallclock_at_death": info.last_activity_at_wallclock_at_death,
                # F8 additions (D11 / SC-7): tools are gone after death; last
                # cleanly-finished tool is preserved in the tombstone (SC-14).
                "current_tools": [],
                "current_tool": None,
                "current_tool_count": 0,
                "last_tool_completed": info.last_tool_completed_at_death,
                "redaction_version": None,
                # F7 additions: subagent-activity fields preserved from tombstone.
                "current_subagents": [],
                "last_subagent_completed": info.last_subagent_completed_at_death,
                "in_flight_subagents_at_death": info.in_flight_subagents_at_death,
                # F14: token/cost fields preserved from tombstone (always numeric on wire).
                "total_input_tokens": info.total_input_tokens_at_death if info.total_input_tokens_at_death is not None else 0,
                "total_output_tokens": info.total_output_tokens_at_death if info.total_output_tokens_at_death is not None else 0,
                "total_cost_usd": info.total_cost_usd_at_death if info.total_cost_usd_at_death is not None else 0.0,
            }

        # Alive: combine TeammateInfo lifecycle fields with live activity snapshot
        teammate = self._teammates.get(teammate_id)
        snap = teammate.status_snapshot() if teammate is not None else {}

        return {
            "teammate_id": teammate_id,
            "name": info.name,
            "role": info.role,
            "alive": True,
            "spawned_at": info.spawned_at,
            "last_activity_at_wallclock": snap.get("last_activity_at_wallclock"),
            "current_turn_started_at_wallclock": snap.get("current_turn_started_at_wallclock"),
            "idle_seconds": snap.get("idle_seconds"),
            "died_at_wallclock": None,
            "exit_code": None,
            "last_activity_at_wallclock_at_death": None,
            # F8 additions (D11 / SC-7): surface tool-tracking fields from
            # status_snapshot().  All keys are always present; values are null
            # when no tool has fired yet.
            "current_tools": snap.get("current_tools", []),
            "current_tool": snap.get("current_tool"),
            "current_tool_count": snap.get("current_tool_count", 0),
            "last_tool_completed": snap.get("last_tool_completed"),
            "redaction_version": snap.get("redaction_version"),
            # F7 additions: subagent-activity fields from status_snapshot().
            "current_subagents": snap.get("current_subagents", []),
            "last_subagent_completed": snap.get("last_subagent_completed"),
            "in_flight_subagents_at_death": None,
            # F14: token/cost fields from live snapshot (always numeric per T2 contract).
            "total_input_tokens": snap.get("total_input_tokens", 0),
            "total_output_tokens": snap.get("total_output_tokens", 0),
            "total_cost_usd": snap.get("total_cost_usd", 0.0),
        }
