"""Teammate factory selection.

A factory is `Callable[[id, name, role], Teammate]`. It carries a
`requires_auth` boolean attribute that `make_server()` consults to
decide whether to invoke `validate_auth_or_exit()` at startup.
"""

from __future__ import annotations

import os

from claude_crew.broker import TeammateFactory
from claude_crew.teammate import StubTeammate, Teammate


def stub_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
) -> Teammate:
    # Stub ignores model/effort — kept for signature uniformity with sdk_factory.
    return StubTeammate(id=id, name=name, role=role)


stub_factory.requires_auth = False  # type: ignore[attr-defined]


def sdk_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    agents: "dict | None" = None,
) -> Teammate:
    from claude_crew.sdk_teammate import SdkTeammate

    kwargs: dict = {}
    if model is not None:
        kwargs["model"] = model
    if effort is not None:
        kwargs["effort"] = effort
    if agents is not None:
        kwargs["agents"] = agents
    return SdkTeammate(id=id, name=name, role=role, **kwargs)


sdk_factory.requires_auth = True  # type: ignore[attr-defined]


def default_factory() -> TeammateFactory:
    """Return the factory selected by CLAUDE_CREW_TEAMMATE_MODE.

    - "sdk" (default in production) → SdkTeammate, requires auth.
      The merged agent pack (default + ``~/.claude/agents/`` + project's
      ``.claude/agents/``) is computed once here and frozen for the
      process lifetime per Feature #3b's design (project root is
      resolved at MCP-server startup, not per-spawn).
    - "stub"                        → StubTeammate
    - anything else                 → StubTeammate (conservative)
    """
    mode = os.environ.get("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
    if mode == "sdk":
        from claude_crew.subagents._user_loader import build_merged_pack

        merged_pack = build_merged_pack()

        def factory(
            id: str, name: str, role: str,
            *, model: str | None = None, effort: str | None = None,
        ) -> Teammate:
            return sdk_factory(
                id, name, role, model=model, effort=effort, agents=merged_pack,
            )

        factory.requires_auth = True  # type: ignore[attr-defined]
        return factory
    return stub_factory
