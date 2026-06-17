# Spec: m3-adaptation-algebra

## Problem

A `Shape` (the frozen-dataclass description of a multi-agent crew topology) can today only be
composed once via `propose_shape` and instantiated via `instantiate_shape`. There is no computable
way to *adapt* an existing pre-instantiation shape — "swap this reviewer's role," "gate this edge,"
"add a second reviewer," "drop this node" — and present the change for approval. Adapting today means
hand-authoring an entirely new shape from scratch and re-reviewing it whole. M3 makes adaptation a
**computable algebra**: five typed verbs, each transforming a frozen `Shape` into a new frozen `Shape`
plus a structured `AdaptationDiff`, with every adaptation surfaced at the *existing* M1.5 human gate
(as a diff of what changed) before it takes effect. claude-crew owns the mechanism; policy consumers
(repo-react right-sizing) own which adaptation to apply. Adaptation is pre-instantiation only —
reshaping a running crew is the separately-milestoned M3.5.

## Architecture Overview

Three additions, two touched modules, two new test files:

- **`claude_crew/shapes.py`** (pure-data module, no broker/SDK dependency — same home as `parse_shape`
  / `shape_to_mermaid`) gains: an abstract base `ShapeAdaptation` with a uniform
  `apply(shape) -> tuple[Shape, AdaptationDiff]`; five concrete verb classes (`AddNode`, `Swap`,
  `Augment`, `SetGate`, `Drop`); a structured frozen `AdaptationDiff` with `render() -> str`; an
  `AdaptationChain` provenance carrier; and a `shape_to_dict(shape) -> dict` serializer (the inverse
  of `parse_shape`, used for the round-trip invariant). `Shape`/`ShapeNode`/`ShapeEdge` stay
  **unchanged** — verbs operate *on* a shape and return a *new* one; no decorator, no mutation.
- **`claude_crew/server.py`** gains ONE lead MCP tool `adapt_shape(verb, params, base_shape_id=,
  base_shape=)`. It resolves the base shape (pre-instantiation only), performs swap/augment role
  resolution through the **same** `factory.known_roles()` / `factory.resolve_role()` seam that
  `instantiate_shape`'s pre-flight uses (server.py:919-934), applies the verb command, and on success
  calls `broker.register_proposal(new_shape, adaptation_diff=diff.render())` — reusing the M1.5 gate
  verbatim. No second approval path; no gate-signature change.
- **`claude_crew/broker.py`** is **not modified**. `register_proposal` already carries the
  `adaptation_diff: str | None` parameter (broker.py:1367); the M0/M1.5 gate was built anticipating M3.
  Adaptation-chain provenance is in-process (the `AdaptationChain` dataclass), not persisted on broker
  state.

### Call-site survey

Role resolution has two structurally-identical call-sites; the new one mirrors the existing one exactly
(shared `factory` seam), so the spec ships a single resolution shape, not two.

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| `instantiate_shape` pre-flight | `claude_crew/server.py:919-934` | `known_roles_fn = getattr(factory, "known_roles", None)`; if present resolve each node role via `factory.resolve_role`, else skip | Existing; all-or-nothing before spawn |
| `adapt_shape` swap/augment | `claude_crew/server.py` (new) | identical getattr+resolve idiom against the new/augmenting role only | Skip resolution when `known_roles` absent (stub mode), exactly as instantiate does |

Resolution: reuse the same getattr/resolve pattern; do not extract a shared helper this slice (two
sites, structurally identical, but the new site resolves a single role rather than iterating nodes —
extraction is deferred as cleanup, not required for correctness).

## Data / API Contracts

```python
# ── claude_crew/shapes.py (additions; Shape/ShapeNode/ShapeEdge unchanged) ──

@dataclass(frozen=True)
class AdaptationDiff:
    verb: str                      # "add_node" | "swap" | "augment" | "set_gate" | "drop"
    target: str                    # slot name (add_node/swap/augment/drop) or "from->to" (set_gate)
    before: dict                   # changed fields only; {} when the verb is purely additive
    after: dict                    # changed fields only; {} when the verb is purely subtractive
    def render(self) -> str: ...   # the human-readable string fed to the gate's adaptation_diff channel

class ShapeAdaptation(ABC):
    @abstractmethod
    def apply(self, shape: Shape) -> tuple[Shape, AdaptationDiff]:
        """Pure: returns a NEW frozen Shape + its diff, or raises ShapeValidationError.
        Never mutates `shape`; never returns a partial/invalid shape."""

@dataclass(frozen=True)
class AddNode(ShapeAdaptation):   # params: node: ShapeNode, edges: tuple[ShapeEdge, ...] = ()
    ...
@dataclass(frozen=True)
class Swap(ShapeAdaptation):      # params: slot: str, role: str, model/extra_tools/extra_skills optional
    ...                           # supplied optionals replace the node's values; omitted ones retained
@dataclass(frozen=True)
class Augment(ShapeAdaptation):   # params: node: ShapeNode, edges: tuple[ShapeEdge, ...] (>=1)
    ...
@dataclass(frozen=True)
class SetGate(ShapeAdaptation):   # params: from_slot, to_slot, mode, reverse_mode optional
    ...
@dataclass(frozen=True)
class Drop(ShapeAdaptation):      # params: slot: str
    ...

@dataclass(frozen=True)
class AdaptationStep:
    diff: AdaptationDiff
    shape: Shape                   # the shape resulting from this step

@dataclass(frozen=True)
class AdaptationChain:
    base: Shape
    steps: tuple[AdaptationStep, ...] = ()
    @property
    def current(self) -> Shape: ...           # steps[-1].shape if steps else base
    def adapt(self, adaptation: ShapeAdaptation) -> "AdaptationChain":
        ...                                    # applies to self.current; returns a NEW chain.
                                               # A step's ShapeValidationError propagates naturally
                                               # (no swallowing; the prior chain is left untouched).

def shape_to_dict(shape: Shape) -> dict: ...   # inverse of parse_shape; parse_shape(shape_to_dict(s)) == s.
                                               # MUST preserve every field: Shape.phases, ShapeNode.cwd/
                                               # model/extra_tools/extra_skills, ShapeEdge.reverse_mode.

# render() format templates (golden-test targets; implementor authors impl + golden from these):
#   add_node  -> "add_node {slot}: +node role={role}" (+ ", +edge {from}->{to} ({mode})" per edge)
#   swap      -> "swap {slot}: role {old_role} -> {new_role}" (+ "; {field} {old} -> {new}" per changed optional)
#   augment   -> "augment {slot} alongside {targets}: +node role={role}, +edge {from}->{to} ({mode})[, ...]"
#   set_gate  -> "set_gate {from}->{to}: mode {old_mode} -> {new_mode}" (+ "; reverse_mode {old} -> {new}")
#   drop      -> "drop {slot}: -node role={role}"
```

```python
# ── claude_crew/server.py (one new MCP tool) ──

@mcp.tool()
async def adapt_shape(
    verb: str,                          # add_node | swap | augment | set_gate | drop
    params: dict,                       # verb-specific params (see verb classes)
    base_shape_id: str | None = None,   # resolve a PENDING or APPROVED proposal as the base shape
    base_shape: dict | None = None,     # OR an inline shape dict (parsed via parse_shape)
) -> dict[str, Any]:
    """Apply one adaptation verb to a pre-instantiation base shape, then register
    the result as a NEW pending proposal carrying the diff (reuses the M1.5 gate).

    Returns on success:  {ok: True, shape_id, status: "pending", diff, shape}
      - diff is AdaptationDiff.render() (the string the gate shows)
      - shape is the shape_to_dict(new_shape) DICT form, NOT a raw Shape
        (MCP tool returns must be JSON-serializable)
    Failure envelopes (NO proposal registered in any failure case):
      {ok: False, stage: "base",  error}   # neither/both base args; unknown id;
                                            #   base status not pending/approved (instantiated/declined/timed_out)
      {ok: False, stage: "parse", error}   # inline base_shape fails parse_shape
      {ok: False, stage: "verb",  error}   # verb not one of the five
      {ok: False, stage: "adapt", error, [unresolved_roles]}
                                            # illegal mutation (ShapeValidationError) OR
                                            #   swap/augment role unresolvable against factory.known_roles
    """
```

## Design Decisions

- **One `adapt_shape` tool with a verb discriminator (resolves Open Question 1)** — *Rationale:* a single
  uniform tool is a smaller MCP surface than five per-verb tools and mirrors `propose_shape`; per-verb
  arg validation lives inside each verb command's `apply`, so discoverability is preserved via the
  docstring's verb→params table. — *Carried into:* `server.py` `adapt_shape(verb, params, ...)`; AT 23,
  AT 32 (unknown-verb rejection).
- **`Shape` stays a pure frozen dataclass; verbs are command/transform objects, not a decorator
  (locked fork 2)** — *Rationale:* every `Shape` consumer (`instantiate_shape` pre-flight,
  `shape_to_mermaid`, broker `Topology`) expects a concrete `Shape`; a wrapper would be flattened at
  every gate anyway. — *Carried into:* `shapes.py` `ShapeAdaptation.apply -> tuple[Shape, AdaptationDiff]`;
  AT 1–5 assert results are plain `Shape` instances.
- **`apply` is pure: no mutation, returns a new frozen `Shape` or raises loudly (locked fork 3)** —
  *Rationale:* no silent no-op, no partial shape; an illegal mutation must be a `ShapeValidationError`. —
  *Carried into:* every verb class; AT 9–22, AT 34 (each sad path raises `ShapeValidationError`).
- **`swap` replaces supplied optional fields (`model`/`extra_tools`/`extra_skills`) and retains omitted
  ones; each changed optional appears in the diff and `render()`** — *Rationale:* swap is the role/config
  re-targeting verb, not just a role rename; the changed-optional behavior is a named deliverable and must
  be exercised. — *Carried into:* `Swap.apply` + `AdaptationDiff.render()`'s `; {field} {old} -> {new}`
  clause; AT 35 (optional-field swap golden), AT 7 (render golden includes AT 35).
- **Every adapted shape re-satisfies `parse_shape`'s invariants, and `shape_to_dict` preserves every
  field** — *Rationale:* a verb can never produce a shape `parse_shape` would reject (unique slots, edges
  reference existing slots, no self-loops, no duplicate edges, valid modes, ≥1 node); and the inverse
  serializer must not silently drop `phases`/`cwd`/`model`/`extra_tools`/`extra_skills`/`reverse_mode`. —
  *Carried into:* `shape_to_dict` + round-trip; AT 6 (rich-field round-trip), AT 34.
- **`AdaptationDiff` is structured; `render()` produces the gate string (locked fork 2, item b)** —
  *Rationale:* the structured form powers golden tests + provenance; `render()` feeds the existing
  `adaptation_diff: str` channel, so the gate signature is unchanged (back-compat). Widening the channel
  to carry the structured object is an explicit non-goal. — *Carried into:* `AdaptationDiff.render()`;
  `register_proposal(new_shape, adaptation_diff=diff.render())`; AT 7, AT 23, AT 35.
- **Adaptation-chain carrier = `AdaptationChain` dataclass in `shapes.py`, returned/threaded in-process;
  NOT persisted on broker (resolves Open Question 3)** — *Rationale:* M3 has no cross-call/cross-session
  observability requirement; broker persistence is deferred until one exists. — *Carried into:*
  `shapes.py` `AdaptationChain`; AT 8; `broker.py` untouched.
- **swap/augment role resolution reuses the `instantiate_shape` seam; skipped when `known_roles` absent**
  — *Rationale:* one resolution truth (the merged-pack `factory.known_roles`/`resolve_role`); stub-mode
  tests with no `known_roles` skip resolution exactly as instantiate does. — *Carried into:* `server.py`
  `adapt_shape`; AT 25, AT 26, AT 27, AT 28.
- **Base is pre-instantiation only: a pending or approved proposal, or an inline shape; never a live
  topology (locked Open Question 2)** — *Rationale:* an adapt verb operates purely on `Shape` data and
  must never touch the live teammate registry/routing — that is M3.5. A `base_shape_id` resolving to a
  proposal with status `instantiated` (or `declined`/`timed_out`) is rejected at `stage:"base"`. —
  *Carried into:* `server.py` base-resolution guard; AT 30.
- **`augment` requires ≥1 wiring edge; edge `mode` defaults to `"gated"` (resolves Open Question 4)** —
  *Rationale:* augment's purpose is wiring a participant alongside an existing node; an augment with no
  edge is just `add_node`. Default mode matches `ShapeEdge`'s default. — *Carried into:* `Augment.apply`;
  AT 3, AT 17.
- **`adapt_shape` success `shape` field is the `shape_to_dict` dict form** — *Rationale:* MCP tool
  returns must be JSON-serializable; a raw frozen `Shape` is not. — *Carried into:* `server.py`
  `adapt_shape` return; contract docstring.
- **`broker.py` is not modified** — *Rationale:* `register_proposal` already accepts `adaptation_diff`;
  no new gate machinery is needed. — *Carried into:* Task `adapt-shape-tool` `taskTouches` excludes
  `broker.py`; AT 24 reuses the existing gate.

## Edge Cases

- **`add_node` with no edges** — node appended unconnected; valid (AT 1 covers the with-edge case;
  the no-edge form is the trivial subset).
- **`swap` with no optional fields** — only `role` changes; `model`/`extra_tools`/`extra_skills` and the
  slot's incident edges are preserved unchanged (AT 2).
- **`swap` optional fields supplied** — when `model`/`extra_tools`/`extra_skills` are supplied they
  replace the node's values and appear in the diff's `before`/`after` and `render()`'s per-optional
  clause; when omitted the existing values are retained (AT 35).
- **`set_gate` with `reverse_mode` omitted vs supplied** — omitted leaves the existing `reverse_mode`
  untouched; supplied validates against `{gated,tee,direct}` (AT 4, AT 20).
- **`drop` of the sole remaining node** — resulting shape would have zero nodes; `parse_shape` requires
  ≥1 node, so the round-trip backstop / explicit guard rejects it (AT 34).
- **`drop` of a node that still has live in- or out-edges** — rejected; operator must `set_gate`/rewire
  or drop the edges first (no dangling-edge shapes) (AT 22).
- **`shape_to_dict` field completeness** — a shape carrying `phases`, a node `cwd`/`model`/`extra_tools`/
  `extra_skills`, and an edge `reverse_mode` must round-trip equal; a serializer that drops any of these
  is a latent bug (AT 6).
- **`AdaptationChain.adapt` on an illegal step** — when a chained step's `apply` raises
  `ShapeValidationError`, the exception propagates naturally out of `adapt` (no swallowing, no partial
  chain returned); since `adapt` returns a NEW chain only on success, the prior chain is left untouched.
- **Base supplied as inline dict vs `base_shape_id`** — exactly one must be supplied; neither or both is
  a `stage:"base"` error (AT 31).
- **Re-adapting an approved-but-not-instantiated shape** — `base_shape_id` of an `approved` proposal is
  accepted; the result is a brand-new pending proposal with a new `shape_id` (AT 24).
- **Idempotence / purity** — `apply` is a pure function: the same verb+params on the same base yields an
  equal `Shape` every time and never mutates the input (implied by AT 1–5 asserting the base is unchanged
  after `apply`).
- **Adaptation does not affect displayed data beyond the existing `adaptation_diff` string channel** — no
  `dashboard.html` work; the diff surfaces through the channel M1.5 already renders. Absent data (no
  diff) is the pre-M3 status quo; a present diff renders as the `render()` string.

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| `adapt_shape` → base resolution | exactly one of `base_shape_id`/`base_shape`; if id, proposal status ∈ {pending, approved} | `{ok:False, stage:"base"}` or `{ok:False, stage:"parse"}` | a concrete frozen base `Shape` in hand | nothing registered |
| base → role resolution (swap/augment) | factory may expose `known_roles`/`resolve_role` | unresolvable role → `{ok:False, stage:"adapt", unresolved_roles}` | new/augmenting role resolves (or resolution skipped) | nothing registered |
| role-OK → verb `apply` | verb ∈ five; params well-formed for verb | illegal mutation → `ShapeValidationError` → `{ok:False, stage:"adapt"}` | a new frozen `Shape` satisfying `parse_shape` + its `AdaptationDiff` | nothing registered |
| apply-OK → gate | — | — | `register_proposal(new_shape, adaptation_diff=diff.render())`; `{ok:True, shape_id, status:"pending", diff, shape=shape_to_dict(new_shape)}` | proposal is the only state change; declinable via existing gate |

## Acceptance Tests

Verb structural happy-paths, diffs, round-trip, and provenance (pure `shapes.py`):

1. Given a 2-node shape, when `AddNode(node=ShapeNode("qa","builder"), edges=(ShapeEdge("reviewer","qa"),)).apply(shape)` runs, then the result is a new `Shape` with 3 nodes and the new edge present, the original shape object is unchanged, and the returned `AdaptationDiff.verb == "add_node"`.
2. Given a 2-node shape with edge `implementor->reviewer`, when `Swap(slot="reviewer", role="security-reviewer").apply(shape)` runs (no optional fields), then the `reviewer` node's role is `security-reviewer`, its slot name / `model` / `extra_tools` / `extra_skills` and the `implementor->reviewer` edge are unchanged, and the diff's `before={"role":"sentinel"}` / `after={"role":"security-reviewer"}`.
3. Given a 2-node shape, when `Augment(node=ShapeNode("reviewer2","sentinel"), edges=(ShapeEdge("implementor","reviewer2"),)).apply(shape)` runs (edge mode defaulting to `"gated"`), then the result has the new node plus the wiring edge alongside the existing `reviewer`, and the diff's `verb == "augment"`.
4. Given a 2-node shape with edge `implementor->reviewer` (mode `gated`), when `SetGate(from_slot="implementor", to_slot="reviewer", mode="tee").apply(shape)` runs, then that edge's mode is `tee`, the diff target is `"implementor->reviewer"` with `before={"mode":"gated"}`/`after={"mode":"tee"}`, and a supplied `reverse_mode` is validated and recorded.
5. Given a 3-node shape where slot `extra` has no incident edges, when `Drop(slot="extra").apply(shape)` runs, then `extra` is gone, the remaining 2 nodes and their edges are unchanged, and the diff's `verb == "drop"`.
6. Given each of the five happy-path results from AT 1–5 AND the AT 35 result — the latter a richer shape that carries a top-level `phases` entry, a node with `cwd`, a node with `model`/`extra_tools`/`extra_skills`, and an edge with `reverse_mode` — when `parse_shape(shape_to_dict(result))` is run for each, then it returns a `Shape` equal to `result` (round-trip invariant: every adapted shape re-satisfies `parse_shape`, and `shape_to_dict` preserves `phases`, `cwd`, `model`, `extra_tools`, `extra_skills`, and `reverse_mode`).
7. Given the happy-path diffs from AT 1–5 AND AT 35, when `.render()` is called on each, then each returns the documented format string for its verb (golden assertion against the `render()` templates in Data / API Contracts), including AT 35's appended per-optional `; {field} {old} -> {new}` clauses.
8. Given `AdaptationChain(base=shape)`, when `.adapt(Swap(slot="reviewer", role="security-reviewer")).adapt(SetGate("implementor","reviewer","tee"))` runs, then the chain has exactly 2 ordered `steps`, `chain.current` equals the twice-adapted shape, and `steps[0].diff.verb == "swap"` and `steps[1].diff.verb == "set_gate"` (in-process provenance).
35. Given a 2-node shape with a top-level `phases` entry, whose `implementor` node has `cwd="/repo"`, whose `reviewer` node has `model="sonnet"` and `extra_tools=("Read",)`, and whose `implementor->reviewer` edge has `reverse_mode="direct"`, when `Swap(slot="reviewer", role="security-reviewer", model="opus", extra_tools=("Read","Grep"), extra_skills=("audit",)).apply(shape)` runs, then the `reviewer` node's `role`/`model`/`extra_tools`/`extra_skills` are all replaced (slot name, incident edge, and the other node's `cwd` and the shape's `phases` unchanged); the diff's `before` includes `{"role":"sentinel","model":"sonnet","extra_tools":("Read",),"extra_skills":None}` and `after` the supplied new values; and `.render()` emits `swap reviewer: role sentinel -> security-reviewer` followed by the per-optional clauses `; model sonnet -> opus`, `; extra_tools ("Read",) -> ("Read", "Grep")`, and `; extra_skills None -> ("audit",)`.

Verb structural sad-paths — each raises `ShapeValidationError`, returns no shape, leaves the base unchanged (pure `shapes.py`):

9. Given a shape already containing slot `reviewer`, when `AddNode(node=ShapeNode("reviewer","builder"))` is applied, then it raises `ShapeValidationError` (duplicate slot).
10. Given a 2-node shape, when `AddNode(node=ShapeNode("qa","builder"), edges=(ShapeEdge("qa","ghost"),))` is applied (edge endpoint `ghost` is not a slot), then it raises `ShapeValidationError` (edge references non-existent slot).
11. Given a 2-node shape, when `AddNode(node=ShapeNode("qa","builder"), edges=(ShapeEdge("qa","qa"),))` is applied, then it raises `ShapeValidationError` (self-loop edge).
12. Given a 2-node shape, when `AddNode(node=ShapeNode("qa","builder"), edges=(ShapeEdge("implementor","qa"), ShapeEdge("implementor","qa")))` is applied, then it raises `ShapeValidationError` (duplicate edge).
13. Given a 2-node shape, when `Swap(slot="ghost", role="builder")` is applied, then it raises `ShapeValidationError` (slot does not exist).
14. Given a 2-node shape, when `Augment(node=ShapeNode("reviewer2","sentinel"), edges=(ShapeEdge("ghost","reviewer2"),))` is applied, then it raises `ShapeValidationError` (target slot does not exist).
15. Given a 2-node shape containing slot `reviewer`, when `Augment(node=ShapeNode("reviewer","sentinel"), edges=(ShapeEdge("implementor","reviewer"),))` is applied, then it raises `ShapeValidationError` (resulting duplicate slot).
16. Given a 2-node shape with edge `implementor->reviewer`, when `Augment(node=ShapeNode("qa","sentinel"), edges=(ShapeEdge("implementor","reviewer"),))` is applied (the wiring edge duplicates an existing edge), then it raises `ShapeValidationError` (resulting duplicate edge).
17. Given a 2-node shape, when `Augment(node=ShapeNode("qa","sentinel"), edges=())` is applied (no wiring edge), then it raises `ShapeValidationError` (augment requires at least one wiring edge).
18. Given a 2-node shape, when `SetGate(from_slot="implementor", to_slot="ghost", mode="tee")` is applied (no such edge), then it raises `ShapeValidationError` (edge does not exist).
19. Given a 2-node shape with edge `implementor->reviewer`, when `SetGate(from_slot="implementor", to_slot="reviewer", mode="bogus")` is applied, then it raises `ShapeValidationError` (mode not in {gated,tee,direct}).
20. Given a 2-node shape with edge `implementor->reviewer`, when `SetGate(from_slot="implementor", to_slot="reviewer", mode="tee", reverse_mode="bogus")` is applied, then it raises `ShapeValidationError` (reverse_mode not in {gated,tee,direct}).
21. Given a 2-node shape, when `Drop(slot="ghost")` is applied, then it raises `ShapeValidationError` (slot does not exist).
22. Given a 2-node shape with edge `implementor->reviewer`, when `Drop(slot="reviewer")` is applied (the node has a live in-edge), then it raises `ShapeValidationError` (node has live in/out edges).
34. Given a 1-node shape with no edges, when `Drop(slot=<that node>)` is applied, then it raises `ShapeValidationError` (resulting shape would have zero nodes; a shape must retain ≥1 node).

Server `adapt_shape` tool — gate integration, role resolution, base/verb guards (stub-mode MCP):

23. Given an inline `base_shape` dict and `verb="set_gate"` with valid params, when `adapt_shape` is called, then it returns `{ok:True, status:"pending", shape_id, diff, shape}` where `diff` equals the verb's `AdaptationDiff.render()`, `shape` is the `shape_to_dict` dict form of the new shape, and `broker.snapshot()` shows exactly one new pending proposal whose `adaptation_diff` equals that string.
24. Given an inline `base_shape`, when `adapt_shape(verb="swap", ...)` registers a pending proposal, then `resolve_shape(shape_id, "approve")` approves it, and a SECOND `adapt_shape(verb="set_gate", base_shape_id=<that approved id>, ...)` returns `{ok:True, status:"pending"}` with a NEW distinct `shape_id` — demonstrating the iterative re-gate loop (each step independently gated).
25. Given a stub factory with `known_roles` injected to `("builder","sentinel","security-reviewer")`, when `adapt_shape(verb="swap", params={"slot":"reviewer","role":"security-reviewer"}, base_shape=...)` is called, then the role resolves and a pending proposal is registered (`ok:True`).
26. Given a stub factory with `known_roles` injected to `("builder","sentinel")`, when `adapt_shape(verb="swap", params={"slot":"reviewer","role":"nonexistent-role"}, base_shape=...)` is called, then it returns `{ok:False, stage:"adapt"}` with the unresolvable role reported, and `broker.snapshot()` shows NO new proposal.
27. Given a stub factory with `known_roles` injected to `("builder","sentinel")`, when `adapt_shape(verb="augment", params={node with role "nonexistent-role", edges:[...]}, base_shape=...)` is called, then it returns `{ok:False, stage:"adapt"}` with the unresolvable role reported, and NO new proposal is registered.
28. Given a stub factory with NO `known_roles` attribute (default), when `adapt_shape(verb="swap", params={"slot":"reviewer","role":"anything"}, base_shape=...)` is called, then role resolution is skipped and a pending proposal is registered (`ok:True`) — mirroring `instantiate_shape`'s no-`known_roles` skip.
29. Given `base_shape_id="does-not-exist"`, when `adapt_shape(verb="drop", params={"slot":"x"})` is called, then it returns `{ok:False, stage:"base"}` and registers no proposal.
30. Given a `base_shape_id` whose proposal has status `instantiated` (a now-live topology), when `adapt_shape` is called against it, then it returns `{ok:False, stage:"base"}` (pre-instantiation-only guard; the same path also rejects `declined`/`timed_out` proposals), and registers no proposal.
31. Given a call supplying NEITHER `base_shape_id` nor `base_shape` (and, separately, a call supplying BOTH), when `adapt_shape` is called, then each returns `{ok:False, stage:"base"}` and registers no proposal.
32. Given a valid inline `base_shape` and `verb="frobnicate"`, when `adapt_shape` is called, then it returns `{ok:False, stage:"verb"}` and registers no proposal.
33. Given a valid inline `base_shape` and `verb="drop"` with `params={"slot":"ghost"}` (no such slot), when `adapt_shape` is called, then the verb's `ShapeValidationError` surfaces as `{ok:False, stage:"adapt"}` and NO proposal is registered (server wraps the pure-layer rejection).

## Test Command

Prerequisite: `uv sync` (installs the `dev` group — `pytest`, `pytest-asyncio`, `mcp`). All test imports
(`pytest`, `mcp.shared.memory`, `claude_crew.*`, `yaml`) are already in `pyproject.toml`
(`pyyaml`, `mcp[cli]`, dev `pytest`/`pytest-asyncio`); no new dependency is introduced. This is a
widely-consumed substrate change, so the gate runs the FULL suite — not a keyword-filtered subset.

```bash
uv run pytest
```

## Out of Scope

- **Reshaping a LIVE (already-instantiated) crew** — kill/respawn teammates on `swap`/`drop`, rewire live
  routing mid-flight. Touches the broker live teammate registry + M2 routing engine. This is M3.5.
- **M5 autonomy** — un-gated / memory-informed adaptation. Every M3 step is human-gated.
- **M4** — re-authoring repo-react as a blessed shape.
- **Blessed-shape library + classifier / right-sizing** — now repo-react policy, not claude-crew. M3 is
  the mechanism it consumes.
- **Widening the gate `adaptation_diff` channel to carry the structured `AdaptationDiff` object** + any
  bespoke dashboard diff rendering. M3 renders to the existing string channel; `dashboard.html` is not
  touched.
- **Persisting/replaying adaptation chains across calls or sessions** — provenance is in-process only;
  `broker.py` is not modified.
- **Extracting a shared role-resolution helper** out of `instantiate_shape` and `adapt_shape` — the two
  sites stay as duplicated-but-identical idiom this slice.

## Assumptions

- **[One `adapt_shape` tool, not five per-verb tools]** — *Default:* a single tool with a `verb`
  discriminator and a `params` dict. — *Rationale:* smaller uniform surface, mirrors `propose_shape`;
  resolves Open Question 1 per the brief's stated lean.
- **[`base` is two optional params, exactly one required]** — *Default:* `base_shape_id: str | None`
  (pending/approved proposal) and `base_shape: dict | None` (inline); supplying neither or both is a
  `stage:"base"` error. — *Rationale:* a `str | dict` union is awkward in the MCP/pydantic schema; two
  explicit params give clear arg-validation and a clear error.
- **[`adapt_shape` success `shape` is the dict form]** — *Default:* the success envelope's `shape` field
  is `shape_to_dict(new_shape)` (a JSON-serializable dict), not a raw `Shape`. — *Rationale:* MCP tool
  returns must be JSON-serializable; this matches `propose_shape`'s serialized-summary convention.
- **[`augment` requires ≥1 wiring edge; edge mode defaults to `gated`]** — *Default:* reject empty edges
  with a `ShapeValidationError`; omitted edge `mode` is `"gated"`. — *Rationale:* augment without an edge
  is just `add_node`; `gated` matches `ShapeEdge`'s own default (resolves Open Question 4).
- **[`AdaptationChain` is returned/threaded in-process, not persisted on broker]** — *Default:* a
  `shapes.py` frozen dataclass; no broker field. — *Rationale:* M3 has no cross-call observability need;
  broker persistence is deferred (resolves Open Question 3 per the brief's lean).
- **[`add_node` does NOT resolve the new node's role]** — *Default:* only `swap` and `augment` resolve
  roles (per brief §4 table). — *Rationale:* the locked verb table assigns role-resolution sad paths to
  `swap`/`augment` only; `add_node`'s rejections are purely structural.
- **[Role resolution happens at the server, not inside `apply`]** — *Default:* `shapes.py` verbs are
  factory-free (pure data); the `adapt_shape` tool resolves swap/augment roles via the
  `factory.known_roles`/`resolve_role` seam before/around calling `apply`, surfacing failures at
  `stage:"adapt"`. — *Rationale:* `shapes.py` has no broker/SDK/factory dependency by design; the factory
  lives only in `server.py`, exactly as `instantiate_shape`'s pre-flight.
- **[`render()` format strings as documented in Data / API Contracts]** — *Default:* the implementor
  authors both `render()` and the golden tests from the same template lines. — *Rationale:* the exact
  string is an implementation choice; pinning the template makes AT 7 / AT 35 buildable without
  over-specifying.

## Open Questions

- (none) — Open Questions 1–4 from the design brief are resolved in Design Decisions / Assumptions
  (1: one tool; 2: pre-instantiation-only, locked by Jerome; 3: in-process `AdaptationChain`;
  4: augment ≥1 edge, default mode `gated`).

## Validation

The end-to-end promised outcome is: an operator can apply each of the five verbs to a pre-instantiation
shape and have the change surface at the existing gate as a diff, iteratively. This is exercised entirely
in stub mode by the suite below (no live SDK, no browser). Because this is a widely-consumed substrate
change, validation runs the full suite (per the project CLAUDE.md full-suite mandate). PASS = exit code 0.

```bash
uv run pytest
```

## Task Breakout

```yaml
tasks:
  - name: shape-adaptation-algebra
    description: |
      Add the pure-data adaptation algebra to claude_crew/shapes.py: the abstract
      ShapeAdaptation base; the five frozen verb classes (AddNode, Swap, Augment,
      SetGate, Drop) each with a pure apply(shape) -> tuple[Shape, AdaptationDiff]
      that validates its enumerated sad paths and never mutates/partially-returns
      (Swap replaces supplied optional model/extra_tools/extra_skills and retains
      omitted ones); the structured frozen AdaptationDiff with render() (incl. the
      per-optional "; {field} {old} -> {new}" clause); the AdaptationChain +
      AdaptationStep provenance carriers; and the shape_to_dict(shape) serializer
      (inverse of parse_shape, preserving phases/cwd/model/extra_tools/extra_skills/
      reverse_mode) used by the round-trip invariant. Shape/ShapeNode/ShapeEdge stay
      unchanged. Authors the new tests/test_shape_adaptation.py with the per-verb
      happy-paths, the optional-field swap golden (AT 35), all structural sad-paths,
      the rich-field round-trip invariant (AT 6), the render() golden tests, and the
      chain-provenance test.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 34, 35]
    taskTouches: ["claude_crew/shapes.py", "tests/test_shape_adaptation.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_adaptation.py
  - name: adapt-shape-tool
    description: |
      Add the adapt_shape MCP tool to claude_crew/server.py: resolve the base shape
      (exactly one of base_shape_id/base_shape; base_shape_id must resolve to a
      pending or approved proposal — reject instantiated/declined/timed_out at
      stage:"base"); for verb in {swap, augment}, resolve the new/augmenting role
      through the same factory.known_roles()/resolve_role() seam instantiate_shape
      uses (skip when known_roles absent); construct and apply the verb command;
      on success call broker.register_proposal(new_shape, adaptation_diff=diff.render())
      and return {ok, shape_id, status:"pending", diff, shape=shape_to_dict(new_shape)};
      map every failure to the documented {ok:False, stage:...} envelope with NO
      proposal registered. broker.py is NOT modified. Authors the new
      tests/test_shape_adapt_tool.py with gate integration, the iterative re-gate loop,
      role-resolution (resolvable / unresolvable / no-known_roles-skip), and the
      base/verb/illegal-mutation guards.
    dependsOn: [shape-adaptation-algebra]
    acceptanceTests: [23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33]
    taskTouches: ["claude_crew/server.py", "tests/test_shape_adapt_tool.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_adapt_tool.py tests/test_shape_gate.py
```

## Design Notes

- **Test conventions (project CLAUDE.md):** imports at module top (no inline imports except optional-dep
  guards); `asyncio.get_running_loop()` not `get_event_loop()`; `asyncio_mode="auto"` (no
  `@pytest.mark.asyncio` needed); server tests use `create_connected_server_and_client_session` +
  `make_server(broker=, factory=)` and a `_content_json` unwrap helper (mirror `tests/test_shape_gate.py`).
- **Stub-factory `known_roles` injection (AT 25–28):** set `stub_factory.known_roles = lambda: (...)`
  (and optionally `stub_factory.resolve_role`) and tear it down in a fixture, exactly as
  `tests/test_shape_gate.py::clean_stub_known_roles` / `_make_resolve_role` do. Do NOT leave it set
  across tests.
- **No deletion-detection gap:** every M3 deliverable is verified by a green-suite behavioral test (pure
  `shapes.py` unit tests + stub-mode MCP tests). Nothing is deferred to a live-SDK turn or browser render,
  so no separate structural grep-guard is required.
- **`broker.py` untouched is load-bearing:** `register_proposal` already carries `adaptation_diff`;
  introducing a second gate path or changing the signature would violate the locked interaction model.
