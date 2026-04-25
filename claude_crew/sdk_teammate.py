"""SdkTeammate: a Teammate backed by claude-agent-sdk's ClaudeSDKClient.

Drives the SDK as documented:
  async with ClaudeSDKClient(options) as client:
      await client.query(prompt, session_id="default")
      async for msg in client.receive_response():
          ...

Each turn:
  1. Pull an envelope from the inbox.
  2. Translate payload → prompt string.
  3. client.query(prompt) and drain receive_response() with a bounded timeout.
  4. Send a result envelope (success or error) back to the original sender.

Errors and timeouts produce a structured error envelope and the loop continues.
The teammate dies (worker task exits) only on shutdown signal or catastrophic
failure outside the per-turn handler.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    TextBlock,
)

from claude_crew.broker import LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate

if TYPE_CHECKING:
    from claude_crew.broker import Broker

# Bounded wait per turn for the SDK's receive_response() to terminate.
# Generous; the SDK can take dozens of seconds on real model calls.
TURN_TIMEOUT_SECONDS: float = 120.0

# Bounded wait for graceful shutdown of the worker task.
SHUTDOWN_TIMEOUT_SECONDS: float = 5.0

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


async def _collect_response_text(client: Any) -> str:
    """Drain client.receive_response() and concatenate all TextBlock content.

    - Ignores tool-use, thinking, and other non-text blocks (Assumption A2).
    - On RateLimitEvent, raises RateLimitedError so the caller can surface
      the `rate_limited` error code distinctly.
    - Terminates when the SDK iterator terminates (typically at ResultMessage).
    - Returns "" if no AssistantMessage with TextBlocks was seen.

    The caller must wrap this in asyncio.wait_for to bound non-termination.
    """
    text_parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, RateLimitEvent):
            # status: 'allowed' (normal), 'allowed_warning' (near limit),
            # 'rejected' (over limit). Only the last is a real failure;
            # the rest are informational and the model still responded.
            info = getattr(msg, "rate_limit_info", None)
            status = getattr(info, "status", None)
            if status == "rejected":
                raise RateLimitedError(f"rate limit hit: {info}")
            continue
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return "".join(text_parts)


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
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._run(), name=f"sdk-{self.id}")

    async def _run(self) -> None:
        opts_kwargs: dict = {
            "model": self._model,
            "system_prompt": self._system_prompt,
            "setting_sources": self._setting_sources,
        }
        if self._effort is not None:
            opts_kwargs["effort"] = self._effort
        options = ClaudeAgentOptions(**opts_kwargs)
        try:
            async with ClaudeSDKClient(options=options) as client:
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
            await self._send_error_envelope(
                to=LEAD_ID,
                code=_classify_error(exc),
                message=f"SdkTeammate {self.id} crashed: {exc}",
            )

    async def _handle_one_turn(self, client: Any, env: Envelope) -> None:
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
            text = await asyncio.wait_for(
                _collect_response_text(client),
                timeout=TURN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._send_error_envelope(
                to=env.sender,
                code="invalid_response",
                message=(
                    f"no response within {TURN_TIMEOUT_SECONDS:.0f}s — "
                    "subprocess may be stuck"
                ),
            )
            return
        except RateLimitedError as exc:
            await self._send_error_envelope(
                to=env.sender, code="rate_limited", message=str(exc),
            )
            return
        except Exception as exc:
            await self._send_error_envelope(
                to=env.sender, code=_classify_error(exc), message=str(exc),
            )
            return
        if not text:
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
