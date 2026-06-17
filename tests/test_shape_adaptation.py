"""Pure-data adaptation algebra tests (AT 1–22, AT 34, AT 35).

Tests the shapes.py additions:
  ShapeAdaptation (ABC), AddNode, Swap, Augment, SetGate, Drop,
  AdaptationDiff (with render()), AdaptationChain, AdaptationStep,
  shape_to_dict.

No async, no broker — pure synchronous unit tests.

Acceptance tests covered:
  AT  1: AddNode happy-path
  AT  2: Swap happy-path (no optional fields)
  AT  3: Augment happy-path
  AT  4: SetGate happy-path
  AT  5: Drop happy-path
  AT  6: shape_to_dict round-trip invariant (all 5 results + AT 35)
  AT  7: render() golden strings (all 5 verbs + AT 35 swap-with-optionals)
  AT  8: AdaptationChain provenance
  AT  9: AddNode duplicate slot
  AT 10: AddNode edge references non-existent slot
  AT 11: AddNode self-loop edge
  AT 12: AddNode duplicate edge
  AT 13: Swap slot does not exist
  AT 14: Augment edge target slot does not exist
  AT 15: Augment duplicate slot
  AT 16: Augment duplicate edge
  AT 17: Augment with no wiring edges
  AT 18: SetGate edge does not exist
  AT 19: SetGate invalid mode
  AT 20: SetGate invalid reverse_mode
  AT 21: Drop slot does not exist
  AT 22: Drop node with live in-edge
  AT 34: Drop sole node → zero-nodes guard
  AT 35: Swap with optional fields (model/extra_tools/extra_skills)
"""
from __future__ import annotations

import pytest

from claude_crew.shapes import (
    AdaptationChain,
    AdaptationDiff,
    AdaptationStep,
    AddNode,
    Augment,
    Drop,
    SetGate,
    Shape,
    ShapeEdge,
    ShapeNode,
    ShapeValidationError,
    Swap,
    parse_shape,
    shape_to_dict,
)


# ---------------------------------------------------------------------------
# Shared fixtures (module-level constants; not mutated between tests)
# ---------------------------------------------------------------------------

# 2-node shape: implementor(builder) → reviewer(sentinel), mode gated
_NODE_IMPL = ShapeNode(slot="implementor", role="builder")
_NODE_REV = ShapeNode(slot="reviewer", role="sentinel")
_EDGE_IMPL_REV = ShapeEdge(from_slot="implementor", to_slot="reviewer", mode="gated")

_SHAPE_2NODE = Shape(
    name="test-crew",
    description="A two-node shape for adaptation testing",
    nodes=(_NODE_IMPL, _NODE_REV),
    edges=(_EDGE_IMPL_REV,),
)

# 3-node shape: implementor → reviewer, "extra" node has NO incident edges
_NODE_EXTRA = ShapeNode(slot="extra", role="builder")
_SHAPE_3NODE = Shape(
    name="three-node-crew",
    description="Three-node shape; extra has no incident edges",
    nodes=(_NODE_IMPL, _NODE_REV, _NODE_EXTRA),
    edges=(_EDGE_IMPL_REV,),
)

# 1-node shape (for the sole-node drop guard, AT 34)
_SHAPE_1NODE = Shape(
    name="solo-crew",
    description="Single-node shape",
    nodes=(_NODE_IMPL,),
    edges=(),
)

# Rich shape for AT 35 (phases, cwd, model/extra_tools/extra_skills, reverse_mode)
_NODE_IMPL_RICH = ShapeNode(slot="implementor", role="builder", cwd="/repo")
_NODE_REV_RICH = ShapeNode(
    slot="reviewer",
    role="sentinel",
    model="sonnet",
    extra_tools=("Read",),
    # extra_skills is intentionally None
)
_EDGE_RICH = ShapeEdge(
    from_slot="implementor",
    to_slot="reviewer",
    mode="gated",
    reverse_mode="direct",
)
_SHAPE_RICH = Shape(
    name="rich-crew",
    description="Rich shape for round-trip and optional-field swap testing",
    nodes=(_NODE_IMPL_RICH, _NODE_REV_RICH),
    edges=(_EDGE_RICH,),
    phases=({"phase": "review"},),
)


# ---------------------------------------------------------------------------
# AT 1 — AddNode happy-path
# ---------------------------------------------------------------------------


class TestAddNodeHappyPath:
    """AT 1: add_node on a 2-node shape adds 1 node + 1 edge; base unchanged."""

    def test_add_node_adds_node_and_edge(self) -> None:
        """AT 1: result has 3 nodes and the new edge; base is unchanged."""
        new_node = ShapeNode(slot="qa", role="builder")
        new_edge = ShapeEdge(from_slot="reviewer", to_slot="qa")

        result, diff = AddNode(node=new_node, edges=(new_edge,)).apply(_SHAPE_2NODE)

        # Result shape has 3 nodes
        assert len(result.nodes) == 3
        slot_set = {n.slot for n in result.nodes}
        assert slot_set == {"implementor", "reviewer", "qa"}

        # New edge is present
        edge_pairs = {(e.from_slot, e.to_slot) for e in result.edges}
        assert ("reviewer", "qa") in edge_pairs
        # Original edge also preserved
        assert ("implementor", "reviewer") in edge_pairs

        # Base is unchanged
        assert len(_SHAPE_2NODE.nodes) == 2
        assert _SHAPE_2NODE is not result

        # Diff verb
        assert diff.verb == "add_node"
        assert diff.target == "qa"
        assert diff.before == {}

    def test_add_node_no_edges(self) -> None:
        """AT 1 (trivial subset): add node with no edges — unconnected, valid."""
        result, diff = AddNode(node=ShapeNode(slot="qa", role="builder")).apply(
            _SHAPE_2NODE
        )
        assert len(result.nodes) == 3
        assert len(result.edges) == 1  # original edge only
        assert diff.verb == "add_node"


# ---------------------------------------------------------------------------
# AT 2 — Swap happy-path (no optional fields)
# ---------------------------------------------------------------------------


class TestSwapHappyPath:
    """AT 2: swap role only; optionals + edges unchanged; diff is role-only."""

    def test_swap_role_only(self) -> None:
        """AT 2: swap reviewer → security-reviewer; model/extra_tools/extra_skills preserved."""
        result, diff = Swap(slot="reviewer", role="security-reviewer").apply(
            _SHAPE_2NODE
        )

        rev_node = next(n for n in result.nodes if n.slot == "reviewer")
        assert rev_node.role == "security-reviewer"

        # Optional fields unchanged (were None, remain None)
        assert rev_node.model is None
        assert rev_node.extra_tools is None
        assert rev_node.extra_skills is None

        # Slot name unchanged
        assert rev_node.slot == "reviewer"

        # Incident edge unchanged
        assert len(result.edges) == 1
        e = result.edges[0]
        assert e.from_slot == "implementor"
        assert e.to_slot == "reviewer"

        # Diff
        assert diff.verb == "swap"
        assert diff.target == "reviewer"
        assert diff.before == {"role": "sentinel"}
        assert diff.after == {"role": "security-reviewer"}

        # Base unchanged
        orig_rev = next(n for n in _SHAPE_2NODE.nodes if n.slot == "reviewer")
        assert orig_rev.role == "sentinel"

    def test_swap_preserves_other_node(self) -> None:
        """Swap only replaces the targeted node; other nodes are untouched."""
        result, _ = Swap(slot="reviewer", role="security-reviewer").apply(_SHAPE_2NODE)

        impl_node = next(n for n in result.nodes if n.slot == "implementor")
        assert impl_node.role == "builder"


# ---------------------------------------------------------------------------
# AT 3 — Augment happy-path
# ---------------------------------------------------------------------------


class TestAugmentHappyPath:
    """AT 3: augment adds new node + wiring edge; diff.verb == "augment"."""

    def test_augment_adds_node_and_wiring_edge(self) -> None:
        """AT 3: reviewer2 alongside implementor; edge implementor→reviewer2 (gated)."""
        new_node = ShapeNode(slot="reviewer2", role="sentinel")
        wiring_edge = ShapeEdge(from_slot="implementor", to_slot="reviewer2")

        result, diff = Augment(node=new_node, edges=(wiring_edge,)).apply(_SHAPE_2NODE)

        slots = {n.slot for n in result.nodes}
        assert slots == {"implementor", "reviewer", "reviewer2"}

        edge_pairs = {(e.from_slot, e.to_slot) for e in result.edges}
        assert ("implementor", "reviewer2") in edge_pairs
        assert ("implementor", "reviewer") in edge_pairs

        assert diff.verb == "augment"
        assert diff.target == "reviewer2"
        assert diff.before == {}

        # Base unchanged
        assert len(_SHAPE_2NODE.nodes) == 2


# ---------------------------------------------------------------------------
# AT 4 — SetGate happy-path
# ---------------------------------------------------------------------------


class TestSetGateHappyPath:
    """AT 4: set_gate changes edge mode; reverse_mode validated + recorded."""

    def test_set_gate_mode_only(self) -> None:
        """AT 4 (mode only): gated → tee; diff target and before/after correct."""
        result, diff = SetGate(
            from_slot="implementor", to_slot="reviewer", mode="tee"
        ).apply(_SHAPE_2NODE)

        e = next(
            e for e in result.edges
            if e.from_slot == "implementor" and e.to_slot == "reviewer"
        )
        assert e.mode == "tee"
        assert e.reverse_mode is None  # not supplied; original was None

        assert diff.verb == "set_gate"
        assert diff.target == "implementor->reviewer"
        assert diff.before == {"mode": "gated"}
        assert diff.after == {"mode": "tee"}

        # Base unchanged
        orig_e = _SHAPE_2NODE.edges[0]
        assert orig_e.mode == "gated"

    def test_set_gate_with_reverse_mode_validated_and_recorded(self) -> None:
        """AT 4 (+ reverse_mode): valid reverse_mode is validated and recorded."""
        result, diff = SetGate(
            from_slot="implementor",
            to_slot="reviewer",
            mode="tee",
            reverse_mode="direct",
        ).apply(_SHAPE_2NODE)

        e = next(
            e for e in result.edges
            if e.from_slot == "implementor" and e.to_slot == "reviewer"
        )
        assert e.mode == "tee"
        assert e.reverse_mode == "direct"

        assert diff.before == {"mode": "gated", "reverse_mode": None}
        assert diff.after == {"mode": "tee", "reverse_mode": "direct"}


# ---------------------------------------------------------------------------
# AT 5 — Drop happy-path
# ---------------------------------------------------------------------------


class TestDropHappyPath:
    """AT 5: drop a node with no incident edges; remaining nodes + edges unchanged."""

    def test_drop_isolated_node(self) -> None:
        """AT 5: drop 'extra' (no incident edges); 2 nodes remain; edges unchanged."""
        result, diff = Drop(slot="extra").apply(_SHAPE_3NODE)

        assert len(result.nodes) == 2
        slots = {n.slot for n in result.nodes}
        assert slots == {"implementor", "reviewer"}

        # Existing edge preserved
        assert len(result.edges) == 1
        assert result.edges[0].from_slot == "implementor"
        assert result.edges[0].to_slot == "reviewer"

        assert diff.verb == "drop"
        assert diff.target == "extra"
        assert diff.before == {"role": "builder"}
        assert diff.after == {}

        # Base unchanged
        assert len(_SHAPE_3NODE.nodes) == 3


# ---------------------------------------------------------------------------
# AT 35 — Swap with optional fields (model/extra_tools/extra_skills)
# ---------------------------------------------------------------------------


class TestSwapWithOptionalFields:
    """AT 35: swap with all optional fields replaces them; cwd + edge + phases preserved."""

    def test_swap_replaces_all_optionals(self) -> None:
        """AT 35: model/extra_tools/extra_skills replaced; other fields unchanged."""
        result, diff = Swap(
            slot="reviewer",
            role="security-reviewer",
            model="opus",
            extra_tools=("Read", "Grep"),
            extra_skills=("audit",),
        ).apply(_SHAPE_RICH)

        rev = next(n for n in result.nodes if n.slot == "reviewer")
        assert rev.role == "security-reviewer"
        assert rev.model == "opus"
        assert rev.extra_tools == ("Read", "Grep")
        assert rev.extra_skills == ("audit",)
        assert rev.slot == "reviewer"

        # implementor node's cwd is preserved
        impl = next(n for n in result.nodes if n.slot == "implementor")
        assert impl.cwd == "/repo"

        # Edge reverse_mode preserved
        e = result.edges[0]
        assert e.reverse_mode == "direct"

        # Phases preserved
        assert result.phases == ({"phase": "review"},)

        # Diff
        assert diff.before == {
            "role": "sentinel",
            "model": "sonnet",
            "extra_tools": ("Read",),
            "extra_skills": None,
        }
        assert diff.after == {
            "role": "security-reviewer",
            "model": "opus",
            "extra_tools": ("Read", "Grep"),
            "extra_skills": ("audit",),
        }


# ---------------------------------------------------------------------------
# AT 6 — shape_to_dict round-trip invariant
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """AT 6: parse_shape(shape_to_dict(result)) == result for all 5 verbs + AT 35."""

    def _round_trip(self, shape: Shape) -> None:
        """Assert that shape_to_dict + parse_shape is the identity."""
        d = shape_to_dict(shape)
        restored = parse_shape(d)
        assert restored == shape, (
            f"round-trip failed:\n  original: {shape}\n  restored: {restored}"
        )

    def test_round_trip_add_node(self) -> None:
        """AT 6: AddNode result round-trips."""
        result, _ = AddNode(
            node=ShapeNode(slot="qa", role="builder"),
            edges=(ShapeEdge(from_slot="reviewer", to_slot="qa"),),
        ).apply(_SHAPE_2NODE)
        self._round_trip(result)

    def test_round_trip_swap_no_optionals(self) -> None:
        """AT 6: Swap (no optionals) result round-trips."""
        result, _ = Swap(slot="reviewer", role="security-reviewer").apply(_SHAPE_2NODE)
        self._round_trip(result)

    def test_round_trip_augment(self) -> None:
        """AT 6: Augment result round-trips."""
        result, _ = Augment(
            node=ShapeNode(slot="reviewer2", role="sentinel"),
            edges=(ShapeEdge(from_slot="implementor", to_slot="reviewer2"),),
        ).apply(_SHAPE_2NODE)
        self._round_trip(result)

    def test_round_trip_set_gate(self) -> None:
        """AT 6: SetGate result round-trips."""
        result, _ = SetGate(
            from_slot="implementor", to_slot="reviewer", mode="tee"
        ).apply(_SHAPE_2NODE)
        self._round_trip(result)

    def test_round_trip_drop(self) -> None:
        """AT 6: Drop result round-trips."""
        result, _ = Drop(slot="extra").apply(_SHAPE_3NODE)
        self._round_trip(result)

    def test_round_trip_rich_swap_at35(self) -> None:
        """AT 6 (AT 35 basis): rich-field swap result preserves phases/cwd/model/
        extra_tools/extra_skills/reverse_mode through shape_to_dict → parse_shape."""
        result, _ = Swap(
            slot="reviewer",
            role="security-reviewer",
            model="opus",
            extra_tools=("Read", "Grep"),
            extra_skills=("audit",),
        ).apply(_SHAPE_RICH)
        self._round_trip(result)

    def test_shape_to_dict_preserves_all_optional_fields(self) -> None:
        """AT 6: shape_to_dict explicitly includes phases, cwd, model,
        extra_tools, extra_skills, and reverse_mode."""
        d = shape_to_dict(_SHAPE_RICH)
        assert d["phases"] == [{"phase": "review"}]

        impl_d = next(n for n in d["nodes"] if n["slot"] == "implementor")
        assert impl_d["cwd"] == "/repo"

        rev_d = next(n for n in d["nodes"] if n["slot"] == "reviewer")
        assert rev_d["model"] == "sonnet"
        assert rev_d["extra_tools"] == ["Read"]
        assert "extra_skills" not in rev_d  # was None; omitted from dict

        e_d = d["edges"][0]
        assert e_d["reverse_mode"] == "direct"


# ---------------------------------------------------------------------------
# AT 7 — render() golden strings
# ---------------------------------------------------------------------------


class TestRenderGolden:
    """AT 7: render() returns the documented format string for each verb."""

    def test_render_add_node(self) -> None:
        """AT 7: add_node render includes +node role and +edge clauses."""
        _, diff = AddNode(
            node=ShapeNode(slot="qa", role="builder"),
            edges=(ShapeEdge(from_slot="reviewer", to_slot="qa"),),
        ).apply(_SHAPE_2NODE)
        assert diff.render() == "add_node qa: +node role=builder, +edge reviewer->qa (gated)"

    def test_render_add_node_no_edges(self) -> None:
        """AT 7: add_node with no edges omits the edge clause."""
        _, diff = AddNode(node=ShapeNode(slot="qa", role="builder")).apply(_SHAPE_2NODE)
        assert diff.render() == "add_node qa: +node role=builder"

    def test_render_swap_no_optionals(self) -> None:
        """AT 7: swap without optional fields renders role clause only."""
        _, diff = Swap(slot="reviewer", role="security-reviewer").apply(_SHAPE_2NODE)
        assert diff.render() == "swap reviewer: role sentinel -> security-reviewer"

    def test_render_augment(self) -> None:
        """AT 7: augment render includes alongside, +node, and +edge clauses."""
        _, diff = Augment(
            node=ShapeNode(slot="reviewer2", role="sentinel"),
            edges=(ShapeEdge(from_slot="implementor", to_slot="reviewer2"),),
        ).apply(_SHAPE_2NODE)
        assert diff.render() == (
            "augment reviewer2 alongside implementor:"
            " +node role=sentinel, +edge implementor->reviewer2 (gated)"
        )

    def test_render_set_gate_mode_only(self) -> None:
        """AT 7: set_gate without reverse_mode renders mode clause only."""
        _, diff = SetGate(
            from_slot="implementor", to_slot="reviewer", mode="tee"
        ).apply(_SHAPE_2NODE)
        assert diff.render() == "set_gate implementor->reviewer: mode gated -> tee"

    def test_render_set_gate_with_reverse_mode(self) -> None:
        """AT 7: set_gate with reverse_mode appends the reverse_mode clause."""
        _, diff = SetGate(
            from_slot="implementor",
            to_slot="reviewer",
            mode="tee",
            reverse_mode="direct",
        ).apply(_SHAPE_2NODE)
        assert diff.render() == (
            "set_gate implementor->reviewer: mode gated -> tee;"
            " reverse_mode None -> direct"
        )

    def test_render_drop(self) -> None:
        """AT 7: drop render is 'drop {slot}: -node role={role}'."""
        _, diff = Drop(slot="extra").apply(_SHAPE_3NODE)
        assert diff.render() == "drop extra: -node role=builder"

    def test_render_swap_with_optionals_at35(self) -> None:
        """AT 7 (AT 35): swap-with-optionals render includes per-optional clauses."""
        _, diff = Swap(
            slot="reviewer",
            role="security-reviewer",
            model="opus",
            extra_tools=("Read", "Grep"),
            extra_skills=("audit",),
        ).apply(_SHAPE_RICH)
        expected = (
            "swap reviewer: role sentinel -> security-reviewer"
            "; model sonnet -> opus"
            "; extra_tools ('Read',) -> ('Read', 'Grep')"
            "; extra_skills None -> ('audit',)"
        )
        assert diff.render() == expected


# ---------------------------------------------------------------------------
# AT 8 — AdaptationChain provenance
# ---------------------------------------------------------------------------


class TestAdaptationChain:
    """AT 8: in-process provenance via AdaptationChain."""

    def test_chain_two_steps(self) -> None:
        """AT 8: 2-step chain has ordered steps, correct verbs, current == result."""
        chain = AdaptationChain(base=_SHAPE_2NODE)

        chain2 = chain.adapt(Swap(slot="reviewer", role="security-reviewer"))
        chain3 = chain2.adapt(
            SetGate(from_slot="implementor", to_slot="reviewer", mode="tee")
        )

        assert len(chain3.steps) == 2
        assert chain3.steps[0].diff.verb == "swap"
        assert chain3.steps[1].diff.verb == "set_gate"

        # chain.current == the twice-adapted shape
        expected, _ = Swap(slot="reviewer", role="security-reviewer").apply(_SHAPE_2NODE)
        expected2, _ = SetGate(
            from_slot="implementor", to_slot="reviewer", mode="tee"
        ).apply(expected)
        assert chain3.current == expected2

    def test_chain_base_unchanged(self) -> None:
        """AT 8: adapting never mutates the original base or intermediate chains."""
        chain = AdaptationChain(base=_SHAPE_2NODE)
        chain2 = chain.adapt(Swap(slot="reviewer", role="security-reviewer"))

        assert len(chain.steps) == 0
        assert chain.current is _SHAPE_2NODE
        assert len(chain2.steps) == 1

    def test_chain_current_before_any_steps(self) -> None:
        """AT 8: chain.current == base when no steps have been applied."""
        chain = AdaptationChain(base=_SHAPE_2NODE)
        assert chain.current is _SHAPE_2NODE

    def test_chain_error_propagates_leaves_chain_intact(self) -> None:
        """AT 8: ShapeValidationError propagates; prior chain is untouched."""
        chain = AdaptationChain(base=_SHAPE_2NODE)
        chain2 = chain.adapt(Swap(slot="reviewer", role="security-reviewer"))

        with pytest.raises(ShapeValidationError):
            # Dropping "reviewer" fails because it has a live in-edge
            chain2.adapt(Drop(slot="reviewer"))

        # chain2 is untouched
        assert len(chain2.steps) == 1

    def test_adaptation_step_carries_diff_and_shape(self) -> None:
        """AT 8: each AdaptationStep carries its diff and the resulting shape."""
        chain = AdaptationChain(base=_SHAPE_2NODE)
        chain2 = chain.adapt(Swap(slot="reviewer", role="security-reviewer"))

        step: AdaptationStep = chain2.steps[0]
        assert isinstance(step.diff, AdaptationDiff)
        assert step.diff.verb == "swap"
        assert isinstance(step.shape, Shape)


# ---------------------------------------------------------------------------
# AT 9 — AddNode: duplicate slot
# ---------------------------------------------------------------------------


class TestAddNodeDuplicateSlot:
    """AT 9: AddNode with an existing slot name raises ShapeValidationError."""

    def test_duplicate_slot_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="duplicate slot"):
            AddNode(node=ShapeNode(slot="reviewer", role="builder")).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 10 — AddNode: edge references non-existent slot
# ---------------------------------------------------------------------------


class TestAddNodeEdgeBadSlot:
    """AT 10: AddNode edge endpoint is not a known slot."""

    def test_edge_references_nonexistent_slot(self) -> None:
        with pytest.raises(ShapeValidationError, match="non-existent slot"):
            AddNode(
                node=ShapeNode(slot="qa", role="builder"),
                edges=(ShapeEdge(from_slot="qa", to_slot="ghost"),),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 11 — AddNode: self-loop edge
# ---------------------------------------------------------------------------


class TestAddNodeSelfLoop:
    """AT 11: AddNode with a self-loop edge raises ShapeValidationError."""

    def test_self_loop_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="self-loop"):
            AddNode(
                node=ShapeNode(slot="qa", role="builder"),
                edges=(ShapeEdge(from_slot="qa", to_slot="qa"),),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 12 — AddNode: duplicate edge
# ---------------------------------------------------------------------------


class TestAddNodeDuplicateEdge:
    """AT 12: AddNode with duplicate edges raises ShapeValidationError."""

    def test_duplicate_edge_in_new_edges_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="duplicate edge"):
            AddNode(
                node=ShapeNode(slot="qa", role="builder"),
                edges=(
                    ShapeEdge(from_slot="implementor", to_slot="qa"),
                    ShapeEdge(from_slot="implementor", to_slot="qa"),
                ),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 13 — Swap: slot does not exist
# ---------------------------------------------------------------------------


class TestSwapMissingSlot:
    """AT 13: Swap on a non-existent slot raises ShapeValidationError."""

    def test_swap_nonexistent_slot_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="does not exist"):
            Swap(slot="ghost", role="builder").apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 14 — Augment: edge target slot does not exist
# ---------------------------------------------------------------------------


class TestAugmentEdgeBadSlot:
    """AT 14: Augment edge references a slot that doesn't exist."""

    def test_edge_target_nonexistent_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="non-existent slot"):
            Augment(
                node=ShapeNode(slot="reviewer2", role="sentinel"),
                edges=(ShapeEdge(from_slot="ghost", to_slot="reviewer2"),),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 15 — Augment: new slot already exists
# ---------------------------------------------------------------------------


class TestAugmentDuplicateSlot:
    """AT 15: Augment with a slot that already exists raises ShapeValidationError."""

    def test_duplicate_slot_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="duplicate slot"):
            Augment(
                node=ShapeNode(slot="reviewer", role="sentinel"),
                edges=(ShapeEdge(from_slot="implementor", to_slot="reviewer"),),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 16 — Augment: wiring edge duplicates an existing edge
# ---------------------------------------------------------------------------


class TestAugmentDuplicateEdge:
    """AT 16: Augment wiring edge duplicates an existing edge."""

    def test_duplicate_existing_edge_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="duplicate edge"):
            # implementor→reviewer already exists in _SHAPE_2NODE
            Augment(
                node=ShapeNode(slot="qa", role="sentinel"),
                edges=(ShapeEdge(from_slot="implementor", to_slot="reviewer"),),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 17 — Augment: no wiring edges
# ---------------------------------------------------------------------------


class TestAugmentNoEdges:
    """AT 17: Augment with empty edges raises ShapeValidationError."""

    def test_no_wiring_edges_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="at least one wiring edge"):
            Augment(
                node=ShapeNode(slot="qa", role="sentinel"),
                edges=(),
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 18 — SetGate: edge does not exist
# ---------------------------------------------------------------------------


class TestSetGateEdgeNotFound:
    """AT 18: SetGate on a non-existent edge raises ShapeValidationError."""

    def test_nonexistent_edge_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="does not exist"):
            SetGate(
                from_slot="implementor", to_slot="ghost", mode="tee"
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 19 — SetGate: invalid mode
# ---------------------------------------------------------------------------


class TestSetGateInvalidMode:
    """AT 19: SetGate with mode not in {gated, tee, direct} raises."""

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="mode"):
            SetGate(
                from_slot="implementor", to_slot="reviewer", mode="bogus"
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 20 — SetGate: invalid reverse_mode
# ---------------------------------------------------------------------------


class TestSetGateInvalidReverseMode:
    """AT 20: SetGate with invalid reverse_mode raises ShapeValidationError."""

    def test_invalid_reverse_mode_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="reverse_mode"):
            SetGate(
                from_slot="implementor",
                to_slot="reviewer",
                mode="tee",
                reverse_mode="bogus",
            ).apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 21 — Drop: slot does not exist
# ---------------------------------------------------------------------------


class TestDropMissingSlot:
    """AT 21: Drop on a non-existent slot raises ShapeValidationError."""

    def test_nonexistent_slot_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="does not exist"):
            Drop(slot="ghost").apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 22 — Drop: node has live incident edge
# ---------------------------------------------------------------------------


class TestDropWithLiveEdge:
    """AT 22: Drop of a node with a live in-edge raises ShapeValidationError."""

    def test_live_in_edge_raises(self) -> None:
        """AT 22: reviewer has implementor→reviewer; dropping it is refused."""
        with pytest.raises(ShapeValidationError, match="live edges"):
            Drop(slot="reviewer").apply(_SHAPE_2NODE)

    def test_live_out_edge_raises(self) -> None:
        """AT 22 (out-edge variant): implementor has an out-edge; dropping refused."""
        with pytest.raises(ShapeValidationError, match="live edges"):
            Drop(slot="implementor").apply(_SHAPE_2NODE)


# ---------------------------------------------------------------------------
# AT 34 — Drop: sole remaining node → zero-nodes guard
# ---------------------------------------------------------------------------


class TestDropSoleNode:
    """AT 34: Dropping the only node is rejected; shape must have ≥1 node."""

    def test_drop_sole_node_raises(self) -> None:
        with pytest.raises(ShapeValidationError, match="sole remaining node"):
            Drop(slot="implementor").apply(_SHAPE_1NODE)
