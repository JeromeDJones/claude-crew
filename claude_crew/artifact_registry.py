"""Per-crew registry for coordinator-pushed markdown artifacts.

The trusted lead calls ``surface_document`` at surface time; the registry
stores the bytes captured at that moment and never re-reads the filesystem.
``GET /artifact/<crew_id>/<artifact_id>`` serves the stored snapshot.

Capacity: 50 artifacts × 1 MiB per crew. When the per-crew cap is reached
the oldest entry is evicted and replaced with a tombstone so that a stale
opaque id returns 404 cleanly rather than serving wrong content.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_MAX_ARTIFACTS = 50
_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB


class ArtifactTooLarge(Exception):
    pass


class ArtifactNotText(Exception):
    pass


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    crew_id: str
    title: str
    path_label: str          # display only — never used as a fetch key
    surfacing_teammate: str
    timestamp_utc: float
    body: str


@dataclass
class ArtifactRegistry:
    """In-memory per-crew artifact store.

    Thread safety: asyncio single-threaded; no locks needed.
    """

    crew_id: str
    _records: list[ArtifactRecord] = field(default_factory=list, repr=False)
    _tombstones: set[str] = field(default_factory=set, repr=False)

    def store(
        self,
        *,
        path_label: str,
        title: str,
        surfacing_teammate: str,
        body_bytes: bytes,
    ) -> str:
        """Validate, store, and return a new opaque artifact_id.

        Raises ArtifactTooLarge when body_bytes exceeds 1 MiB.
        Raises ArtifactNotText when body_bytes is not valid UTF-8.
        """
        if len(body_bytes) > _MAX_BODY_BYTES:
            raise ArtifactTooLarge(
                f"artifact body is {len(body_bytes):,} bytes; "
                f"limit is {_MAX_BODY_BYTES:,} bytes (1 MiB)"
            )
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactNotText(
                f"artifact at {path_label!r} is not valid UTF-8: {exc}"
            ) from exc

        artifact_id = uuid.uuid4().hex

        if len(self._records) >= _MAX_ARTIFACTS:
            evicted = self._records.pop(0)
            self._tombstones.add(evicted.artifact_id)

        self._records.append(
            ArtifactRecord(
                artifact_id=artifact_id,
                crew_id=self.crew_id,
                title=title,
                path_label=path_label,
                surfacing_teammate=surfacing_teammate,
                timestamp_utc=time.time(),
                body=body,
            )
        )
        return artifact_id

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        """Return the record or None. None covers both unknown and tombstoned ids."""
        for rec in self._records:
            if rec.artifact_id == artifact_id:
                return rec
        return None

    def is_tombstoned(self, artifact_id: str) -> bool:
        return artifact_id in self._tombstones

    def metadata_list(self) -> list[dict]:
        """Return metadata for all artifacts (no body) newest-first."""
        return [
            {
                "artifact_id": r.artifact_id,
                "crew_id": r.crew_id,
                "title": r.title,
                "path_label": r.path_label,
                "surfacing_teammate": r.surfacing_teammate,
                "timestamp_utc": r.timestamp_utc,
            }
            for r in reversed(self._records)
        ]
