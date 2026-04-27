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
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    TaskNotificationMessage,
    TextBlock,
)

logger = logging.getLogger(__name__)

from claude_crew.broker import LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.subagents import load_default_pack
from claude_crew.teammate import Teammate

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

_SHUTDOWN_SENTINEL: object = object()


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
    last_failed_task_notif: the most recent TaskNotificationMessage with
        status in {"failed","stopped"}, if any. Used by SC-8(a) to
        synthesize an envelope when the parent didn't narrate over the
        failure.
    """

    text: str
    last_failed_task_notif: TaskNotificationMessage | None


async def _collect_response_text(
    client: Any,
    stamp_activity: Callable[[], None] | None = None,
) -> TurnDrainResult:
    """Drain client.receive_response() and accumulate text + subagent failures.

    - D1: invokes stamp_activity at loop top, BEFORE any continue branch, so
      RateLimitEvent and TaskNotificationMessage events also stamp activity.
    - Ignores tool-use, thinking, and other non-text blocks (Assumption A2).
    - On RateLimitEvent (status=rejected), raises RateLimitedError.
    - Tracks the most recent TaskNotificationMessage with a failure-shaped
      status; logs a WARNING for *every* such notification observed.
    - Terminates when the SDK iterator terminates (typically at ResultMessage).
    - Returns TurnDrainResult(text="", last_failed_task_notif=None) if
      nothing of substance was observed.

    The caller must wrap this in asyncio.wait_for to bound non-termination.
    """
    text_parts: list[str] = []
    last_failed: TaskNotificationMessage | None = None
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
        if isinstance(msg, TaskNotificationMessage):
            if msg.status in ("failed", "stopped"):
                last_failed = msg
                logger.warning(
                    "subagent failure: status=%s task_id=%s summary=%r",
                    msg.status, msg.task_id, msg.summary,
                )
            continue
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return TurnDrainResult(text="".join(text_parts), last_failed_task_notif=last_failed)


def _default_system_prompt(role: str) -> str:
    return f"You are a {role}. Help the lead with {role}-level work."


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
    ) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._model = model
        self._effort = effort
        self._system_prompt = system_prompt or _default_system_prompt(role)
        self._setting_sources = (
            setting_sources if setting_sources is not None else ["user", "project"]
        )
        # `agents=None` → load the bundled default pack. `agents={}` → explicit
        # empty (this teammate cannot delegate). `agents={...}` → custom pack
        # (Feature #3b's seam ride-along).
        self._agents = load_default_pack() if agents is None else agents
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
        opts_kwargs: dict = {
            "model": self._model,
            "system_prompt": self._system_prompt,
            "setting_sources": self._setting_sources,
            "agents": self._agents,
        }
        if self._effort is not None:
            opts_kwargs["effort"] = self._effort
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
                await client.query(prompt, session_id="default")
                result = await asyncio.wait_for(
                    _collect_response_text(client, self._stamp_activity),
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
            # Success path: text/no-text/SC-8(a) subagent failure synthesis.
            text = result.text
            if not text:
                # SC-8(a): empty parent text. If a subagent failed within the
                # turn, synthesize the error from its summary so the lead gets
                # a useful message. Otherwise fall through to the existing
                # generic invalid_response.
                if result.last_failed_task_notif is not None:
                    notif = result.last_failed_task_notif
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
