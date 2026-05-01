"""Markdown + YAML-frontmatter parser for the default subagent pack.

One `.md` file → one `(key, AgentDefinition)` pair. The frontmatter
declares structural fields (model, tools, budgets); the body is the
system prompt.

Used by the orchestration layer in `claude_crew.subagents.__init__`
and re-usable by Feature #3b's user-defined-agent loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from claude_agent_sdk.types import AgentDefinition


class PackLoadError(Exception):
    """Raised when a subagent pack file is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class PackFrontmatter:
    """Validated frontmatter fields for a pack file.

    Required: description, model, tools.
    Optional: effort, maxTurns, initialPrompt, background, skills,
              permissionMode, disallowedTools.
    Unknown fields are ignored (forward-compat).
    """

    description: str
    model: str
    tools: list[str]
    effort: str | None = None
    maxTurns: int | None = None
    initialPrompt: str | None = None
    background: bool | None = None
    skills: tuple[str, ...] | None = None
    permissionMode: str | None = None
    disallowedTools: tuple[str, ...] | None = None
    settingSources: list[str] | None = None


_REQUIRED = ("description", "model", "tools")
_OPTIONAL = ("effort", "maxTurns", "initialPrompt", "background",
             "skills", "permissionMode", "disallowedTools", "settingSources")

_VALID_PERMISSION_MODES = frozenset(
    {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"}
)

# Appended to AgentDefinition.prompt for every subagent (leaf context).
# The raw body (without this suffix) is returned as the 4th element of
# parse_pack_text so the teammate spawn path can use it without the
# leaf-specific constraints.
_LEAF_SUFFIX = """
## Leaf context

You are a leaf subagent. You have no Task tool by design — subagents are leaves
and cannot spawn further subagents. Stop and report when your task is complete.
"""
_VALID_SETTING_SOURCES = frozenset({"user", "project", "local"})


def parse_pack_file(path: Path) -> tuple[str, AgentDefinition, PackFrontmatter, str]:
    """Parse one markdown file with YAML frontmatter into an AgentDefinition.

    Returns ``(key, agent_definition, frontmatter, raw_body)`` where:

    - ``key`` is the file stem with underscores converted to hyphens
      (e.g., ``general_purpose.md`` → ``"general-purpose"``).
    - ``agent_definition.prompt`` is ``raw_body.rstrip() + _LEAF_SUFFIX``
      (subagent / Task context).
    - ``raw_body`` is the body without the leaf suffix (teammate context).

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

    The fourth element ``raw_body`` is the body text without the appended
    ``_LEAF_SUFFIX``. The teammate spawn path uses this to build a
    teammate-context system prompt via ``teammate_prompt.build_teammate_prompt``.
    ``AgentDefinition.prompt`` always has the leaf suffix appended — it is
    the source of truth for subagent (Task) invocations.
    """
    fm_dict, body = _split_frontmatter(text, path)
    fm = _validate_frontmatter(fm_dict, path)

    if not body.strip():
        raise PackLoadError(f"pack file {path} has empty body")

    key = path.stem.replace("_", "-")
    agent_kwargs: dict[str, Any] = {
        "description": fm.description,
        "prompt": body.rstrip() + _LEAF_SUFFIX,
        "tools": list(fm.tools),
        "model": fm.model,
        "effort": fm.effort,
        "maxTurns": fm.maxTurns,
        "initialPrompt": fm.initialPrompt,
        "background": fm.background,
    }
    if fm.skills is not None:
        agent_kwargs["skills"] = list(fm.skills)
    if fm.permissionMode is not None:
        agent_kwargs["permissionMode"] = fm.permissionMode
    if fm.disallowedTools is not None:
        agent_kwargs["disallowedTools"] = list(fm.disallowedTools)

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
    for field in _REQUIRED:
        if field not in d:
            raise PackLoadError(
                f"pack file {path} missing required frontmatter field '{field}'"
            )

    pm = d.get("permissionMode")
    if pm is not None and pm not in _VALID_PERMISSION_MODES:
        raise PackLoadError(
            f"pack file {path}: unknown permissionMode {pm!r}; "
            f"valid values: {sorted(_VALID_PERMISSION_MODES)}"
        )

    ss = d.get("settingSources")
    if ss is not None:
        for item in ss:
            if item not in _VALID_SETTING_SOURCES:
                raise PackLoadError(
                    f"pack file {path}: unknown settingSources item {item!r}; "
                    f"valid values: {sorted(_VALID_SETTING_SOURCES)}"
                )

    return PackFrontmatter(
        description=str(d["description"]),
        model=str(d["model"]),
        tools=list(d["tools"]),
        effort=str(d["effort"]) if d.get("effort") is not None else None,
        maxTurns=int(d["maxTurns"]) if d.get("maxTurns") is not None else None,
        initialPrompt=(
            str(d["initialPrompt"]) if d.get("initialPrompt") is not None else None
        ),
        background=bool(d["background"]) if d.get("background") is not None else None,
        skills=(
            tuple(str(s) for s in d["skills"]) if d.get("skills") is not None else None
        ),
        permissionMode=pm,
        disallowedTools=(
            tuple(str(t) for t in d["disallowedTools"])
            if d.get("disallowedTools") is not None else None
        ),
        settingSources=list(ss) if ss is not None else None,
    )
