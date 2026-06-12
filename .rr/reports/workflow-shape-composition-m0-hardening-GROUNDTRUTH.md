# Coordinator ground-truth — hardening fix batch (1-5)

## Test results (coordinator-run)
- Shape suite (`tests/test_shape*.py`): **117 passed, exit 0** (was 101 → 16 new hardening tests).
- FULL suite (`uv run pytest`): **1440 passed, 34 skipped, 1 xfailed, exit 0** — no regressions, flakes passed this run.

## git diff --stat HEAD (code only)
```
 claude_crew/broker.py      |  42 +++++++++++-
 claude_crew/factories.py   |   4 ++
 claude_crew/server.py      |  77 ++++++++++++++-------
 tests/test_shape_broker.py | 158 ++++++++++++++++++++++++++++++++++++++++++
 tests/test_shape_gate.py   | 167 ++++++++++++++++++++++++++++++++++++++++++++-
 5 files changed, 423 insertions(+), 25 deletions(-)
```

## Full diff: claude_crew/broker.py
```diff
claude_crew/broker.py | 42 +++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 41 insertions(+), 1 deletion(-)

--- Changes ---

claude_crew/broker.py
  @@ -12,7 +12,9 @@ import copy
  +from collections.abc import Mapping
   from dataclasses import dataclass
  +from types import MappingProxyType
   from typing import TYPE_CHECKING, Any, Callable, Literal
   from uuid import uuid4
   
  @@ -130,11 +132,23 @@ class Topology:
  +    ``slot_to_teammate`` is wrapped in a MappingProxyType on construction so
  +    callers cannot mutate it in place even though the field type is Mapping.
       """
   
       shape_name: str
       edges: "tuple[tuple[str, str, str], ...]"   # (from_slot, to_slot, mode)
  -    slot_to_teammate: "dict[str, str]"           # slot -> teammate_id
  +    slot_to_teammate: "Mapping[str, str]"         # slot -> teammate_id (read-only proxy)
  +
  +    def __post_init__(self) -> None:
  +        # Wrap slot_to_teammate in a read-only proxy so in-place mutation raises
  +        # TypeError.  dict() copy first so the proxy owns its own data and the
  +        # caller's original dict cannot be mutated either.
  +        object.__setattr__(
  +            self,
  +            "slot_to_teammate",
  +            MappingProxyType(dict(self.slot_to_teammate)),
  +        )
   
   
   @dataclass(frozen=True)
  @@ -1121,6 +1135,9 @@ class Broker:
  +
  +        Raises KeyError if shape_id is unknown.
  +        Raises ValueError if decision is invalid or the proposal is not 'pending'.
           """
           if decision not in ("approve", "decline"):
               raise ValueError(
  @@ -1129,6 +1146,11 @@ class Broker:
  +        if proposal.status != "pending":
  +            raise ValueError(
  +                f"cannot resolve proposal {shape_id!r} in state {proposal.status!r}; "
  +                "only 'pending' is resolvable"
  +            )
   
           proposal.status = "approved" if decision == "approve" else "declined"
           async with self._proposal_condition:
  @@ -1139,6 +1161,24 @@ class Broker:
  +    def mark_instantiated(self, shape_id: str) -> None:
  +        """Transition an approved proposal to 'instantiated'.
  +
  +        Raises KeyError if shape_id is unknown.
  +        Raises ValueError if the proposal is not in 'approved' state — enforces
  +        that instantiation is the sole path from 'approved' and cannot override
  +        a pending/declined/timed_out/already-instantiated proposal.
  +        """
  +        proposal = self._proposals.get(shape_id)
  +        if proposal is None:
  +            raise KeyError(f"unknown shape_id: {shape_id!r}")
  +        if proposal.status != "approved":
  +            raise ValueError(
  +                f"cannot instantiate proposal {shape_id!r} in state {proposal.status!r}; "
  +                "only 'approved' is instantiable"
  +            )
  +        proposal.status = "instantiated"
  +
       def record_topology(self, topology: Topology) -> None:
           """Record a topology after successful shape instantiation."""
           self._topologies.append(topology)
  +41 -1
```
## Full diff: claude_crew/factories.py
```diff
claude_crew/factories.py | 4 ++++
 1 file changed, 4 insertions(+)

--- Changes ---

claude_crew/factories.py
  @@ -551,6 +551,10 @@ def default_factory(
  +        # Expose the role resolver so instantiate_shape pre-flight can share the
  +        # exact same promotion logic rather than re-implementing it.  Same idiom
  +        # as factory.known_roles above.
  +        factory.resolve_role = _resolve_role  # type: ignore[attr-defined]
           # Expose the holder so tests (and future refresh() wiring) can reach it.
           factory._holder = holder  # type: ignore[attr-defined]
           return factory
  +4 -0
```
## Full diff: claude_crew/server.py
```diff
claude_crew/server.py | 77 ++++++++++++++++++++++++++++++++++++---------------
 1 file changed, 54 insertions(+), 23 deletions(-)

--- Changes ---

claude_crew/server.py
  @@ -836,19 +836,32 @@ def make_server(
  +        # Fix 2: prefer factory.resolve_role (shared closure) when available so
  +        # the promotion logic is never duplicated between factory and pre-flight.
           known_roles_fn = getattr(factory, "known_roles", None)
           if known_roles_fn is not None:
               known: set[str] = set(known_roles_fn())
  +            resolve_role_fn = getattr(factory, "resolve_role", None)
               unresolved: list[str] = []
               for node in shape.nodes:
                   role = node.role
  -                if role in known:
  -                    continue
  -                # Unique ':role' suffix promotion (mirrors factories._resolve_role)
  -                candidates = [k for k in known if k.endswith(f":{role}")]
  -                if len(candidates) == 1:
  -                    continue  # uniquely promotable → accepted
  -                unresolved.append(role)
  +                if resolve_role_fn is not None:
  +                    # Shared resolver: returns the promoted key when resolvable,
  +                    # or the original role when not → only resolvable if the
  +                    # result is actually in known.
  +                    resolved = resolve_role_fn(role)
  +                    if resolved not in known:
  +                        unresolved.append(role)
  +                else:
  +                    # Fallback for factories that expose known_roles but not
  +                    # resolve_role (e.g. stub-mode tests that inject known_roles
  +                    # without also injecting resolve_role).
  +                    if role in known:
  +                        continue
  +                    candidates = [k for k in known if k.endswith(f":{role}")]
  +                    if len(candidates) == 1:
  +                        continue  # uniquely promotable → accepted
  +                    unresolved.append(role)
               if unresolved:
                   return {
                       "ok": False,
  @@ -856,22 +869,40 @@ def make_server(
  -        # Spawn one teammate per node (pre-flight guarantees no partial spawn).
  +        # Spawn one teammate per node.
  +        # Fix 4: transactional — on any spawn failure, roll back already-spawned
  +        # teammates so no partial crew is left alive.  The proposal stays
  +        # 'approved' on failure so a corrected retry can re-attempt instantiation.
           crew: list[dict[str, str]] = []
           slot_to_teammate: dict[str, str] = {}
   
  -        for node in shape.nodes:
  -            tid = await broker.spawn_teammate(
  -                role=node.role,
  -                name=node.slot,
  -                factory=factory,
  -                model=node.model,
  -                extra_tools=list(node.extra_tools or ()) or None,
  -                extra_skills=list(node.extra_skills or ()) or None,
  -                cwd=node.cwd,
  -            )
  -            crew.append({"slot": node.slot, "teammate_id": tid, "role": node.role})
  -            slot_to_teammate[node.slot] = tid
  +        try:
  +            for node in shape.nodes:
  +                tid = await broker.spawn_teammate(
  +                    role=node.role,
  +                    name=node.slot,
  +                    factory=factory,
  +                    model=node.model,
  +                    extra_tools=list(node.extra_tools or ()) or None,
  +                    extra_skills=list(node.extra_skills or ()) or None,
  +                    cwd=node.cwd,
  +                )
  +                crew.append({"slot": node.slot, "teammate_id": tid, "role": node.role})
  +                slot_to_teammate[node.slot] = tid
  +        except Exception as exc:
  +            # Roll back: kill every teammate that was already spawned.
  +            rolled_back: list[str] = []
  +            for entry in crew:
  +                try:
  +                    await broker.kill_teammate(entry["teammate_id"], reason="spawn-rollback", graceful=False)
  +                    rolled_back.append(entry["slot"])
  +                except Exception:
  +                    pass  # best-effort; at minimum the teammate will die naturally
  +            return {
  +                "ok": False,
  +                "error": f"spawn failed mid-instantiation: {exc}",
  +                "partial_crew_rolled_back": rolled_back,
  +            }
   
           # Record topology (immutable snapshot of edges + slot→teammate map).
           topology = Topology(
  @@ -884,8 +915,8 @@ def make_server(
  -        # Mark single-use: prevents double-spawn on a second instantiate call.
  -        proposal.status = "instantiated"
  +        # Fix 5: transition via broker method (enforces state guard).
  +        broker.mark_instantiated(shape_id)
   
           return {
               "ok": True,
  @@ -894,7 +925,7 @@ def make_server(
  -                "slot_to_teammate": slot_to_teammate,
  +                "slot_to_teammate": dict(slot_to_teammate),
               },
           }
   
  +54 -23
```
