"""The crew message broker.

Single source of truth for the bus: teammate registry, append-only message
log, per-recipient inbox queues, monotonic ``seq`` counter, and id-based
dedup. All state mutations happen on the asyncio event loop; no threads.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4

from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate
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


# A factory takes (id, name, role, model=None) and returns an unstarted
# Teammate. The model kwarg is optional — factories that don't care about
# model (e.g., stub) accept and ignore it.
TeammateFactory = Callable[..., Teammate]


class Broker:
    def __init__(self) -> None:
        self.crew_id: str = uuid4().hex[:8]
        self._teammates: dict[str, Teammate] = {}
        self._info: dict[str, TeammateInfo] = {}
        self._inboxes: dict[str, asyncio.Queue] = {LEAD_ID: asyncio.Queue()}
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
    ) -> str:
        teammate_id = f"t-{uuid4().hex[:12]}"
        resolved_name = name if name is not None else role
        inbox: asyncio.Queue = asyncio.Queue()
        teammate = factory(
            teammate_id, resolved_name, role,
            model=model, effort=effort,
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
            teammate._end_turn()

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
            except AttributeError:
                last_activity = None
                idle_at_death = None
                last_tool_completed_at_death = None
        else:
            last_activity = None
            idle_at_death = None
            last_tool_completed_at_death = None

        # 5. Write frozen tombstone BEFORE pop (D2 tombstone-before-pop ordering)
        self._info[teammate_id] = dataclasses.replace(
            info,
            alive=False,
            died_at_wallclock=time.time(),
            exit_code=exit_code,
            last_activity_at_wallclock_at_death=last_activity,
            idle_seconds_at_death=idle_at_death,
            last_tool_completed_at_death=last_tool_completed_at_death,
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

    def list_crew(self) -> list[TeammateInfo]:
        """Return all TeammateInfo entries, including tombstoned (alive=False) teammates."""
        return list(self._info.values())

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
        }
