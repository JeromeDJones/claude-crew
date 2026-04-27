"""Implementation-level tests for StubTeammate.

These run the stub through a real Broker (the broker is the simplest
honest dependency — there is no broker mock worth maintaining).
"""

from __future__ import annotations

import asyncio

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate


def _stub_factory(id: str, name: str, role: str, **_kwargs) -> StubTeammate:
    return StubTeammate(id=id, name=name, role=role)


@pytest.fixture
async def broker() -> Broker:
    b = Broker()
    yield b
    await b.shutdown_all()


async def _wait_for_lead_messages(broker: Broker, count: int, timeout: float = 1.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}"
    )


class TestStubEcho:
    async def test_echoes_payload_back_to_sender(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="parrot", name=None, factory=_stub_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="hello",
        ))
        await _wait_for_lead_messages(broker, count=1)

        lead_msgs = broker.get_messages(recipient=LEAD_ID)
        assert len(lead_msgs) == 1
        assert lead_msgs[0].sender == tid
        assert lead_msgs[0].recipient == LEAD_ID
        assert lead_msgs[0].payload == {"echo": "hello", "from": "parrot"}

    async def test_echoes_each_message_in_fifo_order(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="parrot", name=None, factory=_stub_factory)
        for i in range(5):
            await broker.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=i,
            ))
        await _wait_for_lead_messages(broker, count=5)
        lead_msgs = broker.get_messages(recipient=LEAD_ID)
        assert [m.payload["echo"] for m in lead_msgs] == [0, 1, 2, 3, 4]


class TestStubShutdown:
    async def test_shutdown_drains_cleanly(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="parrot", name=None, factory=_stub_factory)
        await broker.kill_teammate(tid)
        # If shutdown didn't complete, the test would hang here.
        # D11: kill_teammate tombstones (alive=False) rather than evicting from list_crew.
        crew = broker.list_crew()
        assert len(crew) == 1
        assert crew[0].alive is False

    async def test_shutdown_is_idempotent(self, broker: Broker) -> None:
        teammate = StubTeammate(id="t-x", name="x", role="r")
        # Never started; shutdown should still be safe.
        await teammate.shutdown()
        await teammate.shutdown()


class TestStubActivityStamping:
    async def test_stub_stamps_activity_on_every_dequeue(self, broker: Broker) -> None:
        """SC-3 analog for stubs: activity stamps on every envelope dequeue."""
        tid = await broker.spawn_teammate(role="stamper", name=None, factory=_stub_factory)
        teammate = broker._teammates[tid]

        # Record initial state
        initial_monotonic = teammate._last_activity_monotonic

        # Send 3 envelopes
        for i in range(3):
            await broker.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=f"msg-{i}",
            ))

        # Wait for all echoes
        await _wait_for_lead_messages(broker, count=3)

        # Check that monotonic timestamp advanced (at least 3 times)
        final_monotonic = teammate._last_activity_monotonic
        assert final_monotonic > initial_monotonic, \
            "monotonic timestamp should advance after processing envelopes"

        # Check idle_seconds is very small (activity was just stamped)
        snap = teammate.status_snapshot()
        assert snap["idle_seconds"] < 1.0, \
            f"idle_seconds should be < 1.0 right after processing, got {snap['idle_seconds']}"

    async def test_stub_clears_current_turn_between_turns(self, broker: Broker) -> None:
        """SC-4: current_turn_started_at is None between turns and set during."""
        tid = await broker.spawn_teammate(role="turner", name=None, factory=_stub_factory)
        teammate = broker._teammates[tid]

        # Initially between turns: current_turn_started_at should be None
        initial_snap = teammate.status_snapshot()
        assert initial_snap["current_turn_started_at_wallclock"] is None, \
            "Should start with no active turn"
        assert initial_snap["last_activity_at_wallclock"] is not None, \
            "Should have initialization timestamp"

        # Send one envelope
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="test",
        ))

        # Wait for the echo to complete
        await _wait_for_lead_messages(broker, count=1)

        # After turn completes: current_turn_started_at should be None again
        final_snap = teammate.status_snapshot()
        assert final_snap["current_turn_started_at_wallclock"] is None, \
            "Should clear current_turn_started_at after turn ends"
        assert final_snap["last_activity_at_wallclock"] is not None, \
            "Should have updated last_activity_at after processing"

    async def test_stub_sets_current_turn_during_echo(self, broker: Broker) -> None:
        """SC-4: current_turn_started_at is set while processing and None after."""
        # Use slow_echo_delay to keep the turn open long enough to observe
        def slow_stub_factory(id: str, name: str, role: str, **_kwargs) -> StubTeammate:
            return StubTeammate(id=id, name=name, role=role, slow_echo_delay=0.2)

        tid = await broker.spawn_teammate(
            role="slowpoke", name=None, factory=slow_stub_factory
        )
        teammate = broker._teammates[tid]

        # Send one envelope
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="slowmo",
        ))

        # Poll status_snapshot during the echo delay window
        turn_was_active = False
        for _ in range(50):  # Poll for up to 500ms (50 * 10ms)
            snap = teammate.status_snapshot()
            if snap["current_turn_started_at_wallclock"] is not None:
                turn_was_active = True
                break
            await asyncio.sleep(0.01)

        assert turn_was_active, \
            "Should have observed current_turn_started_at set during the echo delay"

        # Wait for echo to complete
        await _wait_for_lead_messages(broker, count=1)

        # After turn ends: should be None
        final_snap = teammate.status_snapshot()
        assert final_snap["current_turn_started_at_wallclock"] is None, \
            "Should clear current_turn_started_at after slow echo completes"
