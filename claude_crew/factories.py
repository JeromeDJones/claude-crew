"""Teammate factory selection.

A factory is `Callable[[id, name, role], Teammate]`. It carries a
`requires_auth` boolean attribute that `make_server()` consults to
decide whether to invoke `validate_auth_or_exit()` at startup.
"""

from __future__ import annotations

import os

from claude_crew.broker import TeammateFactory
from claude_crew.teammate import StubTeammate, Teammate


def stub_factory(id: str, name: str, role: str) -> Teammate:
    return StubTeammate(id=id, name=name, role=role)


stub_factory.requires_auth = False  # type: ignore[attr-defined]


def sdk_factory(id: str, name: str, role: str) -> Teammate:
    # T2 implements SdkTeammate. Imported lazily so that test environments
    # without claude-agent-sdk installed don't fail at module import time.
    from claude_crew.sdk_teammate import SdkTeammate

    return SdkTeammate(id=id, name=name, role=role)


sdk_factory.requires_auth = True  # type: ignore[attr-defined]


def default_factory() -> TeammateFactory:
    """Return the factory selected by CLAUDE_CREW_TEAMMATE_MODE.

    - "sdk" (default in production) → SdkTeammate, requires auth
    - "stub"                        → StubTeammate
    - anything else                 → StubTeammate (conservative)
    """
    mode = os.environ.get("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
    if mode == "sdk":
        return sdk_factory
    return stub_factory
