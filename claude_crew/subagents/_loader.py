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
    Optional: effort, maxTurns, initialPrompt.
    Unknown fields are ignored (forward-compat).
    """

    description: str
    model: str
    tools: list[str]
    effort: str | None = None
    maxTurns: int | None = None
    initialPrompt: str | None = None


_REQUIRED = ("description", "model", "tools")
_OPTIONAL = ("effort", "maxTurns", "initialPrompt")


def parse_pack_file(path: Path) -> tuple[str, AgentDefinition]:
    """Parse one markdown file with YAML frontmatter into an AgentDefinition.

    Returns (key, agent_definition) where the key is the file stem with
    underscores converted to hyphens (e.g., ``general_purpose.md`` →
    ``"general-purpose"``).

    Raises:
        PackLoadError: if the file is missing, lacks frontmatter, omits a
            required field, or has an empty body.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise PackLoadError(f"cannot read pack file {path}: {exc}") from exc

    fm_dict, body = _split_frontmatter(text, path)
    fm = _validate_frontmatter(fm_dict, path)

    if not body.strip():
        raise PackLoadError(f"pack file {path} has empty body")

    key = path.stem.replace("_", "-")
    agent = AgentDefinition(
        description=fm.description,
        prompt=body,
        tools=list(fm.tools),
        model=fm.model,
        effort=fm.effort,
        maxTurns=fm.maxTurns,
        initialPrompt=fm.initialPrompt,
    )
    return key, agent


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
    return PackFrontmatter(
        description=str(d["description"]),
        model=str(d["model"]),
        tools=list(d["tools"]),
        effort=str(d["effort"]) if d.get("effort") is not None else None,
        maxTurns=int(d["maxTurns"]) if d.get("maxTurns") is not None else None,
        initialPrompt=(
            str(d["initialPrompt"]) if d.get("initialPrompt") is not None else None
        ),
    )
