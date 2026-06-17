"""Gated live test: stderr ring wired end-to-end through a real SDK subprocess.

AT 8 (teammate-death-diagnostics): after an ordinary completing turn, the
teammate's stderr ring is non-empty OR status_snapshot()["stderr_tail"] is a
string — proving the stderr callback was registered with the SDK and the
ring-to-snapshot chain is intact.

AT 17 (forced-subprocess-crash): a real subprocess crash (fake claude binary
that writes to stderr and exits non-zero) populates stderr_tail_at_death on
the broker tombstone.

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

Design note on forced-crash test (AT 17)
------------------------------------------
AT 17 adds a complementary end-to-end proof: the ring must survive from
``_on_stderr_line`` → ``_stderr_ring`` → ``_tombstone_teammate`` →
``TeammateInfo.stderr_tail_at_death`` → ``get_teammate_status``.

The forced-crash approach:
  - A temporary fake ``claude`` Python script writes a marker to stderr and
    exits with code 1.  This is a real subprocess with real stderr — not a
    manually-injected probe line and not a mocked ``ProcessError``.
  - ``ClaudeAgentOptions`` in ``claude_crew.sdk_teammate`` is monkeypatched
    to inject ``cli_path`` pointing at the fake script, so the SDK spawns
    the fake binary instead of the real ``claude`` CLI.
  - The fake script handles ``-v`` (version check) by printing a high
    version number and exiting 0; for any other invocation it writes the
    stderr marker and exits 1.
  - Because the subprocess crashes during ``ClaudeSDKClient`` context entry
    (before the poll task is even created), the death does NOT flow through
    the ``ProcessError → _death_suspected → poll`` path.  Instead the
    outer-except handler in ``_run()`` sends an error envelope to LEAD_ID
    and returns.  The broker still holds the teammate in ``_teammates``
    (never tombstoned automatically).  A direct ``kill_teammate`` call is
    used to trigger ``_tombstone_teammate``, which reads ``_stderr_ring``
    and captures ``stderr_tail_at_death``.
  - The ``_handle_stderr`` task fills the ring during ``disconnect()`` →
    ``await __aexit__``, which is an event-loop checkpoint that allows the
    task to read the buffered stderr before the pipe is closed.

Skipped unless CLAUDE_CREW_LIVE_TESTS=1.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import textwrap
from pathlib import Path
from typing import Any

import pytest

import claude_crew.sdk_teammate as _sdk_teammate_module
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


# ---------------------------------------------------------------------------
# AT 17: forced subprocess crash populates stderr_tail_at_death.
# ---------------------------------------------------------------------------

_FORCED_CRASH_MARKER = "forced-crash-stderr-marker"
"""Marker written to stderr by the fake claude binary.

Must be a short, relay-safe string (see CLAUDE.md test convention for
LLM-relayed sentinels). Not relayed through an LLM here, but kept short
for unambiguous grep in assertions.
"""


class TestForcedSubprocessCrash:
    """AT 17: a real subprocess crash populates stderr_tail_at_death.

    Uses a fake ``claude`` Python binary (real subprocess, not a mock) that
    writes a marker to stderr and exits non-zero.  ``ClaudeAgentOptions`` in
    ``claude_crew.sdk_teammate`` is monkeypatched to inject ``cli_path``
    so the SDK spawns the fake binary instead of the real CLI.

    Because the subprocess crashes before the poll task is ever created
    (the context entry fails), the broker never auto-tombstones via the
    ``ProcessError → _death_suspected → poll`` path.  ``kill_teammate`` is
    called explicitly to drive ``_tombstone_teammate``, which reads
    ``_stderr_ring`` and produces ``stderr_tail_at_death``.  This exercises
    the same tombstone/snapshot machinery as the poll-driven path.
    """

    async def test_forced_subprocess_crash_populates_stderr_tail(
        self,
        broker: Broker,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AT 17: real subprocess crash → stderr captured → tombstone has stderr_tail_at_death.

        Steps
        -----
        1. Create a temporary fake ``claude`` script (Python) that:
           - Answers ``-v`` with a high version number (passes SDK version check).
           - For all other invocations: writes a marker to stderr, exits 1.
        2. Monkeypatch ``ClaudeAgentOptions`` in ``claude_crew.sdk_teammate``
           to inject ``cli_path`` pointing at the fake script.
        3. Spawn a teammate via ``sdk_factory`` — the SDK will spawn the fake
           binary instead of the real ``claude``.
        4. Wait for the error envelope the teammate sends to LEAD_ID after
           the context-entry failure (outer-except in ``_run()``).
        5. Call ``broker.kill_teammate(tid, graceful=False)`` to drive
           ``_tombstone_teammate``, which reads ``_stderr_ring`` and captures
           ``stderr_tail_at_death``.
        6. Assert ``stderr_tail_at_death`` is non-null and contains the marker.
        """
        # ------------------------------------------------------------------
        # 1. Build a fake "claude" binary that writes to stderr and exits 1.
        # ------------------------------------------------------------------
        fake_claude = tmp_path / "claude"
        # The script handles -v (SDK version check) by printing a version
        # that satisfies the minimum-version guard.  Any other invocation
        # writes the crash marker to stderr and exits non-zero.
        fake_claude.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env python3
                import sys
                # SDK version check: `claude -v`
                if len(sys.argv) > 1 and sys.argv[1] in ("-v", "--version"):
                    print("99.0.0")
                    sys.exit(0)
                # Forced crash: write marker to stderr, exit non-zero.
                sys.stderr.write("{_FORCED_CRASH_MARKER}\\n")
                sys.stderr.flush()
                sys.exit(1)
            """),
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

        # ------------------------------------------------------------------
        # 2. Monkeypatch ClaudeAgentOptions to inject cli_path.
        # ------------------------------------------------------------------
        # sdk_teammate._run() builds opts_kwargs and calls
        #   options = ClaudeAgentOptions(**opts_kwargs)
        # We wrap the real class to add cli_path so the SDK subprocess
        # uses our fake binary instead of the real claude CLI.
        # Monkeypatch replaces the name in the module's own namespace;
        # the real dataclass is preserved as original_cls.
        original_cls = _sdk_teammate_module.ClaudeAgentOptions

        def _options_with_fake_cli(**kwargs: Any) -> Any:
            kwargs["cli_path"] = str(fake_claude)
            return original_cls(**kwargs)

        monkeypatch.setattr(
            _sdk_teammate_module, "ClaudeAgentOptions", _options_with_fake_cli,
        )

        # ------------------------------------------------------------------
        # 3. Spawn the teammate — SDK spawns the fake binary.
        # ------------------------------------------------------------------
        tid = await broker.spawn_teammate(
            role="forced-crash-probe",
            name=None,
            factory=sdk_factory,
        )

        # ------------------------------------------------------------------
        # 4. Wait for the error envelope at LEAD_ID.
        # ------------------------------------------------------------------
        # The fake binary writes to stderr, exits 1.  The SDK's
        # ClaudeAgentOptions context entry fails (CLIConnectionError or
        # similar).  The outer-except handler in _run() sends an error
        # envelope to LEAD_ID and the _run() task returns.
        # _wait_for_lead polls until ≥1 message arrives at LEAD_ID.
        await _wait_for_lead(broker, 1, timeout=30.0)

        # Sanity: the error should have come from our teammate.
        msgs = broker.get_messages(recipient=LEAD_ID)
        teammate_errors = [
            m for m in msgs
            if m.sender == tid
            and isinstance(m.payload, dict)
            and m.payload.get("error")
        ]
        assert len(teammate_errors) >= 1, (
            f"Expected at least one error envelope from teammate {tid!r} at LEAD_ID, "
            f"but got none.  Messages to LEAD_ID: {[m.payload for m in msgs]}"
        )

        # Yield briefly so _handle_stderr (scheduled in the SDK task group
        # during connect()) has a chance to drain any buffered stderr lines
        # before we tombstone.  The disconnect() call in connect() runs
        # `await task_group.__aexit__(...)` which is the primary driver, but
        # an extra yield is cheap insurance.
        await asyncio.sleep(0.05)

        # Pre-tombstone sanity: the ring should already have content.
        teammate = broker._teammates.get(tid)
        assert teammate is not None, (
            f"Expected teammate {tid!r} to still be in broker._teammates "
            f"(outer-except path does not pop it); got None."
        )
        ring_content = list(getattr(teammate, "_stderr_ring", []))

        # ------------------------------------------------------------------
        # 5. Tombstone via kill_teammate → _tombstone_teammate reads ring.
        # ------------------------------------------------------------------
        # The teammate is still in broker._teammates (the outer-except path
        # sends an error envelope but never pops the teammate).  kill_teammate
        # with graceful=False skips the memory-flush and calls _tombstone_teammate
        # directly, which reads _stderr_ring via status_snapshot().
        await broker.kill_teammate(tid, graceful=False)

        # ------------------------------------------------------------------
        # 6. Assert stderr_tail_at_death is non-null and contains the marker.
        # ------------------------------------------------------------------
        status = broker.get_teammate_status(tid)
        assert not status.get("alive", True), (
            f"Expected teammate {tid!r} to be tombstoned after kill_teammate, "
            f"but status shows alive=True."
        )

        stderr_tail = status.get("stderr_tail_at_death")
        assert stderr_tail is not None, (
            f"Expected stderr_tail_at_death to be non-null after a forced "
            f"subprocess crash with real stderr output.\n"
            f"  Pre-tombstone _stderr_ring content: {ring_content!r}\n"
            f"  Fake claude binary: {fake_claude}\n"
            f"  Teammate ID: {tid!r}\n"
            f"  Error envelopes received: {[m.payload for m in teammate_errors]}\n\n"
            f"If this is None, the _handle_stderr task did not populate the ring "
            f"before _tombstone_teammate read status_snapshot().  This would indicate "
            f"a race between the stderr task group teardown and the ring read."
        )
        assert _FORCED_CRASH_MARKER in stderr_tail, (
            f"Expected stderr_tail_at_death to contain the crash marker "
            f"{_FORCED_CRASH_MARKER!r}, but got: {stderr_tail!r}"
        )
