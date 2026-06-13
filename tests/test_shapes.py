"""Tests for claude_crew/shapes.py — covers AT1, AT2, AT3, AT4.

AT1: happy-path parse + mermaid rendering
AT2: dangling edge raises ShapeValidationError naming the offending slot
AT3: duplicate slot raises ShapeValidationError
AT4: malformed-family — each of zero-nodes, bad mode, missing role, unknown
     shape-level key, self-loop raises; phases with arbitrary keys does NOT.
"""
import pytest

from claude_crew.shapes import (
    Shape,
    ShapeEdge,
    ShapeNode,
    ShapeValidationError,
    parse_shape,
    shape_to_mermaid,
)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _well_formed_dict() -> dict:
    """Minimal valid shape: 2 nodes, 2 edges (one with explicit mode, one omitted)."""
    return {
        "name": "test-shape",
        "description": "A test shape with two nodes.",
        "nodes": [
            {"slot": "planner", "role": "planner-agent"},
            {"slot": "executor", "role": "executor-agent"},
        ],
        "edges": [
            {"from_slot": "planner", "to_slot": "executor", "mode": "gated"},
            {"from_slot": "executor", "to_slot": "planner"},  # omitted → defaults to "gated"
        ],
    }


# ────────────────────────────────────────────────────────────────────────────
# AT1: happy path + mermaid
# ────────────────────────────────────────────────────────────────────────────

class TestAT1HappyPathAndMermaid:
    def test_parse_returns_shape_instance(self):
        shape = parse_shape(_well_formed_dict())
        assert isinstance(shape, Shape)

    def test_node_count(self):
        shape = parse_shape(_well_formed_dict())
        assert len(shape.nodes) == 2

    def test_node_slots_and_roles(self):
        shape = parse_shape(_well_formed_dict())
        by_slot = {n.slot: n for n in shape.nodes}
        assert "planner" in by_slot
        assert "executor" in by_slot
        assert by_slot["planner"].role == "planner-agent"
        assert by_slot["executor"].role == "executor-agent"

    def test_explicit_mode_gated(self):
        shape = parse_shape(_well_formed_dict())
        edge = next(e for e in shape.edges if e.from_slot == "planner")
        assert edge.mode == "gated"

    def test_omitted_mode_defaults_to_gated(self):
        shape = parse_shape(_well_formed_dict())
        edge = next(e for e in shape.edges if e.from_slot == "executor")
        assert edge.mode == "gated"

    def test_edge_count(self):
        shape = parse_shape(_well_formed_dict())
        assert len(shape.edges) == 2

    def test_mermaid_starts_with_graph_td(self):
        shape = parse_shape(_well_formed_dict())
        mermaid = shape_to_mermaid(shape)
        assert mermaid.startswith("graph TD")

    def test_mermaid_contains_slot_labels(self):
        shape = parse_shape(_well_formed_dict())
        mermaid = shape_to_mermaid(shape)
        assert "planner" in mermaid
        assert "executor" in mermaid

    def test_mermaid_contains_role_labels(self):
        shape = parse_shape(_well_formed_dict())
        mermaid = shape_to_mermaid(shape)
        assert "planner-agent" in mermaid
        assert "executor-agent" in mermaid

    def test_mermaid_contains_mode_label(self):
        shape = parse_shape(_well_formed_dict())
        mermaid = shape_to_mermaid(shape)
        assert "gated" in mermaid

    def test_mermaid_node_label_uses_br_not_literal_backslash_n(self):
        """Regression: node labels must use ``<br/>`` for line breaks, not a
        literal ``\\n`` (which mermaid renders verbatim as "slot\\nrole" in the
        browser instead of a two-line label)."""
        shape = parse_shape(_well_formed_dict())
        mermaid = shape_to_mermaid(shape)
        # The slot/role line break is present as <br/> ...
        assert "planner<br/>planner-agent" in mermaid
        # ... and no literal backslash-n leaks into any node label.
        assert "\\n" not in mermaid

    def test_tee_mode_accepted_and_recorded(self):
        data = dict(_well_formed_dict())
        data["edges"] = [{"from_slot": "planner", "to_slot": "executor", "mode": "tee"}]
        shape = parse_shape(data)
        assert shape.edges[0].mode == "tee"

    def test_direct_mode_accepted_and_recorded(self):
        data = dict(_well_formed_dict())
        data["edges"] = [{"from_slot": "planner", "to_slot": "executor", "mode": "direct"}]
        shape = parse_shape(data)
        assert shape.edges[0].mode == "direct"

    def test_parse_yaml_string(self):
        yaml_str = """
name: yaml-shape
description: A YAML-parsed shape.
nodes:
  - slot: alpha
    role: alpha-role
  - slot: beta
    role: beta-role
edges:
  - from_slot: alpha
    to_slot: beta
    mode: tee
"""
        shape = parse_shape(yaml_str, source="test.yaml")
        assert shape.name == "yaml-shape"
        assert len(shape.nodes) == 2
        assert shape.edges[0].mode == "tee"

    def test_shape_is_frozen(self):
        shape = parse_shape(_well_formed_dict())
        with pytest.raises((AttributeError, TypeError)):
            shape.name = "mutated"  # type: ignore[misc]

    def test_nodes_are_frozen_dataclasses(self):
        shape = parse_shape(_well_formed_dict())
        node = shape.nodes[0]
        assert isinstance(node, ShapeNode)
        with pytest.raises((AttributeError, TypeError)):
            node.slot = "mutated"  # type: ignore[misc]

    def test_edges_are_frozen_dataclasses(self):
        shape = parse_shape(_well_formed_dict())
        edge = shape.edges[0]
        assert isinstance(edge, ShapeEdge)
        with pytest.raises((AttributeError, TypeError)):
            edge.mode = "mutated"  # type: ignore[misc]

    def test_phases_empty_by_default(self):
        shape = parse_shape(_well_formed_dict())
        assert shape.phases == ()

    def test_phases_recorded_verbatim(self):
        data = dict(_well_formed_dict())
        data["phases"] = [{"step": "warmup", "custom_key": "custom_val"}]
        shape = parse_shape(data)
        assert len(shape.phases) == 1
        assert shape.phases[0]["custom_key"] == "custom_val"

    def test_optional_node_fields_default_to_none(self):
        shape = parse_shape(_well_formed_dict())
        node = next(n for n in shape.nodes if n.slot == "planner")
        assert node.model is None
        assert node.extra_tools is None
        assert node.extra_skills is None
        assert node.cwd is None

    def test_node_with_optional_fields(self):
        data = {
            "name": "rich-shape",
            "description": "Shape with optional node fields.",
            "nodes": [
                {
                    "slot": "worker",
                    "role": "worker-role",
                    "model": "sonnet",
                    "extra_tools": ["Bash", "Read"],
                    "extra_skills": ["skill-a"],
                    "cwd": "/tmp/work",
                }
            ],
            "edges": [],
        }
        shape = parse_shape(data)
        node = shape.nodes[0]
        assert node.model == "sonnet"
        assert node.extra_tools == ("Bash", "Read")
        assert node.extra_skills == ("skill-a",)
        assert node.cwd == "/tmp/work"

    def test_reverse_mode_recorded(self):
        data = dict(_well_formed_dict())
        data["edges"] = [
            {"from_slot": "planner", "to_slot": "executor", "mode": "gated", "reverse_mode": "tee"}
        ]
        shape = parse_shape(data)
        assert shape.edges[0].reverse_mode == "tee"


# ────────────────────────────────────────────────────────────────────────────
# AT2: dangling edge
# ────────────────────────────────────────────────────────────────────────────

class TestAT2DanglingEdge:
    def test_dangling_to_slot_raises(self):
        data = {
            "name": "dangling",
            "description": "Dangling to_slot.",
            "nodes": [{"slot": "alpha", "role": "r"}],
            "edges": [{"from_slot": "alpha", "to_slot": "missing-slot"}],
        }
        with pytest.raises(ShapeValidationError) as exc_info:
            parse_shape(data)
        assert "missing-slot" in str(exc_info.value)

    def test_dangling_from_slot_raises(self):
        data = {
            "name": "dangling",
            "description": "Dangling from_slot.",
            "nodes": [{"slot": "alpha", "role": "r"}],
            "edges": [{"from_slot": "ghost", "to_slot": "alpha"}],
        }
        with pytest.raises(ShapeValidationError) as exc_info:
            parse_shape(data)
        assert "ghost" in str(exc_info.value)

    def test_no_shape_produced_on_dangling_edge(self):
        """parse_shape must not return a partial Shape — no Shape on error."""
        data = {
            "name": "dangling",
            "description": "Dangling to_slot.",
            "nodes": [{"slot": "alpha", "role": "r"}],
            "edges": [{"from_slot": "alpha", "to_slot": "nowhere"}],
        }
        result = None
        try:
            result = parse_shape(data)
        except ShapeValidationError:
            pass
        assert result is None

    def test_error_message_names_offending_slot(self):
        offending = "totally-absent-slot"
        data = {
            "name": "dangling",
            "description": "Error message test.",
            "nodes": [{"slot": "alpha", "role": "r"}],
            "edges": [{"from_slot": "alpha", "to_slot": offending}],
        }
        with pytest.raises(ShapeValidationError) as exc_info:
            parse_shape(data)
        assert offending in str(exc_info.value)


# ────────────────────────────────────────────────────────────────────────────
# AT3: duplicate slot
# ────────────────────────────────────────────────────────────────────────────

class TestAT3DuplicateSlot:
    def test_duplicate_slot_raises(self):
        data = {
            "name": "dup-slot",
            "description": "Two nodes with the same slot.",
            "nodes": [
                {"slot": "worker", "role": "role-a"},
                {"slot": "worker", "role": "role-b"},
            ],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_duplicate_slot_error_is_validation_error(self):
        data = {
            "name": "dup-slot",
            "description": "Confirms exception type.",
            "nodes": [
                {"slot": "alpha", "role": "r"},
                {"slot": "alpha", "role": "r"},
            ],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_distinct_slots_ok(self):
        """Sanity: three distinct slots should not raise."""
        data = {
            "name": "multi-node",
            "description": "Three distinct slots.",
            "nodes": [
                {"slot": "a", "role": "r"},
                {"slot": "b", "role": "r"},
                {"slot": "c", "role": "r"},
            ],
            "edges": [],
        }
        shape = parse_shape(data)
        assert len(shape.nodes) == 3


# ────────────────────────────────────────────────────────────────────────────
# AT4: malformed family
# ────────────────────────────────────────────────────────────────────────────

class TestAT4MalformedFamily:
    # (a) zero nodes
    def test_zero_nodes_raises(self):
        data = {
            "name": "empty",
            "description": "No nodes.",
            "nodes": [],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_missing_nodes_key_raises(self):
        data = {
            "name": "no-nodes-key",
            "description": "Nodes key absent.",
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    # (b) bad edge mode
    def test_invalid_mode_raises(self):
        data = {
            "name": "bad-mode",
            "description": "Invalid edge mode.",
            "nodes": [
                {"slot": "a", "role": "r"},
                {"slot": "b", "role": "r"},
            ],
            "edges": [{"from_slot": "a", "to_slot": "b", "mode": "unicast"}],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_invalid_mode_not_in_allowed_set(self):
        for bad_mode in ("broadcast", "fanout", "push", "GATED", "Gated"):
            data = {
                "name": "bad-mode",
                "description": "Bad mode.",
                "nodes": [
                    {"slot": "x", "role": "r"},
                    {"slot": "y", "role": "r"},
                ],
                "edges": [{"from_slot": "x", "to_slot": "y", "mode": bad_mode}],
            }
            with pytest.raises(ShapeValidationError, match=bad_mode):
                parse_shape(data)

    # (c) node missing role
    def test_node_missing_role_raises(self):
        data = {
            "name": "no-role",
            "description": "Node missing role.",
            "nodes": [{"slot": "a"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_node_empty_role_raises(self):
        data = {
            "name": "empty-role",
            "description": "Node with empty role.",
            "nodes": [{"slot": "a", "role": ""}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_node_missing_slot_raises(self):
        data = {
            "name": "no-slot",
            "description": "Node missing slot.",
            "nodes": [{"role": "some-role"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    # (d) unknown key at shape level
    def test_unknown_shape_key_raises(self):
        data = {
            "name": "bad-key",
            "description": "Unknown top-level key.",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
            "stray_field": "oops",
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_unknown_node_key_raises(self):
        data = {
            "name": "bad-node-key",
            "description": "Node with unknown key.",
            "nodes": [{"slot": "a", "role": "r", "stray": "oops"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_unknown_edge_key_raises(self):
        data = {
            "name": "bad-edge-key",
            "description": "Edge with unknown key.",
            "nodes": [
                {"slot": "a", "role": "r"},
                {"slot": "b", "role": "r"},
            ],
            "edges": [{"from_slot": "a", "to_slot": "b", "stray_key": "oops"}],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    # (e) self-loop edge
    def test_self_loop_raises(self):
        data = {
            "name": "loop",
            "description": "Self-loop edge.",
            "nodes": [
                {"slot": "a", "role": "r"},
                {"slot": "b", "role": "r"},
            ],
            "edges": [{"from_slot": "a", "to_slot": "a"}],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    # phases exemption
    def test_phases_with_arbitrary_keys_does_not_raise(self):
        """phases entries are exempt from the unknown-key guard."""
        data = {
            "name": "phases-shape",
            "description": "Phases with arbitrary keys.",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
            "phases": [{"arbitrary_key": "value", "another_key": 42, "nested": {"x": 1}}],
        }
        shape = parse_shape(data)
        assert shape.phases[0]["arbitrary_key"] == "value"
        assert shape.phases[0]["another_key"] == 42

    def test_phases_multiple_entries_verbatim(self):
        data = {
            "name": "multi-phases",
            "description": "Multiple phase entries.",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
            "phases": [
                {"phase": "warmup", "duration": 10},
                {"phase": "run", "workers": 4},
            ],
        }
        shape = parse_shape(data)
        assert len(shape.phases) == 2
        assert shape.phases[1]["workers"] == 4

    # duplicate edge
    def test_duplicate_edge_raises(self):
        data = {
            "name": "dup-edge",
            "description": "Duplicate edges.",
            "nodes": [
                {"slot": "a", "role": "r"},
                {"slot": "b", "role": "r"},
            ],
            "edges": [
                {"from_slot": "a", "to_slot": "b"},
                {"from_slot": "a", "to_slot": "b"},
            ],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    # empty name / description
    def test_empty_name_raises(self):
        data = {
            "name": "",
            "description": "Valid description.",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_empty_description_raises(self):
        data = {
            "name": "valid-name",
            "description": "",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)

    def test_whitespace_only_name_raises(self):
        data = {
            "name": "   ",
            "description": "Valid description.",
            "nodes": [{"slot": "a", "role": "r"}],
            "edges": [],
        }
        with pytest.raises(ShapeValidationError):
            parse_shape(data)
