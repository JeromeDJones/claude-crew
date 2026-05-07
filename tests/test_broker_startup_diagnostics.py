"""Tests for BrokerSnapshot.startup_diagnostics field (slice: broker-snapshot-field).

Covers acceptance tests #6 (frozen snapshot field) and #11 (existing
consumers unbroken — additive default).
"""

from __future__ import annotations

import dataclasses

import pytest

from claude_crew.broker import Broker, BrokerSnapshot
from claude_crew.diagnostics import StartupDiagnostic


def _diag(
    *,
    level: str = "WARNING",
    message: str = "extra_skills: skill 'foo' not found",
    source: str = "claude_crew.factories",
    timestamp: float = 1715000000.0,
    category: str = "unknown_skill",
) -> StartupDiagnostic:
    return StartupDiagnostic(
        level=level,
        message=message,
        source=source,
        timestamp=timestamp,
        category=category,
    )


class TestSnapshotStartupDiagnostics:
    """Acceptance test #6: snapshot field is frozen and identity-preserving."""

    @pytest.mark.asyncio
    async def test_default_is_empty_tuple(self) -> None:
        """Broker() with no kwarg → snapshot.startup_diagnostics == ()."""
        broker = Broker()
        snap = broker.snapshot()
        assert snap.startup_diagnostics == ()
        assert isinstance(snap.startup_diagnostics, tuple)

    @pytest.mark.asyncio
    async def test_kwarg_threaded_into_snapshot(self) -> None:
        """startup_diagnostics= kwarg surfaces on every snapshot()."""
        diags = (
            _diag(level="INFO", message="explorer.md shadows default-pack",
                  source="claude_crew.subagents.loader",
                  timestamp=100.0, category="shadow"),
            _diag(level="WARNING",
                  message="extra_skills: skill 'nope' not found",
                  source="claude_crew.factories",
                  timestamp=101.0, category="unknown_skill"),
        )
        broker = Broker(startup_diagnostics=diags)
        snap = broker.snapshot()

        assert len(snap.startup_diagnostics) == 2
        assert snap.startup_diagnostics == diags
        assert snap.startup_diagnostics[0].category == "shadow"
        assert snap.startup_diagnostics[1].level == "WARNING"

    @pytest.mark.asyncio
    async def test_snapshot_field_identity_stable_across_calls(self) -> None:
        """Two snapshot() calls yield value-equal startup_diagnostics."""
        diags = (_diag(),)
        broker = Broker(startup_diagnostics=diags)
        s1 = broker.snapshot()
        s2 = broker.snapshot()
        assert s1.startup_diagnostics == s2.startup_diagnostics
        assert s1.startup_diagnostics == diags

    @pytest.mark.asyncio
    async def test_no_public_mutator(self) -> None:
        """No public method on Broker mutates startup_diagnostics."""
        broker = Broker(startup_diagnostics=(_diag(),))
        public_methods = [
            name for name in dir(broker)
            if not name.startswith("_") and callable(getattr(broker, name))
        ]
        for name in public_methods:
            assert "startup_diagnostic" not in name.lower(), (
                f"unexpected public mutator-shaped method: {name}"
            )

    @pytest.mark.asyncio
    async def test_snapshot_is_frozen_dataclass(self) -> None:
        """BrokerSnapshot is a frozen dataclass; cannot reassign field."""
        broker = Broker(startup_diagnostics=(_diag(),))
        snap = broker.snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.startup_diagnostics = ()  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_runtime_log_does_not_appear_in_subsequent_snapshots(self) -> None:
        """Acceptance test #6 tail: runtime emissions do not leak in.

        The broker stores the tuple passed at construction and re-emits it on
        every snapshot — runtime logger calls cannot grow the field.
        """
        import logging

        diags = (_diag(),)
        broker = Broker(startup_diagnostics=diags)
        # Emit some noise on a logger that the (now-detached) collector
        # would have caught during its capture window.
        logging.getLogger("claude_crew.subagents.loader").warning(
            "this happens AFTER broker construction"
        )
        snap = broker.snapshot()
        assert snap.startup_diagnostics == diags
        assert len(snap.startup_diagnostics) == 1

    @pytest.mark.asyncio
    async def test_list_input_coerced_to_tuple(self) -> None:
        """Defensive: accept list input, store as tuple internally."""
        diag = _diag()
        broker = Broker(startup_diagnostics=[diag])  # type: ignore[arg-type]
        snap = broker.snapshot()
        assert isinstance(snap.startup_diagnostics, tuple)
        assert snap.startup_diagnostics == (diag,)


class TestExistingConsumersUnbroken:
    """Acceptance test #11: BrokerSnapshot field addition is additive."""

    @pytest.mark.asyncio
    async def test_broker_constructible_with_no_args(self) -> None:
        """Broker() — no positional, no kwarg — still works."""
        broker = Broker()
        assert broker.crew_id is not None

    @pytest.mark.asyncio
    async def test_snapshot_preserves_all_existing_fields(self) -> None:
        """All pre-existing snapshot fields default sensibly with no kwarg."""
        broker = Broker()
        snap = broker.snapshot()
        assert snap.teammates == ()
        assert snap.live == ()
        assert snap.log == ()
        assert snap.tool_events == ()
        assert snap.dead_configs == {}
        assert snap.startup_diagnostics == ()

    @pytest.mark.asyncio
    async def test_snapshot_constructor_accepts_default(self) -> None:
        """BrokerSnapshot(...) without startup_diagnostics still works."""
        snap = BrokerSnapshot(
            crew_id="abc",
            teammates=(),
            live=(),
            log=(),
        )
        assert snap.startup_diagnostics == ()
