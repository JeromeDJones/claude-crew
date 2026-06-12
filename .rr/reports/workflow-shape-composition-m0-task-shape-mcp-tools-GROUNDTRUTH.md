# Coordinator ground-truth for slice-review (task shape-mcp-tools)

## Test results (coordinator-run)
- `uv run pytest tests/test_shape_gate.py` → see exit above (coordinator re-ran)
- Implementor reported: 11 slice tests pass; full suite 1380 passed + 2 pre-existing test_shutdown_signals baseline failures.

## git diff --stat HEAD
```
 claude_crew/factories.py |   4 ++
 claude_crew/server.py    | 168 +++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 172 insertions(+)
```

## Full diff (server.py + factories.py)
```diff
diff --git a/claude_crew/factories.py b/claude_crew/factories.py
index 197b343..27b86f9 100644
--- a/claude_crew/factories.py
+++ b/claude_crew/factories.py
@@ -547,6 +547,10 @@ def default_factory(
         # Frozen startup diagnostics tuple; consumed by make_server() when it
         # constructs the default Broker (Broker(startup_diagnostics=...)).
         factory.startup_diagnostics = startup_diagnostics  # type: ignore[attr-defined]
+        # Live enumeration of known roles for instantiate_shape pre-flight.
+        # Reads holder.pack LIVE so post-refresh state is always current.
+        # Mirrors the startup_diagnostics accessor idiom above.
+        factory.known_roles = lambda: tuple(holder.pack.keys())  # type: ignore[attr-defined]
         # Expose the holder so tests (and future refresh() wiring) can reach it.
         factory._holder = holder  # type: ignore[attr-defined]
         return factory
diff --git a/claude_crew/server.py b/claude_crew/server.py
index a4ac35b..6da7d1c 100644
--- a/claude_crew/server.py
+++ b/claude_crew/server.py
@@ -24,8 +24,10 @@ from claude_crew.broker import (
     Broker,
     TeammateAlreadyDeadError,
     TeammateFactory,
+    Topology,
     UnknownTeammateError,
 )
+from claude_crew.shapes import ShapeValidationError, parse_shape
 from claude_crew.artifact_registry import (
     ArtifactNotText,
     ArtifactRegistry,
@@ -730,6 +732,172 @@ def make_server(
             "crew_id": artifact_registry.crew_id,
         }
 
+    @mcp.tool()
+    async def propose_shape(
+        shape: dict,
+        adaptation_diff: str | None = None,
+        timeout_seconds: float = 600,
+    ) -> dict[str, Any]:
+        """Propose a multi-agent topology shape for human approval before instantiation.
+
+        Parses and validates the shape, registers a pending proposal in the
+        broker's shape-gate queue, and blocks until the operator approves,
+        declines, or the timeout expires.  Approve or decline via the Mission
+        Control dashboard (shape-gate panel) or by calling the dashboard's
+        POST /shape-approval/{crew_id}/{shape_id} route directly.
+
+        Args:
+            shape: Shape definition dict. Required keys: name, description,
+                nodes (list of {slot, role, …}). Optional: edges (list of
+                {from_slot, to_slot, mode}), phases.
+            adaptation_diff: Optional diff / notes describing changes from a
+                base shape. Surfaced in the dashboard for operator context.
+            timeout_seconds: Seconds to block waiting for a decision
+                (default 600). Returns status "timed_out" on expiry.
+
+        Returns:
+            ok: True (parse succeeded and gate returned a decision).
+            shape_id: Opaque proposal identifier; pass to instantiate_shape.
+            status: "approved" | "declined" | "timed_out".
+            shape: Serialized summary of the parsed shape.
+
+            On parse failure:
+            ok: False.
+            stage: "parse".
+            error: Validation error message.
+        """
+        try:
+            parsed = parse_shape(shape)
+        except ShapeValidationError as exc:
+            return {"ok": False, "stage": "parse", "error": str(exc)}
+
+        shape_id = broker.register_proposal(parsed, adaptation_diff=adaptation_diff)
+        proposal = await broker.await_proposal(shape_id, timeout=timeout_seconds)
+
+        return {
+            "ok": True,
+            "shape_id": shape_id,
+            "status": proposal.status,
+            "shape": {
+                "name": parsed.name,
+                "description": parsed.description,
+                "nodes": [{"slot": n.slot, "role": n.role} for n in parsed.nodes],
+            },
+        }
+
+    @mcp.tool()
+    async def instantiate_shape(shape_id: str) -> dict[str, Any]:
+        """Instantiate an approved shape by spawning one teammate per declared node.
+
+        Refuses any non-approved proposal (pending, declined, timed_out,
+        instantiated, or unknown) without spawning anything.
+
+        When the factory exposes ``known_roles``, performs an all-or-nothing
+        pre-flight: resolves every node's role against the live pack (exact
+        match or unique ``*:role`` suffix promotion, mirroring the sdk factory's
+        spawn-time promotion).  Any unresolvable role aborts the entire
+        instantiation — zero teammates spawn.  When the factory does NOT expose
+        ``known_roles`` (stub default), the pre-flight is skipped and roles are
+        passed through to spawn_teammate as-is (preserving existing behavior).
+
+        Each node is spawned via ``broker.spawn_teammate`` with
+        ``name=slot`` so teammates appear in ``list_crew`` by their slot name.
+        Instantiation is single-use: the proposal transitions to
+        ``"instantiated"`` and cannot be re-used.
+
+        Args:
+            shape_id: The proposal id returned by propose_shape.
+
+        Returns:
+            ok: True on success.
+            shape_id: The proposal id.
+            crew: List of {slot, teammate_id, role} for each spawned node.
+            topology: {shape_name, edges: [[from, to, mode], …], slot_to_teammate}.
+
+            On failure:
+            ok: False.
+            error: Human-readable reason.
+            unresolved_roles: (pre-flight failure only) list of role strings
+                that could not be resolved against factory.known_roles().
+        """
+        proposal = broker.get_proposal(shape_id)
+        if proposal is None:
+            return {"ok": False, "error": f"unknown shape_id: {shape_id!r}"}
+        if proposal.status != "approved":
+            return {
+                "ok": False,
+                "error": (
+                    f"shape {shape_id!r} has status {proposal.status!r}; "
+                    "only 'approved' proposals can be instantiated"
+                ),
+            }
+
+        shape = proposal.shape
+
+        # Pre-flight role resolution (all-or-nothing).
+        # Only runs when the factory exposes known_roles; stub default skips it.
+        known_roles_fn = getattr(factory, "known_roles", None)
+        if known_roles_fn is not None:
+            known: set[str] = set(known_roles_fn())
+            unresolved: list[str] = []
+            for node in shape.nodes:
+                role = node.role
+                if role in known:
+                    continue
+                # Unique ':role' suffix promotion (mirrors factories._resolve_role)
+                candidates = [k for k in known if k.endswith(f":{role}")]
+                if len(candidates) == 1:
+                    continue  # uniquely promotable → accepted
+                unresolved.append(role)
+            if unresolved:
+                return {
+                    "ok": False,
+                    "error": f"unresolvable role(s) against factory.known_roles: {unresolved}",
+                    "unresolved_roles": unresolved,
+                }
+
+        # Spawn one teammate per node (pre-flight guarantees no partial spawn).
+        crew: list[dict[str, str]] = []
+        slot_to_teammate: dict[str, str] = {}
+
+        for node in shape.nodes:
+            tid = await broker.spawn_teammate(
+                role=node.role,
+                name=node.slot,
+                factory=factory,
+                model=node.model,
+                extra_tools=list(node.extra_tools or ()) or None,
+                extra_skills=list(node.extra_skills or ()) or None,
+                cwd=node.cwd,
+            )
+            crew.append({"slot": node.slot, "teammate_id": tid, "role": node.role})
+            slot_to_teammate[node.slot] = tid
+
+        # Record topology (immutable snapshot of edges + slot→teammate map).
+        topology = Topology(
+            shape_name=shape.name,
+            edges=tuple(
+                (edge.from_slot, edge.to_slot, edge.mode)
+                for edge in shape.edges
+            ),
+            slot_to_teammate=slot_to_teammate,
+        )
+        broker.record_topology(topology)
+
+        # Mark single-use: prevents double-spawn on a second instantiate call.
+        proposal.status = "instantiated"
+
+        return {
+            "ok": True,
+            "shape_id": shape_id,
+            "crew": crew,
+            "topology": {
+                "shape_name": topology.shape_name,
+                "edges": [list(e) for e in topology.edges],
+                "slot_to_teammate": slot_to_teammate,
+            },
+        }
+
     # Stash the broker on the server for tests / introspection.
     mcp._broker = broker  # type: ignore[attr-defined]
 
```
