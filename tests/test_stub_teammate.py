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


def _stub_factory(id: str, name: str, role: str) -> StubTeammate:
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
        assert broker.list_crew() == []

    async def test_shutdown_is_idempotent(self, broker: Broker) -> None:
        teammate = StubTeammate(id="t-x", name="x", role="r")
        # Never started; shutdown should still be safe.
        await teammate.shutdown()
        await teammate.shutdown()
