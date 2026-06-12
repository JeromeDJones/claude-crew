# Coordinator ground-truth for slice-review (task broker-proposals-topology)

## Test results (coordinator-run)
- `uv run pytest tests/test_shape_broker.py` → **18 passed, exit 0**
- `uv run pytest tests/test_shapes.py` (prior task non-regression) → **45 passed, exit 0**
- NOTE: full-suite `test_shutdown_signals` (2) fail on baseline too — pre-existing real-server infra flakes, NOT this slice.

## git diff --stat HEAD
```
 claude_crew/broker.py | 122 ++++++++++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 122 insertions(+)
```

## Full diff of claude_crew/broker.py (the modified file)
```diff
diff --git a/claude_crew/broker.py b/claude_crew/broker.py
index 38a764d..3cc7df4 100644
--- a/claude_crew/broker.py
+++ b/claude_crew/broker.py
@@ -18,6 +18,7 @@ from uuid import uuid4
 
 from claude_crew.diagnostics import StartupDiagnostic
 from claude_crew.envelope import Envelope, new_message_id
+from claude_crew.shapes import Shape
 from claude_crew.teammate import Teammate, ToolEvent
 from claude_crew.transcript import TranscriptSink
 
@@ -109,6 +110,33 @@ class LiveTeammateInfo:
     config: "dict[str, Any] | None" = None
 
 
+@dataclass
+class ShapeProposal:
+    """A pending/approved/declined/timed_out/instantiated gate record.
+
+    Not frozen — ``status`` mutates as the proposal moves through its
+    state machine (pending → approved | declined | timed_out | instantiated).
+    """
+
+    shape_id: str
+    shape: Shape
+    adaptation_diff: str | None
+    status: str  # "pending" | "approved" | "declined" | "timed_out" | "instantiated"
+
+
+@dataclass(frozen=True)
+class Topology:
+    """Recorded edges + per-edge mode + slot→teammate map after instantiation.
+
+    ``edges`` is a tuple of (from_slot, to_slot, mode) triples. Frozen by
+    construction — topology facts don't change once instantiation succeeds.
+    """
+
+    shape_name: str
+    edges: "tuple[tuple[str, str, str], ...]"   # (from_slot, to_slot, mode)
+    slot_to_teammate: "dict[str, str]"           # slot -> teammate_id
+
+
 @dataclass(frozen=True)
 class BrokerSnapshot:
     """Frozen, value-copied view of broker state for downstream consumers.
@@ -134,6 +162,12 @@ class BrokerSnapshot:
     # as startup-only by contract: a future feature wanting runtime
     # diagnostics must introduce a new field, not redefine this one.
     startup_diagnostics: "tuple[StartupDiagnostic, ...]" = ()
+    # M0: shape-gate proposals. Snapshot of the _proposals dict at call time;
+    # entries are mutable ShapeProposal objects — status may advance after the
+    # snapshot is taken (same contract as live teammate references in BrokerSnapshot).
+    shape_proposals: "tuple[ShapeProposal, ...]" = ()
+    # M0: topologies recorded after successful shape instantiation.
+    topologies: "tuple[Topology, ...]" = ()
 
 
 # A factory takes (id, name, role, model=None) and returns an unstarted
@@ -168,6 +202,13 @@ class Broker:
         self._startup_diagnostics: tuple[StartupDiagnostic, ...] = tuple(
             startup_diagnostics
         )
+        # M0: proposal state machine — keyed by shape_id.
+        self._proposals: dict[str, ShapeProposal] = {}
+        # Condition notified by resolve_proposal; awaited by await_proposal.
+        # Mirrors the _lead_message_condition pattern.
+        self._proposal_condition: asyncio.Condition = asyncio.Condition()
+        # M0: recorded topologies after successful instantiation.
+        self._topologies: list[Topology] = []
         # Tombstoned teammates: keyed by teammate_id, holds the Teammate object
         # after it's popped from _teammates so get_tool_output can still delegate
         # to it for evicted-but-recently-dead lookups.
@@ -908,6 +949,8 @@ class Broker:
             tool_events=tuple(all_tool_events),
             dead_configs=dead_configs,
             startup_diagnostics=self._startup_diagnostics,
+            shape_proposals=tuple(self._proposals.values()),
+            topologies=tuple(self._topologies),
         )
 
     def get_teammate_status(self, teammate_id: str) -> dict[str, Any]:
@@ -1025,6 +1068,85 @@ class Broker:
             alive_result["config"] = config
         return alive_result
 
+    # ---------- proposals / topologies (M0) ----------
+
+    def register_proposal(
+        self,
+        shape: Shape,
+        adaptation_diff: str | None = None,
+    ) -> str:
+        """Register a new shape proposal with status 'pending'.
+
+        Returns the shape_id (a 12-hex-char unique key) so the caller can
+        pass it to await_proposal / resolve_proposal / get_proposal.
+        """
+        shape_id = uuid4().hex[:12]
+        proposal = ShapeProposal(
+            shape_id=shape_id,
+            shape=shape,
+            adaptation_diff=adaptation_diff,
+            status="pending",
+        )
+        self._proposals[shape_id] = proposal
+        return shape_id
+
+    async def await_proposal(self, shape_id: str, timeout: float) -> ShapeProposal:
+        """Block until the proposal's status advances from 'pending', or timeout.
+
+        Mirrors the _lead_message_condition long-poll idiom:
+        - acquires _proposal_condition
+        - loops on status == "pending", calling condition.wait()
+        - on asyncio.timeout, sets status to 'timed_out' and returns
+
+        Uses asyncio.timeout() (Python 3.12) rather than asyncio.wait_for()
+        to avoid extra Task-wrapping semantics for asyncio.Condition.wait().
+        """
+        proposal = self._proposals.get(shape_id)
+        if proposal is None:
+            raise KeyError(f"unknown shape_id: {shape_id!r}")
+
+        async with self._proposal_condition:
+            try:
+                async with asyncio.timeout(timeout):
+                    while proposal.status == "pending":
+                        await self._proposal_condition.wait()
+            except TimeoutError:
+                if proposal.status == "pending":
+                    proposal.status = "timed_out"
+
+        return proposal
+
+    async def resolve_proposal(self, shape_id: str, decision: str) -> ShapeProposal:
+        """Resolve a pending proposal to 'approved' or 'declined'.
+
+        decision must be 'approve' or 'decline'.
+        Notifies _proposal_condition so any awaiting await_proposal unblocks.
+        """
+        if decision not in ("approve", "decline"):
+            raise ValueError(
+                f"decision must be 'approve' or 'decline', got {decision!r}"
+            )
+        proposal = self._proposals.get(shape_id)
+        if proposal is None:
+            raise KeyError(f"unknown shape_id: {shape_id!r}")
+
+        proposal.status = "approved" if decision == "approve" else "declined"
+        async with self._proposal_condition:
+            self._proposal_condition.notify_all()
+        return proposal
+
+    def get_proposal(self, shape_id: str) -> "ShapeProposal | None":
+        """Return the ShapeProposal for the given shape_id, or None if unknown."""
+        return self._proposals.get(shape_id)
+
+    def record_topology(self, topology: Topology) -> None:
+        """Record a topology after successful shape instantiation."""
+        self._topologies.append(topology)
+
+    def get_topologies(self) -> "tuple[Topology, ...]":
+        """Return all recorded topologies as an immutable tuple."""
+        return tuple(self._topologies)
+
     def get_tool_output(self, teammate_id: str, tool_use_id: str) -> "str | None":
         """Return the stored tool output for the given (teammate_id, tool_use_id) pair.
 
```
