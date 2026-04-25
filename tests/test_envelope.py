"""Implementation-level tests for the message Envelope."""

from __future__ import annotations

import json
import uuid

import pytest

from claude_crew.envelope import Envelope, new_message_id


class TestEnvelopeConstruction:
    def test_minimal_envelope_constructs_with_all_fields(self) -> None:
        env = Envelope(
            id="m-1",
            seq=1,
            sender="lead",
            recipient="t-abc",
            timestamp=1000.0,
            payload={"hello": "world"},
        )
        assert env.id == "m-1"
        assert env.seq == 1
        assert env.sender == "lead"
        assert env.recipient == "t-abc"
        assert env.timestamp == 1000.0
        assert env.payload == {"hello": "world"}

    def test_envelope_is_frozen(self) -> None:
        env = Envelope(
            id="m-1", seq=1, sender="lead", recipient="t",
            timestamp=0.0, payload=None,
        )
        with pytest.raises(Exception):
            env.seq = 2  # type: ignore[misc]

    def test_seq_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError):
            Envelope(
                id="m", seq=-1, sender="a", recipient="b",
                timestamp=0.0, payload=None,
            )

    def test_payload_can_be_any_json_value(self) -> None:
        for payload in [None, "string", 42, 3.14, True, [], {}, {"nested": [1, 2]}]:
            env = Envelope(
                id="m", seq=1, sender="a", recipient="b",
                timestamp=0.0, payload=payload,
            )
            assert env.payload == payload


class TestEnvelopeSerialization:
    def test_to_dict_contains_all_fields(self) -> None:
        env = Envelope(
            id="m-1", seq=5, sender="lead", recipient="t-abc",
            timestamp=1000.5, payload={"k": "v"},
        )
        d = env.to_dict()
        assert d == {
            "id": "m-1",
            "seq": 5,
            "sender": "lead",
            "recipient": "t-abc",
            "timestamp": 1000.5,
            "payload": {"k": "v"},
        }

    def test_to_dict_is_json_serializable(self) -> None:
        env = Envelope(
            id="m-1", seq=5, sender="lead", recipient="t-abc",
            timestamp=1000.5, payload={"k": "v", "n": [1, 2, 3]},
        )
        # Should not raise.
        json.dumps(env.to_dict())

    def test_round_trip_through_dict(self) -> None:
        original = Envelope(
            id="m-1", seq=5, sender="lead", recipient="t-abc",
            timestamp=1000.5, payload={"k": "v"},
        )
        recovered = Envelope.from_dict(original.to_dict())
        assert recovered == original


class TestNewMessageId:
    def test_returns_unique_uuid_strings(self) -> None:
        ids = {new_message_id() for _ in range(100)}
        assert len(ids) == 100
        for mid in ids:
            uuid.UUID(mid)  # raises if not a valid UUID
