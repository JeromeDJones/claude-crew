"""Teammate factory selection.

A factory is `Callable[[id, name, role], Teammate]`. It carries a
`requires_auth` boolean attribute that `make_server()` consults to
decide whether to invoke `validate_auth_or_exit()` at startup.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from pathlib import Path

from claude_crew.broker import TeammateFactory
from claude_crew.diagnostics import (
    StartupDiagCollector,
    collect_startup_diagnostics,
)
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
    mcp_servers: list[str] | None = None,
    env: "dict[str, str] | None" = None,
) -> Teammate:
    # Stub ignores all configuration kwargs — kept for signature uniformity
    # with sdk_factory.
    return StubTeammate(id=id, name=name, role=role)


stub_factory.requires_auth = False  # type: ignore[attr-defined]


def _stub_refresh_pack() -> dict:
    """No-op refresh for stub mode.

    Returns the empty-diff / zero-counts RefreshResult so make_server() can
    register refresh_agents unconditionally without sdk-mode being required.
    """
    return {
        "ok": True,
        "error": None,
        "counts": {"default": 0, "plugin": 0, "user": 0, "project": 0, "total": 0},
        "diff": {"added": [], "removed": [], "changed": []},
        "warnings": [],
        "note": _REFRESH_NOTE,
    }


stub_factory.refresh_pack = _stub_refresh_pack  # type: ignore[attr-defined]


def sdk_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    agents: "dict | None" = None,
    pack_bodies: "dict | None" = None,
    cwd: str | None = None, permission_mode: str | None = None,
    setting_sources: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    extra_tools: list[str] | None = None,
    extra_skills: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    env: "dict[str, str] | None" = None,
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
    if allowed_tools is not None:
        kwargs["allowed_tools"] = allowed_tools
    if extra_tools is not None:
        kwargs["extra_tools"] = extra_tools
    if mcp_servers is not None:
        kwargs["mcp_servers_grant"] = mcp_servers
    if env is not None:
        kwargs["env"] = env
    return SdkTeammate(id=id, name=name, role=role, **kwargs)


sdk_factory.requires_auth = True  # type: ignore[attr-defined]


# Source loggers we want startup-diagnostics capture to cover. The
# collector attaches at the root logger (assumption A-2); these loggers
# are explicitly probed for propagation (OQ-1) and direct-attached as
# fallback if any of them is silenced upstream by a propagate=False on
# their ancestor chain.
_STARTUP_SOURCE_LOGGERS: tuple[str, ...] = (
    "claude_crew.subagents.loader",
    "claude_crew.subagents._user_loader",
    "claude_crew.factories",
)


_REFRESH_NOTE = (
    "future-spawns-only: running teammates keep their original AgentDefinition snapshot."
)


@dataclasses.dataclass
class _PackState:
    """Mutable holder for the merged agent pack state.

    All three mutable fields (pack, role_ss, bodies) are read live at every
    call-site so that a future refresh() can swap them atomically without
    re-wiring closures.  home_dir and project_root are frozen at startup and
    used as the roots for any subsequent refresh.
    """

    pack: dict  # dict[str, AgentDefinition]
    role_ss: dict  # dict[str, list[str] | None]
    bodies: dict  # dict[str, str]
    home_dir: Path | None  # frozen at startup; refresh reuses
    project_root: Path | None  # frozen at startup; refresh reuses
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def refresh(self) -> dict:
        """Re-read agent definitions from disk and atomically swap the pack.

        Re-invokes ``build_merged_pack`` against the same ``home_dir`` /
        ``project_root`` captured at startup (never ``Path.cwd()``). On
        success, atomically replaces ``pack``, ``role_ss``, and ``bodies``
        under the holder's lock.  On failure the prior pack is left
        untouched and ``ok=False`` is returned.

        Returns a ``RefreshResult`` dict (JSON-serialisable).
        """
        from claude_crew.subagents._user_loader import build_merged_pack

        # Snapshot current pack for diff comparison (outside the lock —
        # grabbing a reference is atomic at the Python level).
        old_pack: dict = self.pack

        new_pack: dict | None = None
        new_role_ss: dict | None = None
        new_bodies: dict | None = None
        error_str: str | None = None

        # Run the rebuild inside a diagnostic capture window — mirrors the
        # startup path in default_factory().
        with collect_startup_diagnostics() as coll:
            extra_attached, restore_pairs = _direct_attach_fallbacks(coll)
            try:
                new_pack, new_role_ss, new_bodies = build_merged_pack(
                    home_dir=self.home_dir,
                    project_root=self.project_root,
                )
            except Exception as exc:
                error_str = repr(exc)
            finally:
                for src in extra_attached:
                    src.removeHandler(coll)
                for src, prev in restore_pairs:
                    src.setLevel(prev)

        diags = coll.freeze()  # idempotent; context exit already froze it

        warnings = [
            {"level": d.level, "logger": d.source, "message": d.message}
            for d in diags
        ]

        if error_str is not None:
            # Rebuild failed — leave state untouched.
            return {
                "ok": False,
                "error": error_str,
                "counts": {
                    "default": 0, "plugin": 0, "user": 0, "project": 0, "total": 0,
                },
                "diff": {"added": [], "removed": [], "changed": []},
                "warnings": warnings,
                "note": _REFRESH_NOTE,
            }

        # Compute diff: added, removed, changed.
        # Role keys exactly match merged_pack keys: bare for default/user/project,
        # "<plugin>:<role>" for plugin agents (AT-7).
        # Use dataclasses.asdict for deterministic field comparison (AT-2).
        assert new_pack is not None  # error_str is None → new_pack was set
        added = sorted(k for k in new_pack if k not in old_pack)
        removed = sorted(k for k in old_pack if k not in new_pack)
        changed = sorted(
            k for k in new_pack
            if k in old_pack
            and dataclasses.asdict(new_pack[k]) != dataclasses.asdict(old_pack[k])
        )

        # Counts: plugin agents use "<plugin>:<role>" keys (namespaced).
        # Per-layer breakdown for default/user/project is not recoverable from
        # the merged result without re-loading each layer separately; plugin count
        # is derivable from key shape.  total is authoritative.
        plugin_count = sum(1 for k in new_pack if ":" in k)
        counts = {
            "default": 0,
            "plugin": plugin_count,
            "user": 0,
            "project": 0,
            "total": len(new_pack),
        }

        # Atomic swap — only executed on rebuild success (AT-4).
        assert new_role_ss is not None
        assert new_bodies is not None
        with self._lock:
            self.pack = new_pack
            self.role_ss = new_role_ss
            self.bodies = new_bodies

        return {
            "ok": True,
            "error": None,
            "counts": counts,
            "diff": {"added": added, "removed": removed, "changed": changed},
            "warnings": warnings,
            "note": _REFRESH_NOTE,
        }


def _propagates_to_root(logger_name: str) -> bool:
    """Return True iff records on ``logger_name`` reach the root logger.

    Walks the ancestor chain. A propagate=False anywhere between the
    source logger and the root breaks propagation; we cannot rely on
    a root-only handler in that case. Pure inspection — no records
    are emitted.
    """
    cur = logging.getLogger(logger_name)
    root = logging.getLogger()
    if cur is root:
        return True
    while cur is not None and cur is not root:
        if not cur.propagate:
            return False
        cur = cur.parent
    return True


def _direct_attach_fallbacks(
    handler: StartupDiagCollector,
) -> tuple[list[logging.Logger], list[tuple[logging.Logger, int]]]:
    """Direct-attach ``handler`` to any source logger that fails the
    propagation probe. Returns ``(attached, restore_pairs)`` so the caller
    can detach symmetrically and restore source-logger levels on context
    exit without coupling state onto the handler instance.
    """
    attached: list[logging.Logger] = []
    restore_pairs: list[tuple[logging.Logger, int]] = []
    for name in _STARTUP_SOURCE_LOGGERS:
        if _propagates_to_root(name):
            continue
        src = logging.getLogger(name)
        src.addHandler(handler)
        if src.level == logging.NOTSET or src.level > logging.INFO:
            restore_pairs.append((src, src.level))
            src.setLevel(logging.INFO)
        attached.append(src)
    return attached, restore_pairs


def default_factory(
    *,
    home_dir: Path | None = None,
    project_root: Path | None = None,
) -> TeammateFactory:
    """Return the factory selected by CLAUDE_CREW_TEAMMATE_MODE.

    - "sdk" (default in production) → SdkTeammate, requires auth.
      The merged agent pack (default + ``~/.claude/agents/`` + project's
      ``.claude/agents/``) is computed once here and frozen for the
      process lifetime per Feature #3b's design (project root is
      resolved at MCP-server startup, not per-spawn).
    - "stub"                        → StubTeammate
    - anything else                 → StubTeammate (conservative)

    In sdk mode the call to ``build_merged_pack()`` is wrapped in a
    :func:`collect_startup_diagnostics` context. The frozen tuple is
    attached to the returned factory as ``factory.startup_diagnostics``
    so :func:`make_server` can thread it into the
    :class:`~claude_crew.broker.Broker` constructor. Stub mode skips
    capture entirely (acceptance test #10): the stub factory carries no
    diagnostics attribute and ``getattr(stub_factory, 'startup_diagnostics', ())``
    yields ``()``.
    """
    mode = os.environ.get("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
    if mode == "sdk":
        import dataclasses

        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.subagents._user_loader import (
            _discover_skill_names,
            build_merged_pack,
        )

        # Capture window: every record propagated by source loggers
        # during pack-load lands in the collector. Direct-attach
        # fallbacks cover any source logger that was silenced upstream.
        collector_handler: StartupDiagCollector
        with collect_startup_diagnostics() as collector_handler:
            extra_attached, restore_pairs = _direct_attach_fallbacks(
                collector_handler
            )
            try:
                # Pass home_dir/project_root only when explicitly provided so
                # existing tests that monkeypatch build_merged_pack with a
                # zero-arg stand-in continue to work.
                _bmp_kwargs: dict = {}
                if home_dir is not None:
                    _bmp_kwargs["home_dir"] = home_dir
                if project_root is not None:
                    _bmp_kwargs["project_root"] = project_root
                merged_pack, role_ss, merged_bodies = build_merged_pack(
                    **_bmp_kwargs
                )
            finally:
                for src in extra_attached:
                    src.removeHandler(collector_handler)
                for src, prev in restore_pairs:
                    src.setLevel(prev)
        startup_diagnostics = collector_handler.freeze()

        # Mutable holder: every read-site reads fields LIVE off this object
        # so a future refresh() can atomically swap the pack without re-wiring
        # closures.  home_dir / project_root are frozen at startup.
        holder = _PackState(
            pack=merged_pack,
            role_ss=role_ss,
            bodies=merged_bodies,
            home_dir=home_dir,
            project_root=project_root,
        )

        def _resolve_role(requested: str) -> str:
            """Promote a bare role name to a namespaced plugin key when the
            promotion is unambiguous.

            Plugin agents are keyed ``<plugin>:<role>`` to match Claude Code's
            surface form. A lead may still spawn by the bare role name (legacy
            usage, or matching a name they saw in another tool). We resolve:

            - Exact match in holder.pack → use as-is.
            - No exact match, and exactly one ``*:requested`` exists → promote,
              log INFO so the operator sees the trail.
            - Multiple ``*:requested`` candidates → WARN listing them; fall
              through with the original (which will hit the synthetic empty
              AgentDef path). The lead has to disambiguate.
            - Zero candidates → fall through; existing unknown-role behavior.
            """
            current_pack = holder.pack
            if requested in current_pack:
                return requested
            candidates = sorted(
                k for k in current_pack if k.endswith(f":{requested}")
            )
            if len(candidates) == 1:
                logger.info(
                    "role %r not in pack; auto-resolving to plugin-namespaced %r",
                    requested, candidates[0],
                )
                return candidates[0]
            if len(candidates) > 1:
                logger.warning(
                    "role %r not in pack; multiple plugin candidates %r — "
                    "spawn the namespaced form to disambiguate",
                    requested, candidates,
                )
            return requested

        def factory(
            id: str, name: str, role: str,
            *, model: str | None = None, effort: str | None = None,
            cwd: str | None = None, permission_mode: str | None = None,
            extra_tools: list[str] | None = None,
            extra_skills: list[str] | None = None,
            mcp_servers: list[str] | None = None,
            env: "dict[str, str] | None" = None,
        ) -> Teammate:
            # Read holder fields live at call time (not captured at closure build).
            current_pack = holder.pack
            current_role_ss = holder.role_ss
            current_bodies = holder.bodies

            role = _resolve_role(role)
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
                # Per-spawn patched agents dict — original pack must not be mutated.
                agent_def = current_pack.get(role)
                if agent_def is not None:
                    pack_tools = agent_def.tools or []
                    pack_skills = agent_def.skills or []
                    effective_tools = list(dict.fromkeys(pack_tools + (extra_tools or [])))
                    effective_skills = list(dict.fromkeys(pack_skills + (extra_skills or [])))
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
                effective_agents = {**current_pack, role: patched_def}
            else:
                effective_agents = current_pack

            # If no explicit model override, apply the pack's declared model (with alias resolution).
            # Pack frontmatter `model: opus` is otherwise only applied for subagent dispatch,
            # not when the role is spawned as a top-level claude-crew teammate.
            resolved_model = model
            resolved_effort = effort
            pack_def = current_pack.get(role)
            if pack_def is not None:
                if resolved_model is None:
                    pack_model = getattr(pack_def, "model", None)
                    if pack_model:
                        resolved_model = _PACK_MODEL_ALIASES.get(pack_model, pack_model)
                if resolved_effort is None:
                    pack_effort = getattr(pack_def, "effort", None)
                    if pack_effort:
                        resolved_effort = pack_effort

            # Pre-approve MCP tool IDs so the subprocess doesn't block on permission prompts.
            # The lead has already authorized these tools by passing them as extra_tools.
            mcp_extra = [t for t in (extra_tools or []) if _mcp_server_name_from_tool_id(t)]

            return sdk_factory(
                id, name, role, model=resolved_model, effort=resolved_effort, agents=effective_agents,
                pack_bodies=current_bodies,
                cwd=cwd, permission_mode=permission_mode,
                setting_sources=current_role_ss.get(role),
                allowed_tools=mcp_extra or None,
                extra_tools=extra_tools,
                mcp_servers=mcp_servers,
                env=env,
            )

        factory.requires_auth = True  # type: ignore[attr-defined]
        # Callable returning RefreshResult dict; consumed by the refresh_agents
        # MCP tool (task 3 — mcp-refresh-agents-tool).
        factory.refresh_pack = holder.refresh  # type: ignore[attr-defined]
        # Expose the merged pack to the broker so it can snapshot each
        # teammate's resolved AgentDefinition at spawn time. Without this,
        # production teammates have no `config` block and dashboard chips
        # render empty.
        def _resolve_agent_def(role: str) -> "AgentDefinition | None":
            # Mirror the spawn-path promotion so the broker's config snapshot
            # finds the same AgentDefinition that the teammate is running on.
            # Without this, a lead spawning by bare name (auto-promoted at
            # factory()) gets a correct teammate but an empty dashboard chip.
            # Reads holder.pack LIVE so post-refresh state is visible immediately.
            role = _resolve_role(role)
            agent_def = holder.pack.get(role)
            if agent_def is None:
                return None
            pack_model = getattr(agent_def, "model", None)
            if pack_model:
                resolved = _PACK_MODEL_ALIASES.get(pack_model, pack_model)
                if resolved != pack_model:
                    return dataclasses.replace(agent_def, model=resolved)
            return agent_def

        factory.agent_def_resolver = _resolve_agent_def  # type: ignore[attr-defined]
        # Frozen startup diagnostics tuple; consumed by make_server() when it
        # constructs the default Broker (Broker(startup_diagnostics=...)).
        factory.startup_diagnostics = startup_diagnostics  # type: ignore[attr-defined]
        # Live enumeration of known roles for instantiate_shape pre-flight.
        # Reads holder.pack LIVE so post-refresh state is always current.
        # Mirrors the startup_diagnostics accessor idiom above.
        factory.known_roles = lambda: tuple(holder.pack.keys())  # type: ignore[attr-defined]
        # Expose the holder so tests (and future refresh() wiring) can reach it.
        factory._holder = holder  # type: ignore[attr-defined]
        return factory
    return stub_factory
