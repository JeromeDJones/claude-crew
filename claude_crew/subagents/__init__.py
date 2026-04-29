"""Default subagent pack for claude-crew teammates (Feature #3a).

Exposes:

- ``load_default_pack()`` — returns the bundled three-member pack
  (``explorer``, ``planner``, ``general-purpose``) as a
  ``dict[str, AgentDefinition]`` ready to pass to
  ``ClaudeAgentOptions(agents=...)``.
- ``merge_packs(default, user)`` — per-key override at whole-
  AgentDefinition level. User wins on collision. Used by Feature #3b
  (user-defined agent loader); shipped now to lock the seam shape.
- ``PACK_MEMBERS`` — the three default keys, in declaration order.
- ``PackLoadError`` — raised by the loader on missing/malformed files.

See ``doc/features/FEATURE-default-subagent-pack.md`` for the design.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import AgentDefinition

from claude_crew.subagents._loader import PackLoadError, parse_pack_file

__all__ = [
    "PACK_MEMBERS",
    "PackLoadError",
    "load_default_pack",
    "merge_packs",
]


PACK_MEMBERS: tuple[str, ...] = ("explorer", "planner", "general-purpose")

_PACK_DIR = Path(__file__).parent
_FILE_FOR_KEY = {
    "explorer": "explorer.md",
    "planner": "planner.md",
    "general-purpose": "general_purpose.md",
}


def load_default_pack() -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None]]:
    """Load the bundled pack from ``claude_crew/subagents/*.md``.

    No module cache: files are re-read on every call. Cost is three
    small-file reads per spawn — negligible. Edits to the .md files take
    effect on the next teammate spawn within the same process.

    Returns a ``(pack, role_ss)`` tuple where ``role_ss`` maps role keys
    to their ``settingSources`` list. Keys without ``settingSources`` are
    absent from ``role_ss`` (not ``None``-valued entries).
    """
    pack: dict[str, AgentDefinition] = {}
    role_ss: dict[str, list[str] | None] = {}
    for key in PACK_MEMBERS:
        path = _PACK_DIR / _FILE_FOR_KEY[key]
        loaded_key, agent, fm = parse_pack_file(path)
        if loaded_key != key:
            raise PackLoadError(
                f"pack file {path} produced key '{loaded_key}', expected '{key}'"
            )
        pack[key] = agent
        if fm.settingSources is not None:
            role_ss[key] = fm.settingSources
    return pack, role_ss


def merge_packs(
    default: dict[str, AgentDefinition],
    user: dict[str, AgentDefinition] | None,
) -> dict[str, AgentDefinition]:
    """Merge a user-defined agent dict over the default pack.

    Per-key override at the whole-AgentDefinition level. User wins on
    collision (full replacement of the default's entry). Non-conflicting
    user keys are added. ``None`` or ``{}`` returns ``default`` unchanged.

    Field-level merging is intentionally not supported. A user wanting
    one different knob redefines the entire entry — keeps "where did
    this value come from" trivially answerable.

    Always returns a fresh dict; callers may mutate the result without
    affecting the inputs.
    """
    return {**default, **(user or {})}
