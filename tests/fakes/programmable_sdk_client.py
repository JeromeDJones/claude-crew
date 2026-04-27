"""Programmable extension of FakeSDKClient for telemetry-liveness tests.

Used by SC-1, SC-3, SC-7, SC-11, and SC-12 verification in T4/T5. Extends
FakeSDKClient with:
  - Configurable per-event delays (event_timings)
  - interrupt() with normal / hang / raise behaviour + call tracking
  - Settable _transport._process.returncode (mutable mid-test)
  - Configurable raise-on-read / raise-on-write to simulate ProcessError
"""

from __future__ import annotations

import asyncio
import time
import types
from typing import Any, AsyncIterator, Literal

from tests.fakes.sdk import FakeSDKClient


class ProgrammableSDKClient(FakeSDKClient):
    """FakeSDKClient extended with per-event timing, interrupt control,
    and a mock transport so tests can flip returncode mid-run.

    Parameters
    ----------
    event_timings:
        List of seconds to sleep before yielding event[i]. If the list is
        exhausted (or None), 0.0 is used for remaining events.
    interrupt_behavior:
        "normal"  — interrupt() records the call and returns immediately.
        "hang"    — interrupt() records the call then waits forever.
        "raise"   — interrupt() records the call then raises interrupt_raises.
    interrupt_raises:
        Exception type to instantiate and raise when interrupt_behavior="raise".
        Defaults to ConnectionError.
    transport_returncode:
        Initial value for self._transport._process.returncode. Tests can
        mutate client._transport._process.returncode directly mid-test.
    read_raises:
        If set, receive_response() raises this exception type on entry (before
        any yield) instead of yielding events.
    write_raises:
        If set, query() raises this exception type instead of recording the call.
    **parent_kwargs:
        Forwarded verbatim to FakeSDKClient.__init__ (scripted_responses,
        query_raises, response_hangs, options).
    """

    def __init__(
        self,
        *,
        event_timings: list[float] | None = None,
        interrupt_behavior: Literal["normal", "hang", "raise"] = "normal",
        interrupt_raises: type[BaseException] = ConnectionError,
        transport_returncode: int | None = None,
        read_raises: type[BaseException] | None = None,
        write_raises: type[BaseException] | None = None,
        **parent_kwargs: Any,
    ) -> None:
        super().__init__(**parent_kwargs)

        self._event_timings = event_timings or []
        self._interrupt_behavior = interrupt_behavior
        self._interrupt_raises = interrupt_raises
        self._read_raises = read_raises
        self._write_raises = write_raises

        # Public — tests read this to verify calls were recorded.
        self.interrupt_calls: list[float] = []

        # Mock transport so SC-12 tests can read/write returncode directly.
        self._transport = types.SimpleNamespace(
            _process=types.SimpleNamespace(returncode=transport_returncode)
        )

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    async def query(self, prompt: str, session_id: str = "default") -> None:
        if self._write_raises is not None:
            raise self._write_raises("write_raises configured on ProgrammableSDKClient")
        await super().query(prompt, session_id)

    async def receive_response(self) -> AsyncIterator[Any]:  # type: ignore[override]
        if self._read_raises is not None:
            raise self._read_raises("read_raises configured on ProgrammableSDKClient")

        event_index = 0
        async for event in super().receive_response():
            delay = (
                self._event_timings[event_index]
                if event_index < len(self._event_timings)
                else 0.0
            )
            if delay > 0.0:
                await asyncio.sleep(delay)
            event_index += 1
            yield event

    async def interrupt(self) -> None:
        self.interrupt_calls.append(time.monotonic())

        if self._interrupt_behavior == "normal":
            return
        elif self._interrupt_behavior == "hang":
            # Wait forever — the caller is expected to wrap in asyncio.wait_for.
            await asyncio.sleep(86400)
        else:  # "raise"
            raise self._interrupt_raises(
                f"interrupt() configured to raise {self._interrupt_raises.__name__}"
            )
