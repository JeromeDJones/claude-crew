"""Memory injection for top-level teammates that declare memory: user.

At spawn time, build_memory_section reads the role-scoped memory file (if it
exists) and returns a formatted section string for injection into the system
prompt via build_teammate_prompt. All file I/O lives here; teammate_prompt.py
stays pure string assembly.

Memory path convention (empirically validated 2026-05-04):
  ~/.claude/projects/<encoded-cwd>/memory/<role>.md

The encoded-cwd uses the same convention as Claude Code's auto-memory
subsystem: "-" + cwd.strip("/").replace("/", "-").
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from claude_crew.teammate_prompt import SENTINEL_MEMORY


_MAX_MEMORY_BYTES = 51_200  # 50 KB — generous for structured memory files

_SAFE_ROLE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


# ---------------------------------------------------------------------------
# Path helpers (pure — no I/O)
# ---------------------------------------------------------------------------


def _encode_cwd() -> str:
    return "-" + os.getcwd().strip("/").replace("/", "-")


def _sanitize_role(role: str) -> str:
    if not _SAFE_ROLE_RE.match(role):
        raise ValueError(
            f"role name {role!r} contains characters not allowed in a memory "
            f"file path (allowed: [a-zA-Z0-9_-])"
        )
    return role


def memory_file_path(role: str) -> Path:
    """Return the role-scoped memory file path. Pure — no I/O."""
    safe = _sanitize_role(role)
    return Path.home() / ".claude" / "projects" / _encode_cwd() / "memory" / f"{safe}.md"


def memory_index_path() -> Path:
    """Return the MEMORY.md index path for the current project. Pure — no I/O."""
    return Path.home() / ".claude" / "projects" / _encode_cwd() / "memory" / "MEMORY.md"


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------


def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str:
    """Build the memory addendum section for a teammate's system prompt.

    Reads the role-scoped memory file if it exists. Never raises — I/O errors
    are caught and reflected as a note in the returned section.

    Args:
        role: the teammate's role name (validated via _sanitize_role).
        tools: the teammate's tool list from AgentDefinition. None and ()
               both mean no tools declared.
    """
    has_write = "Write" in (tools or ())
    path = memory_file_path(role)
    index_path = memory_index_path()

    prior = _read_memory_file(path)

    if prior is None:
        prior_block = "*(No prior memory for this role yet.)*"
    elif prior == "":
        prior_block = "*(Memory file exists but could not be read — permission error.)*"
    else:
        prior_block = f"---\n{prior}\n---"

    if has_write:
        write_instructions = (
            "To update your memory: overwrite `{path}` using the Write tool "
            "with the standard Claude Code frontmatter format:\n\n"
            "```\n"
            "---\n"
            f"name: {role} memory\n"
            "description: one-line summary\n"
            "type: user\n"
            "---\n\n"
            "Your memory content here.\n"
            "```\n\n"
            f"To keep the index current: if `{index_path}` does not already "
            f"have an entry for `{path.name}`, append:\n"
            f"`- [{role.capitalize()} memory]({path.name}) — <one-line hook>`"
        ).format(path=path)
    else:
        write_instructions = (
            "**Note:** The Write tool is not in your tool list — memory updates "
            "cannot be persisted from this session. Ask your operator to add "
            "`Write` to this pack's `tools:` declaration if persistence is needed."
        )

    return (
        f"{SENTINEL_MEMORY}\n\n"
        f"**Your memory file:** `{path}`\n"
        f"**Memory index:** `{index_path}`\n\n"
        f"{prior_block}\n\n"
        f"{write_instructions}"
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_memory_file(path: Path) -> str | None:
    """Read memory file content. Returns None if not found, truncated str if over cap."""
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return ""  # empty string signals "unreadable" to caller

    if len(raw) > _MAX_MEMORY_BYTES:
        truncated = raw[:_MAX_MEMORY_BYTES].decode("utf-8", errors="replace")
        return truncated + f"\n\n[... truncated at 50 KB — full file at {path} ...]"

    return raw.decode("utf-8", errors="replace")
