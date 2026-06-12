# Coordinator ground-truth for slice-review (task dashboard-shape-state-route)

## Test results (coordinator-run)
- `uv run pytest tests/test_shape_dashboard.py` → **24 passed, exit 0**
- Implementor reported full suite: **1408 passed, 34 skipped, 1 xfailed** (flakes did not fire).

## git diff --stat HEAD
```
 claude_crew/ui_server.py | 117 +++++++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 117 insertions(+)
```

## Full diff of claude_crew/ui_server.py (modified file)
```diff
diff --git a/claude_crew/ui_server.py b/claude_crew/ui_server.py
index 2a369f6..0314187 100644
--- a/claude_crew/ui_server.py
+++ b/claude_crew/ui_server.py
@@ -31,6 +31,7 @@ from claude_crew.broker import LEAD_ID, Broker, BrokerSnapshot
 from claude_crew.ctx_window import resolve_ctx_window
 from claude_crew.instance_registry import InstanceRegistry
 from claude_crew.redaction import REDACTION_VERSION, _TOOL_OUTPUT_BYTE_CAP
+from claude_crew.shapes import shape_to_mermaid
 from claude_crew.teammate import ToolEvent
 
 _logger = logging.getLogger(__name__)
@@ -436,6 +437,21 @@ class UIServer:
                 if self._artifact_registry is not None
                 else []
             ),
+            # M0 shape proposals: pending gates surfaced for operator approval.
+            # crew_id is load-bearing (multi-instance rule): the leader must
+            # route /shape-approval POSTs to the owning follower when
+            # crew_id != self._own_crew_id(). mermaid source is pre-rendered
+            # here so the dashboard can feed it directly into mermaid.render().
+            "shape_proposals": [
+                {
+                    "shape_id": p.shape_id,
+                    "crew_id": snapshot.crew_id,
+                    "status": p.status,
+                    "adaptation_diff": p.adaptation_diff,
+                    "mermaid": shape_to_mermaid(p.shape),
+                }
+                for p in snapshot.shape_proposals
+            ],
         }
         return instance, messages
 
@@ -803,6 +819,102 @@ class UIServer:
         except Exception:
             return JSONResponse({"error": "bad_gateway"}, status_code=502)
 
+    async def _handle_shape_approval(self, request: Request) -> JSONResponse:
+        """POST /shape-approval/{crew_id}/{shape_id} — resolve a pending proposal.
+
+        Multi-instance aware: routes locally when crew_id == own crew, else
+        proxies leader→follower via _proxy_shape_approval (mirrors _handle_artifact).
+
+        Body: {"decision": "approve"|"decline"}
+
+        Returns:
+            200  {ok, shape_id, status}    — resolved (or proxied successfully)
+            400  {error: "invalid_param"}  — bad path param
+            400  {error: "invalid_decision"} — decision not approve/decline
+            400  {error: "bad_request"}    — malformed JSON body
+            404  {error: "not_found"}      — unknown shape_id / crew_id not in registry
+            500  {error: "internal_error"} — unexpected exception
+            502  {error: "bad_gateway"}    — proxy to follower failed
+        """
+        try:
+            crew_id = request.path_params["crew_id"]
+            shape_id = request.path_params["shape_id"]
+
+            for name, value in (("crew_id", crew_id), ("shape_id", shape_id)):
+                if not _PATH_PARAM_RE.match(value):
+                    return JSONResponse(
+                        {"error": "invalid_param", "param": name},
+                        status_code=400,
+                    )
+
+            try:
+                body = await request.json()
+            except Exception:
+                return JSONResponse(
+                    {"error": "bad_request", "message": "invalid JSON body"},
+                    status_code=400,
+                )
+
+            decision = body.get("decision")
+            if decision not in ("approve", "decline"):
+                return JSONResponse(
+                    {
+                        "error": "invalid_decision",
+                        "message": "decision must be 'approve' or 'decline'",
+                    },
+                    status_code=400,
+                )
+
+            if crew_id == self._own_crew_id():
+                # Local broker — resolve directly.
+                proposal = self._broker.get_proposal(shape_id)
+                if proposal is None:
+                    return JSONResponse({"error": "not_found"}, status_code=404)
+                resolved = await self._broker.resolve_proposal(shape_id, decision)
+                return JSONResponse(
+                    {"ok": True, "shape_id": shape_id, "status": resolved.status}
+                )
+
+            # Remote crew — proxy to the owning instance.
+            return await self._proxy_shape_approval(crew_id, shape_id, decision)
+        except Exception:
+            _logger.exception("shape-approval handler error")
+            return JSONResponse({"error": "internal_error"}, status_code=500)
+
+    async def _proxy_shape_approval(
+        self, crew_id: str, shape_id: str, decision: str
+    ) -> JSONResponse:
+        """Proxy a shape-approval POST to the instance that owns crew_id.
+
+        Mirror of _proxy_artifact. The follower receives crew_id == its own
+        broker's crew and serves locally (no re-proxy loop). 404 when unknown /
+        not in registry; 502 when registered but unreachable.
+        """
+        if self._registry is None:
+            return JSONResponse({"error": "not_found"}, status_code=404)
+        target = next(
+            (e for e in self._registry.read_all() if e.get("crew_id") == crew_id),
+            None,
+        )
+        if target is None:
+            return JSONResponse({"error": "not_found"}, status_code=404)
+        port = target.get("port")
+        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
+            return JSONResponse({"error": "not_found"}, status_code=404)
+        url = f"http://127.0.0.1:{port}/shape-approval/{crew_id}/{shape_id}"
+        try:
+            resp = await self._http_client.post(url, json={"decision": decision})
+        except Exception:
+            _logger.warning(
+                "shape-approval proxy to crew %s (port %s) failed",
+                crew_id, port, exc_info=True,
+            )
+            return JSONResponse({"error": "bad_gateway"}, status_code=502)
+        try:
+            return JSONResponse(resp.json(), status_code=resp.status_code)
+        except Exception:
+            return JSONResponse({"error": "bad_gateway"}, status_code=502)
+
     def _make_app(self) -> Starlette:
         return Starlette(routes=[
             Route("/", self._handle_root),
@@ -810,6 +922,11 @@ class UIServer:
             Route("/wait-messages", self._handle_wait_messages),
             Route("/tool-output/{crew_id}/{teammate_id}/{tool_use_id}", self._handle_tool_output),
             Route("/artifact/{crew_id}/{artifact_id}", self._handle_artifact),
+            Route(
+                "/shape-approval/{crew_id}/{shape_id}",
+                self._handle_shape_approval,
+                methods=["POST"],
+            ),
             WebSocketRoute("/ws", self._handle_ws),
         ])
 
```
