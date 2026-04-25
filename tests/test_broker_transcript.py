"""Integration tests: Broker writes JSONL transcript correctly.

Exercises the full Broker → TranscriptSink path through real
spawn/send/broadcast/kill/shutdown flows. The transcript file is
read back from disk and parsed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate


@pytest.fixture
def enable_transcripts(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
    return tmp_path


def _stub_factory(id, name, role, **_kwargs):
    return StubTeammate(id=id, name=name, role=role)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l]


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.01)


# ---------- crew_id and file ----------


class TestCrewIdAndFile:
    def test_broker_allocates_crew_id_at_init(self, enable_transcripts) -> None:
        b = Broker()
        try:
            assert isinstance(b.crew_id, str)
            assert len(b.crew_id) == 8
            assert all(c in "0123456789abcdef" for c in b.crew_id)
        finally:
            asyncio.run(b.shutdown_all())

    def test_two_brokers_get_distinct_crew_ids(self, enable_transcripts) -> None:
        a = Broker()
        b = Broker()
        try:
            assert a.crew_id != b.crew_id
        finally:
            asyncio.run(a.shutdown_all())
            asyncio.run(b.shutdown_all())

    def test_transcript_file_appears_at_init(self, enable_transcripts) -> None:
        b = Broker()
        try:
            files = list(enable_transcripts.iterdir())
            assert len(files) == 1
            assert b.crew_id in files[0].name
        finally:
            asyncio.run(b.shutdown_all())


# ---------- lifecycle events ----------


class TestLifecycleEvents:
    async def test_started_lifecycle_written_at_init(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        try:
            files = list(enable_transcripts.iterdir())
            lines = _read_lines(files[0])
            started = [l for l in lines if l.get("kind") == "lifecycle"
                       and l.get("event") == "started"]
            assert len(started) == 1
            assert started[0]["crew_id"] == b.crew_id
        finally:
            await b.shutdown_all()

    async def test_spawn_emits_lifecycle_with_role_and_model(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="planner", name="alice",
                factory=_stub_factory, model="claude-opus-4-7",
            )
            files = list(enable_transcripts.iterdir())
            lines = _read_lines(files[0])
            spawns = [l for l in lines if l.get("event") == "spawn"]
            assert len(spawns) == 1
            assert spawns[0]["teammate_id"] == tid
            assert spawns[0]["name"] == "alice"
            assert spawns[0]["role"] == "planner"
            assert spawns[0]["model"] == "claude-opus-4-7"
        finally:
            await b.shutdown_all()

    async def test_explicit_kill_uses_reason_explicit(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory,
            )
            await b.kill_teammate(tid)
            files = list(enable_transcripts.iterdir())
            lines = _read_lines(files[0])
            kills = [l for l in lines if l.get("event") == "kill"]
            assert len(kills) == 1
            assert kills[0]["teammate_id"] == tid
            assert kills[0]["reason"] == "explicit"
        finally:
            await b.shutdown_all()

    async def test_shutdown_kill_uses_reason_shutdown(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        await b.spawn_teammate(role="r1", name=None, factory=_stub_factory)
        await b.spawn_teammate(role="r2", name=None, factory=_stub_factory)
        await b.shutdown_all()

        files = list(enable_transcripts.iterdir())
        lines = _read_lines(files[0])
        kills = [l for l in lines if l.get("event") == "kill"]
        assert len(kills) == 2
        assert all(k["reason"] == "shutdown" for k in kills)
        shutdowns = [l for l in lines if l.get("event") == "shutdown"]
        assert len(shutdowns) == 1


# ---------- envelope events ----------


class TestEnvelopeEvents:
    async def test_send_writes_envelope_after_log_append(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory,
            )
            await b.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID,
                recipient=tid, timestamp=0.0, payload="hi",
            ))
            await _wait_for_lead(b, 1)  # stub will reply, give it a tick

            files = list(enable_transcripts.iterdir())
            lines = _read_lines(files[0])
            envelopes = [l for l in lines if l.get("kind") == "envelope"]
            # Lead -> teammate (1) plus stub's echo back (1) = at least 2
            assert len(envelopes) >= 2
            # Each envelope line has crew_id and the standard fields.
            for env in envelopes:
                assert env["crew_id"] == b.crew_id
                assert "id" in env
                assert "seq" in env
                assert "sender" in env
                assert "recipient" in env
                assert "payload" in env
        finally:
            await b.shutdown_all()

    async def test_envelope_seq_monotonic_in_file(
        self, enable_transcripts,
    ) -> None:
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory,
            )
            for i in range(5):
                await b.send(Envelope(
                    id=new_message_id(), seq=0, sender=LEAD_ID,
                    recipient=tid, timestamp=0.0, payload=i,
                ))
            await _wait_for_lead(b, 5)
            await b.shutdown_all()
        except Exception:
            await b.shutdown_all()
            raise

        files = list(enable_transcripts.iterdir())
        lines = _read_lines(files[0])
        envelope_seqs = [l["seq"] for l in lines if l.get("kind") == "envelope"]
        assert envelope_seqs == sorted(envelope_seqs)
        assert len(set(envelope_seqs)) == len(envelope_seqs)  # no dupes


# ---------- disabled mode ----------


class TestDisabledMode:
    async def test_broker_works_when_transcript_disabled(
        self, monkeypatch, tmp_path,
    ) -> None:
        # conftest already sets DISABLED=1; just ensure no file appears.
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory,
            )
            await b.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID,
                recipient=tid, timestamp=0.0, payload="x",
            ))
            await _wait_for_lead(b, 1)
            assert list(tmp_path.iterdir()) == []
        finally:
            await b.shutdown_all()
