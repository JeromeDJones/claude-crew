"""AT 9, AT 10: Proposal payload carries structured shape with edge modes.

AT 9  — Given a BrokerSnapshot with one pending shape proposal whose shape has
        a gated edge, when /api/state is fetched via _build_local_instance, then
        shape_proposals[0] contains both "mermaid" (unchanged) and a "shape"
        object whose nodes is non-empty and whose edges[0].mode == "gated".
        (pytest backend, no browser.)

AT 10 — claude_crew/ui_server.py references the NAMED LITERAL ``shape_to_dict``
        within the shape-proposal serialization.
        (grep; fails if the wiring is removed.)
"""
from __future__ import annotations

from pathlib import Path

from claude_crew.broker import Broker, BrokerSnapshot, ShapeProposal
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shape_with_gated_edge() -> Shape:
    """Return a minimal two-node, one-gated-edge Shape."""
    return Shape(
        name="test-shape",
        description="A shape used in the proposal-payload test.",
        nodes=(
            ShapeNode(slot="planner", role="planner"),
            ShapeNode(slot="builder", role="builder"),
        ),
        edges=(
            ShapeEdge(from_slot="planner", to_slot="builder", mode="gated"),
        ),
    )


def _make_snapshot(shape: Shape) -> BrokerSnapshot:
    """Return a BrokerSnapshot carrying one pending ShapeProposal."""
    proposal = ShapeProposal(
        shape_id="test-shape-id-001",
        shape=shape,
        adaptation_diff=None,
        status="pending",
    )
    return BrokerSnapshot(
        crew_id="test-crew-001",
        teammates=(),
        live=(),
        log=(),
        shape_proposals=(proposal,),
    )


# ---------------------------------------------------------------------------
# AT 9: Proposal payload carries structured shape with edge modes
# ---------------------------------------------------------------------------

class TestProposalPayloadStructuredShape:
    """Verify that /api/state shape_proposals include the structured shape dict."""

    def test_proposal_carries_mermaid_and_shape(self):
        """AT 9: shape_proposals[0] has both 'mermaid' and 'shape' keys; edges[0].mode == 'gated'."""
        shape = _make_shape_with_gated_edge()
        snap = _make_snapshot(shape)

        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        instance, _ = ui._build_local_instance(snap)

        proposals = instance["shape_proposals"]
        assert len(proposals) == 1, "Expected exactly one shape proposal in the instance dict"

        entry = proposals[0]

        # Both mermaid (pre-existing) and shape (new) must be present.
        assert "mermaid" in entry, "shape_proposals[0] missing 'mermaid' key"
        assert "shape" in entry, "shape_proposals[0] missing 'shape' key"

        # The mermaid value must be a non-empty string.
        assert isinstance(entry["mermaid"], str) and entry["mermaid"], \
            f"'mermaid' should be a non-empty string, got {entry['mermaid']!r}"

        # The structured shape must have non-empty nodes.
        shape_dict = entry["shape"]
        assert isinstance(shape_dict, dict), f"'shape' should be a dict, got {type(shape_dict)}"
        assert shape_dict.get("nodes"), f"'shape.nodes' should be non-empty, got {shape_dict.get('nodes')!r}"

        # edges[0].mode must equal "gated".
        edges = shape_dict.get("edges", [])
        assert edges, f"'shape.edges' should be non-empty, got {edges!r}"
        assert edges[0]["mode"] == "gated", \
            f"edges[0]['mode'] expected 'gated', got {edges[0].get('mode')!r}"

    def test_existing_keys_preserved(self):
        """AT 9 additive contract: 'shape' is added; shape_id, crew_id, status, mermaid, name, summary remain."""
        shape = _make_shape_with_gated_edge()
        snap = _make_snapshot(shape)

        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        instance, _ = ui._build_local_instance(snap)

        entry = instance["shape_proposals"][0]
        for key in ("shape_id", "crew_id", "status", "mermaid", "name", "summary", "shape"):
            assert key in entry, f"Expected key '{key}' in proposal entry, got keys: {list(entry.keys())}"


# ---------------------------------------------------------------------------
# AT 10: shape_to_dict wired in proposal serialization — structural grep
# ---------------------------------------------------------------------------

class TestShapeToDictWiredInUiServer:
    """AT 10: ui_server.py references shape_to_dict in the shape-proposal serialization."""

    def test_shape_to_dict_referenced_in_ui_server(self):
        """Grep: NAMED LITERAL 'shape_to_dict' must appear in claude_crew/ui_server.py."""
        ui_server_path = Path(__file__).parent.parent / "claude_crew" / "ui_server.py"
        source = ui_server_path.read_text(encoding="utf-8")
        assert "shape_to_dict" in source, (
            f"NAMED LITERAL 'shape_to_dict' not found in {ui_server_path}. "
            "The structured-shape payload wiring may have been removed."
        )
