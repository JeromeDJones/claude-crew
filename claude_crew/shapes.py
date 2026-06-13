"""Shape schema: frozen dataclasses, validating parser, and mermaid renderer.

Pure data module — no broker or SDK dependency. Uses the already-present pyyaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


class ShapeValidationError(ValueError):
    """Raised when a shape dict/YAML fails any validation rule."""


_VALID_MODES: frozenset[str] = frozenset({"gated", "tee", "direct"})

_SHAPE_KEYS: frozenset[str] = frozenset({"name", "description", "nodes", "edges", "phases"})
_NODE_KEYS: frozenset[str] = frozenset({"slot", "role", "model", "extra_tools", "extra_skills", "cwd"})
_EDGE_KEYS: frozenset[str] = frozenset({"from_slot", "to_slot", "mode", "reverse_mode"})


@dataclass(frozen=True)
class ShapeNode:
    slot: str                                  # logical slot name; unique within a shape
    role: str                                  # resolves to an agent-pack definition at instantiate time
    model: str | None = None                   # frontier | local | explicit id (passed through)
    extra_tools: tuple[str, ...] | None = None
    extra_skills: tuple[str, ...] | None = None
    cwd: str | None = None                     # cwd-policy passed through to spawn_teammate


@dataclass(frozen=True)
class ShapeEdge:
    from_slot: str
    to_slot: str
    mode: str = "gated"                        # gated | tee | direct ; M0 RECORDS, does not enforce
    reverse_mode: str | None = None            # optional back-edge mode; recorded only


@dataclass(frozen=True)
class Shape:
    name: str
    description: str
    nodes: tuple[ShapeNode, ...]
    edges: tuple[ShapeEdge, ...]
    phases: tuple[dict, ...] = ()              # lifecycle metadata; recorded VERBATIM, NOT key-validated


def parse_shape(data: dict | str, *, source: str = "<inline>") -> Shape:
    """Accept a dict (MCP tool arg) or a YAML string; validate; raise
    ShapeValidationError loudly on any malformation. No partial Shape returned.
    `phases` entries are recorded verbatim and are exempt from the unknown-key guard."""

    if isinstance(data, str):
        try:
            data = yaml.safe_load(data)
        except yaml.YAMLError as exc:
            raise ShapeValidationError(f"[{source}] YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ShapeValidationError(
            f"[{source}] shape must be a mapping, got {type(data).__name__}"
        )

    # ── unknown keys at shape level (phases is a known key and exempt inside) ──
    unknown_shape = set(data.keys()) - _SHAPE_KEYS
    if unknown_shape:
        raise ShapeValidationError(
            f"[{source}] unknown key(s) at shape level: {', '.join(sorted(unknown_shape))}"
        )

    # ── name ──────────────────────────────────────────────────────────────────
    name = data.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ShapeValidationError(f"[{source}] 'name' must be a non-empty string")

    # ── description ───────────────────────────────────────────────────────────
    description = data.get("description", "")
    if not isinstance(description, str) or not description.strip():
        raise ShapeValidationError(f"[{source}] 'description' must be a non-empty string")

    # ── nodes ─────────────────────────────────────────────────────────────────
    raw_nodes: Any = data.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) == 0:
        raise ShapeValidationError(f"[{source}] shape must have at least one node")

    nodes: list[ShapeNode] = []
    seen_slots: set[str] = set()

    for i, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            raise ShapeValidationError(f"[{source}] nodes[{i}] must be a mapping")

        unknown_node = set(raw_node.keys()) - _NODE_KEYS
        if unknown_node:
            raise ShapeValidationError(
                f"[{source}] nodes[{i}] unknown key(s): {', '.join(sorted(unknown_node))}"
            )

        slot = raw_node.get("slot", "")
        if not isinstance(slot, str) or not slot.strip():
            raise ShapeValidationError(f"[{source}] nodes[{i}] 'slot' must be a non-empty string")

        if slot in seen_slots:
            raise ShapeValidationError(f"[{source}] duplicate slot '{slot}'")
        seen_slots.add(slot)

        role = raw_node.get("role", "")
        if not isinstance(role, str) or not role.strip():
            raise ShapeValidationError(f"[{source}] nodes[{i}] 'role' must be a non-empty string")

        model: str | None = raw_node.get("model")

        raw_extra_tools = raw_node.get("extra_tools")
        extra_tools: tuple[str, ...] | None = (
            tuple(raw_extra_tools) if raw_extra_tools is not None else None
        )

        raw_extra_skills = raw_node.get("extra_skills")
        extra_skills: tuple[str, ...] | None = (
            tuple(raw_extra_skills) if raw_extra_skills is not None else None
        )

        cwd: str | None = raw_node.get("cwd")

        nodes.append(ShapeNode(
            slot=slot,
            role=role,
            model=model,
            extra_tools=extra_tools,
            extra_skills=extra_skills,
            cwd=cwd,
        ))

    # ── edges ─────────────────────────────────────────────────────────────────
    raw_edges: Any = data.get("edges", [])
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise ShapeValidationError(f"[{source}] 'edges' must be a list")

    edges: list[ShapeEdge] = []
    seen_edge_pairs: set[tuple[str, str]] = set()

    for i, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            raise ShapeValidationError(f"[{source}] edges[{i}] must be a mapping")

        unknown_edge = set(raw_edge.keys()) - _EDGE_KEYS
        if unknown_edge:
            raise ShapeValidationError(
                f"[{source}] edges[{i}] unknown key(s): {', '.join(sorted(unknown_edge))}"
            )

        from_slot = raw_edge.get("from_slot", "")
        to_slot = raw_edge.get("to_slot", "")

        if not isinstance(from_slot, str) or not from_slot:
            raise ShapeValidationError(f"[{source}] edges[{i}] 'from_slot' must be a non-empty string")
        if not isinstance(to_slot, str) or not to_slot:
            raise ShapeValidationError(f"[{source}] edges[{i}] 'to_slot' must be a non-empty string")

        if from_slot not in seen_slots:
            raise ShapeValidationError(
                f"[{source}] edges[{i}] 'from_slot' references unknown slot '{from_slot}'"
            )
        if to_slot not in seen_slots:
            raise ShapeValidationError(
                f"[{source}] edges[{i}] 'to_slot' references unknown slot '{to_slot}'"
            )

        if from_slot == to_slot:
            raise ShapeValidationError(
                f"[{source}] edges[{i}] self-loop: 'from_slot' and 'to_slot' are both '{from_slot}'"
            )

        edge_pair = (from_slot, to_slot)
        if edge_pair in seen_edge_pairs:
            raise ShapeValidationError(
                f"[{source}] duplicate edge from '{from_slot}' to '{to_slot}'"
            )
        seen_edge_pairs.add(edge_pair)

        mode_raw = raw_edge.get("mode", "gated")
        if mode_raw not in _VALID_MODES:
            raise ShapeValidationError(
                f"[{source}] edges[{i}] mode '{mode_raw}' must be one of"
                f" {sorted(_VALID_MODES)}"
            )

        reverse_mode_raw = raw_edge.get("reverse_mode")
        reverse_mode: str | None = None
        if reverse_mode_raw is not None:
            if reverse_mode_raw not in _VALID_MODES:
                raise ShapeValidationError(
                    f"[{source}] edges[{i}] reverse_mode '{reverse_mode_raw}' must be one of"
                    f" {sorted(_VALID_MODES)}"
                )
            reverse_mode = reverse_mode_raw

        edges.append(ShapeEdge(
            from_slot=from_slot,
            to_slot=to_slot,
            mode=mode_raw,
            reverse_mode=reverse_mode,
        ))

    # ── phases (verbatim; no key-validation) ──────────────────────────────────
    raw_phases: Any = data.get("phases", [])
    if raw_phases is None:
        raw_phases = []
    phases: tuple[dict, ...] = tuple(raw_phases)

    return Shape(
        name=name,
        description=description,
        nodes=tuple(nodes),
        edges=tuple(edges),
        phases=phases,
    )


def shape_to_mermaid(shape: Shape) -> str:
    """Render a Shape to a mermaid ``graph TD`` source string.

    One node per slot (label = slot<br/>role), one edge per ShapeEdge with the
    mode as the edge label. Consumed by the dashboard's existing
    mermaid.render() pipeline. Uses ``<br/>`` (not ``\\n``) for the in-label
    line break — mermaid renders a literal backslash-n verbatim, whereas
    ``<br/>`` is the supported line-break syntax inside an htmlLabels /
    foreignObject node label (the dashboard's DOMPurify config already allows it).
    """
    lines = ["graph TD"]

    for node in shape.nodes:
        label = f"{node.slot}<br/>{node.role}"
        lines.append(f'    {node.slot}["{label}"]')

    for edge in shape.edges:
        lines.append(f"    {edge.from_slot} -->|{edge.mode}| {edge.to_slot}")

    return "\n".join(lines)
