"""Per-instance registry for claude-crew UI discovery.

Each UIServer writes a small JSON file at startup and removes it at shutdown.
Other instances read the directory to discover all live crew UIs on the host.

Path resolution (highest to lowest priority):
1. CLAUDE_CREW_INSTANCE_REGISTRY_DIR (explicit override — used by tests)
2. $XDG_STATE_HOME/claude-crew/instances/
3. ~/.local/state/claude-crew/instances/

File format: <crew_id>.json  →  {"crew_id", "port", "pid", "started_at"}

Write safety: each process owns exactly one file (keyed by crew_id).
Writes use a tmp-file + os.replace (atomic on POSIX).

Liveness: read_all() checks os.kill(pid, 0) for each entry and deletes the
file if the process is dead. Accept-conservative on PermissionError (treat as
alive — the process exists but belongs to another user).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

_logger = logging.getLogger(__name__)


def resolve_registry_dir() -> Path:
    """Return the registry directory path (no I/O performed)."""
    explicit = os.environ.get("CLAUDE_CREW_INSTANCE_REGISTRY_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "claude-crew" / "instances"
    return Path.home() / ".local" / "state" / "claude-crew" / "instances"


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* refers to a live process."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user — treat as alive.
        return True
    except OSError:
        return False


class InstanceRegistry:
    """Registry of live claude-crew UIServer instances on this host."""

    def __init__(self, crew_id: str, port: int) -> None:
        self.crew_id = crew_id
        self.port = port
        self._dir = resolve_registry_dir()

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    def register(self) -> None:
        """Write this instance's entry to the registry (atomic)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "crew_id": self.crew_id,
            "port": self.port,
            "pid": os.getpid(),
            "started_at": time.time(),
        }
        target = self._dir / f"{self.crew_id}.json"
        tmp = self._dir / f"{self.crew_id}.json.tmp"
        try:
            tmp.write_text(json.dumps(entry))
            os.replace(tmp, target)
        except Exception:
            _logger.warning("instance_registry: failed to register %s", self.crew_id, exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def deregister(self) -> None:
        """Remove this instance's entry. Ignores missing-file and permission errors."""
        target = self._dir / f"{self.crew_id}.json"
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            _logger.warning("instance_registry: could not remove %s (permission denied)", target)
        except Exception:
            _logger.warning("instance_registry: failed to deregister %s", self.crew_id, exc_info=True)

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def read_all(self) -> list[dict]:
        """Return all live registry entries (dead entries are deleted)."""
        if not self._dir.exists():
            return []

        live: list[dict] = []
        for path in self._dir.glob("*.json"):
            entry = self._read_entry(path)
            if entry is None:
                continue
            pid = entry.get("pid", -1)
            if not _pid_alive(pid):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            live.append(entry)
        return live

    def _read_entry(self, path: Path) -> dict | None:
        """Parse one registry file. Returns None and deletes the file if corrupt."""
        try:
            text = path.read_text()
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            _logger.warning("instance_registry: corrupt entry %s — deleting", path)
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        except Exception:
            _logger.warning("instance_registry: could not read %s", path, exc_info=True)
            return None
