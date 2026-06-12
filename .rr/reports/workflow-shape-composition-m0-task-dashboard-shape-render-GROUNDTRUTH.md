# Coordinator ground-truth for slice-review (task dashboard-shape-render)

## Test results (coordinator-run)
- `uv run pytest tests/test_shape_render.py` → see exit above (coordinator re-ran; 3 Playwright tests)
- Implementor: full suite = only the 2 pre-existing test_shutdown_signals baseline flakes, no regressions. Chromium installed + works.

## git diff --stat HEAD
```
claude_crew/ui/dashboard.html | 126 ++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 126 insertions(+)
```

## Full diff of claude_crew/ui/dashboard.html
```diff
diff --git a/claude_crew/ui/dashboard.html b/claude_crew/ui/dashboard.html
index eb64d7d..025ffc2 100644
--- a/claude_crew/ui/dashboard.html
+++ b/claude_crew/ui/dashboard.html
@@ -2204,6 +2204,131 @@
       );
     }
 
+    // --- Shape Gate Panel ---
+    // ShapeProposalCard: renders one pending proposal as a mermaid DAG with
+    // Approve/Decline controls. Reuses the existing renderMermaidBlocks pipeline
+    // (wraps the mermaid source in pre > code.language-mermaid so the function
+    // finds it via its DOM walk, then calls it via a ref callback after mount).
+    function ShapeProposalCard({ proposal }) {
+      const [busy, setBusy] = React.useState(false);
+      const [resolved, setResolved] = React.useState(false);
+
+      // Ref callback: fires once after mount when el is non-null. By then, the
+      // pre > code.language-mermaid child is already in the DOM, so
+      // renderMermaidBlocks can find and replace it with the rendered SVG.
+      const mermaidRef = React.useCallback((el) => {
+        if (el) renderMermaidBlocks(el);
+      }, []);  // proposal.shape_id is stable per card; no dep needed
+
+      if (resolved) return null;
+
+      const decide = (decision) => {
+        if (busy) return;
+        setBusy(true);
+        fetch(`/shape-approval/${proposal.crew_id}/${proposal.shape_id}`, {
+          method: 'POST',
+          headers: { 'Content-Type': 'application/json' },
+          body: JSON.stringify({ decision }),
+        })
+          .then(r => r.json())
+          .then(() => setResolved(true))
+          .catch(() => setBusy(false));
+      };
+
+      return (
+        <div
+          className="shape-proposal-card"
+          data-shape-id={proposal.shape_id}
+          style={{
+            display: 'flex', flexDirection: 'row', alignItems: 'flex-start', gap: 16,
+            padding: '12px 16px',
+            background: 'var(--bg-1)',
+            borderRadius: 6,
+            border: '1px solid var(--line)',
+          }}
+        >
+          {/* Mermaid DAG — rendered via the existing renderMermaidBlocks pipeline */}
+          <div
+            className="shape-proposal-diagram"
+            ref={mermaidRef}
+            style={{ flex: '0 0 auto', minWidth: 180, maxWidth: 420 }}
+          >
+            <pre style={{ margin: 0 }}>
+              <code className="language-mermaid">{proposal.mermaid}</code>
+            </pre>
+          </div>
+          {/* Metadata + controls */}
+          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'center' }}>
+            <div style={{ fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>
+              shape_id: {proposal.shape_id}
+            </div>
+            {proposal.adaptation_diff && (
+              <div style={{ fontSize: 11, color: 'var(--fg-2)' }}>
+                adaptation: {proposal.adaptation_diff}
+              </div>
+            )}
+            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
+              <button
+                className="shape-approve-btn"
+                onClick={() => decide('approve')}
+                disabled={busy}
+                style={{
+                  padding: '4px 14px', fontSize: 12, fontWeight: 600,
+                  background: busy ? 'var(--bg-2)' : '#166534',
+                  color: '#f0fdf4', border: 'none', borderRadius: 4,
+                  cursor: busy ? 'not-allowed' : 'pointer',
+                }}
+              >
+                Approve
+              </button>
+              <button
+                className="shape-decline-btn"
+                onClick={() => decide('decline')}
+                disabled={busy}
+                style={{
+                  padding: '4px 14px', fontSize: 12, fontWeight: 600,
+                  background: busy ? 'var(--bg-2)' : '#991b1b',
+                  color: '#fff1f2', border: 'none', borderRadius: 4,
+                  cursor: busy ? 'not-allowed' : 'pointer',
+                }}
+              >
+                Decline
+              </button>
+            </div>
+          </div>
+        </div>
+      );
+    }
+
+    // ShapeGatePanel: shows all pending proposals for the active instance.
+    // Hidden when there are no pending proposals (returns null).
+    function ShapeGatePanel({ proposals }) {
+      const pending = (proposals || []).filter(p => p.status === 'pending');
+      if (!pending.length) return null;
+
+      return (
+        <div
+          className="shape-gate-panel"
+          style={{
+            padding: '10px 16px',
+            background: 'var(--bg-0)',
+            borderBottom: '1px solid var(--line)',
+            display: 'flex', flexDirection: 'column', gap: 10,
+          }}
+        >
+          <div style={{
+            fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
+            letterSpacing: '0.07em', color: 'var(--fg-2)',
+          }}>
+            Shape Gate — {pending.length} pending proposal{pending.length !== 1 ? 's' : ''}
+          </div>
+          {pending.map(p => (
+            <ShapeProposalCard key={p.shape_id} proposal={p} />
+          ))}
+        </div>
+      );
+    }
+
     // --- MissionControlLayout ---
     function MissionControlLayout({ data, connected, perfAtArrival }) {
       const { instances, transcripts } = data;
@@ -2324,6 +2449,7 @@
             ) : null}
           />
           <InstanceStrip instances={instances} activeId={activeId} onSelect={setActiveId} readState={readState}/>
+          <ShapeGatePanel proposals={cli.shape_proposals || []} />
           <div style={{ flex: 1, display: "grid", gridTemplateColumns: "320px minmax(0, 1fr)", minHeight: 0 }}>
             <RosterRail
               liveAgents={liveAgents}
```
