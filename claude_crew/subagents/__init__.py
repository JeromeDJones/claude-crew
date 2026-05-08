"""Default subagent pack for claude-crew teammates (Feature #3a).

Exposes:

- ``load_default_pack()`` — returns the bundled three-member pack
  (``explorer``, ``planner``, ``general``) as a
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


PACK_MEMBERS: tuple[str, ...] = ("explorer", "planner", "general")

_PACK_DIR = Path(__file__).parent
_FILE_FOR_KEY = {
    "explorer": "explorer.md",
    "planner": "planner.md",
    "general": "general.md",
}


def load_default_pack() -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    """Load the bundled pack from ``claude_crew/subagents/*.md``.

    No module cache: files are re-read on every call. Cost is three
    small-file reads per spawn — negligible. Edits to the .md files take
    effect on the next teammate spawn within the same process.

    Returns a ``(pack, role_ss, bodies)`` tuple where:

    - ``pack`` maps role keys to ``AgentDefinition`` (prompt leads with
      ``SUBSTRATE_SUBAGENT_GUIDANCE``).
    - ``role_ss`` maps role keys to their ``settingSources`` list. Keys
      without ``settingSources`` are absent (not ``None``-valued entries).
    - ``bodies`` maps role keys to the raw body text (no substrate prefix),
      for use by the teammate spawn path to build teammate-context prompts.
    """
    pack: dict[str, AgentDefinition] = {}
    role_ss: dict[str, list[str] | None] = {}
    bodies: dict[str, str] = {}
    for key in PACK_MEMBERS:
        path = _PACK_DIR / _FILE_FOR_KEY[key]
        loaded_key, agent, fm, body = parse_pack_file(path)
        if loaded_key != key:
            raise PackLoadError(
                f"pack file {path} produced key '{loaded_key}', expected '{key}'"
            )
        pack[key] = agent
        bodies[key] = body
        if fm.settingSources is not None:
            role_ss[key] = fm.settingSources
    return pack, role_ss, bodies


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
