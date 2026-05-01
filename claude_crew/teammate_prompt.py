"""Teammate prompt assembly — top-level teammate context, NOT subagent context.

See doc/features/FEATURE-teammate-prompt-parity.md for design rationale.

When a teammate is spawned, its system prompt is:

    <pack_body> + "\\n\\n" + <addendum>

The addendum corrects any leaf-context language in the pack body and
provides peer-awareness and delegation guidance appropriate for a
top-level teammate (one that *can* dispatch subagents).

The leaf-context suffix (_LEAF_SUFFIX in _loader.py) is appended
separately for Task/subagent invocations and is NOT part of this module.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public test surface
# ---------------------------------------------------------------------------

# Curated negative-pattern list for the SC-6 contradiction-lint test.
# Strings in this tuple MUST NOT appear in any assembled teammate prompt.
# Tighten this list to add future contradiction guards.
NEGATIVE_PATTERNS: tuple[str, ...] = (
    "you have no Task tool",
    "no Task tool by design",
    "subagents are leaves",
    "cannot spawn",
    "use the Task tool",
)

# Section sentinels (Markdown headings) for SC-2 ordering assertions.
# Tests assert these appear in the assembled prompt in this order.
# Sentinel text is part of the public test contract; do not change without
# updating tests.
SENTINEL_CONTEXT = "## Operating context"
SENTINEL_PEERS = "## Available teammates"
SENTINEL_DELEGATION = "## Delegation"
SENTINEL_ANTIPATTERNS = "## Anti-patterns"


# ---------------------------------------------------------------------------
# Addendum constants
# ---------------------------------------------------------------------------

_CONTEXT_OVERRIDE = SENTINEL_CONTEXT + """

You are running as a top-level **teammate** in the claude-crew system, not
as a leaf subagent. Any leaf-context constraints in your role definition
above (such as restrictions on delegation) apply to YOUR subagents, not to
you. You DO have the ability to dispatch subagents for delegated work.
"""

_DELEGATION_TEMPLATE = SENTINEL_DELEGATION + """

When you need to read files, run searches, or do bounded "go look at this
and report" work, dispatch a subagent rather than reading directly. Reserve
your context for synthesis and judgment.

{explorer_hint}
"""

_ANTIPATTERNS = SENTINEL_ANTIPATTERNS + """

- Do not spawn other top-level teammates (only subagents).
- Do not ask the lead to load files into your context that a subagent could
  fetch on your behalf.
- Do not retain leaf-context constraints from your role's pack body —
  those apply to your subagents, not to you.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_teammate_prompt(role: str, pack_body: str, agents: dict[str, Any]) -> str:
    """Assemble the system prompt for a top-level teammate.

    Returns: pack_body + "\\n\\n" + addendum

    The addendum contains four ordered sections delimited by SENTINEL_*
    constants:
      1. SENTINEL_CONTEXT  — corrects leaf-context language for teammate use
      2. SENTINEL_PEERS    — sorted peer list, self excluded (R-1, R-2)
      3. SENTINEL_DELEGATION — delegation framework with conditional explorer hint
      4. SENTINEL_ANTIPATTERNS — what not to do

    Args:
        role: the teammate's own role key; filtered out of the peer list
              per OQ-6 / R-2 (a teammate cannot delegate to itself).
        pack_body: raw pack body text, no leaf suffix, no frontmatter.
        agents: the agents dict (role-key → AgentDefinition); used for the
                peer list and the explorer-hint conditional. Defensive on
                missing or malformed description fields (R-3).
    """
    peer_section = _build_peer_list(role, agents)
    delegation = _DELEGATION_TEMPLATE.format(explorer_hint=_explorer_hint(agents))
    addendum = "\n\n".join([_CONTEXT_OVERRIDE, peer_section, delegation, _ANTIPATTERNS])
    return f"{pack_body.rstrip()}\n\n{addendum}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_peer_list(self_role: str, agents: dict[str, Any]) -> str:
    """Build the ## Available teammates section.

    - Sorted by name (R-1: stable ordering across Python versions and envs).
    - Excludes self_role (R-2: a teammate cannot delegate to itself).
    - Defensive on missing / non-string description (R-3: user packs may be
      malformed; fall back to name-only, never raise).
    """
    lines = [SENTINEL_PEERS, ""]
    for name in sorted(agents.keys()):
        if name == self_role:
            continue
        defn = agents[name]
        desc = getattr(defn, "description", None)
        if isinstance(desc, str) and desc.strip():
            lines.append(f"- **{name}** — {desc.strip()}")
        else:
            lines.append(f"- **{name}**")
    return "\n".join(lines)


def _explorer_hint(agents: dict[str, Any]) -> str:
    """Return delegation phrasing that names explorer when present (EC-11 / R-7).

    Avoids referring to a subagent the teammate doesn't actually have
    (edge case: custom agents dict without explorer). Falls back to
    neutral phrasing.
    """
    if "explorer" in agents:
        return (
            "For routine file reads and codebase searches, the `explorer` subagent"
            " is the right tool — it is read-only and Haiku-backed (cheap)."
        )
    return (
        "For routine file reads and codebase searches, prefer a read-only subagent"
        " over reading directly."
    )
