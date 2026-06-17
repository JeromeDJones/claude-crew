## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/m3-adaptation-algebra-feature-review-0.md`

Follow the review-feature skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
diff --git a/claude_crew/server.py b/claude_crew/server.py
index fc20ee0..5f59bbd 100644
--- a/claude_crew/server.py
+++ b/claude_crew/server.py
@@ -27,7 +27,20 @@ from claude_crew.broker import (
     Topology,
     UnknownTeammateError,
 )
-from claude_crew.shapes import ShapeValidationError, parse_shape, shape_to_mermaid
+from claude_crew.shapes import (
+    AddNode,
+    Augment,
+    Drop,
+    SetGate,
+    ShapeAdaptation,
+    ShapeEdge,
+    ShapeNode,
+    ShapeValidationError,
+    Swap,
+    parse_shape,
+    shape_to_dict,
+    shape_to_mermaid,
+)
 from claude_crew.artifact_registry import (
     ArtifactNotText,
     ArtifactRegistry,
@@ -1028,6 +1041,174 @@ def make_server(
             },
         }
 
+    @mcp.tool()
+    async def adapt_shape(
+        verb: str,
+        params: dict,
+        base_shape_id: str | None = None,
+        base_shape: dict | None = None,
+    ) -> dict[str, Any]:
+        """Apply one adaptation verb to a pre-instantiation base shape, then register
+        the result as a NEW pending proposal carrying the diff (reuses the M1.5 gate).
+
+        Verb must be one of: add_node | swap | augment | set_gate | drop.
+
+        Returns on success:  {ok: True, shape_id, status: "pending", diff, shape}
+          - diff is AdaptationDiff.render() (the string the gate shows)
+          - shape is the shape_to_dict(new_shape) DICT form, NOT a raw Shape
+            (MCP tool returns must be JSON-serializable)
+        Failure envelopes (NO proposal registered in any failure case):
+          {ok: False, stage: "base",  error}   # neither/both base args; unknown id;
+                                                #   status not pending/approved
+          {ok: False, stage: "parse", error}   # inline base_shape fails parse_shape
+          {ok: False, stage: "verb",  error}   # verb not one of the five
+          {ok: False, stage: "adapt", error, [unresolved_roles]}
+                                                # illegal mutation (ShapeValidationError) OR
+                                                #   swap/augment role unresolvable
+        """
+        # ── 1. Base resolution ─────────────────────────────────────────────────
+        # Exactly one of base_shape_id / base_shape must be supplied.
+        both_supplied = base_shape_id is not None and base_shape is not None
+        neither_supplied = base_shape_id is None and base_shape is None
+        if both_supplied or neither_supplied:
+            return {
+                "ok": False,
+                "stage": "base",
+                "error": "exactly one of base_shape_id or base_shape must be supplied",
+            }
+
+        if base_shape_id is not None:
+            proposal = broker.get_proposal(base_shape_id)
+            if proposal is None:
+                return {
+                    "ok": False,
+                    "stage": "base",
+                    "error": f"unknown shape_id: {base_shape_id!r}",
+                }
+            if proposal.status not in ("pending", "approved"):
+                return {
+                    "ok": False,
+                    "stage": "base",
+                    "error": (
+                        f"shape {base_shape_id!r} has status {proposal.status!r};"
+                        " only pending or approved proposals can be used as a base"
+                    ),
+                }
+            resolved_base = proposal.shape
+        else:
+            # Inline base_shape dict — parse_shape validates it.
+            try:
+                resolved_base = parse_shape(base_shape)  # type: ignore[arg-type]
+            except ShapeValidationError as exc:
+                return {"ok": False, "stage": "parse", "error": str(exc)}
+
+        # ── 2. Verb validation ─────────────────────────────────────────────────
+        _KNOWN_VERBS: frozenset[str] = frozenset(
+            {"add_node", "swap", "augment", "set_gate", "drop"}
+        )
+        if verb not in _KNOWN_VERBS:
+            return {
+                "ok": False,
+                "stage": "verb",
+                "error": (
+                    f"unknown verb {verb!r}; must be one of {sorted(_KNOWN_VERBS)}"
+                ),
+            }
+
+        # ── 3. Role resolution for swap / augment ──────────────────────────────
+        # Mirrors instantiate_shape's pre-flight seam exactly (single role).
+        # Skip entirely when factory.known_roles is absent (stub / no pack).
+        if verb in ("swap", "augment"):
+            if verb == "swap":
+                role_to_check: str | None = params.get("role")
+            else:
+                node_params = params.get("node")
+                role_to_check = (
+                    node_params.get("role")
+                    if isinstance(node_params, dict)
+                    else None
+                )
+
+            known_roles_fn = getattr(factory, "known_roles", None)
+            if known_roles_fn is not None and role_to_check is not None:
+                known_set: set[str] = set(known_roles_fn())
+                resolve_role_fn = getattr(factory, "resolve_role", None)
+                unresolved: list[str] = []
+                if resolve_role_fn is not None:
+                    resolved_key = resolve_role_fn(role_to_check)
+                    if resolved_key not in known_set:
+                        unresolved.append(role_to_check)
+                else:
+                    if role_to_check not in known_set:
+                        candidates = [
+                            k for k in known_set
+                            if k.endswith(f":{role_to_check}")
+                        ]
+                        if len(candidates) != 1:
+                            unresolved.append(role_to_check)
+                if unresolved:
+                    return {
+                        "ok": False,
+                        "stage": "adapt",
+                        "error": (
+                            f"unresolvable role(s) against factory.known_roles:"
+                            f" {unresolved}"
+                        ),
+                        "unresolved_roles": unresolved,
+                    }
+
+        # ── 4. Construct the verb command and apply ────────────────────────────
+        def _node_from_dict(d: dict) -> ShapeNode:
+            """Build a ShapeNode from a plain dict; coerce lists → tuples."""
+            d = dict(d)
+            if d.get("extra_tools") is not None:
+                d["extra_tools"] = tuple(d["extra_tools"])
+            if d.get("extra_skills") is not None:
+                d["extra_skills"] = tuple(d["extra_skills"])
+            return ShapeNode(**d)
+
+        try:
+            command: ShapeAdaptation
+            if verb == "add_node":
+                node = _node_from_dict(params["node"])
+                raw_edges = params.get("edges", [])
+                edges = tuple(ShapeEdge(**e) for e in raw_edges)
+                command = AddNode(node=node, edges=edges)
+            elif verb == "swap":
+                swap_params = dict(params)
+                if swap_params.get("extra_tools") is not None:
+                    swap_params["extra_tools"] = tuple(swap_params["extra_tools"])
+                if swap_params.get("extra_skills") is not None:
+                    swap_params["extra_skills"] = tuple(swap_params["extra_skills"])
+                command = Swap(**swap_params)
+            elif verb == "augment":
+                node = _node_from_dict(params["node"])
+                raw_edges = params.get("edges", [])
+                edges = tuple(ShapeEdge(**e) for e in raw_edges)
+                command = Augment(node=node, edges=edges)
+            elif verb == "set_gate":
+                command = SetGate(**params)
+            else:  # drop
+                command = Drop(**params)
+
+            new_shape, diff = command.apply(resolved_base)
+        except ShapeValidationError as exc:
+            return {"ok": False, "stage": "adapt", "error": str(exc)}
+        except (KeyError, TypeError) as exc:
+            return {"ok": False, "stage": "adapt", "error": str(exc)}
+
+        # ── 5. Register as a new pending proposal and return ───────────────────
+        diff_str = diff.render()
+        new_shape_id = broker.register_proposal(new_shape, adaptation_diff=diff_str)
+
+        return {
+            "ok": True,
+            "shape_id": new_shape_id,
+            "status": "pending",
+            "diff": diff_str,
+            "shape": shape_to_dict(new_shape),
+        }
+
     # Stash the broker on the server for tests / introspection.
     mcp._broker = broker  # type: ignore[attr-defined]
... and 1982 more lines truncated (read full files in the worktree: claude_crew/shapes.py, claude_crew/server.py, tests/test_shape_adaptation.py, tests/test_shape_adapt_tool.py)
```

This is the synthesis surface — every file the assembled feature touches.

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior feature-review report first. Surface findings
that recurred — unresolved issues carry forward and count against PASS. **The
assembled feature has CHANGED since your last cycle** — it was re-decomposed and
rebuilt to address your findings. Re-read the current branch diff and changed
files from disk NOW; do not rely on your cached prior-cycle assessment or re-emit
your prior verdict. Evaluate the current synthesis on disk, not the one you remember.

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your feature-review with the
architecture it describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- Three checks only: integration coherence, holistic spec satisfaction,
  cracks. Per-slice quality issues belong to slice-review (already done).
- Run the spec's test command at least once via `Bash` as the feature-level
  non-regression check.
- On cycle ≥ 1: compare findings against the prior report.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <feature-review-report-path>`
