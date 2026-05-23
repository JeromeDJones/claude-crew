# Crew Artifact Viewer — UX Proposal (v2, build-ready)

**Author**: ui-designer (working with Kael)
**Status**: **build-ready** — Kael's four open-question rulings + refinements A/B/C folded in. Pending only Jerome's product sign-off.
**Scope**: design recommendation for the `surface_document` feature — how a
coordinator-pushed markdown artifact lands in Mission Control without
violating the "human stays central" principle.

## v2 changelog (vs. v1)

- Open questions resolved: drawer = right/overlay; tray = per-active-instance + InstanceStrip dots; `Open file ↗` **stripped from v1** (browser-triggered `xdg-open` is a real escalation vector — copy-raw only); phasing v1.0 drawer+tray+badge, v1.1 toast.
- **Refinement A (snapshot semantics):** registry stores the bytes captured at `surface_document` call time, not a re-read of the path. `/artifact/...` never touches the filesystem. Immutable + TOCTOU-safe.
- **Refinement B (OverlayPanel migration gated):** `OverlayPanel` + `ArtifactDrawer` ship first; the `ToolOutputModal` migration onto `OverlayPanel` is the **final** slice step, gated by the existing tool-output tests including `test_e2e_multi_instance`. Clean defer-to-fast-follow cut point if it gets hairy.
- **Refinement C (registry discipline):** the registry contains **only** docs the coordinator explicitly surfaced via `surface_document`. Never an auto-scan of `.rr/` or any directory. That is the moat line vs. passive-browse.

---

## Guiding stance

The MOAT is **coordinator-in-the-loop observability**. A pushed artifact must:

- **Announce itself** without yanking the operator out of whatever stream
  they are watching.
- **Stay findable** if the announcement is missed (the operator might be in
  another tab, scrolling a different agent, on a phone call).
- **Read like a document** when opened — not a chat blob.
- **Not become a parallel channel** that lets agents talk past the operator.
  The viewer is one-way; the operator reads, decides, and steers the lead.

That stance drives every call below.

---

## Recommendations

### Q1 — Discoverability / attention signal → **Persistent "Artifacts" tray in the top bar (v1.0) + non-blocking toast (v1.1)**

**Decision.** Add an **Artifacts pill** to `MCTopBar`, scoped to the active
instance: an unobtrusive `📄 Artifacts · N` chip with an unread-count badge.

**v1.0 (this slice).** When the coordinator surfaces a doc:

1. The pill's badge increments (`N → N+1`), and the pill briefly flashes
   (200ms accent border, no layout shift).
2. Clicking the pill opens a **tray dropdown** listing all surfaced
   artifacts in this crew session (newest first), with read/unread state.
3. Inactive-instance unread is signaled by a status dot on the corresponding
   InstanceStrip row (reusing the existing `.dot.active` token).

**v1.1 (fast-follow).** A **non-blocking toast** slides in at the top-right
on each new arrival (~340px wide, 5–6s auto-dismiss, click-to-open,
dismissible). Toast shows: artifact title, surfacing teammate, and "Open" /
"Later" affordances. Toast disappears; the badge persists until the
artifact is opened. The tray remains the canonical inbox; the toast is the
courtesy. **Do not** ship the toast without the tray under it.

**Why this shape.** Toast alone is too ephemeral (missed = lost). A modal
that auto-opens steals focus — violates the principle. A purely passive list
defeats the whole point of "push." Pill+badge is a known idiom (Gmail,
Slack), it survives missed announcements, and it does not interrupt.

**Tradeoff.** One more piece of chrome in the top bar. Acceptable — the
top bar today is sparse, and the pill collapses to a single dot when there
are no artifacts so the chrome cost is near-zero in the empty state.

**Multi-instance note.** The pill counts artifacts for the **active**
instance only. The InstanceStrip rows should also carry a small dot when an
inactive instance has unread artifacts, so the operator does not lose
signal when watching crew A while crew B publishes something.

---

### Q2 — Viewer surface → **Right-side drawer, full height, ~720px wide**

**Decision.** A **right-side drawer**, not a centered modal. Slides in
from the right edge, full viewport height, width `min(720px, 55vw)`,
backed by a translucent backdrop that **dims but does not hide** the agent
stream behind it. Drawer body scrolls; header is sticky.

**Why a drawer, not a modal.**
- Long-form reading needs vertical room. A centered modal at 640×85vh (the
  current `tm-detail-panel`) feels claustrophobic for a multi-screen spec.
- A drawer keeps the agent stream **visible** on the left — the operator
  can read the spec while continuing to monitor agents. That alignment with
  the "stay in the loop" principle is the deciding factor.
- A full-screen takeover would amplify the worst failure mode: the
  operator immersed in agent-authored prose, the live crew forgotten.

**Why not just a wider modal.** A 90vw modal IS basically a drawer minus
the connection to a clear edge. The drawer's anchored edge communicates
"this is a side surface, the main work is still there" — a modal claims
the screen.

**Geometry.**
- Width: `min(720px, 55vw)`.
- Backdrop: existing `tm-detail-backdrop` opacity (`0.45`) feels right;
  agent stream remains legible underneath.
- Animation: 180ms ease-out slide on open, ease-in on close. No bounce.
- Close: `Esc`, backdrop click, explicit `✕` button — match
  `ToolOutputModal` exactly.

---

### Q3 — Markdown rendering → **`marked` + `DOMPurify` via unpkg, SRI-pinned**

**Decision.** Add two CDN scripts alongside the existing React/Babel block:

- `marked@4.3.0` (Markdown → HTML; stable, no runtime deps, ~30KB).
- `DOMPurify@3.0.11` (HTML → safe HTML; battle-tested, default-deny,
  ~22KB).

Pipeline (in the React component):

```js
const rawHtml = marked.parse(markdown, { gfm: true, breaks: false });
const cleanHtml = DOMPurify.sanitize(rawHtml, {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form'],
  FORBID_ATTR: ['style', 'onerror', 'onload'],
  ADD_ATTR: ['target', 'rel'], // we'll set target=_blank rel=noopener on links
});
return <div className="artifact-md" dangerouslySetInnerHTML={{ __html: cleanHtml }} />;
```

**Why this is OK despite the existing "never dangerouslySetInnerHTML" rule.**
That rule (in the `ToolOutputModal` security comment) is correct for raw
attacker-influenced text — it must be JSX children so React auto-escapes.
DOMPurify exists **precisely** to convert "attacker-influenced HTML" into
"safe HTML for `innerHTML`." This is the one place the dashboard
consciously and locally relaxes the rule, surrounded by:

- A loud comment explaining the pipeline,
- A regression test that feeds `<img src=x onerror=alert(1)>` through and
  asserts the rendered DOM has no `onerror`,
- Strict CDN integrity hashes (no runtime swap),
- A post-sanitize link-hardening pass to add `target="_blank"
  rel="noopener noreferrer"`.

**Why not a custom renderer.** Reinventing markdown parsing is a guaranteed
XSS surface. Every shortcut (regex-based, hand-rolled token walker) gets
something wrong on the long tail (autolinks, html-in-md, code fences inside
blockquotes). `marked` + `DOMPurify` is the boring, correct path.

**Why not `react-markdown`.** It needs a build step or a much heavier UMD
chain. Babel-standalone + JSX is enough constraint; do not add JSX
plugins.

**Script tag (proposed):**

```html
<script src="https://unpkg.com/marked@4.3.0/marked.min.js"
        integrity="sha384-<TBD-pin-before-merge>"
        crossorigin="anonymous"></script>
<script src="https://unpkg.com/dompurify@3.0.11/dist/purify.min.js"
        integrity="sha384-<TBD-pin-before-merge>"
        crossorigin="anonymous"></script>
```

SRI hashes are computed at lockdown time, never `null` integrity.

---

### Q4 — Artifact chain → **Tray is the list; drawer has a prev/next pager**

**Decision.** Two surfaces, complementary:

- **Tray (the inbox).** Drop-down list from the top-bar pill. Shows every
  artifact surfaced in the current crew session, newest first, with title,
  surfacing teammate, timestamp, read/unread state. Click → opens drawer.
- **Drawer pager.** When the drawer is open, a small `‹ prev · 2 of 5 · next ›`
  control sits in the drawer header. The chain is the crew session's
  artifact list in **chronological** order (so prev/next has the natural
  semantics of "what came before this in the work product").

The tray is the discoverability/navigation surface. The pager is the
in-context "I'm reading the plan-review, let me jump back to the spec it
reviewed" affordance. No tabbed sidebar inside the drawer — that would
fight the reading space.

**Grouping.** v1 ships flat chronological. If repo-react conventions
stabilize (spec → plan-review → build-report), a future revision can
introduce phase grouping in the tray (sectioned: `Specs / Reviews /
Reports`). Don't pre-build that taxonomy.

---

### Q5 — Reuse vs. divergence → **Extract `OverlayPanel` primitive (gated migration); keep `/tool-output` and `/artifact` as separate endpoints**

**Decision.**

- **Client-side.** Extract a small `OverlayPanel` primitive that owns:
  portal-to-`document.body`, backdrop, `Esc` handler, click-outside-to-
  close, `✕` button, focus management. It takes a `mode: "modal" | "drawer"`
  prop and a `size` prop. **Both** `ToolOutputModal` (post-migration) and
  the new `ArtifactDrawer` compose `OverlayPanel`. One overlay infra, two
  surfaces. This satisfies "no two modal systems."

  **Build ordering (gated migration — non-negotiable).** `ToolOutputModal`
  is shipped, has the multi-instance leader→follower proxy, and is
  covered by `test_e2e_multi_instance`. Do **not** refactor it first.
  Sequence:
  1. Build `OverlayPanel` standalone (no callers).
  2. Build `ArtifactDrawer` composing `OverlayPanel` end-to-end. This is
     the slice's user-visible deliverable.
  3. **Final step:** migrate `ToolOutputModal` onto `OverlayPanel`. Gate:
     the existing tool-output suite **including `test_e2e_multi_instance`**
     stays green. If that migration gets hairy, **stop and defer** —
     `ToolOutputModal` keeps its current implementation as a fast-follow
     item, `ArtifactDrawer` ships on `OverlayPanel`, and the
     "two-modal-systems" smell is a temporary, tracked debt rather than
     destabilized shipped code.

- **Server-side.** Keep `/tool-output/<crew>/<from>/<tool_use_id>` and
  `/artifact/<crew>/<artifact_id>` as **distinct endpoints**. They have
  different storage semantics (rolling buffer keyed by tool-use id vs.
  registered push keyed by opaque artifact id), different lifetimes
  (rolling vs. session-scoped registry), and different content (raw blob
  vs. **snapshotted** markdown intended for rendering). Forcing them into
  one `/artifact` surface would muddy both. The right shared layer is the
  client overlay, not the transport.

### Server-side: storage semantics (refinement A — snapshot, not lazy re-read)

`surface_document(path, title)` is a trusted-lead-only MCP tool. At call
time the broker:

1. Reads `path` **once**.
2. Validates size: `len(bytes) ≤ 1 MiB` — else returns a clear
   MCP-tool-level error (`ArtifactTooLarge`, with the actual size in the
   message). No truncation; the lead chooses how to react.
3. Validates UTF-8 decodability — else `ArtifactNotText`.
4. Mints an opaque `artifact_id` (UUID4 hex; never derived from `path`).
5. Stores `{artifact_id, crew_id, title, surfacing_teammate, timestamp_utc,
   body: <bytes-as-text>}` in the per-crew registry.
6. Returns `artifact_id` to the lead.

`GET /artifact/<crew_id>/<artifact_id>` serves the **stored snapshot
bytes**. It never re-reads the filesystem. The original `path` is captured
into the registry record only as a label (displayed under the title in the
drawer header), not as a fetch key — there is no code path that turns the
label back into a filesystem read.

**Why snapshot, not lazy re-read.**
- *Immutable.* "This is the version I surfaced" — the operator sees
  exactly what the coordinator pushed, even if the file changes after.
- *TOCTOU-safe.* No window where the path could be swapped, moved, or
  symlinked between push and view.
- *Bounded cost.* Memory is already bounded by `≤ 50 artifacts × ≤ 1 MiB`
  per crew = 50 MiB worst case. Acceptable for a localhost orchestrator.

**Living-doc lazy-re-read is a deliberate v2 option, not a v1 gap.** If
operators later ask "I want the spec to update as the planner edits it,"
we add a `surface_document(path, title, mode="live")` variant that opts
into re-read on fetch. v1 is snapshot-only and that is the documented
contract.

### Server-side: registry discipline (refinement C — push-only, never auto-scan)

The artifact registry contains **only** documents the coordinator
explicitly surfaced via the `surface_document` MCP tool. There is no
auto-scan of `.rr/`, `docs/`, or any other directory; no filesystem
watcher; no implicit registration. Every entry is the result of one
intentional coordinator call.

This is the line between this feature (active push, coordinator-in-the-loop
— the moat) and the deprioritized passive-browse variant. If a future
contributor proposes "let's just list every markdown file in the project
in the tray," that is a different feature with a different security and
attention model, and it must come back through design review.

**The smell to avoid.** Two backdrop implementations, two `Esc` handlers,
two close buttons with subtly different hit areas, two ways to fail at
focus return. That is the "two modal systems" smell, and `OverlayPanel`
(with the gated migration above) eliminates it.

---

## Mockups

### (a) Attention / discoverability state

```
┌────────────────────────────────────────────────────────────────────────────┐
│  claude-crew · Mission Control      [● connected]   📄 Artifacts · 3  ⚙   │ ← top bar; pill w/ badge
├────────────────────────────────────────────────────────────────────────────┤
│ ▣ crew-alpha   ▢ crew-beta•   ▢ crew-gamma                                 │ ← InstanceStrip; • = unread on inactive
├──────────────┬─────────────────────────────────────────┬───────────────────┤
│              │                                         │ ┌───────────────┐ │
│  ROSTER      │  AGENT STREAMS                          │ │ 📄 New        │ │ ← toast (top-right,
│              │                                         │ │ spec/repo-    │ │   non-blocking, 6s)
│  ▣ planner   │   planner ▸ Write   "spec.md" 12kb      │ │ react-v3.md   │ │
│  ▣ reviewer  │   reviewer ▸ Read   "spec.md"           │ │ from planner  │ │
│  ▢ builder   │                                         │ │ [Open] [Later]│ │
│              │                                         │ └───────────────┘ │
│              │                                         │                   │
└──────────────┴─────────────────────────────────────────┴───────────────────┘

Click pill → opens dropdown:

       📄 Artifacts · 3 ▼
       ┌────────────────────────────────────────────────────┐
       │  • spec/repo-react-v3.md            planner · 09:14│ ← unread (bold + dot)
       │    plan-review/repo-react-v3.md     reviewer · 09:31│
       │  • build-report/2026-05-23.md       builder · 09:48│ ← unread
       │  ─────────────────────────────────────────────────  │
       │   Clear read state                                  │
       └────────────────────────────────────────────────────┘
```

**Read states:** unread = bold + leading `•` dot in accent color. Opening
the drawer marks read. The badge count = unread count, not total.

---

### (b) Open viewer reading a multi-screen spec

```
┌────────────────────────────────────────────────────────────────────────────┐
│  claude-crew · Mission Control      [● connected]   📄 Artifacts · 2  ⚙   │
├────────────────────────────────────────────────────────────────────────────┤
│ ▣ crew-alpha   ▢ crew-beta   ▢ crew-gamma                                  │
├──────────────┬──────────────────────────┬──────────────────────────────────┤
│              │                          │  ╔════════════════════════════╗  │
│  ROSTER      │  AGENT STREAMS           │  ║ ‹ prev   2 of 3   next ›  ✕║  │ ← sticky drawer header
│              │   (still visible,        │  ╟────────────────────────────╢  │
│  ▣ planner   │    dimmed ~30%)          │  ║ spec/repo-react-v3.md      ║  │
│  ▣ reviewer  │                          │  ║ planner · 09:14 · 12.3 KB  ║  │ ← meta line
│  ▢ builder   │   reviewer ▸ Read ...    │  ╟────────────────────────────╢  │
│              │   builder  ▸ Bash ...    │  ║                            ║  │
│              │                          │  ║ # Repo-React v3 Spec       ║  │ ← rendered md
│              │                          │  ║                            ║  │
│              │                          │  ║ ## Goals                   ║  │
│              │                          │  ║ - Faster cycle time        ║  │
│              │                          │  ║ - Fewer manual reviews     ║  │
│              │                          │  ║                            ║  │
│              │                          │  ║ ## Data contract           ║  │
│              │                          │  ║ ```json                    ║  │ ← fenced code,
│              │                          │  ║ { "id": "...", ... }       ║  │   mono font, bg-2
│              │                          │  ║ ```                        ║  │
│              │                          │  ║                            ║  │
│              │                          │  ║ ▼  (scroll)                ║  │
│              │                          │  ╟────────────────────────────╢  │
│              │                          │  ║ [Copy raw]                 ║  │ ← sticky footer
│              │                          │  ╚════════════════════════════╝  │
└──────────────┴──────────────────────────┴──────────────────────────────────┘
                                              ↑
                              Drawer: width min(720px, 55vw),
                              full height, slides from right.
                              Backdrop dims the streams behind
                              but keeps them legible (operator
                              still in the loop).
```

**Pager semantics:** `prev` / `next` walk chronologically through the
crew's artifact list. Disabled at the ends. Position label is `N of M`.

**No `Open file ↗` action in v1.** A browser-triggered server-side
`xdg-open` (or any OS-default-handler invocation) reopens the exact
attack surface the opaque-id design was built to close, and is a real
escalation vector on a localhost service with no auth. **Not specced,
not implemented.** Copy-raw is the only export affordance in v1. If a
future operator workflow genuinely needs "jump to the source file in my
editor," that comes back through design review as a separate proposal
with its own threat model.

---

## What I'd reuse vs. build new

| Concern                       | Source                                    | Plan                                                    |
| ----------------------------- | ----------------------------------------- | ------------------------------------------------------- |
| Backdrop + portal             | `tm-detail-backdrop` class                | Reuse class; `OverlayPanel` primitive wraps the pattern |
| Panel chrome (modal variant)  | `tm-detail-panel`                         | Reuse for ToolOutputModal; **new** `.artifact-drawer` class for drawer variant |
| Esc / click-outside / close   | `ToolOutputModal` `useEffect` block       | Extract into `OverlayPanel`                             |
| Fetch state machine           | `ToolOutputModal` (`loading/ok/not_found/error`) | Reuse pattern verbatim for `/artifact/<crew>/<id>` |
| Copy button                   | `ToolOutputModal` clipboard helper        | Reuse verbatim                                          |
| Chip / pill styling           | `.chip`                                   | Reuse for Artifacts pill in MCTopBar                    |
| Status dot                    | `.dot.active`                             | Reuse on InstanceStrip rows to show "this crew has unread" |
| Monospace                     | `.mono` + `--font-mono`                   | Reuse inside fenced code blocks in rendered markdown    |
| State (modal selection)       | `openToolModal` pattern in MissionControlLayout | Mirror: `openArtifact = {crewId, artifactId} | null` |
| Toast                         | none                                      | **New** lightweight component (~40 lines), portal'd, queue of 1 |
| Tray dropdown                 | none                                      | **New** popover anchored to the pill; positioned with absolute, click-outside to close |
| Markdown rendering            | none                                      | **New**: `marked@4.3.0` + `DOMPurify@3.0.11` CDN scripts with SRI pins (pin v4 of `marked` — v5+ changed the API) |

CSS additions are small and ride on existing CSS variables (`--bg-1`,
`--line`, `--fg-3`, etc.) — no new color tokens needed.

### Build order (gated; do not reorder)

1. **Server:** `surface_document` MCP tool, per-crew registry (capped
   50/1 MiB, snapshot semantics), `GET /artifact/<crew>/<id>` endpoint,
   leader→follower proxy mirroring `_proxy_tool_output`, and the
   multi-instance e2e test. **No client work yet.** This is the security
   boundary — get it right first.
2. **Client (new surfaces, no refactor):** `OverlayPanel` primitive
   (standalone, no consumers yet), `ArtifactDrawer` composing it, the
   `marked`+`DOMPurify` sanitize helper with its XSS regression test,
   the Artifacts pill in `MCTopBar`, the tray dropdown, the
   InstanceStrip unread dots, and the `openArtifact` state in
   `MissionControlLayout`. At the end of this step the feature is
   fully usable; `ToolOutputModal` is untouched.
3. **Final, gated:** migrate `ToolOutputModal` onto `OverlayPanel`. Gate
   = the existing tool-output suite **including
   `test_e2e_multi_instance`** stays green. **If it gets hairy, stop
   and defer** — file a fast-follow, keep `ToolOutputModal` on its
   current implementation, and ship. The slice is still a net win.
4. **v1.1 (separate slice):** toast on new arrival. Do not bolt this on
   late in v1.0.

---

## Risks & smells

1. **Multi-instance lazy-fetch trap.** Per `CLAUDE.md`, any new dashboard
   endpoint that serves per-instance data must carry `crew_id` on its row
   record and the leader must **proxy** to the owning instance when the
   `crew_id` is non-local. The Artifacts tray must:
   - Tag every artifact list entry with its `crew_id` in `/api/state`
     (just like `tool_use_id`/`crew_id` are tagged on tool records).
   - Make `/artifact/<crew_id>/<artifact_id>` route via the same
     leader→follower proxy pattern as `_proxy_tool_output`.
   - Have a **multi-instance e2e test**, not just a single-instance one.
   This is the single biggest invisible failure mode. Flag it loud on the
   build slice.

2. **DOMPurify is good, but configuration matters.** The default profile
   already blocks `<script>`, `<iframe>`, inline event handlers, and
   `javascript:` URIs — but a permissive `ADD_TAGS` later (e.g., to allow
   `<details>` for collapsible sections) could regress this. Wrap the
   sanitize call in a `sanitizeArtifact(md)` helper with a single config
   constant and a regression test asserting common XSS payloads are
   defanged. Do not let the config drift across files.

3. **Artifact storage lifetime / unbounded growth.** The artifact registry
   should be **per-crew, capped** (e.g., 50 artifacts, evict oldest with a
   tombstone so stale opaque ids return `404` cleanly — reuse the
   `not_found` branch of the fetch state machine). Server-side size cap on
   the markdown body (suggest 1 MiB) — reject with a clear MCP-tool error.

4. **Toast-as-only-channel anti-pattern.** If the toast becomes the
   primary discoverability surface, missed toasts = invisible work. The
   tray must always be the canonical inbox; the toast is a courtesy. Code
   review should reject any feature that emits a toast without also
   appending to the tray.

5. **Read-state persistence scope.** v1: read state lives in client memory
   (resets on dashboard reload). Cheap and acceptable for localhost. If
   operators complain about losing read state after a refresh, promote to
   server-side per-crew read-state — but that's v2.

6. **Link handling in rendered markdown.** External `http(s)://` links
   should open in a new tab with `target="_blank" rel="noopener noreferrer"`.
   Relative paths (e.g., `[foo](./other.md)`) are meaningless in this
   context — render as plain text or as a non-clickable token. Do **not**
   try to resolve them server-side; that re-opens the path-traversal door
   the opaque-id design was meant to close.

7. **Drawer + tray + toast = three new surfaces — phased.** Confirmed
   ordering:
   - **v1.0**: drawer + tray pill + badge + InstanceStrip unread dots
     (no toast). Operator-pulls via the pill is enough to validate the
     design.
   - **v1.1**: toast on new arrival, in a separate slice.
   - The tray remains the canonical inbox in both phases; the toast (when
     it lands) is courtesy-only and must always co-emit a tray entry.

8. **Snapshot ≠ live view.** Refinement A means an artifact is the version
   surfaced at push time. If the planner edits `spec.md` after surfacing,
   the operator still sees the old version until the lead calls
   `surface_document` again. This is intentional ("the version the
   coordinator chose to push") but is a UX expectation worth flagging in
   the operator-facing release notes. Living-doc mode is a v2 option.

9. **Push-only registry.** Refinement C: any future PR that adds an
   auto-scan, directory-watch, or implicit registration path is a
   different feature and must come back through design review. The MOAT
   is "the coordinator chose to surface this." Drift here erodes the
   product premise.

---

## Resolved (was: open questions for Kael)

All v1 open questions are now decided. Recorded here as a changelog so the
build slice has the full reasoning chain in one place:

1. **Drawer side → RIGHT, overlay (not push).** Pushing reflows the agent
   columns mid-read; overlay preserves layout stability while keeping the
   streams legible underneath.
2. **Tray scope → per-active-instance + InstanceStrip unread dots.** Flat
   global tray would mix work products from independent crews; the dots
   carry the cross-crew signal without forcing global aggregation.
3. **`Open file ↗` → STRIPPED from v1.** Browser-triggered OS-handler
   invocation on a no-auth localhost service is a real escalation
   vector and reopens the attack surface the opaque-id design was built
   to close. Not specced. Copy-raw only.
4. **Phasing → v1.0 drawer+tray+badge, v1.1 toast.** Confirmed.
