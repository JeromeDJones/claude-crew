"""Memory injection for top-level teammates that declare memory: user.

The CLI provides memory scaffolding to subagents with `memory: user` natively
— it tells them where to read/write and how to organize entries. The SDK does
not. This module fills that gap by injecting equivalent guidance into the
SDK-spawned teammate's system prompt at spawn time, so the same role behaves
identically whether dispatched as a Task subagent (CLI) or spawned as a
top-level teammate (claude-crew SDK).

Memory location: `~/.claude/agent-memory/<role>/`
  - One directory per role, user-scoped (NOT project-scoped)
  - Topic-named detail files (the agent picks filenames)
  - Per-role MEMORY.md index inside the directory
  - Index auto-loads in CLI subagent context; we mirror that by injecting
    its first 200 lines at spawn time

Empirically validated 2026-05-04 by spawning a CLI sentinel subagent with no
path hints and observing where it wrote — `~/.claude/agent-memory/sentinel/`.
"""

from __future__ import annotations

import re
from pathlib import Path

from claude_crew.teammate_prompt import SENTINEL_MEMORY


_MAX_INDEX_LINES = 200  # mirror the CLI's MEMORY.md auto-load truncation

_SAFE_ROLE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


# ---------------------------------------------------------------------------
# Path helpers (pure — no I/O)
# ---------------------------------------------------------------------------


def _sanitize_role(role: str) -> str:
    if not _SAFE_ROLE_RE.match(role):
        raise ValueError(
            f"role name {role!r} contains characters not allowed in a memory "
            f"directory path (allowed: [a-zA-Z0-9_-])"
        )
    return role


def memory_dir(role: str) -> Path:
    """Return the role's memory directory. Pure — no I/O."""
    safe = _sanitize_role(role)
    return Path.home() / ".claude" / "agent-memory" / safe


def memory_index_path(role: str) -> Path:
    """Return the role's MEMORY.md path. Pure — no I/O."""
    return memory_dir(role) / "MEMORY.md"


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------


def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str:
    """Build the memory addendum for a teammate's system prompt.

    Mirrors the scaffolding the CLI provides to subagents with `memory: user`:
    location guidance, save/skip rules, save format, and the first 200 lines
    of the role's MEMORY.md index. Never raises — I/O errors surface in the
    injected text.
    """
    has_write = "Write" in (tools or ())
    directory = memory_dir(role)
    index_path = memory_index_path(role)
    index_block = _read_index(index_path)

    persistence_note = "" if has_write else (
        "\n\n**Note:** The Write tool is not in your tool list — you cannot "
        "persist new memories from this session. You can still read prior "
        "memories from the index above. Ask your operator to add `Write` to "
        "this pack's `tools:` declaration if persistence is needed."
    )

    return (
        f"{SENTINEL_MEMORY}\n\n"
        f"{_INSTRUCTIONS_HEADER.format(role=role, directory=directory, index_path=index_path)}\n\n"
        f"{_INSTRUCTIONS_WHAT_TO_SAVE}\n\n"
        f"{_INSTRUCTIONS_WHAT_NOT_TO_SAVE}\n\n"
        f"{_INSTRUCTIONS_WHEN_NOT_TO_SAVE}\n\n"
        f"{_INSTRUCTIONS_HOW_TO_SAVE.format(directory=directory, index_path=index_path)}\n\n"
        f"### Your prior memories (from MEMORY.md)\n\n"
        f"{index_block}"
        f"{persistence_note}"
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_index(path: Path) -> str:
    """Read first 200 lines of MEMORY.md. Returns 'no prior memories' note if absent."""
    if not path.exists():
        return "*(No prior memories yet. Create the directory and MEMORY.md when you write your first entry.)*"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"*(Memory index exists but could not be read: {exc})*"
    lines = text.splitlines()
    if len(lines) > _MAX_INDEX_LINES:
        truncated = "\n".join(lines[:_MAX_INDEX_LINES])
        return f"{truncated}\n\n[... truncated at {_MAX_INDEX_LINES} lines — read the full file at {path} ...]"
    return text


# ---------------------------------------------------------------------------
# Instruction text — mirrors the CLI subagent memory scaffolding
# ---------------------------------------------------------------------------


_INSTRUCTIONS_HEADER = """\
You have a persistent, file-based memory system at `{directory}`.

Build this memory over time so future invocations of this role can pick up where prior ones left off — but be selective. Memory is for things that wouldn't be obvious from reading the code, the git history, or project documentation. The same role may be invoked across many projects; favor entries with cross-project value.

The MEMORY.md index at `{index_path}` is your table of contents. The agent in a future session sees this index automatically (first 200 lines). Detail files do not auto-load — they are read on demand when an index entry looks relevant."""


_INSTRUCTIONS_WHAT_TO_SAVE = """\
### What to save

- **Principles and lessons** that apply across projects, not just this one
- **Hidden constraints** — things that are true but not visible in source: an external API quirk, a workaround for a known bug, a convention that exists for a non-obvious reason
- **Patterns you've internalized** through doing the work — what tends to work, what doesn't, why
- **Failure modes you've hit before** so you can flag them faster next time"""


_INSTRUCTIONS_WHAT_NOT_TO_SAVE = """\
### What NOT to save

- Code conventions, file paths, function locations, or project structure — the agent can grep or use the knowledge graph to find these
- Recent commits or who-changed-what — `git log` and `git blame` are authoritative
- Bug fixes — the fix is in the code; the commit message has the why
- Anything already documented in CLAUDE.md or other authoritative docs
- Per-session task state, plans, or ephemera
- **Code-derivable references.** `type: reference` is for *external* pointers only (issue trackers, dashboards, channels, external API docs) — never for things visible in the code"""


_INSTRUCTIONS_WHEN_NOT_TO_SAVE = """\
### When NOT to save

Not every session ends with something worth remembering. Default to no. The bar: would a future invocation of this role be meaningfully better at its job because of this entry? If you can't answer yes with a specific reason, don't write it."""


_INSTRUCTIONS_HOW_TO_SAVE = """\
### How to save

Two-step process:

**Step 1** — write the memory to its own file at `{directory}/<descriptive-name>.md`:

```
---
name: short title
description: one-line hook — used to decide relevance in future invocations
type: principle | pattern | gotcha | reference
---

The memory itself. Lead with the rule or finding. Then **Why:** (reason — often an incident or evidence) and **How to apply:** (when this kicks in, what to do). The why lets future-you judge edge cases.
```

**Step 2** — add a one-line pointer to `{index_path}`:

`- [Title](filename.md) — one-line hook`

If the directory or MEMORY.md does not exist yet, create them — your first write bootstraps the role's memory system."""
