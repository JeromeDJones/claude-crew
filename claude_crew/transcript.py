"""JSONL transcript sink for crew bus events.

One file per crew (= one file per claude-crew process). Each line is a
self-contained JSON object with `v`, `kind`, `ts`, and `crew_id` always
present, plus kind-specific fields. Designed to be `tail -f`-able and
robust to concatenation across crews.

Path resolution (highest to lowest):
1. CLAUDE_CREW_TRANSCRIPT_DIR (explicit override)
2. $XDG_STATE_HOME/claude-crew/transcripts/
3. ~/.local/state/claude-crew/transcripts/

Set CLAUDE_CREW_TRANSCRIPT_DISABLED=1 to opt out entirely (used by tests).

The sink is best-effort: any I/O failure during init disables the sink
and surfaces a stderr message. The broker continues to function.
Writes after init are tolerated similarly — transient failures log to
stderr but do not crash the broker.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def resolve_transcript_dir() -> Path:
    """Return the directory transcripts should live in (no I/O performed)."""
    explicit = os.environ.get("CLAUDE_CREW_TRANSCRIPT_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "claude-crew" / "transcripts"
    return Path.home() / ".local" / "state" / "claude-crew" / "transcripts"


def _utc_filename_stem() -> str:
    """Return YYYYMMDD-HHMMSSZ for now in UTC."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def _stamp(msg: str) -> None:
    sys.stderr.write(f"[claude-crew] {msg}\n")


class TranscriptSink:
    """Writes crew bus events to a JSONL file. Self-disabling on failure."""

    def __init__(self, crew_id: str) -> None:
        self.crew_id = crew_id
        self.path: Path | None = None
        self._fp: TextIO | None = None
        self.disabled = False

        if os.environ.get("CLAUDE_CREW_TRANSCRIPT_DISABLED") == "1":
            self.disabled = True
            return

        try:
            directory = resolve_transcript_dir()
            directory.mkdir(parents=True, exist_ok=True)
            self.path = directory / f"{_utc_filename_stem()}-{crew_id}.jsonl"
            self._fp = self.path.open("a", encoding="utf-8", buffering=1)
        except Exception as exc:
            _stamp(f"transcript disabled: {type(exc).__name__}: {exc}")
            self.disabled = True
            self.path = None
            self._fp = None

    def write_envelope(self, envelope: dict[str, Any]) -> None:
        """Append an envelope record to the transcript."""
        if self.disabled or self._fp is None:
            return
        self._write({
            "v": 1,
            "kind": "envelope",
            "ts": time.time(),
            "crew_id": self.crew_id,
            **envelope,
        })

    def write_lifecycle(self, event: str, fields: dict[str, Any] | None = None) -> None:
        """Append a lifecycle record to the transcript.

        Args:
            event: One of "started", "spawn", "kill", "shutdown".
            fields: Event-specific data (teammate_id, name, role, model, reason, etc.).
        """
        if self.disabled or self._fp is None:
            return
        record: dict[str, Any] = {
            "v": 1,
            "kind": "lifecycle",
            "ts": time.time(),
            "crew_id": self.crew_id,
            "event": event,
        }
        if fields:
            record.update(fields)
        self._write(record)

    def _write(self, obj: dict[str, Any]) -> None:
        try:
            self._fp.write(json.dumps(obj, default=str))
            self._fp.write("\n")
            self._fp.flush()
        except Exception as exc:
            # Transient write failures: log and continue. Don't disable —
            # the next write may succeed (e.g., disk freed up).
            _stamp(f"transcript write failed: {type(exc).__name__}: {exc}")

    def close(self) -> None:
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
