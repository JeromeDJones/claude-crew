"""Test double for claude_agent_sdk.ClaudeSDKClient.

Mimics the externally-observed contract:
  - async context manager (__aenter__/__aexit__)
  - async query(prompt, session_id="default") that records the call
  - async receive_response() that yields the messages set up by the most
    recent query()

Each query() resets the stream for the next receive_response() iteration.
This matches the real SDK's behavior: messages produced by query N are
fully consumed before query N+1 starts. Multi-turn tests can drive the
fake the same way they'd drive the real client.

Configurable behaviors:
  - scripted_responses: per-turn list of messages or callables-of-prompt
  - query_raises:       per-turn exception to raise from query()
  - response_hangs:     if True for a turn, receive_response() awaits
                        forever until cancelled
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TaskNotificationMessage,
    TaskUsage,
    TextBlock,
)


def text_response(text: str) -> list[Any]:
    """Convenience: construct a normal AssistantMessage + ResultMessage pair."""
    return [
        AssistantMessage(
            content=[TextBlock(text=text)],
            model="fake-model",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="default",
        ),
    ]


def task_notification(
    *, status: str, summary: str | None,
    task_id: str = "task-fake",
) -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        output_file="",
        summary=summary or "",
        uuid="uuid-fake",
        session_id="default",
        usage=TaskUsage(total_tokens=0, tool_uses=0, duration_ms=0),
    )


def task_failure_response(
    summary: str | None, *, status: str = "failed",
) -> list[Any]:
    """Stream a TaskNotificationMessage(status=failed|stopped) followed by a
    terminating ResultMessage with no AssistantMessage text.

    Used by Feature #3a SC-8(a) tests: the parent observed a subagent
    failure but produced no text of its own.
    """
    return [
        task_notification(status=status, summary=summary),
        ResultMessage(
            subtype="success",  # SDK uses "success" for normal stream end
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="default",
        ),
    ]


def task_failure_then_text(
    summary: str | None, recovery_text: str,
    *, status: str = "failed",
) -> list[Any]:
    """Stream a failure notification followed by parent recovery text.

    Used by Feature #3a SC-8(β): subagent failed, parent narrated over it.
    Recovery wins (envelope contains recovery_text), but the warning log
    must still fire.
    """
    return [
        task_notification(status=status, summary=summary),
        AssistantMessage(
            content=[TextBlock(text=recovery_text)],
            model="fake-model",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="default",
        ),
    ]


class FakeSDKClient:
    """Test double for ClaudeSDKClient.

    Pass either:
      - scripted_responses=[ <list[Message]> | <Callable[[prompt], list[Message]]>, ... ]
      - query_raises=[None, SomeException(), None, ...]
      - response_hangs=[False, True, False, ...]

    Per-turn behavior is selected by the index of the query() call.
    """

    def __init__(
        self,
        *,
        options: Any = None,
        scripted_responses: list | None = None,
        query_raises: list | None = None,
        response_hangs: list | None = None,
    ) -> None:
        self.options = options
        self._scripted = scripted_responses or []
        self._query_raises = query_raises or []
        self._response_hangs = response_hangs or []

        self.queries_received: list[tuple[str, str]] = []
        self.aenter_count = 0
        self.aexit_count = 0

        self._pending: list | None = None
        self._hang: bool = False

    async def __aenter__(self) -> "FakeSDKClient":
        self.aenter_count += 1
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.aexit_count += 1

    async def query(self, prompt: str, session_id: str = "default") -> None:
        idx = len(self.queries_received)
        self.queries_received.append((prompt, session_id))

        if idx < len(self._query_raises) and self._query_raises[idx] is not None:
            raise self._query_raises[idx]

        # Set up the stream for the upcoming receive_response().
        self._hang = (
            idx < len(self._response_hangs) and self._response_hangs[idx]
        )
        if idx < len(self._scripted):
            spec = self._scripted[idx]
            self._pending = spec(prompt) if callable(spec) else list(spec)
        else:
            # Default canned reply if none specified.
            self._pending = text_response(f"<no scripted response for turn {idx + 1}>")

    async def receive_response(self) -> AsyncIterator[Any]:
        if self._hang:
            # Block forever, simulating a stream that never sees a ResultMessage.
            await asyncio.Event().wait()
            return  # pragma: no cover

        for msg in self._pending or []:
            yield msg
            if isinstance(msg, ResultMessage):
                self._pending = None
                return
        self._pending = None
