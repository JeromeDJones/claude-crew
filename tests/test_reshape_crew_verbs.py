"""Tests for the reshape_crew MCP tool's per-verb live dispatch — ATs 5-9.

AT5: set_gate → set_edge_override only; no spawn/kill/inform/topology append.
AT6: add_node → spawn new node, record new Topology carrying BOTH old+new ids,
     existing source teammate_id UNCHANGED (no respawn), and source is informed
     via a {type:'crew_reshape', event:'neighbor_added'} message.
AT7: augment → same as add_node (respawn-free wiring for existing source).
AT8: drop → new minus-topology appended, dropped teammate graceful-killed,
     stale _edge_overrides swept, surviving affected neighbours informed.
AT9: swap → replacement spawned + record_topology BEFORE kill_teammate(old),
     asserted via call-order capture (no dead-slot window).

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import Broker, ShapeProposal, Topology
from claude_crew.factories import stub_factory
from claude_crew.server import make_server
from claude_crew.shapes import parse_shape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    assert result.content, f"empty content: {result}"
    return json.loads(result.content[0].text)


def _client(broker: Broker | None = None, factory: Any = None):
    server = make_server(broker=broker, factory=factory)
    return create_connected_server_and_client_session(server)


async def _poll_for_new_proposal(
    broker: Broker,
    existing_ids: set[str],
    max_attempts: int = 200,
    delay: float = 0.02,
) -> ShapeProposal:
    """Poll until a proposal appears whose shape_id is NOT in existing_ids."""
    for _ in range(max_attempts):
        snap = broker.snapshot()
        for p in snap.shape_proposals:
            if p.shape_id not in existing_ids:
                return p
        await asyncio.sleep(delay)
    raise AssertionError(
        f"No new proposal appeared after {max_attempts * delay:.2f}s "
        f"(known ids: {sorted(existing_ids)})"
    )


async def _make_running_crew(
    broker: Broker,
    shape_dict: dict,
    *,
    factory: Any = stub_factory,
) -> tuple[str, dict[str, str]]:
    """Register + approve a proposal, spawn one stub teammate per node,
    record the topology, and mark_instantiated. Returns (shape_id,
    slot_to_teammate)."""
    parsed = parse_shape(shape_dict)
    sid = broker.register_proposal(parsed)
    await broker.resolve_proposal(sid, "approve")

    slot_to_teammate: dict[str, str] = {}
    for node in parsed.nodes:
        tid = await broker.spawn_teammate(
            role=node.role,
            name=node.slot,
            factory=factory,
            model=node.model,
            cwd=node.cwd,
        )
        slot_to_teammate[node.slot] = tid

    topo = Topology(
        shape_name=parsed.name,
        edges=tuple(
            (e.from_slot, e.to_slot, e.mode) for e in parsed.edges
        ),
        slot_to_teammate=slot_to_teammate,
    )
    broker.record_topology(topo)
    broker.mark_instantiated(sid)
    return sid, slot_to_teammate


def _lead_inbox_payloads(broker: Broker) -> list[dict]:
    """Return payloads of envelopes delivered to LEAD from the broker log."""
    from claude_crew.broker import LEAD_ID
    return [env.payload for env in broker._log if env.recipient == LEAD_ID]


def _teammate_inbox_payloads(broker: Broker, teammate_id: str) -> list[dict]:
    """Return payloads of envelopes delivered to teammate_id from the broker log."""
    return [
        env.payload for env in broker._log
        if env.recipient == teammate_id
    ]


def _alive_ids(broker: Broker) -> set[str]:
    """Return the set of teammate_ids currently alive in the broker."""
    return {info.id for info in broker.list_crew() if info.alive}


def _all_ids(broker: Broker) -> set[str]:
    """Return the set of teammate_ids known to the broker (alive + tombstoned)."""
    return {info.id for info in broker.list_crew()}


# ---------------------------------------------------------------------------
# Shape fixtures
# ---------------------------------------------------------------------------

# Base shape with a `direct` edge — used for set_gate (AT 5) and swap (AT 9) tests.
_SHAPE_WITH_EDGE: dict = {
    "name": "crew-with-edge",
    "description": "impl → reviewer (direct)",
    "nodes": [
        {"slot": "impl", "role": "builder"},
        {"slot": "reviewer", "role": "sentinel"},
    ],
    "edges": [
        {"from_slot": "impl", "to_slot": "reviewer", "mode": "direct"},
    ],
}

# Single-impl base — used for add_node (AT 6) and augment (AT 7).
_SHAPE_IMPL_ONLY: dict = {
    "name": "crew-impl-only",
    "description": "single node",
    "nodes": [
        {"slot": "impl", "role": "builder"},
    ],
    "edges": [],
}

# Edgeless two-node base — used for drop (AT 8): stale override survives to be swept.
_SHAPE_EDGELESS_AB: dict = {
    "name": "crew-edgeless-ab",
    "description": "a and b, no edges",
    "nodes": [
        {"slot": "a", "role": "builder"},
        {"slot": "b", "role": "sentinel"},
    ],
    "edges": [],
}


@pytest.fixture(autouse=False)
def clean_stub_known_roles():
    """Ensure stub_factory.known_roles / resolve_role are absent after the test."""
    yield
    for attr in ("known_roles", "resolve_role"):
        if hasattr(stub_factory, attr):
            delattr(stub_factory, attr)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AT 5 — set_gate live: set_edge_override only, no other effect
# ---------------------------------------------------------------------------


class TestSetGateLive:
    """AT 5: approved set_gate writes _edge_overrides only.

    No teammate spawned, no teammate killed, no informing message sent, no
    new Topology appended.
    """

    async def test_set_gate_writes_override_and_nothing_else(self) -> None:
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_WITH_EDGE
        )
        impl_id = slot_to_teammate["impl"]
        reviewer_id = slot_to_teammate["reviewer"]
        pre_topologies = len(broker.get_topologies())
        pre_crew_ids = _alive_ids(broker)
        pre_log_len = len(broker._log)

        async with _client(broker=broker) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "gated",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        assert result["verb"] == "set_gate"
        assert result["status"] == "instantiated"

        # Override was written.
        assert broker._edge_overrides.get(("impl", "reviewer")) == "gated"

        # actions record populated correctly.
        acts = result["actions"]
        assert acts["edge_overrides_set"] == [["impl", "reviewer", "gated"]]
        assert acts["spawned"] == []
        assert acts["killed"] == []
        assert acts["informed"] == []
        assert acts["edge_overrides_removed"] == []

        # No teammate spawned or killed.
        post_crew_ids = _alive_ids(broker)
        assert post_crew_ids == pre_crew_ids
        assert impl_id in post_crew_ids
        assert reviewer_id in post_crew_ids

        # No new Topology appended.
        assert len(broker.get_topologies()) == pre_topologies

        # No informing message sent to either teammate (only lead-bound
        # broker envelopes may exist; none go to the live teammates).
        assert not any(
            env.recipient in (impl_id, reviewer_id)
            and isinstance(env.payload, dict)
            and env.payload.get("type") == "crew_reshape"
            for env in broker._log[pre_log_len:]
        )


# ---------------------------------------------------------------------------
# AT 6 — add_node live: spawn + record topology + inform running source
# ---------------------------------------------------------------------------


class TestAddNodeLive:
    """AT 6: add_node spawns new teammate; existing source keeps its id;
    source teammate receives {type:'crew_reshape', event:'neighbor_added'}."""

    async def test_add_node_spawns_records_and_informs_running_source(self) -> None:
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_IMPL_ONLY
        )
        original_impl_id = slot_to_teammate["impl"]
        pre_topologies = len(broker.get_topologies())

        async with _client(broker=broker) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "add_node",
                        "params": {
                            "node": {"slot": "reviewer", "role": "sentinel"},
                            "edges": [
                                {
                                    "from_slot": "impl",
                                    "to_slot": "reviewer",
                                    "mode": "direct",
                                }
                            ],
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        assert result["verb"] == "add_node"

        acts = result["actions"]
        # (a) a new teammate was spawned for `reviewer`.
        assert len(acts["spawned"]) == 1
        spawned = acts["spawned"][0]
        assert spawned["slot"] == "reviewer"
        assert spawned["role"] == "sentinel"
        new_reviewer_id = spawned["teammate_id"]
        assert new_reviewer_id != original_impl_id

        # (b) a new Topology is appended whose slot_to_teammate contains BOTH
        # impl's ORIGINAL id and the new reviewer id.
        assert len(broker.get_topologies()) == pre_topologies + 1
        latest = broker.latest_topology()
        assert latest is not None
        assert latest.slot_to_teammate["impl"] == original_impl_id
        assert latest.slot_to_teammate["reviewer"] == new_reviewer_id
        assert ("impl", "reviewer", "direct") in latest.edges

        # (c) impl's teammate_id is UNCHANGED (no respawn).
        crew_ids = _alive_ids(broker)
        assert original_impl_id in crew_ids
        assert new_reviewer_id in crew_ids

        # (d) impl receives a {type:'crew_reshape', event:'neighbor_added'}.
        impl_msgs = _teammate_inbox_payloads(broker, original_impl_id)
        crew_reshape_msgs = [
            p for p in impl_msgs
            if isinstance(p, dict) and p.get("type") == "crew_reshape"
        ]
        assert len(crew_reshape_msgs) == 1
        msg = crew_reshape_msgs[0]
        assert msg["event"] == "neighbor_added"
        assert msg["neighbor_slot"] == "reviewer"
        assert msg["neighbor_role"] == "sentinel"
        assert msg["reachable_via"] == "send_to"

        # actions.informed carries the source teammate_id.
        assert acts["informed"] == [original_impl_id]

        # No teammate was killed.
        assert acts["killed"] == []

        # The freshly-spawned reviewer itself was NOT informed (edge cases:
        # source-is-new-node no-inform).
        reviewer_msgs = _teammate_inbox_payloads(broker, new_reviewer_id)
        reshape_msgs = [
            p for p in reviewer_msgs
            if isinstance(p, dict) and p.get("type") == "crew_reshape"
        ]
        assert reshape_msgs == []


# ---------------------------------------------------------------------------
# AT 7 — augment live: same behaviour as add_node (respawn-free wiring)
# ---------------------------------------------------------------------------


class TestAugmentLive:
    """AT 7: augment spawns new node, appends Topology, informs running source."""

    async def test_augment_spawns_records_and_informs_running_source(self) -> None:
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_IMPL_ONLY
        )
        original_impl_id = slot_to_teammate["impl"]
        pre_topologies = len(broker.get_topologies())

        async with _client(broker=broker) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "augment",
                        "params": {
                            "node": {"slot": "reviewer", "role": "sentinel"},
                            "edges": [
                                {
                                    "from_slot": "impl",
                                    "to_slot": "reviewer",
                                    "mode": "direct",
                                }
                            ],
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        assert result["verb"] == "augment"

        acts = result["actions"]
        assert len(acts["spawned"]) == 1
        assert acts["spawned"][0]["slot"] == "reviewer"
        new_reviewer_id = acts["spawned"][0]["teammate_id"]

        # New Topology recorded, containing BOTH the original impl id and the
        # new reviewer id.
        assert len(broker.get_topologies()) == pre_topologies + 1
        latest = broker.latest_topology()
        assert latest is not None
        assert latest.slot_to_teammate["impl"] == original_impl_id
        assert latest.slot_to_teammate["reviewer"] == new_reviewer_id

        # Impl was NOT respawned.
        assert original_impl_id in _alive_ids(broker)

        # Impl received a neighbor_added inform.
        impl_msgs = _teammate_inbox_payloads(broker, original_impl_id)
        crew_reshape_msgs = [
            p for p in impl_msgs
            if isinstance(p, dict) and p.get("type") == "crew_reshape"
        ]
        assert len(crew_reshape_msgs) == 1
        assert crew_reshape_msgs[0]["event"] == "neighbor_added"
        assert crew_reshape_msgs[0]["neighbor_slot"] == "reviewer"

        assert acts["informed"] == [original_impl_id]
        assert acts["killed"] == []


# ---------------------------------------------------------------------------
# AT 8 — drop live + stale-override cleanup (D6)
# ---------------------------------------------------------------------------


class TestDropLive:
    """AT 8: drop appends minus-topology, graceful-kills the dropped node,
    sweeps stale `_edge_overrides`, and informs surviving neighbours."""

    async def test_drop_removes_node_and_stale_override_and_informs(self) -> None:
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_EDGELESS_AB
        )
        a_id = slot_to_teammate["a"]
        b_id = slot_to_teammate["b"]

        # Plant a stale override on the (now-absent) a→b edge — this is the
        # D6 scenario: an override left over from a prior edge that has since
        # been removed. Drop must sweep it.
        broker._edge_overrides[("a", "b")] = "gated"

        pre_topologies = len(broker.get_topologies())

        async with _client(broker=broker) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "drop",
                        "params": {"slot": "b"},
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        assert result["verb"] == "drop"

        acts = result["actions"]

        # (a) a new Topology minus `b` was appended.
        assert len(broker.get_topologies()) == pre_topologies + 1
        latest = broker.latest_topology()
        assert latest is not None
        assert "a" in latest.slot_to_teammate
        assert "b" not in latest.slot_to_teammate
        assert latest.slot_to_teammate["a"] == a_id

        # (b) b's teammate is graceful-killed → dead in the registry.
        assert b_id in acts["killed"]
        assert b_id not in _alive_ids(broker)
        info = broker._info.get(b_id)
        assert info is not None and info.alive is False

        # (c) the stale _edge_overrides[("a","b")] entry is removed.
        assert ("a", "b") not in broker._edge_overrides
        assert ["a", "b"] in acts["edge_overrides_removed"]

        # (d) surviving affected neighbour `a` received a neighbor_removed msg.
        a_msgs = _teammate_inbox_payloads(broker, a_id)
        reshape_msgs = [
            p for p in a_msgs
            if isinstance(p, dict) and p.get("type") == "crew_reshape"
        ]
        assert len(reshape_msgs) == 1
        assert reshape_msgs[0]["event"] == "neighbor_removed"
        assert reshape_msgs[0]["neighbor_slot"] == "b"
        assert a_id in acts["informed"]

    async def test_drop_no_stale_override_still_records_and_kills(self) -> None:
        """No stale overrides → still records minus-topology and kills b."""
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_EDGELESS_AB
        )
        b_id = slot_to_teammate["b"]

        async with _client(broker=broker) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "drop",
                        "params": {"slot": "b"},
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        acts = result["actions"]
        assert acts["killed"] == [b_id]
        assert acts["edge_overrides_removed"] == []
        # No overrides → no neighbours identified via override sweep.
        assert acts["informed"] == []


# ---------------------------------------------------------------------------
# AT 9 — swap live: ORDERING is load-bearing (spawn+record BEFORE kill)
# ---------------------------------------------------------------------------


class TestSwapLive:
    """AT 9: swap replaces slot's teammate; assert record_topology precedes
    kill_teammate via call-order capture — no dead-slot window."""

    async def test_swap_ordering_record_topology_before_kill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_WITH_EDGE
        )
        original_reviewer_id = slot_to_teammate["reviewer"]

        # Instrument the broker: record the exact order of record_topology
        # and kill_teammate calls. Ordering is load-bearing (D5, AT 9).
        call_order: list[tuple[str, Any]] = []
        real_record_topology = broker.record_topology
        real_kill_teammate = broker.kill_teammate

        def _wrapped_record_topology(topo: Topology) -> None:
            call_order.append(("record_topology", topo))
            return real_record_topology(topo)

        async def _wrapped_kill_teammate(
            teammate_id: str, reason: str = "explicit", **kw
        ) -> None:
            call_order.append(("kill_teammate", teammate_id))
            return await real_kill_teammate(teammate_id, reason, **kw)

        monkeypatch.setattr(broker, "record_topology", _wrapped_record_topology)
        monkeypatch.setattr(broker, "kill_teammate", _wrapped_kill_teammate)

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "swap",
                        "params": {
                            "slot": "reviewer",
                            "role": "planner",  # new role
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True, result
        assert result["verb"] == "swap"

        acts = result["actions"]
        assert len(acts["spawned"]) == 1
        new_reviewer_id = acts["spawned"][0]["teammate_id"]
        assert new_reviewer_id != original_reviewer_id
        assert acts["killed"] == [original_reviewer_id]

        # ── ORDERING ASSERTION (AT 9) ────────────────────────────────────
        # record_topology must precede kill_teammate(old_id) in the call log.
        kinds = [c[0] for c in call_order]
        assert "record_topology" in kinds, call_order
        assert "kill_teammate" in kinds, call_order
        record_idx = next(
            i for i, c in enumerate(call_order) if c[0] == "record_topology"
        )
        # Find the kill_teammate call targeting the OLD teammate.
        kill_idx = next(
            i for i, c in enumerate(call_order)
            if c[0] == "kill_teammate" and c[1] == original_reviewer_id
        )
        assert record_idx < kill_idx, (
            f"record_topology must precede kill_teammate(old_id) in swap; "
            f"got call_order={call_order}"
        )

        # The recorded topology maps the swapped slot to the NEW id.
        recorded_topo = call_order[record_idx][1]
        assert recorded_topo.slot_to_teammate["reviewer"] == new_reviewer_id

        # Old teammate is dead; new teammate occupies the slot in latest topo.
        info = broker._info.get(original_reviewer_id)
        assert info is not None and info.alive is False
        latest = broker.latest_topology()
        assert latest is not None
        assert latest.slot_to_teammate["reviewer"] == new_reviewer_id

    async def test_swap_final_state_new_teammate_in_slot(self) -> None:
        """Final observable state: latest topology maps slot to replacement."""
        broker = Broker()
        base_sid, slot_to_teammate = await _make_running_crew(
            broker, _SHAPE_WITH_EDGE
        )
        original_reviewer_id = slot_to_teammate["reviewer"]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()
            task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "planner"},
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_prop = await _poll_for_new_proposal(broker, {base_sid})
            await broker.resolve_proposal(gate_prop.shape_id, "approve")
            result = _content_json(await task)

        assert result["ok"] is True

        # The old teammate is tombstoned; a new one occupies the slot.
        alive_ids = _alive_ids(broker)
        assert original_reviewer_id not in alive_ids
        latest = broker.latest_topology()
        assert latest is not None
        new_reviewer_id = latest.slot_to_teammate["reviewer"]
        assert new_reviewer_id in alive_ids
        assert new_reviewer_id != original_reviewer_id

        # actions record reflects both spawn + kill.
        acts = result["actions"]
        assert acts["spawned"][0]["teammate_id"] == new_reviewer_id
        assert acts["killed"] == [original_reviewer_id]
