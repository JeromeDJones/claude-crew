"""Teammate factory selection.

A factory is `Callable[[id, name, role], Teammate]`. It carries a
`requires_auth` boolean attribute that `make_server()` consults to
decide whether to invoke `validate_auth_or_exit()` at startup.
"""

from __future__ import annotations

import logging
import os

from claude_crew.broker import TeammateFactory
from claude_crew.teammate import StubTeammate, Teammate

logger = logging.getLogger(__name__)

# Update this table when Anthropic releases new model generations.
# These shorthands appear in pack frontmatter (e.g., `model: opus`) and are
# resolved to full IDs when spawning top-level teammates. Packs used as subagents
# have their model field interpreted by the Claude Code host, which resolves the
# same shorthands independently — keep this table in sync with CLAUDE.md invariants.
_PACK_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def _mcp_server_name_from_tool_id(tool_id: str) -> str | None:
    """Extract MCP server name from a tool ID like 'mcp__knowledge-graph__repo_map'.

    Returns None for non-MCP tool IDs (e.g. 'Read', 'Bash').
    """
    parts = tool_id.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return parts[1]
    return None


def stub_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    cwd: str | None = None, permission_mode: str | None = None,
    setting_sources: list[str] | None = None,
    extra_tools: list[str] | None = None,
    extra_skills: list[str] | None = None,
) -> Teammate:
    # Stub ignores model/effort/cwd/permission_mode/setting_sources/extra_tools/extra_skills
    # — kept for signature uniformity with sdk_factory.
    return StubTeammate(id=id, name=name, role=role)


stub_factory.requires_auth = False  # type: ignore[attr-defined]


def sdk_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    agents: "dict | None" = None,
    pack_bodies: "dict | None" = None,
    cwd: str | None = None, permission_mode: str | None = None,
    setting_sources: list[str] | None = None,
    extra_tools: list[str] | None = None,
    extra_skills: list[str] | None = None,
) -> Teammate:
    from claude_crew.sdk_teammate import SdkTeammate

    kwargs: dict = {}
    if model is not None:
        kwargs["model"] = model
    if effort is not None:
        kwargs["effort"] = effort
    if agents is not None:
        kwargs["agents"] = agents
    if pack_bodies is not None:
        kwargs["pack_bodies"] = pack_bodies
    if cwd is not None:
        kwargs["cwd"] = cwd
    if permission_mode is not None:
        kwargs["permission_mode"] = permission_mode
    # None means "use SDK default"; [] means "no sources" — keep is-not-None, not truthiness.
    if setting_sources is not None:
        kwargs["setting_sources"] = setting_sources
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
        import dataclasses

        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.subagents._user_loader import (
            _discover_skill_names,
            build_merged_pack,
        )

        merged_pack, role_ss, merged_bodies = build_merged_pack()

        def factory(
            id: str, name: str, role: str,
            *, model: str | None = None, effort: str | None = None,
            cwd: str | None = None, permission_mode: str | None = None,
            extra_tools: list[str] | None = None,
            extra_skills: list[str] | None = None,
        ) -> Teammate:
            # Warn about unknown extra skills at spawn time.
            if extra_skills:
                discovered = _discover_skill_names()
                for skill in extra_skills:
                    if skill not in discovered:
                        logger.warning(
                            "extra_skills: skill %r not found in any skill directory "
                            "— SDK will reject it at spawn if truly absent",
                            skill,
                        )

            if extra_tools or extra_skills:
                # Per-spawn patched agents dict — original merged_pack must not be mutated.
                agent_def = merged_pack.get(role)
                if agent_def is not None:
                    pack_tools = agent_def.tools or []
                    pack_skills = agent_def.skills
                    # skills may be "all" literal or a list; only merge when it's a list
                    if isinstance(pack_skills, str):
                        # "all" literal — keep as-is, just append extras without duplication
                        effective_tools = list(dict.fromkeys(pack_tools + (extra_tools or [])))
                        patched_def = dataclasses.replace(
                            agent_def,
                            tools=effective_tools,
                        )
                    else:
                        pack_skills_list = pack_skills or []
                        effective_tools = list(dict.fromkeys(pack_tools + (extra_tools or [])))
                        effective_skills = list(dict.fromkeys(pack_skills_list + (extra_skills or [])))
                        patched_def = dataclasses.replace(
                            agent_def,
                            tools=effective_tools,
                            skills=effective_skills,
                        )
                else:
                    # Role not in pack — create a synthetic AgentDefinition for extras only.
                    patched_def = AgentDefinition(
                        description="",
                        prompt="",
                        tools=list(dict.fromkeys(extra_tools or [])),
                        skills=list(dict.fromkeys(extra_skills or [])) or None,
                    )
                # Auto-wire MCP servers for any MCP tool IDs in extra_tools.
                # Granting the tool ID is necessary but not sufficient — the
                # subprocess also needs the server name in AgentDefinition.mcpServers
                # to establish the connection at spawn time.
                if extra_tools:
                    extra_servers = list(dict.fromkeys(
                        s for t in extra_tools
                        if (s := _mcp_server_name_from_tool_id(t)) is not None
                    ))
                    if extra_servers:
                        existing_mcp = list(patched_def.mcpServers or [])
                        existing_names = {
                            e if isinstance(e, str) else e.get("name", "")
                            for e in existing_mcp
                        }
                        new_servers = [s for s in extra_servers if s not in existing_names]
                        if new_servers:
                            patched_def = dataclasses.replace(
                                patched_def,
                                mcpServers=(existing_mcp + new_servers) or None,
                            )

                # Fresh dict per spawn — no shared mutable reference
                effective_agents = {**merged_pack, role: patched_def}
            else:
                effective_agents = merged_pack

            # If no explicit model override, apply the pack's declared model (with alias resolution).
            # Pack frontmatter `model: opus` is otherwise only applied for subagent dispatch,
            # not when the role is spawned as a top-level claude-crew teammate.
            resolved_model = model
            if resolved_model is None:
                pack_def = merged_pack.get(role)
                if pack_def is not None:
                    pack_model = getattr(pack_def, "model", None)
                    if pack_model:
                        resolved_model = _PACK_MODEL_ALIASES.get(pack_model, pack_model)

            return sdk_factory(
                id, name, role, model=resolved_model, effort=effort, agents=effective_agents,
                pack_bodies=merged_bodies,
                cwd=cwd, permission_mode=permission_mode,
                setting_sources=role_ss.get(role),
            )

        factory.requires_auth = True  # type: ignore[attr-defined]
        # Expose the merged pack to the broker so it can snapshot each
        # teammate's resolved AgentDefinition at spawn time. Without this,
        # production teammates have no `config` block and dashboard chips
        # render empty.
        def _resolve_agent_def(role: str) -> "AgentDefinition | None":
            agent_def = merged_pack.get(role)
            if agent_def is None:
                return None
            pack_model = getattr(agent_def, "model", None)
            if pack_model:
                resolved = _PACK_MODEL_ALIASES.get(pack_model, pack_model)
                if resolved != pack_model:
                    return dataclasses.replace(agent_def, model=resolved)
            return agent_def

        factory.agent_def_resolver = _resolve_agent_def  # type: ignore[attr-defined]
        return factory
    return stub_factory
