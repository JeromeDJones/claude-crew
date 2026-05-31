"""AT#11 — Broker shutdown_all parallel flush tests.

Covers:
AT#11 — N (>=3) live teammates each with has_memory_surface() True and each
        taking ~T seconds to flush; shutdown_all(graceful=True) flushes all in
        PARALLEL under ONE shared deadline, so total wall time ≈ T, not ≈ N×T.

Plan-review NOTE (MEDIUM-01): AT#11 requires a controllable double whose
begin_graceful_termination sleeps ~T seconds so parallel-vs-sequential is
measurable. The double (_SlowFlushTeammate) is constructed inline in this module
per the spec note. StubTeammate's zero-length flush would make both strategies
indistinguishable.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from claude_crew.broker import Broker
from claude_crew.teammate import StubTeammate


# ---------------------------------------------------------------------------
# Controllable double — sleeps T seconds inside begin_graceful_termination
# so parallel vs sequential strategies produce measurably different wall times.
# ---------------------------------------------------------------------------


class _SlowFlushTeammate(StubTeammate):
    """begin_graceful_termination sleeps for ``sleep_seconds`` then returns.

    Parallel execution:   wall time ≈ sleep_seconds          (one shared deadline)
    Sequential execution: wall time ≈ N × sleep_seconds      (N serial waits)
    """

    def __init__(self, *args: object, sleep_seconds: float = 0.15, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.sleep_seconds = sleep_seconds

    async def begin_graceful_termination(self, *, timeout: float) -> None:
        self._flush_invoked = True
        await asyncio.sleep(self.sleep_seconds)


def _slow_factory(sleep_seconds: float = 0.15):
    """Return a teammate factory that spawns _SlowFlushTeammate instances."""

    def factory(id: str, name: str, role: str, **kw: object) -> _SlowFlushTeammate:
        return _SlowFlushTeammate(
            id=id,
            name=name,
            role=role,
            has_memory=True,
            sleep_seconds=sleep_seconds,
        )

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestShutdownAllParallel:
    """AT#11 and companion tests for broker.shutdown_all parallel flush."""

    # ------------------------------------------------------------------
    # AT#11 — parallel wall-time assertion (the primary AT for this task)
    # ------------------------------------------------------------------

    async def test_parallel_flushes_wall_time(self) -> None:
        """AT#11: N=3 teammates × T=0.15 s each; parallel total ≈ T, not N×T.

        Controllable double: _SlowFlushTeammate.begin_graceful_termination
        sleeps T seconds so the strategies are measurably different.

        N=3, T=0.15 s:
          Parallel expected  : ~0.15 s (one asyncio.gather under one wait_for)
          Sequential expected : ~0.45 s (three serial awaits)
        Assert wall time < 2×T = 0.30 s — proves parallel, rules out sequential.
        All N teammates must be tombstoned after shutdown_all completes.
        """
        N = 3
        T = 0.15  # seconds per flush
        flush_budget = 2.0  # generous: timeout must NOT fire during this test

        b = Broker()

        factory = _slow_factory(sleep_seconds=T)
        tids = []
        for i in range(N):
            tid = await b.spawn_teammate(
                role=f"worker-{i}", name=None, factory=factory
            )
            tids.append(tid)

        t0 = time.monotonic()
        await b.shutdown_all(graceful=True, flush_timeout=flush_budget)
        wall_time = time.monotonic() - t0

        # Parallel: wall time should be close to T, well below N×T.
        assert wall_time < 2 * T, (
            f"shutdown_all took {wall_time:.3f}s; expected < {2 * T:.3f}s "
            f"(parallel ≈ {T:.2f}s); sequential would be ~{N * T:.2f}s"
        )

        # All N teammates tombstoned.
        crew = b.list_crew()
        assert len(crew) == N, f"expected {N} crew entries, got {len(crew)}"
        for info in crew:
            assert info.alive is False, f"teammate {info.id} was not tombstoned"

    # ------------------------------------------------------------------
    # Backward compatibility — no-arg call still works
    # ------------------------------------------------------------------

    async def test_shutdown_all_no_args_backward_compat(self) -> None:
        """shutdown_all() with no args succeeds (defaults: graceful=True, 90s)."""
        b = Broker()
        tid = await b.spawn_teammate(
            role="worker",
            name=None,
            factory=lambda id, name, role, **kw: StubTeammate(
                id=id, name=name, role=role, has_memory=True
            ),
        )

        await b.shutdown_all()  # no args — must not raise

        crew = b.list_crew()
        assert len(crew) == 1
        assert crew[0].alive is False

    # ------------------------------------------------------------------
    # graceful=False skips flush
    # ------------------------------------------------------------------

    async def test_shutdown_all_graceful_false_skips_flush(self) -> None:
        """shutdown_all(graceful=False) hard-kills without flush, even with memory surface."""
        b = Broker()
        teammate_ref: list[StubTeammate] = []

        def tracking_factory(id: str, name: str, role: str, **kw: object) -> StubTeammate:
            tm = StubTeammate(id=id, name=name, role=role, has_memory=True)
            teammate_ref.append(tm)
            return tm

        await b.spawn_teammate(role="worker", name=None, factory=tracking_factory)

        await b.shutdown_all(graceful=False)

        assert teammate_ref[0]._flush_invoked is False, (
            "graceful=False must skip flush in shutdown_all"
        )
        assert b.list_crew()[0].alive is False

    # ------------------------------------------------------------------
    # Memory-less teammates not flushed
    # ------------------------------------------------------------------

    async def test_shutdown_all_skips_memory_less_teammates(self) -> None:
        """Teammates with no memory surface are tombstoned without flush."""
        b = Broker()
        teammate_ref: list[StubTeammate] = []

        def no_memory_factory(id: str, name: str, role: str, **kw: object) -> StubTeammate:
            tm = StubTeammate(id=id, name=name, role=role, has_memory=False)
            teammate_ref.append(tm)
            return tm

        await b.spawn_teammate(role="worker", name=None, factory=no_memory_factory)

        await b.shutdown_all(graceful=True)

        assert teammate_ref[0]._flush_invoked is False, (
            "memory-less teammates must not be flushed in shutdown_all"
        )
        assert b.list_crew()[0].alive is False

    # ------------------------------------------------------------------
    # Mixed crew: some with memory, some without
    # ------------------------------------------------------------------

    async def test_shutdown_all_mixed_crew(self) -> None:
        """Mixed crew: memory-surface teammates flushed; memory-less hard-killed."""
        N_memory = 2
        N_no_memory = 2
        T = 0.1

        b = Broker()

        memory_refs: list[_SlowFlushTeammate] = []
        no_memory_refs: list[StubTeammate] = []

        def mem_factory(id: str, name: str, role: str, **kw: object) -> _SlowFlushTeammate:
            tm = _SlowFlushTeammate(
                id=id, name=name, role=role, has_memory=True, sleep_seconds=T
            )
            memory_refs.append(tm)
            return tm

        def no_mem_factory(id: str, name: str, role: str, **kw: object) -> StubTeammate:
            tm = StubTeammate(id=id, name=name, role=role, has_memory=False)
            no_memory_refs.append(tm)
            return tm

        for i in range(N_memory):
            await b.spawn_teammate(role=f"mem-{i}", name=None, factory=mem_factory)
        for i in range(N_no_memory):
            await b.spawn_teammate(role=f"nomem-{i}", name=None, factory=no_mem_factory)

        t0 = time.monotonic()
        await b.shutdown_all(graceful=True, flush_timeout=2.0)
        wall_time = time.monotonic() - t0

        # Memory teammates were flushed, in parallel (wall ≈ T, not N_memory×T)
        for tm in memory_refs:
            assert tm._flush_invoked is True
        # Memory-less teammates were NOT flushed
        for tm in no_memory_refs:
            assert tm._flush_invoked is False

        # Parallel: wall time < 2×T (rules out sequential N_memory×T)
        assert wall_time < 2 * T, (
            f"mixed-crew shutdown took {wall_time:.3f}s; expected < {2 * T:.3f}s"
        )

        # All tombstoned
        crew = b.list_crew()
        assert len(crew) == N_memory + N_no_memory
        for info in crew:
            assert info.alive is False

    # ------------------------------------------------------------------
    # shutdown_all with flush timeout exceeded — must still tombstone all
    # ------------------------------------------------------------------

    async def test_shutdown_all_parallel_timeout_still_tombstones(self) -> None:
        """Parallel flush times out; shutdown_all still tombstones all teammates."""
        N = 3
        T_hang = 999.0  # teammates hang indefinitely
        budget = 0.2    # tight budget so timeout fires quickly

        b = Broker()

        factory = _slow_factory(sleep_seconds=T_hang)
        for i in range(N):
            await b.spawn_teammate(role=f"worker-{i}", name=None, factory=factory)

        t0 = time.monotonic()
        # Must not raise despite timeout
        await b.shutdown_all(graceful=True, flush_timeout=budget)
        wall_time = time.monotonic() - t0

        # Returns within a reasonable bound after the budget
        assert wall_time < budget + 2.0, (
            f"shutdown_all took {wall_time:.3f}s after {budget}s budget"
        )

        # All N tombstoned despite the timeout
        crew = b.list_crew()
        assert len(crew) == N
        for info in crew:
            assert info.alive is False
