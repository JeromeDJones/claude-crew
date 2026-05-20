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


def memory_dir(
    role: str,
    scope: str = "user",
    project_root: Path | None = None,
) -> Path:
    """Return the role's memory directory for the given scope. Pure — no I/O.

    user    -> Path.home() / ".claude" / "agent-memory" / <role>
    project -> project_root / ".claude" / "agent-memory" / <role>
    local   -> project_root / ".claude" / "agent-memory.local" / <role>

    Raises ValueError if scope is "project" or "local" and project_root is None.
    """
    safe = _sanitize_role(role)
    if scope == "user":
        return Path.home() / ".claude" / "agent-memory" / safe
    elif scope == "project":
        if project_root is None:
            raise ValueError(
                "project_root is required when scope is 'project'"
            )
        return Path(project_root) / ".claude" / "agent-memory" / safe
    elif scope == "local":
        if project_root is None:
            raise ValueError(
                "project_root is required when scope is 'local'"
            )
        return Path(project_root) / ".claude" / "agent-memory.local" / safe
    else:
        raise ValueError(f"Unknown scope: {scope!r}")


def memory_index_path(
    role: str,
    scope: str = "user",
    project_root: Path | None = None,
) -> Path:
    """Return the role's MEMORY.md path. Pure — no I/O."""
    return memory_dir(role, scope=scope, project_root=project_root) / "MEMORY.md"


# ---------------------------------------------------------------------------
# Write guard — protects the lead's project-scoped memory from teammate writes
# ---------------------------------------------------------------------------


def is_lead_project_memory_path(write_path: str) -> bool:
    """True if the path resolves into ~/.claude/projects/*/memory/**.

    Dual-checks the expanded path AND the resolved path so that both
    symlink-in (caller-supplied path is a symlink into the protected zone)
    and symlink-out (a path within the zone is itself a symlink to a safe
    location) bypasses are caught.

    Position-specific check on parts ensures we only block paths shaped as
    <protected_root>/<project_slug>/memory/<...>, not paths that merely
    contain "memory" elsewhere (e.g., ~/.claude/agent-memory/projects/memory/).
    """
    expanded = Path(write_path).expanduser()
    protected_root = Path.home() / ".claude" / "projects"

    candidates: list[Path] = [expanded]
    try:
        candidates.append(expanded.resolve(strict=False))
    except (OSError, RuntimeError):
        pass  # broken symlinks etc. — expanded check still applies

    for candidate in candidates:
        try:
            rel = candidate.relative_to(protected_root)
        except ValueError:
            continue
        # rel.parts must be (<project_slug>, "memory", ...).
        if len(rel.parts) >= 2 and rel.parts[1] == "memory":
            return True
    return False


def write_guard_deny_message(role: str, attempted_path: str) -> str:
    """Return the deny reason text for a blocked write to lead project memory."""
    target = memory_dir(role)
    return (
        f"Write blocked: `{attempted_path}` is under the lead's project memory "
        f"(`~/.claude/projects/*/memory/`), which is the lead session's curated "
        f"memory — not yours. SDK teammates write their memory to "
        f"`{target}/` (your role-scoped agent memory). "
        f"Retry with a path under that directory."
    )


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------


def build_memory_section(
    role: str,
    tools: tuple[str, ...] | None,
    scope: str = "user",
    project_root: Path | None = None,
) -> str:
    """Build the memory addendum for a teammate's system prompt.

    Mirrors the scaffolding the CLI provides to subagents with `memory: user`:
    location guidance, save/skip rules, save format, and the first 200 lines
    of the role's MEMORY.md index. Never raises — I/O errors surface in the
    injected text.

    Picks scope-specific guidance text based on the `scope` kwarg.
    """
    has_write = "Write" in (tools or ())
    directory = memory_dir(role, scope=scope, project_root=project_root)
    index_path = memory_index_path(role, scope=scope, project_root=project_root)
    index_block = _read_index(index_path)

    if scope == "project":
        header = _INSTRUCTIONS_HEADER_PROJECT
        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_PROJECT
        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_PROJECT
    elif scope == "local":
        header = _INSTRUCTIONS_HEADER_LOCAL
        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_LOCAL
        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_LOCAL
    else:
        header = _INSTRUCTIONS_HEADER_USER
        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_USER
        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_USER

    persistence_note = "" if has_write else (
        "\n\n**Note:** The Write tool is not in your tool list — you cannot "
        "persist new memories from this session. You can still read prior "
        "memories from the index above. Ask your operator to add `Write` to "
        "this pack's `tools:` declaration if persistence is needed."
    )

    return (
        f"{SENTINEL_MEMORY}\n\n"
        f"{header.format(role=role, directory=directory, index_path=index_path)}\n\n"
        f"{what_to_save}\n\n"
        f"{what_not_to_save}\n\n"
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


# ---------------------------------------------------------------------------
# User-scope guidance (cross-project, home-based memory)
# ---------------------------------------------------------------------------

_INSTRUCTIONS_HEADER_USER = """\
You have a persistent, file-based memory system at `{directory}`.

Build this memory over time so future invocations of this role can pick up where prior ones left off — but be selective. Memory is for things that wouldn't be obvious from reading the code, the git history, or project documentation. Entries here apply across projects — favor lessons with cross-project value that transfer from one codebase or engagement to another.

The MEMORY.md index at `{index_path}` is your table of contents. The agent in a future session sees this index automatically (first 200 lines). Detail files do not auto-load — they are read on demand when an index entry looks relevant.

**Boundaries.** Project memory at `~/.claude/projects/*/memory/` belongs to the lead session — it is suppressed from your context (via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` at spawn), and a runtime write guard blocks any attempt to write into it. Your memory is exclusively at `{directory}`."""


_INSTRUCTIONS_WHAT_TO_SAVE_USER = """\
### What to save

- **Principles and lessons** that apply across projects, not just this one
- **Hidden constraints** — things that are true but not visible in source: an external API quirk, a workaround for a known bug, a convention that exists for a non-obvious reason
- **Patterns you've internalized** through doing the work — what tends to work, what doesn't, why
- **Failure modes you've hit before** so you can flag them faster next time"""


_INSTRUCTIONS_WHAT_NOT_TO_SAVE_USER = """\
### What NOT to save

- Code conventions, file paths, function locations, or project structure — the agent can grep or use the knowledge graph to find these
- Recent commits or who-changed-what — `git log` and `git blame` are authoritative
- Bug fixes — the fix is in the code; the commit message has the why
- Anything already documented in CLAUDE.md or other authoritative docs
- Per-session task state, plans, or ephemera
- **Code-derivable references.** `type: reference` is for *external* pointers only (issue trackers, dashboards, channels, external API docs) — never for things visible in the code"""


# ---------------------------------------------------------------------------
# Project-scope guidance (project-shared, committed, team-visible)
# ---------------------------------------------------------------------------

_INSTRUCTIONS_HEADER_PROJECT = """\
You have a persistent, file-based memory system at `{directory}`.

This is **project-scoped memory** — it lives inside the project repository at `.claude/agent-memory/` and is committed alongside the code. Entries here are shared across the whole team and visible to every teammate that uses this role on this project. Write only findings that are project-specific, relevant to future contributors, and safe to commit.

The MEMORY.md index at `{index_path}` is your table of contents. The agent in a future session sees this index automatically (first 200 lines). Detail files do not auto-load — they are read on demand when an index entry looks relevant.

**Boundaries.** Project memory at `~/.claude/projects/*/memory/` belongs to the lead session — it is suppressed from your context (via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` at spawn), and a runtime write guard blocks any attempt to write into it. Your memory is exclusively at `{directory}`."""


_INSTRUCTIONS_WHAT_TO_SAVE_PROJECT = """\
### What to save

- **Project-specific constraints and conventions** — things that are true for this project but not derivable from the code or README
- **Integration quirks** — how this project's external dependencies behave in practice (rate limits, undocumented edge cases, workarounds in use)
- **Architectural decisions** and their rationale, when not already captured in ADRs or docs
- **Patterns that apply to this codebase** — what tends to work here, what doesn't, and why"""


_INSTRUCTIONS_WHAT_NOT_TO_SAVE_PROJECT = """\
### What NOT to save

- **Secrets, credentials, tokens, or API keys** — this memory is committed; never write anything that must stay private
- **Machine-specific paths or environment details** — they will be wrong on other contributors' machines
- Code structure, file paths, or function locations visible via `grep` or the knowledge graph
- Per-session task state, plans, or ephemera
- Anything that applies only to your local checkout or environment"""


# ---------------------------------------------------------------------------
# Local-scope guidance (machine-local, non-committed scratch memory)
# ---------------------------------------------------------------------------

_INSTRUCTIONS_HEADER_LOCAL = """\
You have a persistent, file-based memory system at `{directory}`.

This is **local-scoped memory** — it lives at `.claude/agent-memory.local/` inside the project directory and is **not committed to version control**. It is machine-local and not shared with other contributors. Use it for experimental notes, in-progress investigations, and scratch observations that are not yet ready to share — or that should never be shared (e.g., machine-specific paths, personal workflow tweaks).

**Recommended `.gitignore` entry:** add `.claude/agent-memory.local/` to your project's `.gitignore` so this directory is never accidentally committed.

The MEMORY.md index at `{index_path}` is your table of contents. The agent in a future session sees this index automatically (first 200 lines). Detail files do not auto-load — they are read on demand when an index entry looks relevant.

**Boundaries.** Project memory at `~/.claude/projects/*/memory/` belongs to the lead session — it is suppressed from your context (via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` at spawn), and a runtime write guard blocks any attempt to write into it. Your memory is exclusively at `{directory}`."""


_INSTRUCTIONS_WHAT_TO_SAVE_LOCAL = """\
### What to save

- **Experimental notes** — hypotheses being tested, approaches under investigation, observations that haven't solidified into conclusions yet
- **Machine-specific paths or environment quirks** that affect your local workflow but would not apply to others
- **Work-in-progress findings** — notes you want to carry across sessions before deciding whether to promote to project memory
- **Personal workflow shortcuts** specific to your local setup"""


_INSTRUCTIONS_WHAT_NOT_TO_SAVE_LOCAL = """\
### What NOT to save

- Secrets or credentials — even in local memory, avoid writing anything sensitive
- Anything you intend to share with the team — promote those findings to project memory instead (`.claude/agent-memory/`)
- Final, stable conclusions that belong in project or user memory
- Per-session task state or ephemera that won't be useful next session"""


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
