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

import json
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
    "load_plugin_agents",
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
    - ``raw_body`` is the body text without the substrate prefix, for use by
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
    - ``bodies`` maps role keys to the raw body text (no substrate prefix),
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


def _normalize_path(p: Path) -> Path:
    """Canonicalize a path for cross-filesystem comparison.

    Always expands ``~`` and resolves symlinks/``..`` segments via
    ``Path.resolve(strict=False)``. Unlike ``Path.resolve()`` without
    args, this does not require the path to exist — needed because
    ``installed_plugins.json`` may reference paths that were valid
    at install time but moved since (or, for tests, paths that don't
    exist on the host filesystem).

    Used for both the installPath escape check (H1) and the
    projectPath equality check (M1: avoids the case where one side
    resolves and the other doesn't, producing a false-mismatch on
    case-preserving case-insensitive filesystems like macOS).
    """
    return p.expanduser().resolve(strict=False)


def _read_installed_plugins(
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> list[tuple[str, Path]]:
    """Resolve installed-plugin agent dirs from ``~/.claude/plugins/installed_plugins.json``.

    Returns a list of ``(plugin_key, agents_dir)`` tuples sorted by
    ``plugin_key`` for deterministic iteration. ``plugin_key`` is the
    top-level key in ``installed_plugins.json`` (e.g.,
    ``"frontend-design@claude-plugins-official"``); ``agents_dir`` is
    ``<installPath>/agents``.

    Scope filter:

    - ``scope: "user"`` installs are always included (apply across every
      project the lead opens).
    - ``scope: "local"`` installs are included only when their
      ``projectPath`` equals ``project_root`` — local installs apply
      only to the project they were installed in. ``project_root``
      defaults to ``Path.cwd()``.

    Best-effort: missing file, malformed JSON, missing/wrong-typed keys
    all return ``[]`` silently. Mirrors :func:`_load_user_mcp_server_names`
    (Feature #17 SC-7) — load-time validation only checks membership;
    breaking on a malformed user config would block MCP server startup.
    """
    home = home_dir if home_dir is not None else Path.home()
    project = project_root if project_root is not None else Path.cwd()
    cfg_path = home / ".claude" / "plugins" / "installed_plugins.json"
    try:
        text = cfg_path.read_text()
    except (OSError, FileNotFoundError):
        return []
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(cfg, dict):
        return []
    plugins = cfg.get("plugins")
    if not isinstance(plugins, dict):
        return []

    plugins_root = (home / ".claude" / "plugins").expanduser()
    plugins_root_resolved = _normalize_path(plugins_root)
    project_resolved = _normalize_path(project)
    pairs: list[tuple[str, Path]] = []
    for key in sorted(plugins.keys()):
        installs = plugins.get(key)
        if not isinstance(installs, list):
            continue
        for install in installs:
            if not isinstance(install, dict):
                continue
            install_path_raw = install.get("installPath")
            if not isinstance(install_path_raw, str) or not install_path_raw:
                continue
            scope = install.get("scope")
            if scope == "local":
                project_path_raw = install.get("projectPath")
                if not isinstance(project_path_raw, str):
                    continue
                plugin_project = _normalize_path(Path(project_path_raw))
                try:
                    within = project_resolved.is_relative_to(plugin_project)
                except ValueError:
                    within = False
                if not within:
                    logger.warning(
                        "project-scope plugin %r is scoped to %s but claude-crew cwd %s "
                        "is not under that path; agents not loaded",
                        key, plugin_project, project_resolved,
                    )
                    continue
            elif scope != "user":
                # Unknown scope: skip silently. Future-compat — an installer
                # writing an unrecognized scope token shouldn't crash startup.
                continue

            # H1 / sentinel: refuse installPaths that escape ~/.claude/plugins/.
            # The trust model is "Claude Code's installer wrote this file"; a
            # corrupted manifest pointing installPath at /etc or ../.. would
            # otherwise turn an arbitrary on-disk .md file into a spawnable
            # role. discover_dir doesn't exec, but a crafted agent file in an
            # attacker-controlled path becomes a teammate prompt — code-
            # execution-adjacent in an agent context.
            install_path = Path(install_path_raw).expanduser()
            install_resolved = _normalize_path(install_path)
            try:
                escapes = not install_resolved.is_relative_to(plugins_root_resolved)
            except ValueError:
                escapes = True
            if escapes:
                logger.warning(
                    "plugin %r installPath %r is outside %s; skipping",
                    key, install_path_raw, plugins_root_resolved,
                )
                continue
            pairs.append((key, install_resolved / "agents"))
    return pairs


def load_plugin_agents(
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Load agents from every installed Claude Code plugin's ``agents/`` dir.

    Walks ``installed_plugins.json`` (see :func:`_read_installed_plugins`
    for scope filter), then calls :func:`discover_dir` on each install's
    ``agents/`` directory. Plugins are processed in lexicographic
    ``plugin_key`` order; on cross-plugin role-key collision the
    later-sorted plugin wins, with a WARN naming both plugins and the
    losing/winning files.

    Plugin-sourced agents are keyed as ``<plugin_short>:<role>`` to match
    Claude Code's own surface form (the lead's agent list shows plugin
    agents as ``repo-reactor:rr-planner``, not bare ``rr-planner``).
    ``plugin_short`` is the part of ``plugin_key`` before ``@`` — for
    ``"repo-reactor@repo-reactor"`` that's ``"repo-reactor"``. Without
    this prefix, a lead spawning by the namespaced name would get
    ``unknown role`` and two plugins shipping the same bare role name
    would silently collide.

    Returns ``(pack, role_ss, bodies)`` aggregated across all plugins.
    Missing ``installed_plugins.json`` or zero plugins with an
    ``agents/`` dir → ``({}, {}, {})``.
    """
    pairs = _read_installed_plugins(home_dir, project_root)
    pack: dict[str, AgentDefinition] = {}
    role_ss: dict[str, list[str] | None] = {}
    bodies: dict[str, str] = {}
    # H2: track (plugin_key, agents_dir) so the collision WARN distinguishes
    # same-plugin-multiple-installs from cross-plugin collisions. The plugin
    # key alone reads as a meaningless "appears in 'p@m' and 'p@m'" when one
    # plugin has both user and local-scope installs that ship the same role.
    seen_for_key: dict[str, tuple[str, Path]] = {}

    for plugin_key, agents_dir in pairs:
        plugin_short = plugin_key.split("@", 1)[0]
        plugin_pack, plugin_ss, plugin_bodies = discover_dir(agents_dir)
        for role_key, agent in plugin_pack.items():
            namespaced_key = f"{plugin_short}:{role_key}"
            if namespaced_key in pack:
                prior_plugin, prior_dir = seen_for_key[namespaced_key]
                logger.warning(
                    "agent key %r appears in plugin %r at %s and plugin %r at %s; "
                    "%s wins (later in plugin/install iteration order)",
                    namespaced_key, prior_plugin, prior_dir,
                    plugin_key, agents_dir, agents_dir,
                )
            pack[namespaced_key] = agent
            bodies[namespaced_key] = plugin_bodies[role_key]
            if role_key in plugin_ss:
                role_ss[namespaced_key] = plugin_ss[role_key]
            else:
                role_ss.pop(namespaced_key, None)
            seen_for_key[namespaced_key] = (plugin_key, agents_dir)
    return pack, role_ss, bodies


def _discover_skill_names(
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> set[str]:
    """Walk user + project skill dirs, return the set of skill names found.

    A skill is identified by an immediate subdirectory under
    ``<base>/.claude/skills/`` containing a ``SKILL.md`` file. The directory
    name is the skill name. Best-effort: missing dirs are treated as empty
    (A-7 cwd trap and A-8 PermissionError are documented assumptions —
    misconfigured launches surface as spurious WARNs from
    :func:`_warn_unknown_skills`).
    """
    home = home_dir if home_dir is not None else Path.home()
    project = project_root if project_root is not None else Path.cwd()
    names: set[str] = set()
    for base in (home / ".claude" / "skills", project / ".claude" / "skills"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                names.add(child.name)
    return names


def _warn_unknown_skills(
    merged: dict[str, AgentDefinition],
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> None:
    """Emit a WARN for each declared skill that is not on disk at startup.

    Walks each role's ``AgentDefinition.skills`` list; skips ``None`` and the
    ``"all"`` literal (no name list to validate). Compares the listed names
    against :func:`_discover_skill_names`; any unknown name produces a single
    WARN naming the role and the unknown skill names. Does not raise — the
    operator may add the SKILL.md later, or the skill may live in a search
    path we don't traverse (D-4, SC-4).
    """
    discovered = _discover_skill_names(home_dir, project_root)
    for role, agent in merged.items():
        skills = getattr(agent, "skills", None)
        if skills is None or skills == "all":
            continue
        unknown = [s for s in skills if s not in discovered]
        if unknown:
            logger.warning(
                "agent %r declares unknown skills %s — not found in user or "
                "project skill dirs at startup; teammate will fail to invoke "
                "them at runtime",
                role,
                unknown,
            )


def _load_user_mcp_server_names(home_dir: Path | None = None) -> set[str]:
    """Return the set of MCP server names registered in ``~/.claude.json``.

    Best-effort: missing file, malformed JSON, or absent ``mcpServers`` top-level
    key all return the empty set (no exception). Callers use the result to
    validate string-form pack ``mcpServers`` references — failures are
    observability, not blocking, so silent degradation is correct here.

    Feature #17 SC-7. Mirrors :func:`_discover_skill_names` shape (set of names,
    not the configs themselves) — load-time validation only checks membership;
    only the spawn-time path (``sdk_teammate._load_user_mcp_servers``) needs
    the full configs.
    """
    home = home_dir if home_dir is not None else Path.home()
    cfg_path = home / ".claude.json"
    try:
        text = cfg_path.read_text()
    except (OSError, FileNotFoundError):
        return set()
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        return set()
    if not isinstance(cfg, dict):
        return set()
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return set()
    return set(servers.keys())


def _warn_unknown_mcp_servers(
    merged: dict[str, AgentDefinition],
    home_dir: Path | None = None,
) -> None:
    """Emit a WARN for each string-form mcpServers entry not in ``~/.claude.json``.

    Inline-dict entries are self-contained and not warned. Mirrors the
    :func:`_warn_unknown_skills` pattern: load-time best-effort validation,
    no failure on missing/malformed user config (Feature #17 SC-7).
    """
    user_servers = _load_user_mcp_server_names(home_dir)
    for role, agent in merged.items():
        entries = getattr(agent, "mcpServers", None) or []
        unresolved = [
            e for e in entries
            if isinstance(e, str) and e not in user_servers
        ]
        if unresolved:
            logger.warning(
                "agent %r declares unknown mcpServers %s — not registered in "
                "~/.claude.json; teammate will skip them at spawn",
                role, unresolved,
            )


# Optional fields whose AgentDefinition default is None. A drop is detected via
# `is None` on the higher-precedence pack. `description` is required and cannot
# drop. `tools` lives in _COLLECTION_FIELDS instead — its AgentDefinition default
# is `[]` (not None), so shrink-to-empty needs a separate branch (#15 sentinel
# H-2). `disallowedTools` IS in this set (default None) AND in _COLLECTION_FIELDS
# (covers the explicit-empty case `disallowedTools: []`).
_OPTIONAL_AGENTDEF_FIELDS: tuple[str, ...] = (
    "mcpServers", "memory", "skills", "disallowedTools", "permissionMode",
    "maxTurns", "background", "initialPrompt", "effort", "model",
)

# Fields whose AgentDefinition default is an empty collection (not None). Shadow
# detection here checks for non-empty → empty shrinkage, since the existing
# `is None` branch can't see the drop. Only `tools` qualifies — losing the
# tool surface silently is dangerous. `disallowedTools=[]` is operator intent
# (deliberately removing a restriction) and must NOT warn — see #17 test
# `test_explicit_empty_in_higher_does_NOT_warn`.
_COLLECTION_FIELDS: tuple[str, ...] = ("tools",)


def _check_drop(
    layer: str, role: str, lower: AgentDefinition, higher: AgentDefinition
) -> None:
    """Emit a WARN per optional field set on ``lower`` but None on ``higher``,
    plus a WARN per collection field that shrinks to empty across layers."""
    for field in _OPTIONAL_AGENTDEF_FIELDS:
        lower_val = getattr(lower, field, None)
        higher_val = getattr(higher, field, None)
        if lower_val is not None and higher_val is None:
            logger.warning(
                "%s-level agent %r drops optional field %r set by lower-precedence "
                "pack (value=%r); pack-merge is whole-replacement, not field-level",
                layer, role, field, lower_val,
            )
    # Collection-shrinkage: tools/disallowedTools default to [] (not None). A
    # higher pack with tools=[] (or absent → []) silently strips a non-empty
    # tool surface. Only fire when the lower has entries AND the higher is
    # empty/None — partial-subset is operator intent, not a drop.
    for field in _COLLECTION_FIELDS:
        lower_val = getattr(lower, field, None) or ()
        higher_val = getattr(higher, field, None) or ()
        if lower_val and not higher_val:
            logger.warning(
                "%s-level agent %r drops collection field %r set by lower-precedence "
                "pack (lost: %r); pack-merge is whole-replacement, not field-level",
                layer, role, field, list(lower_val),
            )


def _warn_shadow_drop(
    layers: list[tuple[str, dict[str, AgentDefinition] | None]],
) -> None:
    """Emit a WARN when a higher-precedence pack drops an optional field a lower one set.

    Pack-merge does whole-AgentDefinition replacement at the role-key level
    (``__init__.merge_packs``). A higher-precedence shadow that doesn't
    mention ``mcpServers`` silently *clears* the lower value. Pre-existing
    footgun for skills/disallowedTools/permissionMode; Feature #17 closes
    it uniformly across all 9 optional AgentDefinition fields (D-9, D-10).

    ``layers`` is a list of ``(label, pack)`` in *increasing* precedence
    (lowest first). For each role appearing in a higher layer, the check
    runs against whichever lower layer most-recently introduced that role
    — i.e., the layer it actually shadows in the effective merged pack.
    ``None`` packs are treated as empty.
    """
    accumulated: dict[str, AgentDefinition] = {}
    for label, pack in layers:
        if not pack:
            continue
        for role, higher in pack.items():
            if role in accumulated:
                _check_drop(label, role, accumulated[role], higher)
            accumulated[role] = higher


def build_merged_pack(
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Compose default + plugin + user + project agents.

    Effective pack = ``merge_packs(merge_packs(merge_packs(default, plugin), user), project)``.

    The bare-key cascade — project shadows user shadows default — is
    observable via an INFO log on the ``claude_crew.subagents.loader``
    logger so a user debugging "why does my user-level agent behave
    differently here" can see the trail.

    Plugin agents are keyed as ``<plugin_short>:<role>`` to match Claude
    Code's surface form, so they coexist alongside any bare-keyed default
    or user/project agent of the same ``<role>`` rather than shadowing it.
    To override a plugin agent, ship a project/user agent file with the
    same namespaced name (filename stem ``rr-planner`` and frontmatter
    ``name: repo-reactor:rr-planner``).

    The plugin layer aggregates agents from every installed Claude Code
    plugin's ``agents/`` directory (see :func:`load_plugin_agents`).
    User-scope plugin installs are always included; local-scope installs
    only apply to their owning ``projectPath``.

    ``home_dir`` and ``project_root`` default to ``Path.home()`` and
    ``Path.cwd()``. The MCP server resolves these once at startup and
    freezes them; tests may inject tempdirs.

    Returns ``(merged_agents, role_ss, bodies)`` where:

    - ``role_ss`` maps role keys to their ``settingSources`` list.
      Precedence mirrors the agents dict: project > user > plugin > default.
    - ``bodies`` maps role keys to raw body text (no substrate prefix).
      Precedence mirrors the agents dict: project > user > plugin > default.
    """
    default, default_ss, default_bodies = load_default_pack()
    plugin, plugin_ss, plugin_bodies = load_plugin_agents(home_dir, project_root)
    user, user_ss, user_bodies = load_user_agents(home_dir)
    project, project_ss, project_bodies = load_project_agents(project_root)

    # Per-source INFO contract (#15 SC-11): one log per source naming the
    # source label, count, and role keys loaded. Operator's authoritative
    # startup record of which packs the substrate is using.
    home = home_dir if home_dir is not None else Path.home()
    project_dir = project_root if project_root is not None else Path.cwd()
    logger.info(
        "loaded %d pack(s) from bundled (claude_crew/subagents): %s",
        len(default), sorted(default.keys()),
    )
    logger.info(
        "loaded %d pack(s) from plugins (%s/.claude/plugins/cache/.../agents): %s",
        len(plugin), home, sorted(plugin.keys()),
    )
    logger.info(
        "loaded %d pack(s) from user (%s/.claude/agents): %s",
        len(user), home, sorted(user.keys()),
    )
    logger.info(
        "loaded %d pack(s) from project (%s/.claude/agents): %s",
        len(project), project_dir, sorted(project.keys()),
    )

    # Shadowing trail. Plugin keys are namespaced (`<plugin>:<role>`) so
    # they only collide with project/user files that opt into the
    # namespaced name; bare-keyed default/user/project layers cascade
    # among themselves as before.
    for key in plugin:
        if key in default:
            logger.info("agent %r from plugin shadows default pack", key)
    for key in user:
        if key in plugin:
            logger.info("agent %r from user-level shadows plugin", key)
        elif key in default:
            logger.info("agent %r from user-level shadows default pack", key)
    for key in project:
        if key in user:
            logger.info("agent %r from project-level shadows user-level", key)
        elif key in plugin:
            logger.info("agent %r from project-level shadows plugin", key)
        elif key in default:
            logger.info("agent %r from project-level shadows default pack", key)

    role_ss = {**default_ss, **plugin_ss, **user_ss, **project_ss}
    bodies = {**default_bodies, **plugin_bodies, **user_bodies, **project_bodies}
    # Skills cascade via AgentDefinition (unlike settingSources which uses
    # role_ss side-channel — see discover_dir). merge_packs whole-key
    # replacement of AgentDefinition handles list-over-list, "all"-over-list,
    # and list-over-"all" trivially. No role_skills dict needed (D-6).
    merged = merge_packs(merge_packs(merge_packs(default, plugin), user), project)
    _warn_unknown_skills(merged, home_dir, project_root)
    _warn_unknown_mcp_servers(merged, home_dir)
    _warn_shadow_drop([
        ("default", default),
        ("plugin", plugin),
        ("user", user),
        ("project", project),
    ])
    return merged, role_ss, bodies
