"""Gated live test: stderr ring wired end-to-end through a real SDK subprocess.

AT 8 (teammate-death-diagnostics): after an ordinary completing turn, the
teammate's stderr ring is non-empty OR status_snapshot()["stderr_tail"] is a
string — proving the stderr callback was registered with the SDK and the
ring-to-snapshot chain is intact.

Design note on registration vs. natural invocation
----------------------------------------------------
The current ``claude`` CLI emits no bytes to stderr during normal turns — all
output goes to stdout as a JSON stream.  The SDK transport's
``_handle_stderr`` task therefore never invokes the callback during an
ordinary turn.  This test proves registration without relying on natural
CLI-stderr output in two independent steps:

  1. **Registration assertion (HIGH-01 requirement):** after the turn,
     ``tm._client.options.stderr is tm._on_stderr_line`` checks the live
     ``ClaudeAgentOptions`` object held by the SDK client.  This assertion
     is directly sensitive to ``sdk_teammate.py:1432``
     (``opts_kwargs["stderr"] = self._on_stderr_line``): delete that line
     and ``options.stderr`` reverts to its ``None`` default, making this
     assertion fail.

  2. **Ring-to-snapshot smoke:** ``tm._on_stderr_line(PROBE)`` is called
     directly to verify the callback populates the ring and that
     ``status_snapshot()["stderr_tail"]`` surfaces the content.  This step
     is *not* a substitute for the registration proof; it is a separate
     check of the ring-buffer implementation.

Skipped unless CLAUDE_CREW_LIVE_TESTS=1.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.sdk_teammate import SdkTeammate

# ---------------------------------------------------------------------------
# Module-level gate — every test in this file is skipped unless live.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)

# ---------------------------------------------------------------------------
# Captured at module-import time, BEFORE any test's monkeypatch.setenv("HOME",
# ...) can fire.  _preserve_sdk_auth reads this — not Path.home() or
# expanduser("~") — because both resolve via the live HOME env var, which is
# already tmp_path inside tests that monkeypatch HOME.
# ---------------------------------------------------------------------------
_REAL_HOME = Path(os.path.expanduser("~"))


def _preserve_sdk_auth(tmp_home: Path) -> None:
    """Copy SDK auth artifacts from the real HOME into tmp_home.

    Tests that monkeypatch.setenv("HOME", tmp_path) to plant fixtures under
    tmp_home/.claude/ otherwise strip the spawned SDK subprocess of its
    credentials (``~/.claude/.credentials.json``) and global config
    (``~/.claude.json``), producing "Not logged in · Please run /login"
    instead of real model output.

    Copies (not symlinks) so the tmp HOME is self-contained and removable.
    Best-effort: missing source files are silently skipped — the test will
    surface its own auth failure if the SDK still can't find credentials.
    """
    # Use _REAL_HOME captured at module-import time; Path.home() /
    # expanduser("~") would resolve via the already-monkeypatched HOME
    # and point at tmp_home itself (empty), causing the copies to silently
    # skip and the SDK to come back "Not logged in".
    src_creds = _REAL_HOME / ".claude" / ".credentials.json"
    src_config = _REAL_HOME / ".claude.json"
    dst_claude = tmp_home / ".claude"
    dst_claude.mkdir(parents=True, exist_ok=True)
    if src_creds.exists():
        shutil.copy2(src_creds, dst_claude / ".credentials.json")
    if src_config.exists():
        shutil.copy2(src_config, tmp_home / ".claude.json")


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 90.0) -> None:
    """Bounded wait until broker has at least count messages for LEAD_ID.

    Uses asyncio.get_running_loop() (not the deprecated get_event_loop()).
    Raises AssertionError on timeout so stalled SDK subprocesses surface as
    clean failures instead of hanging the test process.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages after {timeout}s; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


# ---------------------------------------------------------------------------
# Broker fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
async def broker() -> Any:  # noqa: ANN401
    """Live Broker instance. Shuts down all teammates on teardown."""
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# AT 8: live stderr wiring.
# ---------------------------------------------------------------------------


class TestLiveStderrWiring:
    """AT 8: stderr callback registered with the SDK and ring wired to snapshot.

    Two assertions:

    1. Registration proof: ``tm._client.options.stderr is tm._on_stderr_line``
       — sensitive to the deletion of ``sdk_teammate.py:1432``.

    2. Ring-to-snapshot smoke: after a direct ``_on_stderr_line`` call the
       ring is non-empty and ``status_snapshot()["stderr_tail"]`` is a string
       — verifies the ring/snapshot chain independently of CLI stderr output.
    """

    async def test_stderr_callback_registered_and_ring_wired(
        self,
        broker: Broker,
    ) -> None:
        """Spawn a real teammate, run one completing turn, assert registration + ring.

        Bounded drain: _wait_for_lead polls with a 90-second wall-clock cap
        (the established hang-detection budget) so a stalled SDK subprocess
        surfaces as a clean AssertionError rather than a hung test process.

        Registration assertion: ``ClaudeSDKClient.options`` is the
        ``ClaudeAgentOptions`` passed at construction time (``self.options =
        options`` in ``ClaudeSDKClient.__init__``).  After the turn the
        client is still alive (the teammate is blocking on its inbox waiting
        for the next message), so ``tm._client.options`` is the options
        object that ``_run()`` constructed.  Asserting
        ``tm._client.options.stderr is tm._on_stderr_line`` directly
        verifies that line 1432 of ``sdk_teammate.py``
        (``opts_kwargs["stderr"] = self._on_stderr_line``) executed —
        deleting that line causes ``options.stderr`` to keep its ``None``
        default, failing this assertion.
        """
        tid = await broker.spawn_teammate(
            role="live-stderr-probe", name=None, factory=sdk_factory,
        )

        # Send a trivial prompt that completes normally without tools.
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="Reply with exactly two words: STDERR WIRED",
        ))

        # Bounded wait — 90-second cap matches the established hang-detection budget.
        await _wait_for_lead(broker, 1, timeout=90.0)

        # Access the live SdkTeammate directly.
        # broker._teammates holds alive teammates; after a normal completing
        # turn the teammate is back in its inbox loop — alive with _client set.
        tm = broker._teammates.get(tid)
        assert tm is not None, (
            f"Expected teammate {tid!r} to still be alive in broker after a "
            f"normal completing turn; found None. "
            f"(Was the teammate killed or tombstoned unexpectedly?)"
        )

        # ---------------------------------------------------------------
        # Registration assertion (HIGH-01).
        # ClaudeSDKClient stores the ClaudeAgentOptions it was constructed
        # with as a public attribute: self.options = options.  After the
        # turn the client context is still open (the while-True loop blocks
        # on inbox.get()), so tm._client.options is the live options object.
        # If sdk_teammate.py:1432 (opts_kwargs["stderr"] = self._on_stderr_line)
        # were deleted, options.stderr would be None (the dataclass default),
        # making this assertion fail.
        # ---------------------------------------------------------------
        client = tm._client
        assert client is not None, (
            "Expected tm._client to be non-None while the teammate is alive "
            "between turns (ClaudeSDKClient context is open until shutdown)."
        )
        # Python creates a new bound-method object on each attribute access, so
        # `client.options.stderr is tm._on_stderr_line` is always False even
        # when correct.  Compare the underlying components instead:
        #   __self__ — the SdkTeammate instance the method is bound to
        #   __func__ — the unbound function (SdkTeammate._on_stderr_line)
        registered = client.options.stderr
        assert registered is not None, (
            "client.options.stderr is None — "
            "opts_kwargs[\"stderr\"] = self._on_stderr_line at "
            "sdk_teammate.py:1432 was not executed (or was deleted)."
        )
        assert registered.__self__ is tm, (
            f"client.options.stderr is bound to {registered.__self__!r}, "
            f"expected the live SdkTeammate {tm!r}."
        )
        assert registered.__func__ is SdkTeammate._on_stderr_line, (
            f"client.options.stderr.__func__ is {registered.__func__!r}, "
            f"expected SdkTeammate._on_stderr_line."
        )

        # ---------------------------------------------------------------
        # Ring-to-snapshot smoke (separate from the registration proof).
        # The current Claude CLI writes nothing to stderr during normal turns.
        # Calling _on_stderr_line directly mirrors the SDK transport's
        # _handle_stderr task invocation and verifies the ring/snapshot chain.
        # ---------------------------------------------------------------
        PROBE = "live-stderr-probe-line"
        tm._on_stderr_line(PROBE)

        # Yield to the event loop so any concurrent async task can drain.
        await asyncio.sleep(0.0)

        snap = tm.status_snapshot()
        stderr_tail = snap.get("stderr_tail")

        # AT 8 assertion: ring non-empty OR tail is a string.
        assert len(tm._stderr_ring) > 0 or isinstance(stderr_tail, str), (
            f"Expected stderr ring to be non-empty or stderr_tail to be a "
            f"non-None string after calling _on_stderr_line({PROBE!r}), but got:\n"
            f"  _stderr_ring length = {len(tm._stderr_ring)}\n"
            f"  stderr_tail = {stderr_tail!r}\n"
            f"This means the ring buffer or status_snapshot wiring is broken."
        )
        assert PROBE in (stderr_tail or ""), (
            f"Expected probe line {PROBE!r} to appear in stderr_tail, "
            f"but got {stderr_tail!r}"
        )
