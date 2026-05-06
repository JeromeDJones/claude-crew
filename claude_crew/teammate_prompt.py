"""Teammate prompt assembly — top-level teammate context, NOT subagent context.

See doc/features/FEATURE-teammate-prompt-parity.md for design rationale.

When a teammate is spawned, its system prompt is:

    <pack_body> + "\\n\\n" + <addendum>

The addendum corrects any leaf-context language in the pack body and
provides delegation guidance and a subagent roster appropriate for a
top-level teammate (one that *can* dispatch subagents).

The subagent path's substrate framing (SUBSTRATE_SUBAGENT_GUIDANCE +
build_subagent_prompt in _loader.py) is composed separately for Task/
subagent invocations and is NOT part of this module. The teammate path
deliberately retains body-first ordering (the addendum injects late-bound
subagent-roster context that may reference roles named in the body).
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public test surface
# ---------------------------------------------------------------------------

# Curated negative-pattern list for the contradiction-lint test.
# Strings in this tuple MUST NOT appear in any assembled teammate prompt.
NEGATIVE_PATTERNS: tuple[str, ...] = (
    "you have no Task tool",
    "no Task tool by design",
    "subagents are leaves",
    "cannot spawn",
    "use the Task tool",
)

# Section sentinels (Markdown headings) for ordering assertions.
# Tests assert these appear in the assembled prompt in this order.
SENTINEL_CONTEXT = "## Operating context"
SENTINEL_SUBAGENTS = "## Available subagents"
SENTINEL_DELEGATION = "## Delegation"
SENTINEL_MEMORY = "## Memory from prior sessions"


# ---------------------------------------------------------------------------
# Addendum constants
# ---------------------------------------------------------------------------

_CONTEXT_OVERRIDE = SENTINEL_CONTEXT + """

You are a top-level **teammate** in claude-crew, not a leaf. Any delegation
constraints in your role above apply to your subagents, not to you — you
can dispatch subagents via Task.
"""

_DELEGATION_TEMPLATE = SENTINEL_DELEGATION + """

Delegate bounded "go look and report" work to subagents to preserve your
context for synthesis. Read directly only when the content will appear
verbatim in your response (a quoted line, cited path, file you're showing
the lead). Don't ask the lead to load files a subagent could fetch.

{explorer_hint}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_teammate_prompt(
    role: str,
    pack_body: str,
    agents: dict[str, Any],
    memory_section: str | None = None,
) -> str:
    """Assemble the system prompt for a top-level teammate.

    Returns: pack_body + "\\n\\n" + addendum

    The addendum contains three ordered sections delimited by SENTINEL_*
    constants, plus an optional fourth when memory_section is provided:
      1. SENTINEL_CONTEXT    — corrects leaf-context language for teammate use
      2. SENTINEL_SUBAGENTS  — sorted subagent roster, self excluded
      3. SENTINEL_DELEGATION — delegation heuristic + conditional explorer hint
      4. SENTINEL_MEMORY     — injected when pack declares memory: user (optional)

    Args:
        role: the teammate's own role key; filtered out of the subagent list
              (a teammate cannot delegate to itself).
        pack_body: raw pack body text, no substrate framing, no frontmatter.
        agents: the agents dict (role-key → AgentDefinition); used for the
                subagent list and the explorer-hint conditional. Defensive on
                missing or malformed description fields.
        memory_section: pre-built memory section string from
                        teammate_memory.build_memory_section, or None.
    """
    subagent_section = _build_subagent_list(role, agents)
    delegation = _DELEGATION_TEMPLATE.format(explorer_hint=_explorer_hint(agents))
    parts = [_CONTEXT_OVERRIDE, subagent_section, delegation]
    if memory_section is not None:
        parts.append(memory_section)
    addendum = "\n\n".join(parts)
    return f"{pack_body.rstrip()}\n\n{addendum}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_subagent_list(self_role: str, agents: dict[str, Any]) -> str:
    """Build the ## Available subagents section.

    - Sorted by name (stable ordering across Python versions and envs).
    - Excludes self_role (a teammate cannot delegate to itself).
    - Defensive on missing / non-string description (user packs may be
      malformed; fall back to name-only, never raise).
    - Lists each subagent's available tools as an indented sub-bullet so the
      teammate can route work correctly (BACKLOG 2026-05-01: parents were
      mis-routing shell tasks to subagents that lack Bash because the list
      didn't surface tool surfaces). Defensive on missing tools field.
    """
    lines = [SENTINEL_SUBAGENTS, ""]
    for name in sorted(agents.keys()):
        if name == self_role:
            continue
        defn = agents[name]
        desc = getattr(defn, "description", None)
        if isinstance(desc, str) and desc.strip():
            lines.append(f"- **{name}** — {desc.strip()}")
        else:
            lines.append(f"- **{name}**")
        tools = getattr(defn, "tools", None)
        if isinstance(tools, (list, tuple)) and tools:
            tool_str = ", ".join(str(t) for t in tools)
            lines.append(f"  - tools: {tool_str}")
    return "\n".join(lines)


def _explorer_hint(agents: dict[str, Any]) -> str:
    """Return delegation phrasing that names explorer when present.

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
