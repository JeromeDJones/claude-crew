"""AT 19 — Additive broker helpers for M3-5 reshape-live-crew.

Covers:
  - latest_topology(): None on empty broker; last Topology after record_topology.
  - set_edge_override(): writes any mode (gated/tee/direct); overwrites existing.
  - remove_edge_overrides(): deletes present keys; silently skips absent keys.
"""

from __future__ import annotations

import pytest

from claude_crew.broker import Broker
from claude_crew.shapes import Shape
from claude_crew.broker import Topology
from types import MappingProxyType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_broker() -> Broker:
    """Return a minimal Broker with no teammates (stub mode / no factory)."""
    return Broker()


def make_topology(name: str = "t1") -> Topology:
    """Return a minimal Topology for test use."""
    return Topology(
        shape_name=name,
        edges=(("a", "b", "direct"),),
        slot_to_teammate={"a": "tm-1", "b": "tm-2"},
    )


# ---------------------------------------------------------------------------
# latest_topology
# ---------------------------------------------------------------------------

class TestLatestTopology:
    def test_returns_none_when_no_topologies(self) -> None:
        """Happy-ish path: empty broker → None."""
        broker = make_broker()
        assert broker.latest_topology() is None

    def test_returns_last_topology_after_one_record(self) -> None:
        """Happy path: single topology recorded → returned."""
        broker = make_broker()
        t = make_topology("first")
        broker.record_topology(t)
        assert broker.latest_topology() is t

    def test_returns_last_topology_after_multiple_records(self) -> None:
        """Happy path: multiple topologies → last one returned."""
        broker = make_broker()
        t1 = make_topology("first")
        t2 = make_topology("second")
        t3 = make_topology("third")
        broker.record_topology(t1)
        broker.record_topology(t2)
        broker.record_topology(t3)
        result = broker.latest_topology()
        assert result is t3
        assert result.shape_name == "third"

    def test_does_not_remove_topology_from_list(self) -> None:
        """Calling latest_topology() must not pop from _topologies."""
        broker = make_broker()
        t = make_topology()
        broker.record_topology(t)
        _ = broker.latest_topology()
        # Still present in get_topologies
        assert len(broker.get_topologies()) == 1


# ---------------------------------------------------------------------------
# set_edge_override
# ---------------------------------------------------------------------------

class TestSetEdgeOverride:
    def test_writes_gated_mode(self) -> None:
        """Happy path: mode='gated' written correctly."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "gated")
        assert broker._edge_overrides[("a", "b")] == "gated"

    def test_writes_tee_mode(self) -> None:
        """Happy path: mode='tee' written correctly (AT 19 explicit example)."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        assert broker._edge_overrides[("a", "b")] == "tee"

    def test_writes_direct_mode(self) -> None:
        """Happy path: mode='direct' written correctly."""
        broker = make_broker()
        broker.set_edge_override("x", "y", "direct")
        assert broker._edge_overrides[("x", "y")] == "direct"

    def test_overwrites_existing_entry(self) -> None:
        """Idempotent overwrite: calling twice with different mode updates."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "gated")
        broker.set_edge_override("a", "b", "tee")
        assert broker._edge_overrides[("a", "b")] == "tee"

    def test_multiple_independent_edges(self) -> None:
        """Different edge pairs are stored independently."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.set_edge_override("c", "d", "gated")
        broker.set_edge_override("e", "f", "direct")
        assert broker._edge_overrides[("a", "b")] == "tee"
        assert broker._edge_overrides[("c", "d")] == "gated"
        assert broker._edge_overrides[("e", "f")] == "direct"

    def test_unknown_edge_still_writes(self) -> None:
        """Writing an override for a slot pair not in any topology is harmless."""
        broker = make_broker()
        broker.set_edge_override("ghost", "slot", "tee")
        assert broker._edge_overrides[("ghost", "slot")] == "tee"

    def test_consistent_with_promote_edge(self) -> None:
        """set_edge_override('a','b','gated') is equivalent to promote_edge('a','b')."""
        b1 = make_broker()
        b2 = make_broker()
        b1.promote_edge("a", "b")
        b2.set_edge_override("a", "b", "gated")
        assert b1._edge_overrides == b2._edge_overrides


# ---------------------------------------------------------------------------
# remove_edge_overrides
# ---------------------------------------------------------------------------

class TestRemoveEdgeOverrides:
    def test_removes_present_key(self) -> None:
        """Happy path: existing key is deleted."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.remove_edge_overrides([("a", "b")])
        assert ("a", "b") not in broker._edge_overrides

    def test_removes_multiple_present_keys(self) -> None:
        """Happy path: multiple existing keys deleted in one call."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.set_edge_override("c", "d", "gated")
        broker.remove_edge_overrides([("a", "b"), ("c", "d")])
        assert ("a", "b") not in broker._edge_overrides
        assert ("c", "d") not in broker._edge_overrides

    def test_absent_key_is_silently_skipped(self) -> None:
        """Sad path: absent key does not raise — idempotent."""
        broker = make_broker()
        # No setup — key never existed
        broker.remove_edge_overrides([("x", "y")])  # must not raise

    def test_mixed_present_and_absent(self) -> None:
        """AT 19 explicit: [('a','b'), ('x','y')] — 'a'→'b' present, 'x'→'y' absent."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.remove_edge_overrides([("a", "b"), ("x", "y")])
        assert ("a", "b") not in broker._edge_overrides
        # _edge_overrides is otherwise empty
        assert broker._edge_overrides == {}

    def test_empty_pairs_is_no_op(self) -> None:
        """Edge case: empty iterable leaves overrides unchanged."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "gated")
        broker.remove_edge_overrides([])
        assert broker._edge_overrides == {("a", "b"): "gated"}

    def test_idempotent_double_remove(self) -> None:
        """Calling remove twice on the same pair must not raise."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "direct")
        broker.remove_edge_overrides([("a", "b")])
        broker.remove_edge_overrides([("a", "b")])  # second call: absent → no-op
        assert ("a", "b") not in broker._edge_overrides

    def test_preserves_unrelated_overrides(self) -> None:
        """Only the specified pairs are removed; others remain."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.set_edge_override("c", "d", "gated")
        broker.remove_edge_overrides([("a", "b")])
        assert ("a", "b") not in broker._edge_overrides
        assert broker._edge_overrides[("c", "d")] == "gated"

    def test_accepts_generator(self) -> None:
        """remove_edge_overrides accepts any Iterable, not just list."""
        broker = make_broker()
        broker.set_edge_override("a", "b", "tee")
        broker.remove_edge_overrides(p for p in [("a", "b")])
        assert ("a", "b") not in broker._edge_overrides
