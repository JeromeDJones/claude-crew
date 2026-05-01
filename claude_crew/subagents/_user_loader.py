"""User- and project-level agent definition loader (Feature #3b).

Walks ``~/.claude/agents/`` and ``<project-root>/.claude/agents/``,
parses each ``*.md`` via the existing
``claude_crew.subagents._loader.parse_pack_file``, and returns a
``dict[str, AgentDefinition]`` ready to feed to ``merge_packs``.

Differences from the bundled-pack loader:

- Forward-compat-silence is replaced with explicit warnings on
  unsupported frontmatter fields (``strict_parse``).
- Per-file failures are isolated: a single bad file emits a warning
  and is skipped; valid sibling files still load.
- Pathological inputs are bounded: per-file size cap and per-directory
  file-count cap, so a 50 MB ``.md`` can't block MCP server startup.

See ``doc/features/FEATURE-agent-definition-loader.md`` Phase 2 design
pin-downs for the rationale on each cap, the warning channel, and
intra-directory key-collision behavior.
"""

from __future__ import annotations

import logging
from pathlib import Path

from claude_agent_sdk.types import AgentDefinition

from claude_crew.subagents import load_default_pack, merge_packs
from claude_crew.subagents._loader import (
    PackFrontmatter,
    PackLoadError,
    _split_frontmatter,
    parse_pack_text,
)

__all__ = [
    "build_merged_pack",
    "discover_dir",
    "load_project_agents",
    "load_user_agents",
    "strict_parse",
]


logger = logging.getLogger("claude_crew.subagents.loader")


_MAX_FILE_BYTES = 256 * 1024  # 256 KB per file
_MAX_FILES_PER_DIR = 100
_README = "README.md"

# Frontmatter keys the bundled parser accepts. Derived from
# ``PackFrontmatter`` so adding a field there can't drift this set out
# of date — silently warn-on-valid would be a paper cut.
_ACCEPTED_FRONTMATTER_KEYS = frozenset(PackFrontmatter.__dataclass_fields__)


def strict_parse(path: Path) -> tuple[str, AgentDefinition, list[str] | None, str]:
    """Parse a user/project agent file, warning on unsupported frontmatter keys.

    Wraps ``parse_pack_text``. Diffs the frontmatter dict against
    ``_ACCEPTED_FRONTMATTER_KEYS``; any extra key (typo'd ``descrption``,
    forward-compat ``setting_sources``, etc.) is logged at WARNING via
    the ``claude_crew.subagents.loader`` logger. The agent still loads
    using the supported fields.

    Returns ``(key, agent, setting_sources, raw_body)`` where:

    - ``setting_sources`` is ``None`` if the frontmatter does not declare
      ``settingSources``.
    - ``raw_body`` is the body text without the leaf suffix, for use by
      the teammate spawn path.

    Raises:
        PackLoadError: if the file is missing required fields, has
            invalid YAML, or is otherwise unparseable. Callers in
            this module catch and isolate per-file failures.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise PackLoadError(f"cannot read pack file {path}: {exc}") from exc
    fm_dict, _ = _split_frontmatter(text, path)
    extras = sorted(set(fm_dict) - _ACCEPTED_FRONTMATTER_KEYS)
    if extras:
        logger.warning(
            "agent file %s has unsupported frontmatter key(s) %s; dropping",
            path,
            extras,
        )
    key, agent, fm, body = parse_pack_text(text, path)
    return key, agent, fm.settingSources, body


def discover_dir(
    directory: Path,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Walk a single directory and return its agents as a dict.

    Behavior:
    - Missing directory → ``({}, {}, {})`` silently. (User without
      ``~/.claude/agents/`` sees nothing.)
    - Non-recursive ``*.md`` glob, lowercase extension only,
      ``README.md`` excluded.
    - Results sorted alphabetically before parsing for determinism.
    - Per-file size cap (``_MAX_FILE_BYTES``): oversize files emit a
      warning and are skipped.
    - Per-directory file-count cap (``_MAX_FILES_PER_DIR``): more than
      the cap → take the first N sorted, warn-and-skip the rest.
    - Per-file parse errors emit a warning and are skipped; sibling
      files still load.
    - Intra-directory kebab-key collision (e.g.,
      ``general_purpose.md`` and ``general-purpose.md`` in the same
      dir): warn naming both files and which one wins (alphabetically
      later).

    Returns ``(pack, role_ss, bodies)`` where:

    - ``role_ss`` maps role keys to their ``settingSources`` list.
      Keys without ``settingSources`` are absent from ``role_ss``.
    - ``bodies`` maps role keys to the raw body text (no leaf suffix),
      for use by the teammate spawn path.
    """
    if not directory.is_dir():
        return {}, {}, {}

    candidates = sorted(p for p in directory.glob("*.md") if p.name != _README)
    if len(candidates) > _MAX_FILES_PER_DIR:
        logger.warning(
            "agent directory %s contains %d files; taking first %d (alphabetical), skipping rest",
            directory,
            len(candidates),
            _MAX_FILES_PER_DIR,
        )
        candidates = candidates[:_MAX_FILES_PER_DIR]

    pack: dict[str, AgentDefinition] = {}
    role_ss: dict[str, list[str] | None] = {}
    bodies: dict[str, str] = {}
    seen_path_for_key: dict[str, Path] = {}

    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("agent file %s could not be stat'd (%s); skipping", path, exc)
            continue
        if size > _MAX_FILE_BYTES:
            logger.warning(
                "agent file %s is %d bytes (cap %d); skipping",
                path,
                size,
                _MAX_FILE_BYTES,
            )
            continue

        try:
            key, agent, ss, body = strict_parse(path)
        except PackLoadError as exc:
            logger.warning("agent file %s could not be loaded: %s; skipping", path, exc)
            continue
        except Exception as exc:  # defensive: yaml.safe_load can surface odd errors
            logger.warning(
                "agent file %s raised %s: %s; skipping",
                path,
                type(exc).__name__,
                exc,
            )
            continue

        if key in pack:
            prior = seen_path_for_key[key]
            logger.warning(
                "agent key %r appears in both %s and %s; %s wins (alphabetically later)",
                key,
                prior,
                path,
                path,
            )
        pack[key] = agent
        bodies[key] = body
        # Keep role_ss in sync with pack — if the winning file has no settingSources,
        # clear any stale entry from a previously-seen file for the same key.
        if ss is not None:
            role_ss[key] = ss
        else:
            role_ss.pop(key, None)
        seen_path_for_key[key] = path

    return pack, role_ss, bodies


def load_user_agents(
    home_dir: Path | None = None,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Load agents from ``<home_dir>/.claude/agents/``.

    ``home_dir`` defaults to the user's home directory. Made injectable
    for tests (SC-9) so fixtures can plant agents in a tempdir without
    touching the real ``~/.claude/agents/``.

    Returns ``(pack, role_ss, bodies)`` — see ``discover_dir`` for details.
    """
    home = home_dir if home_dir is not None else Path.home()
    return discover_dir(home / ".claude" / "agents")


def load_project_agents(
    project_root: Path | None = None,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Load agents from ``<project_root>/.claude/agents/``.

    ``project_root`` defaults to the current working directory at the
    time of the call. The MCP server resolves this once at startup and
    freezes it for the process lifetime; per-spawn resolution is a
    footgun (a teammate's pack would silently change with ``cwd``).

    Returns ``(pack, role_ss, bodies)`` — see ``discover_dir`` for details.
    """
    root = project_root if project_root is not None else Path.cwd()
    return discover_dir(root / ".claude" / "agents")


def build_merged_pack(
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Compose default + user + project agents in precedence order.

    Effective pack = ``merge_packs(merge_packs(default, user), project)``.
    Project shadows user shadows default. Shadowing is observable via an
    INFO log on the ``claude_crew.subagents.loader`` logger so a user
    debugging "why does my user-level agent behave differently here"
    can see the trail.

    ``home_dir`` and ``project_root`` default to ``Path.home()`` and
    ``Path.cwd()``. The MCP server resolves these once at startup and
    freezes them; tests may inject tempdirs.

    Returns ``(merged_agents, role_ss, bodies)`` where:

    - ``role_ss`` maps role keys to their ``settingSources`` list.
      Precedence mirrors the agents dict: project > user > default.
    - ``bodies`` maps role keys to raw body text (no leaf suffix).
      Precedence mirrors the agents dict: project > user > default.
    """
    default, default_ss, default_bodies = load_default_pack()
    user, user_ss, user_bodies = load_user_agents(home_dir)
    project, project_ss, project_bodies = load_project_agents(project_root)

    for key in user:
        if key in default:
            logger.info("agent %r from user-level shadows default pack", key)
    for key in project:
        if key in user:
            logger.info("agent %r from project-level shadows user-level", key)
        elif key in default:
            logger.info("agent %r from project-level shadows default pack", key)

    role_ss = {**default_ss, **user_ss, **project_ss}
    bodies = {**default_bodies, **user_bodies, **project_bodies}
    return merge_packs(merge_packs(default, user), project), role_ss, bodies
