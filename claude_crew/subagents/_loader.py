"""Markdown + YAML-frontmatter parser for the default subagent pack.

One `.md` file → one `(key, AgentDefinition)` pair. The frontmatter
declares structural fields (model, tools, budgets); the body is the
system prompt.

Used by the orchestration layer in `claude_crew.subagents.__init__`
and re-usable by Feature #3b's user-defined-agent loader.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from claude_agent_sdk.types import AgentDefinition

logger = logging.getLogger(__name__)

# Per Claude Code's agent spec: lowercase, hyphens. Allow digits (operators
# might want `2fa-helper` etc.). Type-check happens before regex (#15 sentinel
# H-1 — `name: 42` parses as YAML int, str(42) would otherwise pass regex).
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PackLoadError(Exception):
    """Raised when a subagent pack file is missing, malformed, or incomplete."""


def _coerce_str_or_list(value: Any, field_name: str, path: Path) -> list[str]:
    """Coerce YAML string-or-list polymorphism into a list of stripped strings.

    Closes a latent bug at the tools/disallowedTools call sites where bare
    list(d["tools"]) silently iterated a string into characters when the
    operator wrote `tools: Read` (no comma). Per Claude Code's agent file
    spec, both fields accept either a comma-separated string or a YAML list.

    Strict on element types: list elements must be strings. Coercing
    [None, "Read"] silently to ["None", "Read"] would produce bogus tool
    names that fail at spawn time with cryptic errors.
    """
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for s in value:
            if not isinstance(s, str):
                raise PackLoadError(
                    f"pack file {path}: {field_name} list element must be a string, "
                    f"got {type(s).__name__}: {s!r}"
                )
            stripped = s.strip()
            if stripped:
                result.append(stripped)
        return result
    raise PackLoadError(
        f"pack file {path}: {field_name} expected string or list, "
        f"got {type(value).__name__}"
    )


@dataclass(frozen=True)
class PackFrontmatter:
    """Validated frontmatter fields for a pack file.

    Required: description.
    Optional: model, tools, effort, maxTurns, initialPrompt, background, skills,
              permissionMode, disallowedTools, settingSources, mcpServers, memory.

    `model` and `tools` were required pre-#15. Per Claude Code's agent file
    spec, both are optional; absence yields `model=None` (SDK applies its own
    default) and `tools=()` (empty tuple, NOT None — claude-crew teammates
    have no parent to inherit from, and the SDK's "inherits all if omitted"
    semantic would silently grant full tool access). Empty tuple is
    safe-by-default.

    The ``mcpServers`` field accepts a list of (str | dict) entries — string
    entries reference servers in ``~/.claude.json``; dict entries are inline
    ``McpServerConfig`` (type ∈ {stdio, sse, http}; ``sdk``-type rejected at
    pack-load because it requires an in-process Python callable). For inline
    dicts, a ``name`` key on the entry sets the dict-form key in
    ``ClaudeAgentOptions.mcp_servers`` (see Feature #17 D-7).

    On the teammate path, malformed inline dicts (e.g., ``stdio`` without
    ``command``) cause a CLI subprocess crash that surfaces via the existing
    teammate death path — shallow validation here trades depth for not
    duplicating SDK schema (D-12).

    The ``memory`` field is honored on the subagent path (rides the SDK
    initialize message via AgentDefinition serialization) but has no carrier
    on ``ClaudeAgentOptions`` — declaring it on a role spawned as a top-level
    teammate emits a WARN at spawn time (D-8).

    Unknown fields are ignored (forward-compat).
    """

    description: str
    name: str | None = None
    model: str | None = None
    tools: tuple[str, ...] = field(default_factory=tuple)
    color: str | None = None
    effort: str | None = None
    maxTurns: int | None = None
    initialPrompt: str | None = None
    background: bool | None = None
    skills: tuple[str, ...] | Literal["all"] | None = None
    permissionMode: str | None = None
    disallowedTools: tuple[str, ...] | None = None
    settingSources: list[str] | None = None
    mcpServers: tuple[str | dict[str, Any], ...] | None = None
    memory: Literal["user", "project", "local"] | None = None


_REQUIRED = ("description",)
_OPTIONAL = ("name", "model", "tools", "color", "effort", "maxTurns",
             "initialPrompt", "background", "skills", "permissionMode",
             "disallowedTools", "settingSources", "mcpServers", "memory")

_VALID_PERMISSION_MODES = frozenset(
    {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"}
)
_VALID_MEMORY_MODES = frozenset({"user", "project", "local"})
# `sdk`-type is rejected in pack form: McpSdkServerConfig requires an
# in-process Python `instance` callable that cannot survive YAML serialization.
_VALID_MCP_DICT_TYPES = frozenset({"stdio", "sse", "http"})

# Prepended to AgentDefinition.prompt for every subagent. Substrate-context
# framing leads with what claude-crew is, what the dispatch model is, and what
# the agent's responsibilities are. The raw body (without this prefix)
# is returned as the 4th element of parse_pack_text so the teammate spawn path
# can build its own substrate-context prompt without the subagent-specific framing.
#
# Model-agnostic by design (#15 X.2): post-#15 model is optional and resolves
# at the SDK boundary. Guidance text must NOT mention specific models — it
# would be inaccurate for inherit-default agents.
SUBSTRATE_SUBAGENT_GUIDANCE = """\
## Substrate context

You are operating as a subagent within claude-crew, a multi-agent substrate
coordinated via an MCP server. The crew lead has dispatched you to complete a
focused task. Your role definition fixes your tool surface. If your tools
include Agent or Task, you may spawn specialist subagents one level deeper to
complete your work — delegate freely when it helps. Complete the assigned task,
report your findings clearly, and exit.

---

"""


def build_subagent_prompt(body: str) -> str:
    """Compose a subagent's system prompt: substrate guidance, then role body.

    Asymmetric with build_teammate_prompt (which retains body-first ordering
    for peer-list injection — the addendum is computed from sibling agents
    and must land after the body it may reference). The subagent path leads
    with substrate framing because subagents have no peer context to inject;
    the framing is foundational.
    """
    return SUBSTRATE_SUBAGENT_GUIDANCE + body.rstrip()
_VALID_SETTING_SOURCES = frozenset({"user", "project", "local"})


def parse_pack_file(path: Path) -> tuple[str, AgentDefinition, PackFrontmatter, str]:
    """Parse one markdown file with YAML frontmatter into an AgentDefinition.

    Returns ``(key, agent_definition, frontmatter, raw_body)`` where:

    - ``key`` is the file stem with underscores converted to hyphens
      (e.g., ``general_purpose.md`` → ``"general-purpose"``).
    - ``agent_definition.prompt`` is ``SUBSTRATE_SUBAGENT_GUIDANCE +
      raw_body.rstrip()`` (subagent / Task context — substrate framing leads).
    - ``raw_body`` is the body without the substrate prefix (teammate context).

    Raises:
        PackLoadError: if the file is missing, lacks frontmatter, omits a
            required field, or has an empty body.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise PackLoadError(f"cannot read pack file {path}: {exc}") from exc
    return parse_pack_text(text, path)


def parse_pack_text(text: str, path: Path) -> tuple[str, AgentDefinition, PackFrontmatter, str]:
    """Parse already-read pack text into ``(key, AgentDefinition, PackFrontmatter, raw_body)``.

    Lets callers that already have the file contents (e.g., the user-
    loader's ``strict_parse``, which inspects frontmatter before
    delegating) avoid a second read. ``path`` is used for the kebab-key
    and for error messages. The ``PackFrontmatter`` is returned as the
    third element so callers can access fields (e.g., ``settingSources``)
    that do not map onto ``AgentDefinition``.

    The fourth element ``raw_body`` is the body text without the substrate
    framing. The teammate spawn path uses this to build a teammate-context
    system prompt via ``teammate_prompt.build_teammate_prompt``.
    ``AgentDefinition.prompt`` always leads with ``SUBSTRATE_SUBAGENT_GUIDANCE``
    — it is the source of truth for subagent (Task) invocations.
    """
    fm_dict, body = _split_frontmatter(text, path)
    fm = _validate_frontmatter(fm_dict, path)

    if not body.strip():
        raise PackLoadError(f"pack file {path} has empty body")

    # Canonical key per Claude Code spec: `name:` if declared, else file-stem
    # fallback (with underscore→hyphen normalization preserved for
    # backward compat with bundled-pack files like general_purpose.md).
    stem_key = path.stem.replace("_", "-")
    key = fm.name if fm.name is not None else stem_key

    # Transition INFO when `name:` and stem-derived key diverge (D-7). One
    # INFO per divergent pack at load-time so operators see the canonical
    # role they'll spawn under.
    if fm.name is not None and fm.name != stem_key:
        logger.info(
            "pack %s declares name '%s' (file stem: '%s') — canonical key is '%s'",
            path, fm.name, stem_key, fm.name,
        )

    # X.3: no-tools INFO fires only when `tools:` is fully absent — distinguishes
    # "operator forgot" (INFO) from "operator chose empty" (silent).
    if "tools" not in fm_dict:
        logger.info(
            "agent %r has no tools declared — teammate will spawn but cannot invoke tools",
            key,
        )
    agent_kwargs: dict[str, Any] = {
        "description": fm.description,
        "prompt": build_subagent_prompt(body),
        "tools": list(fm.tools),
        "model": fm.model,
        "effort": fm.effort,
        "maxTurns": fm.maxTurns,
        "initialPrompt": fm.initialPrompt,
        "background": fm.background,
    }
    if fm.skills is not None:
        if isinstance(fm.skills, str):
            # "all" passes through as the SDK Literal (D-1)
            agent_kwargs["skills"] = fm.skills
        elif fm.skills:
            # non-empty tuple → list for AgentDefinition
            agent_kwargs["skills"] = list(fm.skills)
        # empty tuple → no-op (D-2): leave AgentDefinition.skills at default None
    if fm.permissionMode is not None:
        agent_kwargs["permissionMode"] = fm.permissionMode
    if fm.disallowedTools is not None:
        agent_kwargs["disallowedTools"] = list(fm.disallowedTools)
    if fm.mcpServers is not None:
        agent_kwargs["mcpServers"] = list(fm.mcpServers)
    if fm.memory is not None:
        agent_kwargs["memory"] = fm.memory

    agent = AgentDefinition(**agent_kwargs)
    return key, agent, fm, body


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    """Split a YAML-frontmatter markdown file into (dict, body).

    Frontmatter is delimited by ``---`` on its own line at the top of the
    file and a closing ``---`` on its own line. Anything after the closing
    delimiter is the body (returned verbatim, including leading whitespace).
    """
    if not text.startswith("---\n"):
        raise PackLoadError(
            f"pack file {path} does not start with YAML frontmatter delimiter '---'"
        )
    # First "---\n" consumed; find the closer.
    rest = text[len("---\n"):]
    closer_idx = rest.find("\n---\n")
    if closer_idx == -1:
        raise PackLoadError(f"pack file {path} has no closing '---' delimiter")
    fm_text = rest[:closer_idx]
    body = rest[closer_idx + len("\n---\n"):]

    try:
        fm_dict = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise PackLoadError(f"pack file {path} has invalid YAML: {exc}") from exc
    if not isinstance(fm_dict, dict):
        raise PackLoadError(f"pack file {path} frontmatter is not a YAML mapping")
    return fm_dict, body


def _validate_frontmatter(d: dict[str, Any], path: Path) -> PackFrontmatter:
    for fld in _REQUIRED:
        if fld not in d:
            raise PackLoadError(
                f"pack file {path} missing required frontmatter field '{fld}'"
            )

    # name: optional. Two-stage validation per #15 sentinel H-1 + M-4:
    #   1. type-check (catches YAML int, bool, list, dict before regex)
    #   2. regex (lowercase, hyphens, digits per Claude Code spec)
    # `name: ` with no value parses to YAML None → treated as absent
    # (canonical key falls back to stem).
    raw_name = d.get("name")
    if raw_name is not None:
        if not isinstance(raw_name, str):
            raise PackLoadError(
                f"pack file {path}: name must be a string, "
                f"got {type(raw_name).__name__}"
            )
        if not _VALID_NAME_RE.match(raw_name):
            raise PackLoadError(
                f"pack file {path}: invalid name '{raw_name}': "
                f"must match [a-z0-9][a-z0-9-]*"
            )

    pm = d.get("permissionMode")
    if pm is not None and pm not in _VALID_PERMISSION_MODES:
        raise PackLoadError(
            f"pack file {path}: unknown permissionMode {pm!r}; "
            f"valid values: {sorted(_VALID_PERMISSION_MODES)}"
        )

    ss = d.get("settingSources")
    if ss is not None:
        if not isinstance(ss, list):
            raise PackLoadError(
                f"pack file {path}: settingSources must be a list; "
                f"got {type(ss).__name__}"
            )
        for item in ss:
            if item not in _VALID_SETTING_SOURCES:
                raise PackLoadError(
                    f"pack file {path}: unknown settingSources item {item!r}; "
                    f"valid values: {sorted(_VALID_SETTING_SOURCES)}"
                )

    # skills: tuple[str, ...] | Literal["all"] | None — three accepted shapes (D-1).
    raw_skills = d.get("skills")
    parsed_skills: tuple[str, ...] | Literal["all"] | None
    if raw_skills is None:
        parsed_skills = None
    elif isinstance(raw_skills, str):
        if raw_skills != "all":
            raise PackLoadError(
                f"pack file {path}: skills string value must be 'all'; "
                f"got {raw_skills!r}"
            )
        parsed_skills = "all"
    elif isinstance(raw_skills, list):
        for s in raw_skills:
            if not isinstance(s, str):
                raise PackLoadError(
                    f"pack file {path}: skills list elements must be strings; "
                    f"got element {s!r} of type {type(s).__name__}"
                )
        parsed_skills = tuple(raw_skills)  # may be empty (D-2 no-op)
    else:
        raise PackLoadError(
            f"pack file {path}: skills must be a list of strings or the string 'all'; "
            f"got {type(raw_skills).__name__}"
        )

    # SC-3: reject the silent-misconfig where active skills are declared with an
    # explicit empty settingSources. Skills would be sent on the wire as
    # --allowedTools Skill(name) but SKILL.md discovery would be blocked, so
    # the teammate would silently have nothing to invoke. None is OK (SDK
    # auto-injects ['user','project']); empty tuple skills is a no-op (D-2)
    # and pairs cleanly with empty settingSources.
    skills_active = parsed_skills is not None and parsed_skills != ()
    ss_explicit_empty = ss is not None and len(ss) == 0
    if skills_active and ss_explicit_empty:
        raise PackLoadError(
            f"pack file {path}: declaring skills with settingSources=[] (explicit "
            f"empty list) is contradictory — skills are sent on the wire but "
            f"SKILL.md discovery is blocked. Either omit settingSources (SDK "
            f"auto-injects ['user','project']), or set it explicitly."
        )

    # memory: 3-string enum, mirrors permissionMode pattern (Feature #17 D-2).
    raw_memory = d.get("memory")
    if raw_memory is not None and raw_memory not in _VALID_MEMORY_MODES:
        raise PackLoadError(
            f"pack file {path}: memory {raw_memory!r} is invalid; "
            f"accepted: {sorted(_VALID_MEMORY_MODES)}"
        )

    # mcpServers: list of (str | dict-with-known-type), shallow validation
    # only. `sdk`-type is rejected in pack form (Feature #17 D-7) — it
    # requires an in-process Python callable that cannot survive YAML.
    raw_mcp = d.get("mcpServers")
    parsed_mcp: tuple[str | dict[str, Any], ...] | None
    if raw_mcp is None:
        parsed_mcp = None
    else:
        if not isinstance(raw_mcp, list):
            raise PackLoadError(
                f"pack file {path}: mcpServers must be a list; "
                f"got {type(raw_mcp).__name__}"
            )
        for i, entry in enumerate(raw_mcp):
            if isinstance(entry, str):
                continue
            if isinstance(entry, dict):
                t = entry.get("type")
                if t == "sdk":
                    raise PackLoadError(
                        f"pack file {path}: mcpServers[{i}] type='sdk' is not "
                        f"supported in pack form (requires in-process instance); "
                        f"register the server in ~/.claude.json and reference by name"
                    )
                if t not in _VALID_MCP_DICT_TYPES:
                    raise PackLoadError(
                        f"pack file {path}: mcpServers[{i}] dict has type={t!r}; "
                        f"accepted: {sorted(_VALID_MCP_DICT_TYPES)}"
                    )
                continue
            raise PackLoadError(
                f"pack file {path}: mcpServers[{i}] must be str or dict; "
                f"got {type(entry).__name__}"
            )
        parsed_mcp = tuple(raw_mcp)

    return PackFrontmatter(
        description=str(d["description"]),
        name=raw_name if raw_name is not None else None,
        model=str(d["model"]) if d.get("model") is not None else None,
        tools=tuple(_coerce_str_or_list(d["tools"], "tools", path)) if "tools" in d else (),
        color=str(d["color"]) if d.get("color") is not None else None,
        effort=str(d["effort"]) if d.get("effort") is not None else None,
        maxTurns=int(d["maxTurns"]) if d.get("maxTurns") is not None else None,
        initialPrompt=(
            str(d["initialPrompt"]) if d.get("initialPrompt") is not None else None
        ),
        background=bool(d["background"]) if d.get("background") is not None else None,
        skills=parsed_skills,
        permissionMode=pm,
        disallowedTools=(
            tuple(_coerce_str_or_list(d["disallowedTools"], "disallowedTools", path))
            if d.get("disallowedTools") is not None else None
        ),
        settingSources=list(ss) if ss is not None else None,
        mcpServers=parsed_mcp,
        memory=raw_memory,
    )
