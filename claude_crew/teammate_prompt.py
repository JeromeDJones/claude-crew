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
SENTINEL_DELEGATION = "## Delegation"
SENTINEL_MEMORY = "## Memory from prior sessions"

# Historical note (2026-05-17): SENTINEL_SUBAGENTS / _build_subagent_list
# were removed. The framework-injected Agent tool description already
# carries per-agent names + descriptions + tool surfaces — including names
# alone in the spawn-time prompt was duplication costing ~700 tokens per
# LLM invocation across every teammate. Routing signal is unchanged; the
# delegation section now points at the Agent tool's parameter docs.


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

Route work by reading each subagent's description in the Agent tool's
parameter documentation. If a dispatched subagent reports it can't complete
the work because it lacks a tool, switch to a different subagent or handle
that step directly.
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

    The addendum contains two ordered sections delimited by SENTINEL_*
    constants, plus an optional third when memory_section is provided:
      1. SENTINEL_CONTEXT    — corrects leaf-context language for teammate use
      2. SENTINEL_DELEGATION — delegation heuristic + conditional explorer hint
      3. SENTINEL_MEMORY     — injected when pack declares memory: user (optional)

    The ``role`` argument is kept on the API for backward compatibility and
    future use (e.g., role-scoped memory selection); it is currently
    unused by the assembly logic now that the subagent list has been removed.

    Args:
        role: the teammate's own role key.
        pack_body: raw pack body text, no substrate framing, no frontmatter.
        agents: the agents dict (role-key → AgentDefinition); used for the
                explorer-hint conditional only. Defensive on absent ``explorer``.
        memory_section: pre-built memory section string from
                        teammate_memory.build_memory_section, or None.
    """
    del role  # retained on the API; unused since subagent-list removal (2026-05-17)
    delegation = _DELEGATION_TEMPLATE.format(explorer_hint=_explorer_hint(agents))
    parts = [_CONTEXT_OVERRIDE, delegation]
    if memory_section is not None:
        parts.append(memory_section)
    addendum = "\n\n".join(parts)
    return f"{pack_body.rstrip()}\n\n{addendum}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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
