"""Shape schema: frozen dataclasses, validating parser, and mermaid renderer.

Pure data module — no broker or SDK dependency. Uses the already-present pyyaml.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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


# ── Sentinel for "field not supplied" ────────────────────────────────────────
# Used by Swap and SetGate to distinguish "caller omitted this optional"
# from "caller explicitly passed None".  A module-level singleton; never
# exported — tests construct verbs without referencing it.
_UNSET: Any = object()


# ── AdaptationDiff ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdaptationDiff:
    """Structured record of what changed in one adaptation step.

    ``before`` and ``after`` hold only the fields that changed (or {} for
    purely-additive / purely-subtractive verbs).  ``render()`` produces the
    human-readable string fed to the gate's ``adaptation_diff`` channel.
    """

    verb: str           # "add_node" | "swap" | "augment" | "set_gate" | "drop"
    target: str         # slot name, or "from->to" for set_gate
    before: dict = field(hash=False)   # changed fields; {} for additive verbs
    after: dict = field(hash=False)    # changed fields; {} for subtractive verbs

    def render(self) -> str:
        """Return the human-readable diff string for the gate channel.

        Format templates (from the spec's Data / API Contracts):
          add_node  → "add_node {slot}: +node role={role}[, +edge {f}->{t} ({m})]"
          swap      → "swap {slot}: role {old} -> {new}[; {field} {old} -> {new} ...]"
          augment   → "augment {slot} alongside {targets}: +node role={role}, +edge ..."
          set_gate  → "set_gate {f}->{t}: mode {old} -> {new}[; reverse_mode {old} -> {new}]"
          drop      → "drop {slot}: -node role={role}"
        """
        if self.verb == "add_node":
            slot = self.target
            role = self.after["role"]
            edges: list[Any] = self.after.get("edges", [])
            result = f"add_node {slot}: +node role={role}"
            for from_s, to_s, mode in edges:
                result += f", +edge {from_s}->{to_s} ({mode})"
            return result

        if self.verb == "swap":
            slot = self.target
            result = (
                f"swap {slot}: role {self.before['role']} -> {self.after['role']}"
            )
            for field_name in ("model", "extra_tools", "extra_skills"):
                if field_name in self.before:
                    result += (
                        f"; {field_name} {self.before[field_name]}"
                        f" -> {self.after[field_name]}"
                    )
            return result

        if self.verb == "augment":
            slot = self.target
            alongside: list[str] = self.after.get("alongside", [])
            role = self.after["role"]
            aug_edges: list[Any] = self.after.get("edges", [])
            targets_str = ", ".join(alongside)
            result = f"augment {slot} alongside {targets_str}: +node role={role}"
            edge_strs = [f"+edge {f}->{t} ({m})" for f, t, m in aug_edges]
            if edge_strs:
                result += ", " + ", ".join(edge_strs)
            return result

        if self.verb == "set_gate":
            result = (
                f"set_gate {self.target}: mode"
                f" {self.before['mode']} -> {self.after['mode']}"
            )
            if "reverse_mode" in self.before:
                result += (
                    f"; reverse_mode {self.before['reverse_mode']}"
                    f" -> {self.after['reverse_mode']}"
                )
            return result

        if self.verb == "drop":
            return f"drop {self.target}: -node role={self.before['role']}"

        # Fallback for unknown verbs (should not occur in practice)
        return f"{self.verb} {self.target}"


# ── ShapeAdaptation (abstract base) ──────────────────────────────────────────

class ShapeAdaptation(ABC):
    """Abstract base for all shape adaptation verbs.

    Each concrete verb is a frozen dataclass that implements ``apply()``.
    ``apply()`` is a pure function: it returns a NEW frozen Shape and its
    ``AdaptationDiff``, or raises ``ShapeValidationError`` on any illegal
    mutation.  It never mutates the input shape and never returns a partial
    or invalid shape.
    """

    @abstractmethod
    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        """Apply this adaptation to ``shape``.

        Returns:
            A 2-tuple of (new_shape, diff) on success.

        Raises:
            ShapeValidationError: on any illegal mutation (duplicate slot,
                missing slot, self-loop, duplicate edge, invalid mode, etc.).
        """


# ── Verb: AddNode ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AddNode(ShapeAdaptation):
    """Append a new node (and optional wiring edges) to the shape."""

    node: ShapeNode
    edges: tuple[ShapeEdge, ...] = ()

    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        existing_slots = {n.slot for n in shape.nodes}

        # Slot uniqueness
        if self.node.slot in existing_slots:
            raise ShapeValidationError(
                f"duplicate slot '{self.node.slot}'"
            )

        # Validate new edges
        all_slots = existing_slots | {self.node.slot}
        seen_pairs: set[tuple[str, str]] = {
            (e.from_slot, e.to_slot) for e in shape.edges
        }

        for edge in self.edges:
            if edge.from_slot not in all_slots:
                raise ShapeValidationError(
                    f"edge references non-existent slot '{edge.from_slot}'"
                )
            if edge.to_slot not in all_slots:
                raise ShapeValidationError(
                    f"edge references non-existent slot '{edge.to_slot}'"
                )
            if edge.from_slot == edge.to_slot:
                raise ShapeValidationError(
                    f"self-loop edge: '{edge.from_slot}' -> '{edge.to_slot}'"
                )
            pair = (edge.from_slot, edge.to_slot)
            if pair in seen_pairs:
                raise ShapeValidationError(
                    f"duplicate edge '{edge.from_slot}' -> '{edge.to_slot}'"
                )
            seen_pairs.add(pair)

        new_shape = Shape(
            name=shape.name,
            description=shape.description,
            nodes=shape.nodes + (self.node,),
            edges=shape.edges + self.edges,
            phases=shape.phases,
        )
        diff = AdaptationDiff(
            verb="add_node",
            target=self.node.slot,
            before={},
            after={
                "role": self.node.role,
                "edges": [
                    (e.from_slot, e.to_slot, e.mode) for e in self.edges
                ],
            },
        )
        return new_shape, diff


# ── Verb: Swap ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Swap(ShapeAdaptation):
    """Replace a node's role and optionally its model/extra_tools/extra_skills.

    Supplied optional fields (those not left at their ``_UNSET`` defaults)
    replace the node's existing values.  Omitted ones are retained unchanged.
    The diff's ``before``/``after`` and ``render()`` include only the fields
    that actually changed.
    """

    slot: str
    role: str
    model: Any = field(default=_UNSET)
    extra_tools: Any = field(default=_UNSET)
    extra_skills: Any = field(default=_UNSET)

    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        node = next((n for n in shape.nodes if n.slot == self.slot), None)
        if node is None:
            raise ShapeValidationError(f"slot '{self.slot}' does not exist")

        new_model = self.model if self.model is not _UNSET else node.model
        new_extra_tools = (
            self.extra_tools if self.extra_tools is not _UNSET else node.extra_tools
        )
        new_extra_skills = (
            self.extra_skills if self.extra_skills is not _UNSET else node.extra_skills
        )

        new_node = ShapeNode(
            slot=node.slot,
            role=self.role,
            model=new_model,
            extra_tools=new_extra_tools,
            extra_skills=new_extra_skills,
            cwd=node.cwd,
        )
        new_nodes = tuple(
            new_node if n.slot == self.slot else n for n in shape.nodes
        )

        new_shape = Shape(
            name=shape.name,
            description=shape.description,
            nodes=new_nodes,
            edges=shape.edges,
            phases=shape.phases,
        )

        before: dict = {"role": node.role}
        after: dict = {"role": self.role}
        if self.model is not _UNSET:
            before["model"] = node.model
            after["model"] = self.model
        if self.extra_tools is not _UNSET:
            before["extra_tools"] = node.extra_tools
            after["extra_tools"] = self.extra_tools
        if self.extra_skills is not _UNSET:
            before["extra_skills"] = node.extra_skills
            after["extra_skills"] = self.extra_skills

        diff = AdaptationDiff(
            verb="swap",
            target=self.slot,
            before=before,
            after=after,
        )
        return new_shape, diff


# ── Verb: Augment ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Augment(ShapeAdaptation):
    """Add a new node alongside existing ones, with ≥1 wiring edges required."""

    node: ShapeNode
    edges: tuple[ShapeEdge, ...] = ()

    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        # Augment without wiring edges is just add_node — reject it.
        if not self.edges:
            raise ShapeValidationError(
                "augment requires at least one wiring edge"
            )

        existing_slots = {n.slot for n in shape.nodes}

        # Slot uniqueness
        if self.node.slot in existing_slots:
            raise ShapeValidationError(
                f"duplicate slot '{self.node.slot}'"
            )

        # Validate wiring edges
        all_slots = existing_slots | {self.node.slot}
        seen_pairs: set[tuple[str, str]] = {
            (e.from_slot, e.to_slot) for e in shape.edges
        }

        for edge in self.edges:
            if edge.from_slot not in all_slots:
                raise ShapeValidationError(
                    f"edge references non-existent slot '{edge.from_slot}'"
                )
            if edge.to_slot not in all_slots:
                raise ShapeValidationError(
                    f"edge references non-existent slot '{edge.to_slot}'"
                )
            if edge.from_slot == edge.to_slot:
                raise ShapeValidationError(
                    f"self-loop edge: '{edge.from_slot}' -> '{edge.to_slot}'"
                )
            pair = (edge.from_slot, edge.to_slot)
            if pair in seen_pairs:
                raise ShapeValidationError(
                    f"duplicate edge '{edge.from_slot}' -> '{edge.to_slot}'"
                )
            seen_pairs.add(pair)

        new_shape = Shape(
            name=shape.name,
            description=shape.description,
            nodes=shape.nodes + (self.node,),
            edges=shape.edges + self.edges,
            phases=shape.phases,
        )

        # "alongside" = existing slots referenced in the wiring edges
        alongside = sorted(
            {
                s
                for e in self.edges
                for s in (e.from_slot, e.to_slot)
                if s != self.node.slot
            }
        )

        diff = AdaptationDiff(
            verb="augment",
            target=self.node.slot,
            before={},
            after={
                "role": self.node.role,
                "edges": [
                    (e.from_slot, e.to_slot, e.mode) for e in self.edges
                ],
                "alongside": alongside,
            },
        )
        return new_shape, diff


# ── Verb: SetGate ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetGate(ShapeAdaptation):
    """Change the mode (and optionally reverse_mode) of an existing edge."""

    from_slot: str
    to_slot: str
    mode: str
    reverse_mode: Any = field(default=_UNSET)

    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        if self.mode not in _VALID_MODES:
            raise ShapeValidationError(
                f"mode '{self.mode}' must be one of {sorted(_VALID_MODES)}"
            )
        if (
            self.reverse_mode is not _UNSET
            and self.reverse_mode not in _VALID_MODES
        ):
            raise ShapeValidationError(
                f"reverse_mode '{self.reverse_mode}' must be one of"
                f" {sorted(_VALID_MODES)}"
            )

        edge = next(
            (
                e
                for e in shape.edges
                if e.from_slot == self.from_slot and e.to_slot == self.to_slot
            ),
            None,
        )
        if edge is None:
            raise ShapeValidationError(
                f"edge '{self.from_slot}' -> '{self.to_slot}' does not exist"
            )

        new_reverse_mode = (
            self.reverse_mode if self.reverse_mode is not _UNSET else edge.reverse_mode
        )

        new_edge = ShapeEdge(
            from_slot=edge.from_slot,
            to_slot=edge.to_slot,
            mode=self.mode,
            reverse_mode=new_reverse_mode,
        )
        new_edges = tuple(
            new_edge
            if (e.from_slot == self.from_slot and e.to_slot == self.to_slot)
            else e
            for e in shape.edges
        )

        new_shape = Shape(
            name=shape.name,
            description=shape.description,
            nodes=shape.nodes,
            edges=new_edges,
            phases=shape.phases,
        )

        before: dict = {"mode": edge.mode}
        after: dict = {"mode": self.mode}
        if self.reverse_mode is not _UNSET:
            before["reverse_mode"] = edge.reverse_mode
            after["reverse_mode"] = self.reverse_mode

        diff = AdaptationDiff(
            verb="set_gate",
            target=f"{self.from_slot}->{self.to_slot}",
            before=before,
            after=after,
        )
        return new_shape, diff


# ── Verb: Drop ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Drop(ShapeAdaptation):
    """Remove a node from the shape.

    The node must have no live incident edges and must not be the sole node.
    """

    slot: str

    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        node = next((n for n in shape.nodes if n.slot == self.slot), None)
        if node is None:
            raise ShapeValidationError(f"slot '{self.slot}' does not exist")

        # Live-edge guard (no dangling edges)
        for edge in shape.edges:
            if edge.from_slot == self.slot or edge.to_slot == self.slot:
                raise ShapeValidationError(
                    f"node '{self.slot}' has live edges;"
                    " remove edges before dropping"
                )

        # Shape must retain ≥1 node
        if len(shape.nodes) <= 1:
            raise ShapeValidationError(
                "cannot drop the sole remaining node;"
                " a shape must have at least one node"
            )

        new_nodes = tuple(n for n in shape.nodes if n.slot != self.slot)
        new_shape = Shape(
            name=shape.name,
            description=shape.description,
            nodes=new_nodes,
            edges=shape.edges,
            phases=shape.phases,
        )
        diff = AdaptationDiff(
            verb="drop",
            target=self.slot,
            before={"role": node.role},
            after={},
        )
        return new_shape, diff


# ── AdaptationStep ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdaptationStep:
    """One step in an adaptation chain: the diff and the resulting shape."""

    diff: AdaptationDiff
    shape: Shape


# ── AdaptationChain ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdaptationChain:
    """In-process provenance carrier for a sequence of shape adaptations.

    Each ``adapt()`` call returns a NEW chain with one more step appended.
    The original chain is never mutated.  A ``ShapeValidationError`` from
    ``apply()`` propagates naturally out of ``adapt()`` without swallowing
    and without modifying the caller's chain.
    """

    base: Shape
    steps: tuple[AdaptationStep, ...] = ()

    @property
    def current(self) -> Shape:
        """The shape resulting from the last step, or ``base`` if no steps."""
        return self.steps[-1].shape if self.steps else self.base

    def adapt(self, adaptation: ShapeAdaptation) -> "AdaptationChain":
        """Apply ``adaptation`` to the current shape and return a NEW chain.

        On success appends an ``AdaptationStep`` and returns a new chain.
        On failure (``ShapeValidationError``) the exception propagates;
        ``self`` is left untouched.
        """
        new_shape, diff = adaptation.apply(self.current)
        step = AdaptationStep(diff=diff, shape=new_shape)
        return AdaptationChain(base=self.base, steps=self.steps + (step,))


# ── shape_to_dict ─────────────────────────────────────────────────────────────

def shape_to_dict(shape: Shape) -> dict:
    """Serialize a Shape to a plain dict (inverse of parse_shape).

    ``parse_shape(shape_to_dict(s)) == s`` for any valid Shape.

    Every optional field (model, extra_tools, extra_skills, cwd, reverse_mode,
    phases) is included only when it carries a non-None / non-empty value so
    that the round-trip through parse_shape is clean.  extra_tools /
    extra_skills tuples are serialized as lists (parse_shape converts back via
    ``tuple()``).
    """
    nodes_list: list[dict] = []
    for n in shape.nodes:
        node_dict: dict = {"slot": n.slot, "role": n.role}
        if n.model is not None:
            node_dict["model"] = n.model
        if n.extra_tools is not None:
            node_dict["extra_tools"] = list(n.extra_tools)
        if n.extra_skills is not None:
            node_dict["extra_skills"] = list(n.extra_skills)
        if n.cwd is not None:
            node_dict["cwd"] = n.cwd
        nodes_list.append(node_dict)

    edges_list: list[dict] = []
    for e in shape.edges:
        edge_dict: dict = {
            "from_slot": e.from_slot,
            "to_slot": e.to_slot,
            "mode": e.mode,
        }
        if e.reverse_mode is not None:
            edge_dict["reverse_mode"] = e.reverse_mode
        edges_list.append(edge_dict)

    result: dict = {
        "name": shape.name,
        "description": shape.description,
        "nodes": nodes_list,
        "edges": edges_list,
    }
    if shape.phases:
        result["phases"] = list(shape.phases)

    return result


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
