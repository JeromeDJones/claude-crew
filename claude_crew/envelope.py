"""Message envelope for the crew bus.

The envelope is the wire format every message on the bus uses. Two distinct
identifiers live here for two distinct purposes:

- ``id`` is content-level (UUID). Sender-supplied so retries can dedup.
- ``seq`` is broker-assigned, monotonic per crew, used as the cursor primitive
  for ``get_messages`` reads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4


def new_message_id() -> str:
    """Return a fresh UUIDv4 string suitable for ``Envelope.id``."""
    return str(uuid4())


@dataclass(frozen=True)
class Envelope:
    id: str
    seq: int
    sender: str
    recipient: str
    timestamp: float
    payload: Any

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError(f"seq must be non-negative, got {self.seq}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Envelope:
        return cls(
            id=d["id"],
            seq=d["seq"],
            sender=d["sender"],
            recipient=d["recipient"],
            timestamp=d["timestamp"],
            payload=d["payload"],
        )
