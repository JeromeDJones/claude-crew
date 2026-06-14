"""The crew message broker.

Single source of truth for the bus: teammate registry, append-only message
log, per-recipient inbox queues, monotonic ``seq`` counter, and id-based
dedup. All state mutations happen on the asyncio event loop; no threads.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Literal
from uuid import uuid4

from claude_crew.diagnostics import StartupDiagnostic
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.shapes import Shape
from claude_crew.teammate import Teammate, ToolEvent
from claude_crew.transcript import TranscriptSink

if TYPE_CHECKING:
    from claude_agent_sdk.types import AgentDefinition

LEAD_ID = "lead"

# M2: default per-directed-edge exchange budget for the circuit breaker.
# Override via Broker._circuit_breaker_max_exchanges at construction time or
# for targeted tests.  8 exchanges per edge is the M2 default; tune per-crew
# by passing max_exchanges to Broker() once that kwarg is wired in.
CIRCUIT_BREAKER_MAX_EXCHANGES: int = 8

logger = logging.getLogger(__name__)


class UnknownTeammateError(KeyError):
    """Raised when a teammate id is not registered."""


class TeammateAlreadyDeadError(RuntimeError):
    """Raised when attempting to send to a tombstoned (killed/dead) teammate."""


class UnauthorizedEdgeError(Exception):
    """Raised when a teammate's scoped send targets a non-declared recipient."""


@dataclass(frozen=True)
class TeammateInfo:
    id: str
    name: str
    role: str
    spawned_at: float
    alive: bool
    # Death-record fields (all None for alive teammates)
    died_at_wallclock: float | None = None
    exit_code: int | None = None
    last_activity_at_wallclock_at_death: float | None = None
    idle_seconds_at_death: float | None = None
    # F8: last cleanly-bracketed tool (Pre→Post pair) observed before death/kill.
    # Populated by _tombstone_teammate from status_snapshot() BEFORE _close_open_tools
    # runs, so abandoned tools (outcome="abandoned"/"killed") never update this field
    # (SC-14 / D9). None if no tool completed cleanly before death.
    last_tool_completed_at_death: dict[str, Any] | None = None
    # F7: subagent-activity snapshot at death.
    in_flight_subagents_at_death: int | None = None
    last_subagent_completed_at_death: dict[str, Any] | None = None
    # F14: token/cost snapshot at death (numeric zero when snap available but no turns ran;
    # None only if status_snapshot() raised — callers coerce None → 0 on the wire).
    total_input_tokens_at_death: int | None = None
    total_output_tokens_at_death: int | None = None
    total_cost_usd_at_death: float | None = None
    # Last-turn deltas captured at tombstone time — preserved so the dashboard's
    # context-window bar keeps its final reading on dead teammates.
    last_turn_input_tokens_at_death: int | None = None
    last_turn_output_tokens_at_death: int | None = None
    # Peak per-invocation input from the most recent turn at death — the real
    # cliff signal (single-invocation context size, distinct from the turn-cumulative
    # number above which sums across all LLM invocations in the turn).
    last_turn_peak_invocation_input_tokens_at_death: int | None = None
    # API-authoritative model id from the most recent AssistantMessage at death.
    # None if no AssistantMessage was observed before tombstone (teammate died
    # before its first turn produced a response).
    active_model_at_death: str | None = None
    # F19 D-7: per-teammate completed-tool-events snapshot at tombstone time.
    # Captured AFTER _close_open_tools runs (step 8c) so abandoned/killed events
    # land in this tuple. None during the brief window between tombstone (step 5,
    # alive=False) and step 8c — snapshot contributes zero events from this
    # teammate during that window (E-3, intentional). Empty tuple is a possible
    # final value (teammate ran no tools before death).
    tool_events_at_death: "tuple[ToolEvent, ...] | None" = None
    # Diagnostic death-record fields (teammate-death-diagnostics). None for alive.
    stderr_tail_at_death: str | None = None
    # Snapshot of in-flight tools (current_tools) captured at tombstone time,
    # BEFORE _close_open_tools abandons them. Each entry carries redacted
    # args_summary (so the last in-flight Bash command is visible). None if the
    # teammate's snapshot could not be read; [] if no tool was in flight.
    in_flight_tools_at_death: "list[dict[str, Any]] | None" = None


@dataclass(frozen=True)
class LiveTeammateInfo:
    """Pairs a TeammateInfo with the alive-teammate-only fields the UI needs.

    ``status`` is value-copied at snapshot build time (D-2: deepcopy) so callers
    cannot reach back into teammate internals via the snapshot.
    ``model`` is captured from ``teammate._model`` (D-3 — workaround until a future
    feature promotes ``Teammate.model`` to a public attribute).
    """

    info: TeammateInfo
    status: dict[str, Any]
    model: str | None
    # Config snapshot taken at spawn time (ui-agent-transparency).
    # None when no AgentDefinition was resolved for the role; omitted from
    # the WS payload in that case (key absent, not null).
    config: "dict[str, Any] | None" = None


@dataclass
class ShapeProposal:
    """A pending/approved/declined/timed_out/instantiated gate record.

    Not frozen — ``status`` mutates as the proposal moves through its
    state machine (pending → approved | declined | timed_out | instantiated).
    """

    shape_id: str
    shape: Shape
    adaptation_diff: str | None
    status: str  # "pending" | "approved" | "declined" | "timed_out" | "instantiated"


@dataclass(frozen=True)
class Topology:
    """Recorded edges + per-edge mode + slot→teammate map after instantiation.

    ``edges`` is a tuple of (from_slot, to_slot, mode) triples. Frozen by
    construction — topology facts don't change once instantiation succeeds.
    ``slot_to_teammate`` is wrapped in a MappingProxyType on construction so
    callers cannot mutate it in place even though the field type is Mapping.
    """

    shape_name: str
    edges: "tuple[tuple[str, str, str], ...]"   # (from_slot, to_slot, mode)
    slot_to_teammate: "Mapping[str, str]"         # slot -> teammate_id (read-only proxy)

    def __post_init__(self) -> None:
        # Wrap slot_to_teammate in a read-only proxy so in-place mutation raises
        # TypeError.  dict() copy first so the proxy owns its own data and the
        # caller's original dict cannot be mutated either.
        object.__setattr__(
            self,
            "slot_to_teammate",
            MappingProxyType(dict(self.slot_to_teammate)),
        )


@dataclass(frozen=True)
class EdgeStat:
    """Per-directed-edge statistics snapshot for the dashboard and circuit breaker.

    ``mode`` is the *effective* routing mode (honors ``_edge_overrides`` and
    circuit-breaker trips); it may differ from the declared mode in
    ``Topology.edges`` when the edge has been overridden or promoted.

    ``tripped`` is True when the circuit breaker specifically tripped this edge
    (as opposed to a manual ``promote_edge`` call, which also sets an override
    but is operator-initiated rather than auto-triggered).
    """

    from_slot: str
    to_slot: str
    mode: str        # effective mode: "gated" | "tee" | "direct"
    exchanges: int   # how many tee/direct messages were delivered on this edge
    tripped: bool    # True when circuit breaker auto-tripped this edge


@dataclass(frozen=True)
class BrokerSnapshot:
    """Frozen, value-copied view of broker state for downstream consumers.

    Produced by ``Broker.snapshot()``. UIServer is the canonical consumer (#18).
    Synchronous to build (D-1) — no I/O, no awaits.
    """

    crew_id: str
    teammates: tuple[TeammateInfo, ...]
    live: tuple[LiveTeammateInfo, ...]
    log: tuple[Envelope, ...]
    tool_events: "tuple[ToolEvent, ...]" = ()  # F19 D-5: tightened from tuple[Any, ...]
    # ui-agent-transparency edge case: config snapshots for dead teammates so the
    # dashboard can render dimmed rows with accessible chips/panel post-kill.
    # Mapping: teammate_id → config_dict (only entries with non-None config included).
    dead_configs: "dict[str, dict[str, Any]]" = dataclasses.field(default_factory=dict)
    # Startup-time diagnostics captured during default_factory()'s pack-load
    # window (spec: startup-diagnostics-dashboard). Frozen by construction —
    # populated once at Broker init from the StartupDiagCollector tuple and
    # never mutated thereafter. Empty tuple when capture is skipped (stub
    # mode) or when no diagnostics were emitted (clean config). Documented
    # as startup-only by contract: a future feature wanting runtime
    # diagnostics must introduce a new field, not redefine this one.
    startup_diagnostics: "tuple[StartupDiagnostic, ...]" = ()
    # M0: shape-gate proposals. Snapshot of the _proposals dict at call time;
    # entries are mutable ShapeProposal objects — status may advance after the
    # snapshot is taken (same contract as live teammate references in BrokerSnapshot).
    shape_proposals: "tuple[ShapeProposal, ...]" = ()
    # M0: topologies recorded after successful shape instantiation.
    topologies: "tuple[Topology, ...]" = ()
    # M2: per-edge exchange stats derived from the active topology/topologies.
    # Each entry covers one directed (from_slot, to_slot) pair; mode reflects
    # the effective routing (honoring overrides/trips); tripped=True when the
    # circuit breaker auto-tripped that edge.
    topology_edge_stats: "tuple[EdgeStat, ...]" = ()


# A factory takes (id, name, role, model=None) and returns an unstarted
# Teammate. The model kwarg is optional — factories that don't care about
# model (e.g., stub) accept and ignore it.
TeammateFactory = Callable[..., Teammate]

# A resolver maps a role string to the resolved AgentDefinition for that role,
# or None if the role is absent from the merged pack.
AgentDefResolver = Callable[[str], "AgentDefinition | None"]


class Broker:
    def __init__(
        self,
        startup_diagnostics: "tuple[StartupDiagnostic, ...]" = (),
    ) -> None:
        self.crew_id: str = uuid4().hex[:8]
        self._teammates: dict[str, Teammate] = {}
        self._info: dict[str, TeammateInfo] = {}
        self._inboxes: dict[str, asyncio.Queue] = {}
        self._lead_message_condition: asyncio.Condition = asyncio.Condition()
        self._log: list[Envelope] = []
        self._seen_ids: set[str] = set()
        self._next_seq: int = 1
        # Per-teammate config snapshots keyed by teammate_id. Value is None when
        # no AgentDefinition was resolved for the role at spawn time.
        self._configs: dict[str, dict[str, Any] | None] = {}
        # Startup diagnostics tuple — frozen at construction (see field doc on
        # BrokerSnapshot). Coerced to tuple defensively so callers passing a
        # list still get an immutable internal record.
        self._startup_diagnostics: tuple[StartupDiagnostic, ...] = tuple(
            startup_diagnostics
        )
        # M0: proposal state machine — keyed by shape_id.
        self._proposals: dict[str, ShapeProposal] = {}
        # Condition notified by resolve_proposal; awaited by await_proposal.
        # Mirrors the _lead_message_condition pattern.
        self._proposal_condition: asyncio.Condition = asyncio.Condition()
        # M0: recorded topologies after successful instantiation.
        self._topologies: list[Topology] = []
        # M2: per-edge routing overrides. Keys are (from_slot, to_slot) pairs;
        # values are forced routing modes (currently always "gated") set by the
        # circuit breaker or promote_edge. Checked before the topology's declared
        # mode in _edge_mode so overrides take precedence.
        self._edge_overrides: dict[tuple[str, str], str] = {}
        # M2 circuit breaker: per-directed-edge exchange counter. Keyed by
        # (from_slot, to_slot); incremented on every successful tee/direct
        # delivery before the trip check. Counts the triggering message too.
        self._edge_exchanges: dict[tuple[str, str], int] = {}
        # M2 circuit breaker: subset of _edge_overrides that were auto-tripped
        # by the circuit breaker (as opposed to manual promote_edge calls).
        # Used to set EdgeStat.tripped=True in the snapshot.
        self._edge_tripped: set[tuple[str, str]] = set()
        # Per-edge exchange budget. Default from the module constant; tests may
        # lower this directly (broker._circuit_breaker_max_exchanges = N).
        self._circuit_breaker_max_exchanges: int = CIRCUIT_BREAKER_MAX_EXCHANGES
        # Tombstoned teammates: keyed by teammate_id, holds the Teammate object
        # after it's popped from _teammates so get_tool_output can still delegate
        # to it for evicted-but-recently-dead lookups.
        self._dead_teammates: dict[str, Teammate] = {}
        # Teammate ids currently in the graceful-flush window (between flush start
        # and tombstone). send() bounces new envelopes addressed to these ids with
        # TeammateAlreadyDeadError semantics — the flush turn must not be
        # interleaved with real inbound traffic. Ids are discarded on tombstone.
        self._terminating: set[str] = set()
        self._sink = TranscriptSink(crew_id=self.crew_id)
        self._sink.write_lifecycle("started", {})

    # ---------- spawn / kill ----------

    async def spawn_teammate(
        self,
        role: str,
        name: str | None,
        factory: TeammateFactory,
        model: str | None = None,
        effort: str | None = None,
        cwd: str | None = None,
        permission_mode: str | None = None,
        agent_def_resolver: "AgentDefResolver | None" = None,
        extra_tools: list[str] | None = None,
        extra_skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
        env: "dict[str, str] | None" = None,
        neighbors: "list[dict] | None" = None,
    ) -> str:
        teammate_id = f"t-{uuid4().hex[:12]}"
        resolved_name = name if name is not None else role
        inbox: asyncio.Queue = asyncio.Queue()
        factory_kwargs: dict = {
            "model": model, "effort": effort, "cwd": cwd,
            "permission_mode": permission_mode,
            "extra_tools": extra_tools, "extra_skills": extra_skills,
            "mcp_servers": mcp_servers,
        }
        if env is not None:
            factory_kwargs["env"] = env
        if neighbors is not None:
            factory_kwargs["neighbors"] = neighbors
        teammate = factory(teammate_id, resolved_name, role, **factory_kwargs)
        await teammate.start(self, inbox)

        self._teammates[teammate_id] = teammate
        self._inboxes[teammate_id] = inbox
        self._info[teammate_id] = TeammateInfo(
            id=teammate_id,
            name=resolved_name,
            role=role,
            spawned_at=time.time(),
            alive=True,
        )

        # Capture the config snapshot at spawn time. If the caller did not
        # pass an explicit resolver, fall back to one attached to the factory
        # (production path: factories.default_factory exposes the merged pack
        # via the factory.agent_def_resolver attribute).
        if agent_def_resolver is None:
            agent_def_resolver = getattr(factory, "agent_def_resolver", None)
        agent_def = agent_def_resolver(role) if agent_def_resolver is not None else None
        self._configs[teammate_id] = self._snapshot_config(
            agent_def=agent_def,
            effort=effort,
            permission_mode=permission_mode,
            extra_tools=extra_tools,
            extra_skills=extra_skills,
            system_prompt_override=getattr(teammate, "_system_prompt", None),
        )

        self._sink.write_lifecycle("spawn", {
            "teammate_id": teammate_id,
            "name": resolved_name,
            "role": role,
            "model": model,
            "extra_tools": extra_tools or [],
            "extra_skills": extra_skills or [],
        })
        return teammate_id

    def _snapshot_config(
        self,
        agent_def: "AgentDefinition | None",
        effort: str | None,
        permission_mode: str | None,
        extra_tools: list[str] | None = None,
        extra_skills: list[str] | None = None,
        system_prompt_override: str | None = None,
    ) -> "dict[str, Any] | None":
        """Build a config snapshot dict from a resolved AgentDefinition.

        Returns None when agent_def is None and no extras are provided
        (role absent from merged pack). When extras are provided but
        agent_def is None, returns a minimal snapshot so extras are visible
        in get_teammate_status.
        Tombstoned teammates retain their config until reaped.

        Resolution rules:
        - effort: spawn-time kwarg override, else AgentDefinition.effort
        - permission_mode: spawn-time kwarg override, else AgentDefinition.permissionMode
        - mcp_servers: names only — bare strings pass through; dict entries
          use their "name" key (falling back to "<unnamed>"). Never serializes
          any other dict field (that is where API keys live).
        - extra_tools/extra_skills: net-new tools/skills beyond the pack baseline.
          Always present as empty lists when no extras (consistent snapshot shape).
        - system_prompt_override: the fully-assembled teammate prompt (body +
          addendum) from SdkTeammate._system_prompt. Wins over agent_def.prompt,
          which holds the subagent-context prompt (different ordering/framing).
          None for StubTeammate (falls through to agent_def.prompt).
        """
        if agent_def is None:
            # No pack entry — return minimal snapshot if extras provided, else None.
            if extra_tools or extra_skills:
                effective_tools = list(dict.fromkeys(extra_tools or []))
                effective_skills = list(dict.fromkeys(extra_skills or []))
                return {
                    "model": None,
                    "tools": effective_tools,
                    "disallowed_tools": [],
                    "skills": effective_skills,
                    "permission_mode": permission_mode,
                    "mcp_servers": [],
                    "system_prompt": system_prompt_override,
                    "effort": effort,
                    # effort provenance: no pack ⇒ no pack default; resolved == requested.
                    "effort_requested": effort,
                    "effort_pack_default": None,
                    # All extras are net-new (no pack baseline to compare against)
                    "extra_tools": effective_tools[:],
                    "extra_skills": effective_skills[:],
                }
            return None

        # tools: list[str] (may be empty, must not be None)
        tools_raw = getattr(agent_def, "tools", None)
        pack_tools: list[str] = list(tools_raw) if tools_raw is not None else []

        # disallowed_tools: list[str]
        dt_raw = getattr(agent_def, "disallowedTools", None)
        disallowed_tools = list(dt_raw) if dt_raw is not None else []

        # skills: list[str] — spec data contract is always list[str].
        skills_raw = getattr(agent_def, "skills", None)
        pack_skills: list[str] = list(skills_raw) if skills_raw is not None else []

        # mcp_servers: names only
        mcp_raw = getattr(agent_def, "mcpServers", None)
        if mcp_raw is None:
            mcp_names: list[str] = []
        else:
            mcp_names = []
            for entry in mcp_raw:
                if isinstance(entry, str):
                    mcp_names.append(entry)
                elif isinstance(entry, dict):
                    mcp_names.append(entry.get("name", "<unnamed>"))

        # effort: kwarg override OR AgentDefinition.effort
        # Capture both inputs separately so the dashboard can show
        # operator "what we asked for" vs "what actually ran with".
        effort_pack_default = getattr(agent_def, "effort", None)
        resolved_effort = effort if effort is not None else effort_pack_default

        # permission_mode: kwarg override OR AgentDefinition.permissionMode
        resolved_pm = (
            permission_mode if permission_mode is not None
            else getattr(agent_def, "permissionMode", None)
        )

        # Additive+deduped merge: effective = pack ∪ extras (insertion-order preserved)
        pack_tools_set = set(pack_tools)
        effective_tools = list(dict.fromkeys(pack_tools + (extra_tools or [])))

        pack_skills_set = set(pack_skills)
        effective_skills = list(dict.fromkeys(pack_skills + (extra_skills or [])))

        # Net-new extras: items from caller's list not already in the pack (deduped)
        net_extra_tools = list(dict.fromkeys(
            t for t in (extra_tools or []) if t not in pack_tools_set
        ))
        net_extra_skills = list(dict.fromkeys(
            s for s in (extra_skills or []) if s not in pack_skills_set
        ))

        return {
            "model": getattr(agent_def, "model", None),
            "tools": effective_tools,
            "disallowed_tools": disallowed_tools,
            "skills": effective_skills,
            "permission_mode": resolved_pm,
            "mcp_servers": mcp_names,
            "system_prompt": system_prompt_override if system_prompt_override is not None else getattr(agent_def, "prompt", None),
            "effort": resolved_effort,
            # effort provenance fields (null-when-absent, never omitted):
            # - effort_requested: the spawn-time kwarg only (None ⇒ no override)
            # - effort_pack_default: AgentDefinition.effort (None ⇒ no pack default)
            "effort_requested": effort,
            "effort_pack_default": effort_pack_default,
            # Extra fields: always present as lists (empty when no extras)
            "extra_tools": net_extra_tools,
            "extra_skills": net_extra_skills,
        }

    async def _tombstone_teammate(
        self,
        teammate_id: str,
        exit_code: int | None,
        lifecycle_event_name: str,
        **lifecycle_extra: Any,
    ) -> None:
        """Shared tombstone code path for kill and death detection.

        Idempotent — silently returns if already tombstoned or unknown.

        Execution order (D2):
        1. Idempotency check
        2. End turn on teammate
        3. Capture in-flight envelope (SdkTeammate only)
        4. Snapshot activity at death
        5. Write frozen tombstone to _info (BEFORE pop — ensures concurrent
           send sees alive=False → TeammateAlreadyDeadError, not UnknownTeammateError)
        6. Pop from _teammates active set
        7. Bounce in-flight envelope (if any)
        8. Drain inbox and bounce each pending envelope
        9. Emit lifecycle event
        10. Detached shutdown (fire-and-forget, must NOT await own task)
        """
        # 1. Idempotency check
        info = self._info.get(teammate_id)
        if info is None or not info.alive:
            return

        teammate = self._teammates.get(teammate_id)

        # 2. End turn (clears current_turn_started_at_wallclock)
        if teammate is not None:
            # close_tools=False: broker owns the tool-closing call at step 8b
            # with the correct death/kill reason. Calling _end_turn() with the
            # default (close_tools=True) here would abandon the tools as
            # "turn_end" before step 8b can emit them as "death"/"killed".
            teammate._end_turn(close_tools=False)

        # 3. Capture in-flight envelope (SdkTeammate sets this; others don't)
        in_flight = (
            getattr(teammate, "_death_in_flight_envelope", None)
            if teammate is not None
            else None
        )

        # 4. Snapshot activity at death.
        # Called BEFORE _close_open_tools (between steps 8-9) so that
        # last_tool_completed reflects the last *clean* Pre→Post pair — not
        # the abandoned/killed tools that _close_open_tools is about to emit.
        # (SC-14 / D9: abandoned tools go to the transcript, not status payload.)
        if teammate is not None:
            try:
                snap = teammate.status_snapshot()
                last_activity = snap.get("last_activity_at_wallclock")
                idle_at_death = snap.get("idle_seconds", 0.0)
                last_tool_completed_at_death: dict[str, Any] | None = snap.get(
                    "last_tool_completed"
                )
                last_subagent_completed_at_death: dict[str, Any] | None = snap.get(
                    "last_subagent_completed"
                )
                in_flight_subagents_at_death: int = (
                    len(getattr(teammate, "_subagent_uses", {})) +
                    len(getattr(teammate, "_closed_subagent_scratch", {}))
                )
                # F14: capture last cumulative token/cost values (D-7: numeric zero
                # when no turns ran; overwrite semantics mean final value == cumulative).
                total_input_tokens_at_death: int = snap.get("total_input_tokens", 0)
                total_output_tokens_at_death: int = snap.get("total_output_tokens", 0)
                total_cost_usd_at_death: float = snap.get("total_cost_usd", 0.0)
                last_turn_input_tokens_at_death: int = snap.get("last_turn_input_tokens", 0)
                last_turn_output_tokens_at_death: int = snap.get("last_turn_output_tokens", 0)
                last_turn_peak_invocation_input_tokens_at_death: int = snap.get(
                    "last_turn_peak_invocation_input_tokens", 0
                )
                active_model_at_death: str | None = snap.get("active_model")
                stderr_tail_at_death: str | None = snap.get("stderr_tail")
                in_flight_tools_at_death: list[dict[str, Any]] = list(
                    snap.get("in_flight_tools", [])
                )
            except AttributeError:
                last_activity = None
                idle_at_death = None
                last_tool_completed_at_death = None
                last_subagent_completed_at_death = None
                in_flight_subagents_at_death = 0
                total_input_tokens_at_death = None
                total_output_tokens_at_death = None
                total_cost_usd_at_death = None
                last_turn_input_tokens_at_death = None
                last_turn_output_tokens_at_death = None
                last_turn_peak_invocation_input_tokens_at_death = None
                active_model_at_death = None
                stderr_tail_at_death = None
                in_flight_tools_at_death = None
        else:
            last_activity = None
            idle_at_death = None
            last_tool_completed_at_death = None
            last_subagent_completed_at_death = None
            in_flight_subagents_at_death = 0
            total_input_tokens_at_death = None
            total_output_tokens_at_death = None
            total_cost_usd_at_death = None
            last_turn_input_tokens_at_death = None
            last_turn_output_tokens_at_death = None
            last_turn_peak_invocation_input_tokens_at_death = None
            active_model_at_death = None
            stderr_tail_at_death = None
            in_flight_tools_at_death = None

        # 5. Write frozen tombstone BEFORE pop (D2 tombstone-before-pop ordering)
        self._info[teammate_id] = dataclasses.replace(
            info,
            alive=False,
            died_at_wallclock=time.time(),
            exit_code=exit_code,
            last_activity_at_wallclock_at_death=last_activity,
            idle_seconds_at_death=idle_at_death,
            last_tool_completed_at_death=last_tool_completed_at_death,
            in_flight_subagents_at_death=in_flight_subagents_at_death,
            last_subagent_completed_at_death=last_subagent_completed_at_death,
            total_input_tokens_at_death=total_input_tokens_at_death,
            total_output_tokens_at_death=total_output_tokens_at_death,
            total_cost_usd_at_death=total_cost_usd_at_death,
            last_turn_input_tokens_at_death=last_turn_input_tokens_at_death,
            last_turn_output_tokens_at_death=last_turn_output_tokens_at_death,
            last_turn_peak_invocation_input_tokens_at_death=last_turn_peak_invocation_input_tokens_at_death,
            active_model_at_death=active_model_at_death,
            stderr_tail_at_death=stderr_tail_at_death,
            in_flight_tools_at_death=in_flight_tools_at_death,
        )

        # 6. Pop from active set; stash the object in _dead_teammates so
        #    get_tool_output can still delegate to it after tombstoning.
        if teammate is not None:
            self._dead_teammates[teammate_id] = teammate
        self._teammates.pop(teammate_id, None)

        # 7. Bounce in-flight envelope (if any)
        if in_flight is not None and isinstance(in_flight, Envelope):
            await self._bounce_dead(in_flight, teammate_id, exit_code)

        # 8. Drain inbox; bounce each pending envelope
        inbox = self._inboxes.pop(teammate_id, None)
        while inbox is not None:
            try:
                pending = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(pending, Envelope):
                await self._bounce_dead(pending, teammate_id, exit_code)

        # 8b. Close open tools before lifecycle event (SC-14 / D9).
        # Emits one tool_end transcript record (outcome="abandoned" for death,
        # "killed" for kill) per still-open tool_use_id.  Must run BEFORE the
        # lifecycle line so transcript replay sees tool_end < lifecycle:died/kill.
        if teammate is not None:
            close_reason: Literal["death", "kill"] = (
                "death" if lifecycle_event_name == "died" else "kill"
            )
            teammate._close_open_tools(reason=close_reason)
            if hasattr(teammate, "_close_open_subagents"):
                teammate._close_open_subagents(reason=close_reason)

        # 8c. F19 D-7: capture per-teammate completed_tool_events into TeammateInfo.
        # Must run AFTER 8b so abandoned/killed events appended by _close_open_tools
        # are included in the captured tuple. dataclasses.replace because TeammateInfo
        # is frozen (sentinel F1 — direct mutation would raise FrozenInstanceError).
        # Defensive on missing attribute: a teammate that never initialized the deque
        # (older test fixtures, bare-bones mocks) contributes None instead of crashing.
        if teammate is not None:
            captured = getattr(teammate, "_completed_tool_events", None)
            if captured is not None:
                self._info[teammate_id] = dataclasses.replace(
                    self._info[teammate_id],
                    tool_events_at_death=tuple(captured),
                )

        # 9. Emit lifecycle event
        lifecycle_fields: dict[str, Any] = {"teammate_id": teammate_id}
        if lifecycle_event_name == "died":
            lifecycle_fields.update({
                "exit_code": exit_code,
                "idle_seconds_at_death": idle_at_death,
                "last_activity_at_wallclock": last_activity,
            })
        else:
            # "kill" and others preserve event-specific fields (e.g., reason)
            lifecycle_fields.update(lifecycle_extra)
        self._sink.write_lifecycle(lifecycle_event_name, lifecycle_fields)

        # 10. Detached shutdown — do NOT await (handler must not cancel its own task)
        if teammate is not None:
            loop = asyncio.get_running_loop()
            _t = teammate  # capture for closure

            async def _safe_shutdown() -> None:
                try:
                    await _t.shutdown()
                except Exception as exc:
                    logger.warning(
                        "shutdown error for teammate %s: %s", teammate_id, exc
                    )

            loop.create_task(_safe_shutdown())

    async def _bounce_dead(
        self,
        env: Envelope,
        dead_id: str,
        exit_code: int | None,
    ) -> None:
        """Send a teammate_dead error envelope back to env's sender."""
        info = self._info.get(dead_id)
        died_at = info.died_at_wallclock if info else None
        msg = f"teammate {dead_id!r} is dead"
        if died_at is not None:
            msg += f"; died_at={died_at:.3f}"
        if exit_code is not None:
            msg += f"; exit_code={exit_code}"
        bounce = Envelope(
            id=new_message_id(),
            seq=0,
            sender=dead_id,
            recipient=env.sender,
            timestamp=time.time(),
            payload={"error": "teammate_dead", "message": msg},
        )
        try:
            await self.send(bounce)
        except (UnknownTeammateError, TeammateAlreadyDeadError):
            # Sender is also dead or unknown — log at INFO and drop.
            logger.info(
                "dead-bounce to %r dropped: sender is dead or unknown", env.sender
            )

    async def _handle_teammate_death(
        self,
        teammate_id: str,
        exit_code: int | None,
    ) -> None:
        """Single-writer death handler for unexpected subprocess death. Idempotent."""
        await self._tombstone_teammate(teammate_id, exit_code, "died")

    async def kill_teammate(
        self, teammate_id: str, reason: str = "explicit",
        *, graceful: bool = True, flush_timeout: float = 90.0,
    ) -> None:
        if teammate_id not in self._teammates:
            # Already tombstoned → distinct error from never-existed
            if teammate_id in self._info:
                raise TeammateAlreadyDeadError(teammate_id)
            raise UnknownTeammateError(teammate_id)

        teammate = self._teammates[teammate_id]

        # Graceful flush: skip when not graceful, or teammate has no memory surface.
        # Pre-tombstone step — runs BEFORE _tombstone_teammate (D2 ordering preserved).
        if graceful and teammate.has_memory_surface():
            self._terminating.add(teammate_id)
            try:
                await asyncio.wait_for(
                    teammate.begin_graceful_termination(timeout=flush_timeout),
                    timeout=flush_timeout,
                )
            except Exception as exc:
                # Timeout or SDK error: swallow so tombstone always proceeds.
                logger.warning(
                    "graceful flush for teammate %s timed out or errored: %s",
                    teammate_id, exc,
                )
            finally:
                self._terminating.discard(teammate_id)

        await self._tombstone_teammate(teammate_id, None, "kill", reason=reason)

    async def shutdown_all(
        self, *, graceful: bool = True, flush_timeout: float = 90.0
    ) -> None:
        """Terminate all alive teammates and close the broker.

        When ``graceful=True`` (default), teammates that have a memory surface
        are flushed in **parallel** under ONE shared deadline (a single
        ``asyncio.wait_for`` over a ``gather``), not N×timeout sequentially.
        Teammates without a memory surface are hard-tombstoned directly.
        When ``graceful=False``, all teammates are hard-killed immediately.
        """
        teammate_ids = list(self._teammates.keys())

        if graceful and teammate_ids:
            # Collect teammates that need a flush.
            memory_ids = [
                tid for tid in teammate_ids
                if tid in self._teammates and self._teammates[tid].has_memory_surface()
            ]

            if memory_ids:
                # Mark all as terminating so sends bounce during the flush window.
                for tid in memory_ids:
                    self._terminating.add(tid)

                try:
                    # ONE shared deadline — flush all in parallel, not N×timeout.
                    await asyncio.wait_for(
                        asyncio.gather(
                            *(
                                self._teammates[tid].begin_graceful_termination(
                                    timeout=flush_timeout
                                )
                                for tid in memory_ids
                                if tid in self._teammates
                            ),
                            return_exceptions=True,
                        ),
                        timeout=flush_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    logger.warning(
                        "parallel graceful flush in shutdown_all timed out: %s", exc
                    )
                except Exception as exc:
                    logger.warning(
                        "parallel graceful flush in shutdown_all errored: %s", exc
                    )
                finally:
                    for tid in memory_ids:
                        self._terminating.discard(tid)

        # Tombstone all remaining alive teammates (hard-kill; parallel flush already done).
        for tid in teammate_ids:
            try:
                await self._tombstone_teammate(tid, None, "kill", reason="shutdown")
            except Exception:
                pass

        self._sink.write_lifecycle("shutdown", {
            "teammate_count": len(teammate_ids),
        })
        # Wake any pending long-polls before closing the sink so they return
        # cleanly rather than hanging until their wait_seconds cap expires.
        async with self._lead_message_condition:
            self._lead_message_condition.notify_all()
        self._sink.close()

    # ---------- send / broadcast ----------

    async def send(self, env: Envelope) -> Envelope | None:
        """Enqueue ``env`` for delivery.

        Returns the envelope as enqueued (with broker-assigned seq), or
        ``None`` if it was dropped as a duplicate.

        M2: teammate→teammate sends (both non-LEAD, sender is a live teammate)
        are routed via the active topology's edge mode (gated/tee/direct).
        Lead-origin and lead-bound sends short-circuit to the original behavior
        (unchanged). Internal/broker/dead-sender sends also bypass routing.
        """
        if env.id in self._seen_ids:
            return None

        # D6: check tombstone BEFORE _teammates (tombstoned = in _info but NOT in _teammates)
        info = self._info.get(env.recipient)
        if info is not None and not info.alive:
            raise TeammateAlreadyDeadError(env.recipient)

        # Bounce sends during graceful-flush window (terminating but not yet tombstoned).
        # The flush turn must not be interleaved with new inbound traffic.
        if env.recipient in self._terminating:
            raise TeammateAlreadyDeadError(env.recipient)

        if env.recipient != LEAD_ID and env.recipient not in self._teammates:
            raise UnknownTeammateError(env.recipient)

        # M2: teammate→teammate routing. Short-circuit for lead-bound (recipient==LEAD)
        # and lead-origin (sender==LEAD) sends, and for internal/dead-sender sends.
        # Only live-teammate→live-teammate sends go through edge routing.
        if env.recipient != LEAD_ID and env.sender in self._teammates:
            return await self._send_routed(env)

        seq = self._next_seq
        self._next_seq += 1
        stamped = Envelope(
            id=env.id,
            seq=seq,
            sender=env.sender,
            recipient=env.recipient,
            timestamp=env.timestamp if env.timestamp else time.time(),
            payload=env.payload,
        )
        self._seen_ids.add(stamped.id)
        self._log.append(stamped)
        self._sink.write_envelope(stamped.to_dict())
        if stamped.recipient == LEAD_ID:
            # LEAD has no inbox queue; readers consume via get_messages(_log).
            # Notify the long-poll Condition so any waiting get_messages call wakes.
            # CRITICAL: notify comes AFTER _log.append so every waiter that wakes
            # is guaranteed to find the new envelope when it re-reads _log.
            async with self._lead_message_condition:
                self._lead_message_condition.notify_all()
        else:
            await self._inboxes[stamped.recipient].put(stamped)
        return stamped

    async def _apply_circuit_breaker(
        self,
        env: Envelope,
        routing_mode: str,
        ts: float,
    ) -> str:
        """Update circuit-breaker state and trip the edge if necessary.

        Called for tee/direct routing candidates only. Increments
        ``_edge_exchanges`` for the (from_slot, to_slot) edge, then checks
        whether the exchange count exceeds ``_circuit_breaker_max_exchanges``.

        The per-edge exchange budget is the sole trip condition (no deadlock
        detector — spec amendment, 2026-06-13). Reciprocal ping-pong traffic
        (a→b→a→b) must stay off the lead while below the budget (AT#10
        precondition); the budget alone force-inserts the lead on a runaway loop.

        When the budget is exceeded:
        - ``_edge_overrides[(f,t)]`` is set to ``"gated"`` (idempotent guard).
        - ``_edge_tripped`` records the edge as auto-tripped.
        - A control envelope ``{type:"circuit_breaker", edge:[f,t],
          reason:"budget_exceeded"}`` is emitted to LEAD.
        - Returns ``"gated"`` so the caller re-routes the triggering message.

        When the budget is not exceeded, returns ``routing_mode`` unchanged.

        Idempotency: if the edge is already in ``_edge_overrides`` (tripped or
        manually promoted), ``_resolve_routing_mode`` would have returned "gated"
        and this method would not be called.  The guard on ``_edge_overrides``
        membership is a belt-and-suspenders defence against any bypass.
        """
        topo = self._active_topology_for(env.sender, env.recipient)
        if topo is None:
            return routing_mode

        from_slot = self._id_to_slot(topo, env.sender)
        to_slot = self._id_to_slot(topo, env.recipient)
        if from_slot is None or to_slot is None:
            return routing_mode

        forward_key = (from_slot, to_slot)

        # Increment exchange counter (counts the triggering message too).
        count = self._edge_exchanges.get(forward_key, 0) + 1
        self._edge_exchanges[forward_key] = count

        # Belt-and-suspenders: edge already overridden by a previous trip or
        # promote_edge — shouldn't reach here via _resolve_routing_mode but
        # guard for any direct-call bypass.
        if forward_key in self._edge_overrides:
            return routing_mode

        # Budget is the sole trip condition.
        if count <= self._circuit_breaker_max_exchanges:
            return routing_mode  # Within budget; normal delivery proceeds.

        # ---- TRIP ----
        self._edge_overrides[forward_key] = "gated"
        self._edge_tripped.add(forward_key)

        # Emit circuit-breaker control envelope to LEAD.
        ctrl_seq = self._next_seq
        self._next_seq += 1
        ctrl = Envelope(
            id=new_message_id(),
            seq=ctrl_seq,
            sender="broker",
            recipient=LEAD_ID,
            timestamp=ts,
            payload={
                "type": "circuit_breaker",
                "edge": [from_slot, to_slot],
                "reason": "budget_exceeded",
            },
        )
        self._seen_ids.add(ctrl.id)
        self._log.append(ctrl)
        self._sink.write_envelope(ctrl.to_dict())
        async with self._lead_message_condition:
            self._lead_message_condition.notify_all()

        # Signal to the caller to re-route the triggering message as "gated".
        return "gated"

    async def _send_routed(self, env: Envelope) -> Envelope | None:
        """Apply per-edge routing for teammate→teammate sends (M2).

        Routing modes:
          gated   → lead-bound wrapper {gated_for, from, payload} to LEAD;
                    original NOT delivered to recipient inbox and NOT in _log.
                    Original id still added to _seen_ids for dedup.
          tee     → original delivered to recipient inbox AND cc envelope
                    {cc_of, from, to, payload} to LEAD; both in _log.
          direct  → original delivered to recipient inbox; in _log; no LEAD notify.
          fallback (no edge / no topology) → same as gated.

        Circuit breaker (M2): on tee/direct modes, ``_apply_circuit_breaker``
        is called first.  If it trips the edge it returns ``"gated"`` and the
        triggering message is re-routed to LEAD; a separate control envelope
        is also emitted to LEAD by the helper.
        """
        routing_mode = self._resolve_routing_mode(env.sender, env.recipient)
        ts = env.timestamp if env.timestamp else time.time()

        # Apply circuit breaker before tee/direct delivery.  A trip converts
        # routing_mode to "gated" so the triggering message lands on LEAD.
        if routing_mode in ("direct", "tee"):
            routing_mode = await self._apply_circuit_breaker(env, routing_mode, ts)

        if routing_mode == "direct":
            seq = self._next_seq
            self._next_seq += 1
            stamped = Envelope(
                id=env.id, seq=seq, sender=env.sender, recipient=env.recipient,
                timestamp=ts, payload=env.payload,
            )
            self._seen_ids.add(stamped.id)
            self._log.append(stamped)
            self._sink.write_envelope(stamped.to_dict())
            await self._inboxes[stamped.recipient].put(stamped)
            return stamped

        elif routing_mode == "tee":
            # Deliver original to recipient inbox AND a derived cc to LEAD.
            # Both are appended to _log.
            seq = self._next_seq
            self._next_seq += 1
            stamped = Envelope(
                id=env.id, seq=seq, sender=env.sender, recipient=env.recipient,
                timestamp=ts, payload=env.payload,
            )
            self._seen_ids.add(stamped.id)
            self._log.append(stamped)
            self._sink.write_envelope(stamped.to_dict())
            await self._inboxes[stamped.recipient].put(stamped)

            # Derived cc envelope to LEAD (new id so it has its own dedup slot)
            cc_seq = self._next_seq
            self._next_seq += 1
            cc_id = new_message_id()
            cc = Envelope(
                id=cc_id, seq=cc_seq, sender=env.sender, recipient=LEAD_ID,
                timestamp=ts,
                payload={
                    "cc_of": stamped.id,
                    "from": env.sender,
                    "to": env.recipient,
                    "payload": env.payload,
                },
            )
            self._seen_ids.add(cc.id)
            self._log.append(cc)
            self._sink.write_envelope(cc.to_dict())
            async with self._lead_message_condition:
                self._lead_message_condition.notify_all()
            return stamped

        else:
            # gated or no-edge fallback: lead-bound wrapper; NOT to recipient inbox.
            # Mark original id as seen (dedup) without logging the original.
            self._seen_ids.add(env.id)

            wrapper_seq = self._next_seq
            self._next_seq += 1
            wrapper_id = new_message_id()
            wrapper = Envelope(
                id=wrapper_id, seq=wrapper_seq, sender=env.sender, recipient=LEAD_ID,
                timestamp=ts,
                payload={
                    "gated_for": env.recipient,
                    "from": env.sender,
                    "payload": env.payload,
                },
            )
            self._seen_ids.add(wrapper.id)
            self._log.append(wrapper)
            self._sink.write_envelope(wrapper.to_dict())
            async with self._lead_message_condition:
                self._lead_message_condition.notify_all()
            return wrapper

    async def broadcast(
        self,
        sender: str,
        payload: Any,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Fan-out one envelope per alive teammate (sender excluded).

        Returns dict with ``message_ids`` (delivered ids) and
        ``skipped_dead`` (list of tombstoned teammate ids skipped).
        """
        # D12: filter to alive recipients only
        alive_recipients = [tid for tid in self._teammates if tid != sender]
        dead_recipients = [
            tid for tid, info in self._info.items()
            if not info.alive and tid != sender
        ]

        out_ids: list[str] = []
        for rid in alive_recipients:
            mid = id if id is not None else new_message_id()
            if id is not None:
                mid = f"{id}:{rid}"
            env = Envelope(
                id=mid,
                seq=0,
                sender=sender,
                recipient=rid,
                timestamp=time.time(),
                payload=payload,
            )
            stamped = await self.send(env)
            if stamped is not None:
                out_ids.append(stamped.id)
        return {"message_ids": out_ids, "skipped_dead": dead_recipients}

    # ---------- reads ----------

    def get_messages(
        self,
        recipient: str,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[Envelope]:
        result = [m for m in self._log if m.recipient == recipient and m.seq > since_seq]
        if limit is not None:
            result = result[:limit]
        return result

    async def wait_for_lead_message(self, timeout: float) -> None:
        """Block until any send to LEAD fires the Condition, or timeout elapses.

        No-op when timeout <= 0. Returns silently on timeout (no exception).
        The caller is responsible for re-reading get_messages() after this
        returns — this helper does not inspect or modify _log.

        Uses asyncio.timeout() (Python 3.11+, project requires 3.12) rather than
        asyncio.wait_for() to avoid the extra Task-wrapping semantics of the latter
        for asyncio.Condition.wait() coroutines.
        """
        if timeout <= 0:
            return
        async with self._lead_message_condition:
            try:
                async with asyncio.timeout(timeout):
                    await self._lead_message_condition.wait()
            except TimeoutError:
                pass

    def list_crew(self) -> list[TeammateInfo]:
        """Return all TeammateInfo entries, including tombstoned (alive=False) teammates."""
        return list(self._info.values())

    def snapshot(self, log_limit: int | None = None) -> BrokerSnapshot:
        """Return a frozen, value-copied view of broker state.

        Synchronous and in-memory only (D-1). Status dicts deep-copied (D-2)
        so callers can't reach back into live teammate state.

        Args:
            log_limit: If int, return the last N envelopes; if None (default),
                return the full log.
        """
        teammates_tuple = tuple(self._info.values())

        live_entries: list[LiveTeammateInfo] = []
        for info in teammates_tuple:
            if not info.alive:
                continue
            teammate = self._teammates.get(info.id)
            status: dict[str, Any] = {}
            if teammate is not None:
                try:
                    raw = teammate.status_snapshot()
                except Exception:
                    raw = {}
                status = copy.deepcopy(raw)
            model = getattr(teammate, "_model", None) if teammate is not None else None
            # Config snapshot taken at spawn time; None when no AgentDef resolved.
            config = self._configs.get(info.id)
            live_entries.append(LiveTeammateInfo(
                info=info, status=status, model=model, config=config,
            ))

        if log_limit is None:
            log_tuple = tuple(self._log)
        else:
            log_tuple = tuple(self._log[-log_limit:])

        # F19 D-6: flatten per-teammate completed-tool-events. Live teammates
        # contribute their current deque; tombstoned teammates contribute their
        # frozen tool_events_at_death tuple (D-7). Stable sort by
        # finished_at_wallclock asc gives deterministic chronological order.
        # Mid-tombstone window where info.alive=False but tool_events_at_death=None
        # contributes zero events from that teammate (E-3, intentional).
        all_tool_events: list[ToolEvent] = []
        for info in teammates_tuple:
            if info.alive:
                tm = self._teammates.get(info.id)
                if tm is not None:
                    captured = getattr(tm, "_completed_tool_events", None)
                    if captured is not None:
                        all_tool_events.extend(captured)
            elif info.tool_events_at_death is not None:
                all_tool_events.extend(info.tool_events_at_death)
        all_tool_events.sort(key=lambda e: e.finished_at_wallclock)

        # ui-agent-transparency: surface config for dead teammates so the dashboard
        # can render dimmed rows with accessible chips/panel post-kill.
        dead_configs: dict[str, Any] = {}
        for info in teammates_tuple:
            if not info.alive:
                cfg = self._configs.get(info.id)
                if cfg is not None:
                    dead_configs[info.id] = cfg

        # M2: build topology_edge_stats from all recorded topologies.
        # Walk in reverse order so later topologies take precedence when the
        # same (from_slot, to_slot) pair appears in multiple topologies.
        edge_stats: list[EdgeStat] = []
        seen_edge_keys: set[tuple[str, str]] = set()
        for topo in reversed(self._topologies):
            for f_slot, t_slot, _declared_mode in topo.edges:
                key = (f_slot, t_slot)
                if key in seen_edge_keys:
                    continue
                seen_edge_keys.add(key)
                effective_mode = self._edge_mode(topo, f_slot, t_slot) or _declared_mode
                edge_stats.append(EdgeStat(
                    from_slot=f_slot,
                    to_slot=t_slot,
                    mode=effective_mode,
                    exchanges=self._edge_exchanges.get(key, 0),
                    tripped=key in self._edge_tripped,
                ))

        return BrokerSnapshot(
            crew_id=self.crew_id,
            teammates=teammates_tuple,
            live=tuple(live_entries),
            log=log_tuple,
            tool_events=tuple(all_tool_events),
            dead_configs=dead_configs,
            startup_diagnostics=self._startup_diagnostics,
            shape_proposals=tuple(self._proposals.values()),
            topologies=tuple(self._topologies),
            topology_edge_stats=tuple(edge_stats),
        )

    def get_teammate_status(self, teammate_id: str) -> dict[str, Any]:
        """Read-only status for alive or tombstoned teammates.

        Returns a uniform payload shape regardless of alive/dead status.
        Unknown id returns an error dict matching the existing unknown_teammate shape.

        F8 additions — always present on alive and tombstoned payloads:
            current_tools: list of in-flight tool dicts, each with
                {tool_name, tool_use_id, started_at_wallclock, args_summary}.
                Empty list for tombstoned teammates.
            current_tool: convenience accessor — last-started tool name, or
                null if no tool is in flight (SC-9 last-started semantics).
            current_tool_count: len(current_tools).
            last_tool_completed: dict from the most recent fully-bracketed
                Pre→Post pair, or null if none completed cleanly.  Tombstoned
                teammates preserve the last clean value captured before death.
            redaction_version: active redaction schema version, or null for
                tombstoned teammates.
        """
        info = self._info.get(teammate_id)
        if info is None:
            return {
                "error": "unknown_teammate",
                "message": f"no teammate with id {teammate_id!r}",
            }

        if not info.alive:
            # Short-circuit: all data comes from the frozen tombstone (D3).
            # Do NOT call status_snapshot() — the teammate was popped from _teammates.
            # Force current_turn_started_at_wallclock=None (D3 defense-in-depth on top of
            # _end_turn() in the death handler).
            dead_result: dict[str, Any] = {
                "teammate_id": teammate_id,
                "name": info.name,
                "role": info.role,
                "alive": False,
                "spawned_at": info.spawned_at,
                "last_activity_at_wallclock": info.last_activity_at_wallclock_at_death,
                "current_turn_started_at_wallclock": None,
                "idle_seconds": info.idle_seconds_at_death,
                "died_at_wallclock": info.died_at_wallclock,
                "exit_code": info.exit_code,
                "last_activity_at_wallclock_at_death": info.last_activity_at_wallclock_at_death,
                # F8 additions (D11 / SC-7): tools are gone after death; last
                # cleanly-finished tool is preserved in the tombstone (SC-14).
                "current_tools": [],
                "current_tool": None,
                "current_tool_count": 0,
                "last_tool_completed": info.last_tool_completed_at_death,
                "redaction_version": None,
                # F7 additions: subagent-activity fields preserved from tombstone.
                "current_subagents": [],
                "last_subagent_completed": info.last_subagent_completed_at_death,
                "in_flight_subagents_at_death": info.in_flight_subagents_at_death,
                # F14: token/cost fields preserved from tombstone (always numeric on wire).
                "total_input_tokens": info.total_input_tokens_at_death if info.total_input_tokens_at_death is not None else 0,
                "total_output_tokens": info.total_output_tokens_at_death if info.total_output_tokens_at_death is not None else 0,
                "total_cost_usd": info.total_cost_usd_at_death if info.total_cost_usd_at_death is not None else 0.0,
                "last_turn_input_tokens": info.last_turn_input_tokens_at_death if info.last_turn_input_tokens_at_death is not None else 0,
                "last_turn_output_tokens": info.last_turn_output_tokens_at_death if info.last_turn_output_tokens_at_death is not None else 0,
                "last_turn_peak_invocation_input_tokens": info.last_turn_peak_invocation_input_tokens_at_death if info.last_turn_peak_invocation_input_tokens_at_death is not None else 0,
                "active_model": info.active_model_at_death,
                # teammate-death-diagnostics: death-record fields
                "stderr_tail_at_death": info.stderr_tail_at_death,
                "in_flight_tools_at_death": info.in_flight_tools_at_death,
            }
            # Config snapshot retained from spawn (omit key when no AgentDef resolved).
            config = self._configs.get(teammate_id)
            if config is not None:
                dead_result["config"] = config
            return dead_result

        # Alive: combine TeammateInfo lifecycle fields with live activity snapshot
        teammate = self._teammates.get(teammate_id)
        snap = teammate.status_snapshot() if teammate is not None else {}

        alive_result: dict[str, Any] = {
            "teammate_id": teammate_id,
            "name": info.name,
            "role": info.role,
            "alive": True,
            "spawned_at": info.spawned_at,
            "last_activity_at_wallclock": snap.get("last_activity_at_wallclock"),
            "current_turn_started_at_wallclock": snap.get("current_turn_started_at_wallclock"),
            "idle_seconds": snap.get("idle_seconds"),
            "died_at_wallclock": None,
            "exit_code": None,
            "last_activity_at_wallclock_at_death": None,
            # F8 additions (D11 / SC-7): surface tool-tracking fields from
            # status_snapshot().  All keys are always present; values are null
            # when no tool has fired yet.
            "current_tools": snap.get("current_tools", []),
            "current_tool": snap.get("current_tool"),
            "current_tool_count": snap.get("current_tool_count", 0),
            "last_tool_completed": snap.get("last_tool_completed"),
            "redaction_version": snap.get("redaction_version"),
            # F7 additions: subagent-activity fields from status_snapshot().
            "current_subagents": snap.get("current_subagents", []),
            "last_subagent_completed": snap.get("last_subagent_completed"),
            "in_flight_subagents_at_death": None,
            # F14: token/cost fields from live snapshot (always numeric per T2 contract).
            "total_input_tokens": snap.get("total_input_tokens", 0),
            "total_output_tokens": snap.get("total_output_tokens", 0),
            "total_cost_usd": snap.get("total_cost_usd", 0.0),
            "last_turn_input_tokens": snap.get("last_turn_input_tokens", 0),
            "last_turn_output_tokens": snap.get("last_turn_output_tokens", 0),
            "last_turn_peak_invocation_input_tokens": snap.get("last_turn_peak_invocation_input_tokens", 0),
            "active_model": snap.get("active_model"),
        }
        # Config snapshot from spawn time (omit key when no AgentDef resolved).
        config = self._configs.get(teammate_id)
        if config is not None:
            alive_result["config"] = config
        return alive_result

    # ---------- proposals / topologies (M0) ----------

    def register_proposal(
        self,
        shape: Shape,
        adaptation_diff: str | None = None,
    ) -> str:
        """Register a new shape proposal with status 'pending'.

        Returns the shape_id (a 12-hex-char unique key) so the caller can
        pass it to await_proposal / resolve_proposal / get_proposal.
        """
        shape_id = uuid4().hex[:12]
        proposal = ShapeProposal(
            shape_id=shape_id,
            shape=shape,
            adaptation_diff=adaptation_diff,
            status="pending",
        )
        self._proposals[shape_id] = proposal
        return shape_id

    async def await_proposal(self, shape_id: str, timeout: float) -> ShapeProposal:
        """Block until the proposal's status advances from 'pending', or timeout.

        Mirrors the _lead_message_condition long-poll idiom:
        - acquires _proposal_condition
        - loops on status == "pending", calling condition.wait()
        - on asyncio.timeout, sets status to 'timed_out' and returns

        Uses asyncio.timeout() (Python 3.12) rather than asyncio.wait_for()
        to avoid extra Task-wrapping semantics for asyncio.Condition.wait().
        """
        proposal = self._proposals.get(shape_id)
        if proposal is None:
            raise KeyError(f"unknown shape_id: {shape_id!r}")

        async with self._proposal_condition:
            try:
                async with asyncio.timeout(timeout):
                    while proposal.status == "pending":
                        await self._proposal_condition.wait()
            except TimeoutError:
                if proposal.status == "pending":
                    proposal.status = "timed_out"

        return proposal

    async def resolve_proposal(self, shape_id: str, decision: str) -> ShapeProposal:
        """Resolve a pending proposal to 'approved' or 'declined'.

        decision must be 'approve' or 'decline'.
        Notifies _proposal_condition so any awaiting await_proposal unblocks.

        Raises KeyError if shape_id is unknown.
        Raises ValueError if decision is invalid or the proposal is not 'pending'.
        """
        if decision not in ("approve", "decline"):
            raise ValueError(
                f"decision must be 'approve' or 'decline', got {decision!r}"
            )
        proposal = self._proposals.get(shape_id)
        if proposal is None:
            raise KeyError(f"unknown shape_id: {shape_id!r}")
        if proposal.status != "pending":
            raise ValueError(
                f"cannot resolve proposal {shape_id!r} in state {proposal.status!r}; "
                "only 'pending' is resolvable"
            )

        proposal.status = "approved" if decision == "approve" else "declined"
        async with self._proposal_condition:
            self._proposal_condition.notify_all()
        # Notify the lead inbox so get_messages wakes without polling.
        # Guard fires above, so only one notify per successful resolution.
        await self.send(Envelope(
            id=new_message_id(),
            seq=0,
            sender="broker",
            recipient=LEAD_ID,
            timestamp=time.time(),
            payload={"type": "shape_resolved", "shape_id": shape_id, "status": proposal.status},
        ))
        return proposal

    def get_proposal(self, shape_id: str) -> "ShapeProposal | None":
        """Return the ShapeProposal for the given shape_id, or None if unknown."""
        return self._proposals.get(shape_id)

    def mark_instantiated(self, shape_id: str) -> None:
        """Transition an approved proposal to 'instantiated'.

        Raises KeyError if shape_id is unknown.
        Raises ValueError if the proposal is not in 'approved' state — enforces
        that instantiation is the sole path from 'approved' and cannot override
        a pending/declined/timed_out/already-instantiated proposal.
        """
        proposal = self._proposals.get(shape_id)
        if proposal is None:
            raise KeyError(f"unknown shape_id: {shape_id!r}")
        if proposal.status != "approved":
            raise ValueError(
                f"cannot instantiate proposal {shape_id!r} in state {proposal.status!r}; "
                "only 'approved' is instantiable"
            )
        proposal.status = "instantiated"

    def record_topology(self, topology: Topology) -> None:
        """Record a topology after successful shape instantiation."""
        self._topologies.append(topology)

    def get_topologies(self) -> "tuple[Topology, ...]":
        """Return all recorded topologies as an immutable tuple."""
        return tuple(self._topologies)

    # ---------- M2 edge routing (helpers) ----------

    def _active_topology_for(
        self, sender_id: str, recipient_id: str
    ) -> "Topology | None":
        """Return the latest recorded Topology whose slot_to_teammate contains
        BOTH sender_id and recipient_id; else None.

        "Latest" = last element in _topologies that satisfies the predicate.
        """
        for topo in reversed(self._topologies):
            vals = topo.slot_to_teammate.values()
            if sender_id in vals and recipient_id in vals:
                return topo
        return None

    def _id_to_slot(self, topo: "Topology", teammate_id: str) -> "str | None":
        """Reverse-map a teammate_id to its slot name in this topology."""
        for slot, tid in topo.slot_to_teammate.items():
            if tid == teammate_id:
                return slot
        return None

    def _edge_mode(
        self, topo: "Topology", from_slot: str, to_slot: str
    ) -> "str | None":
        """Return the routing mode for the forward edge (from_slot→to_slot).

        Checks _edge_overrides first (circuit-breaker / promote_edge results)
        then falls back to the declared mode in topo.edges. Returns None when
        no such forward edge exists.
        """
        override = self._edge_overrides.get((from_slot, to_slot))
        if override is not None:
            return override
        for f, t, mode in topo.edges:
            if f == from_slot and t == to_slot:
                return mode
        return None

    def _resolve_routing_mode(self, sender_id: str, recipient_id: str) -> str:
        """Resolve the effective routing mode for a teammate→teammate send.

        Returns "gated", "tee", or "direct". Falls back to "gated" when no
        active topology contains both endpoints, or no forward edge is declared.
        """
        topo = self._active_topology_for(sender_id, recipient_id)
        if topo is None:
            return "gated"
        sender_slot = self._id_to_slot(topo, sender_id)
        recipient_slot = self._id_to_slot(topo, recipient_id)
        if sender_slot is None or recipient_slot is None:
            return "gated"
        mode = self._edge_mode(topo, sender_slot, recipient_slot)
        return mode if mode is not None else "gated"

    def _resolve_scoped_recipient(
        self, sender_id: str, recipient: str
    ) -> str:
        """Resolve a send_scoped recipient to a concrete teammate_id.

        Accepts slot name, teammate_id (alive or tombstoned), or LEAD_ID.
        Raises UnauthorizedEdgeError when the recipient cannot be resolved.
        """
        if recipient == LEAD_ID:
            return LEAD_ID
        # Known teammate_id (alive or tombstoned in _info)
        if recipient in self._info:
            return recipient
        # Try as slot name in the latest topology containing sender_id
        for topo in reversed(self._topologies):
            if sender_id in topo.slot_to_teammate.values():
                if recipient in topo.slot_to_teammate:
                    return topo.slot_to_teammate[recipient]
        raise UnauthorizedEdgeError(
            f"cannot resolve recipient {recipient!r} for sender {sender_id!r}"
        )

    # ---------- M2 edge routing (public API) ----------

    def authorize_send(self, sender_id: str, recipient_id: str) -> None:
        """Authorize a directed send from sender_id to recipient_id.

        No-op when recipient_id == LEAD_ID (lead is always reachable).
        Else raises UnauthorizedEdgeError unless a forward edge
        (sender_slot→recipient_slot) exists in the active topology.
        """
        if recipient_id == LEAD_ID:
            return
        topo = self._active_topology_for(sender_id, recipient_id)
        if topo is None:
            raise UnauthorizedEdgeError(
                f"no active topology containing both {sender_id!r} and {recipient_id!r}"
            )
        sender_slot = self._id_to_slot(topo, sender_id)
        recipient_slot = self._id_to_slot(topo, recipient_id)
        if sender_slot is None or recipient_slot is None:
            raise UnauthorizedEdgeError(
                f"could not resolve slots for {sender_id!r}→{recipient_id!r}"
            )
        if self._edge_mode(topo, sender_slot, recipient_slot) is None:
            raise UnauthorizedEdgeError(
                f"no forward edge {sender_slot!r}→{recipient_slot!r} in active topology"
            )

    async def send_scoped(
        self,
        sender_id: str,
        recipient: str,
        payload: Any,
        *,
        id: str | None = None,
    ) -> "Envelope | None":
        """Send from sender_id to recipient with topology-based authorization.

        recipient may be a slot name OR teammate_id OR LEAD_ID.
        Slot names are resolved to teammate_ids via the active topology.
        Calls authorize_send before enqueuing — raises UnauthorizedEdgeError
        when the recipient is not a declared out-edge neighbor of sender.
        On success, builds an Envelope and calls send().
        """
        resolved = self._resolve_scoped_recipient(sender_id, recipient)
        self.authorize_send(sender_id, resolved)
        env = Envelope(
            id=id if id is not None else new_message_id(),
            seq=0,
            sender=sender_id,
            recipient=resolved,
            timestamp=time.time(),
            payload=payload,
        )
        return await self.send(env)

    def promote_edge(self, from_slot: str, to_slot: str) -> None:
        """Force an edge to 'gated' (operator steps back onto it).

        Sets _edge_overrides[(from_slot, to_slot)] = 'gated'. Idempotent —
        already-gated overrides are silently overwritten with the same value.
        Unknown edges record a harmless override (no edge ever matches it).
        """
        self._edge_overrides[(from_slot, to_slot)] = "gated"

    def get_tool_output(self, teammate_id: str, tool_use_id: str) -> "str | None":
        """Return the stored tool output for the given (teammate_id, tool_use_id) pair.

        Walks both live and tombstoned teammates.  Returns None when:
        - teammate_id is unknown, or
        - tool_use_id was evicted from the rolling 50-entry buffer.
        """
        # Check live teammates first.
        tm: Teammate | None = self._teammates.get(teammate_id)
        if tm is None:
            # Fall back to tombstoned teammates preserved for post-death lookups.
            tm = self._dead_teammates.get(teammate_id)
        if tm is None:
            return None
        return tm.get_tool_output(tool_use_id)
