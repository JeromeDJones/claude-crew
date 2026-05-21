# Spec: click-to-view-tool-output

## Problem

Coordinators driving a teammate crew through the Mission Control dashboard
currently see only redacted, 256-byte `args_summary` and `error_summary` lines
for each tool invocation — e.g. `Read (ok, 0.04s)`. The actual tool output
(file contents, shell stdout, web responses) is observed by the PostToolUse
hook in `claude_crew/sdk_teammate.py` and immediately discarded. That output
is the highest-bandwidth observability signal the operator has into what a
teammate just did; without it the coordinator cannot tell whether `Read`
returned the expected file or an empty stub, whether `Bash` produced a real
result or an error, etc. The slice closes this blind spot: clicking a
tool-event row in the dashboard opens a modal showing the (redacted,
truncated) output body that the hook saw.

## Architecture Overview

Four touchpoints, in data-flow order:

1. **Capture** — In `sdk_teammate.py::_on_post_common`, extract the tool's
   raw response from the PostToolUse hook payload (`inp["tool_response"]`,
   defensive against missing/non-string), pass it through a new output
   redactor + 4096-byte UTF-8-safe cap, and store the result in a new
   in-memory store on the Teammate base.
2. **Store** — New `claude_crew/teammate.py` field `_tool_outputs:
   collections.OrderedDict[str, str]` (FIFO, `maxlen=50` enforced manually
   on insert by popping `last=False`). Keyed by `tool_use_id`. NOT added to
   the frozen `ToolEvent` dataclass; NOT included in `BrokerSnapshot`; NOT
   pushed over the WebSocket state frame. Lazy-fetch only.
3. **Redact** — New `claude_crew/redaction.py::redact_output(text: str) -> str`
   that applies a superset of the existing `REDACTION_PATTERNS_V1` plus three
   new patterns required by the idea: AWS session tokens, PEM private-key
   blocks, OpenAI-style `sk-...` keys (already covered by pattern 7 in V1 —
   verify and reuse). Failure mode: if `redact_output` itself raises,
   `_on_post_common` writes the literal sentinel
   `[REDACTION_FAILED: <exception class name>]` into the store in place of
   the raw body and emits a WARNING. Never store a raw unredacted body.
4. **Serve** — New Starlette route `GET /tool-output/{teammate_id}/{tool_use_id}`
   registered in `UiServer._make_app` alongside `/wait-messages`. Localhost
   bind is inherited from the existing `serve()` posture (127.0.0.1 / fd
   inherit). Returns `{"body": "...", "truncated": bool}` or 404 if the
   `(teammate_id, tool_use_id)` pair is not in any teammate's store.
5. **Render** — Make the existing MiniMessage tool row in
   `claude_crew/ui/dashboard.html` clickable (cursor pointer, hover state).
   Click opens a modal in the existing `ConfigDetailPanel` visual style that
   fetches the endpoint with `teammate_id` (= `msg.from`) and `tool_use_id`,
   shows the body in a `<pre>` block, and offers a copy-to-clipboard button.
   `tool_use_id` must be propagated to the rendered tool message — extend
   the merge step in `_build_state` so the `kind:"tool"` record includes
   `tool_use_id` (already on `ToolEvent`).

### Call-site survey

Single capture site (`_on_post_common`) and single render site
(`MiniMessage` tool branch). No helper proliferation — omit divergence
table.

## Data / API Contracts

```
# Teammate base (claude_crew/teammate.py)
class Teammate:
    _tool_outputs: collections.OrderedDict[str, str]  # tool_use_id -> redacted body
    _TOOL_OUTPUT_MAX_ENTRIES: ClassVar[int] = 50
    _TOOL_OUTPUT_BYTE_CAP: ClassVar[int] = 4096

    def store_tool_output(self, tool_use_id: str, body: str) -> None
    def get_tool_output(self, tool_use_id: str) -> str | None

# Redaction (claude_crew/redaction.py)
def redact_output(text: str) -> str
    # Applies REDACTION_PATTERNS_V1 plus output-specific additions:
    #   - PEM blocks: -----BEGIN [A-Z ]+ PRIVATE KEY----- ... -----END ...-----
    #   - AWS session token (40+ char base64 after "aws_session_token" keyword)
    # Then caps at _TOOL_OUTPUT_BYTE_CAP (UTF-8 safe via _cap_utf8).
    # On exception inside the function: re-raise (caller writes sentinel).

# UI server route (claude_crew/ui_server.py)
GET /tool-output/{teammate_id}/{tool_use_id}
    200 {"body": str, "truncated": bool, "redaction_version": "v1"}
    404 {"error": "not_found"}    # unknown teammate or tool_use_id evicted
    500 {"error": "internal_error"}

# Dashboard merged message record (additive field)
{ "t":..., "from":..., "to":..., "kind":"tool", "body":..., "tool_use_id": "toolu_..." }
```

## Design Decisions

- **Parallel store, not ToolEvent field** — *Rationale:* `ToolEvent` is
  frozen and rides on every `BrokerSnapshot` / WebSocket push. Inlining 4KB
  bodies would multiply state-frame size by ~100× under load. — *Carried into:*
  `Teammate._tool_outputs` field + assertion in
  `tests/test_tool_outputs.py::test_tool_event_dataclass_has_no_body_field`.

- **Bounded FIFO at 50 entries** — *Rationale:* Bounds memory at ~200KB per
  teammate; oldest events are least likely to be reviewed. — *Carried into:*
  `_TOOL_OUTPUT_MAX_ENTRIES`; AT-3.

- **4KB hard cap on stored body** — *Rationale:* Idea-specified. Aligns with
  the existing 256B `args_summary` posture (preview, not archive). —
  *Carried into:* `_TOOL_OUTPUT_BYTE_CAP`; AT-4.

- **Lazy-fetch HTTP endpoint, not WebSocket push** — *Rationale:* Tool bodies
  are sometimes-viewed, never-streamed data; pushing on every state frame
  wastes bandwidth and bloats the React tree. — *Carried into:* New route
  `/tool-output/...` and `_make_app` registration; AT-5.

- **Mirror `/wait-messages` security posture** — *Rationale:* Same trust
  boundary (localhost bind, no auth token, structured-error 500). —
  *Carried into:* Route registered in `_make_app`; no host override in
  `serve()`; AT-6.

- **Mask-in-place on secret match, sentinel on redactor crash** — *Rationale:*
  Operator wants surrounding context preserved when a secret is matched;
  fail-loud (never silently pass raw) when the redactor itself raises. —
  *Carried into:* `redact_output` returns string with `<redacted-key>` /
  `<redacted-jwt>` / etc. inline; `_on_post_common` wraps the call in a
  `try/except Exception` and writes `[REDACTION_FAILED: <ClassName>]`;
  AT-7, AT-8.

- **Reuse `REDACTION_VERSION = "v1"`** — *Rationale:* Output redaction shares
  the V1 pattern set; the additional output-only patterns are appended (not
  prepended) so V1's auditable ordering is preserved. If a future change
  diverges, bump to v2 per the documented version-bump procedure. —
  *Carried into:* `redact_output` returns response with the V1 version label.

- **Broker exposes a lookup, not direct dict access** — *Rationale:* The
  UI server already holds a `Broker` reference; expose
  `Broker.get_tool_output(teammate_id, tool_use_id)` that walks the
  teammate registry (including tombstoned teammates) and returns
  `None` when missing. Keeps the Teammate store private. — *Carried into:*
  New `Broker.get_tool_output` method; AT-5.

- **`tool_use_id` plumbed through to the rendered tool record** — *Rationale:*
  Without it the dashboard cannot construct the fetch URL. — *Carried into:*
  `_build_state` merge step in `ui_server.py`; AT-9.

## Edge Cases

- **PostToolUse with no `tool_response` key** (e.g. failure path) — skip
  storage; the modal will 404 cleanly. No log noise.
- **`tool_response` is not a string** (dict, list, bytes) — coerce via
  `json.dumps(..., default=str)` for dict/list; `bytes.decode("utf-8",
  errors="replace")` for bytes; `str(...)` fallback. Then redact+cap.
- **Output longer than 4KB** — cap to 4096 bytes via UTF-8-safe truncate
  (reuse `_cap_utf8` from `redaction.py`); endpoint returns
  `truncated: true`.
- **Tool failed (PostToolUseFailure)** — still capture if `tool_response`
  present; many tool failures still produce a body the operator wants.
- **`tool_use_id` already in the deque (duplicate Post)** — overwrite (last
  write wins, matching `_recently_closed_tool_use_ids` dedup which suppresses
  duplicates anyway).
- **Subagent (Task) tool calls** — `Task` is filtered out of the dashboard
  tool-event stream today (`_build_state` skips `ev.tool_name == "Task"`);
  storage may still happen but rows are not clickable. Acceptable.
- **Teammate tombstoned (dead) before fetch** — broker preserves dead
  teammates in registry for status queries; lookup must still succeed.
  Evicted entries (older than 50) return 404.
- **Unknown teammate_id in URL** — 404, not 500.
- **`tool_use_id` shape from URL path is path-traversal-suspicious**
  (contains `..`, `/`, NUL) — Starlette path converter excludes `/` by
  default; reject any non-`[A-Za-z0-9_\-]+` value with 400. Same for
  `teammate_id`.
- **Redactor crashes on adversarial input** (catastrophic regex backtrack,
  encoding bug) — `_on_post_common` catches and stores the
  `[REDACTION_FAILED: ...]` sentinel; never store the raw text. WARNING log.
- **Concurrent fetch + eviction** — `OrderedDict` is GIL-safe for the
  ops used (get, popitem, `__setitem__`); no explicit lock needed.
- **UI shows zero events vs. evicted event** — modal distinguishes by
  rendering "Output no longer available (evicted from rolling buffer)" on
  404 vs. the body on 200.
- **Copy-to-clipboard in non-HTTPS context** — `navigator.clipboard` requires
  secure context; localhost is treated as secure by browsers. Acceptable.

## Acceptance Tests

1. Given a teammate captures a PostToolUse with `tool_response="hello world"`,
   when `Teammate.get_tool_output(tool_use_id)` is called, then it returns
   `"hello world"` (no redaction triggered).
2. Given a teammate's PostToolUse `tool_response` contains
   `"AKIAIOSFODNN7EXAMPLE"`, `"ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`,
   `"sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"`, a JWT
   `"eyJhbGciOi...x.y.z"`, a PEM block
   `"-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"`,
   and the surrounding text `"BEFORE ... AFTER"`, when stored, then
   `get_tool_output` returns text where each secret is replaced by the
   matching `<redacted-*>` marker AND the literal substrings `"BEFORE"` and
   `"AFTER"` remain present (sad-path: secrets masked, context preserved).
3. Given 51 successive PostToolUse events on one teammate, when the 52nd is
   captured, then `_tool_outputs` contains exactly 50 entries and the
   first-captured `tool_use_id` returns `None` (FIFO eviction).
4. Given a `tool_response` of 8192 bytes, when stored, then
   `len(get_tool_output(...).encode("utf-8")) <= 4096` AND the HTTP endpoint
   reports `"truncated": true`.
5. Given a teammate captured tool output for `tool_use_id="toolu_abc"`, when
   `GET /tool-output/<teammate_id>/toolu_abc` is requested against the
   running UI server, then the response is 200 with JSON
   `{"body": "...", "truncated": false, "redaction_version": "v1"}`
   matching the stored body.
6. Given the UI server is running, when `GET /tool-output/unknown/unknown`
   is requested, then the response is 404 with JSON
   `{"error": "not_found"}`; when `GET /tool-output/bad..id/toolu_x` is
   requested, then the response is 400.
7. Given the redactor itself raises (monkeypatch `redact_output` to raise
   `RuntimeError`), when a PostToolUse fires, then `get_tool_output`
   returns the literal sentinel `"[REDACTION_FAILED: RuntimeError]"` and a
   WARNING is logged citing the teammate and tool_use_id.
8. Given the `ToolEvent` dataclass, when inspected via `dataclasses.fields`,
   then no field named `body`, `output`, `response`, or `tool_response`
   exists (regression guard: outputs never inlined onto the snapshot
   contract).
9. Given a snapshot with one completed tool event for tool_use_id
   `"toolu_abc"`, when `UiServer._build_state` produces the merged
   messages list, then the `kind:"tool"` entry contains the key
   `tool_use_id` with value `"toolu_abc"`.
10. Given a Playwright session opens the dashboard against a fixture
    backend that has one tool event with a stored body `"file contents"`,
    when the operator clicks the tool-event row, then a modal appears
    containing the literal text `"file contents"` and a button labeled
    `"Copy"`.

## Test Command

All imports used by the new tests are already in the dev dependency group
(`pytest`, `pytest-asyncio`, `pytest-playwright`, `httpx`). Playwright
browser binaries must be installed once with `uv run playwright install
chromium` before the dashboard test runs; CI scripts already do this.
Per the CLAUDE.md "validate the whole suite" rule, the validation gate runs
the full suite, not a filtered subset.

```bash
uv run playwright install chromium >/dev/null 2>&1 || true && uv run pytest
```

## Out of Scope

- Persistent on-disk JSONL of tool outputs (separate, larger feature).
- Syntax highlighting / language detection in the modal.
- Diff view across outputs.
- Any change to existing `args_summary` / `error_summary` semantics.
- Capturing tool output for `Task` (subagent) calls in the dashboard view.
- An auth token on `/tool-output` (matches the v1 `/wait-messages` posture).
- Bumping `REDACTION_VERSION` to `v2`.

## Assumptions

- **PostToolUse `inp` carries the tool body under `tool_response`.** —
  *Default:* Read with `inp.get("tool_response")`; guard against missing /
  non-string. — *Rationale:* The `claude-agent-sdk` PostToolUse hook
  passes the raw tool result on the `inp` dict; if the field is named
  differently (`tool_output`, `result`), the implementor probes both and
  prefers `tool_response`.
- **50 entries × 4KB per teammate is acceptable memory.** — *Default:* 50
  entries / 4KB cap. — *Rationale:* ~200KB per teammate, ~2MB at a 10-agent
  crew. Negligible vs. existing Broker memory.
- **A single new `redact_output` function (not a separate module) is the
  right shape.** — *Default:* Add to `claude_crew/redaction.py`. —
  *Rationale:* Cohabits with existing patterns; keeps the v1 pattern list
  the single source of truth.
- **Existing patterns 7 (`sk-ant-*`/`sk-proj-*`) already cover OpenAI-style
  `sk-...` keys for output context.** — *Default:* Verify, do not add a
  duplicate. — *Rationale:* Pattern 7 currently matches `sk-(?:ant-|proj-)?
  [A-Za-z0-9_\-]{20,}` — the `?` on the prefix means bare `sk-...` keys
  match. Implementor adds a regression AT to lock this in.
- **PEM and AWS session token patterns are output-only.** — *Default:* Add
  them inside `redact_output` *after* applying `REDACTION_PATTERNS_V1`
  rather than mutating the shared `REDACTION_PATTERNS_V1` list. —
  *Rationale:* Args from the v1 allowlist (Bash/Task/WebFetch) won't carry
  PEM blocks; adding to V1 broadens the pattern set's blast radius
  unnecessarily and conflicts with the v1-is-frozen invariant.
- **Dashboard modal styling mirrors `ConfigDetailPanel`** — *Default:* Reuse
  the existing panel CSS classes / overlay backdrop. — *Rationale:* Idea
  asked for this; minimizes new design surface.
- **`fetch` from the dashboard to `/tool-output/...` works on the same
  origin.** — *Default:* The dashboard is already served by the same
  `UiServer`; no CORS configuration needed. — *Rationale:* Verified via
  existing `/api/state` / `/wait-messages` fetches in the dashboard.
- **`navigator.clipboard.writeText` is available.** — *Default:* Use it; on
  exception, fall back to a `document.execCommand("copy")` path. —
  *Rationale:* Localhost is a secure context in all modern browsers.

## Open Questions

(none)

## Validation

End-to-end exercise: start a crew, drive one teammate through a real tool
call that produces non-trivial output, open the dashboard, click the
tool-event row, confirm the modal shows the (redacted) body and the Copy
button populates the clipboard. Automated below.

Per the CLAUDE.md "validate the whole suite when changing widely-consumed
behavior" rule — this slice modifies `redaction.py`, `teammate.py`,
`broker.py`, and `ui_server.py`, all of which other suites assert on — the
validation gate runs the **full** suite, not just the dashboard test.

```bash
uv run playwright install chromium >/dev/null 2>&1 || true && uv run pytest
```

## Task Breakout

```yaml
tasks:
  - name: redact-output-fn
    description: |
      Add `redact_output(text: str) -> str` to claude_crew/redaction.py.
      Applies REDACTION_PATTERNS_V1, then output-only additions (PEM
      private-key blocks; AWS session token keyword pair). Caps result via
      _cap_utf8 to 4096 bytes. Adds unit tests covering happy path + every
      secret shape called out in AT-2 (including the regression check that
      bare `sk-...` matches pattern 7).
    dependsOn: []
    acceptanceTests: [2]
    taskTouches:
      - "claude_crew/redaction.py"
      - "tests/test_redaction_output.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_redaction_output.py -v

  - name: tool-output-store
    description: |
      Add `_tool_outputs: OrderedDict[str, str]` field to Teammate base
      with `store_tool_output` / `get_tool_output` helpers enforcing
      50-entry FIFO and 4096-byte cap. Wire capture into
      sdk_teammate.py::_on_post_common: extract inp["tool_response"],
      coerce non-string values (dict/list via json.dumps, bytes via
      decode("utf-8", "replace"), else str), call redact_output inside a
      try/except that writes `[REDACTION_FAILED: <ClassName>]` on raise
      and logs WARNING. Includes the ToolEvent-fields regression assertion.
    dependsOn: [redact-output-fn]
    acceptanceTests: [1, 3, 4, 7, 8]
    taskTouches:
      - "claude_crew/teammate.py"
      - "claude_crew/sdk_teammate.py"
      - "tests/test_tool_outputs.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_tool_outputs.py -v

  - name: broker-lookup
    description: |
      Add `Broker.get_tool_output(teammate_id, tool_use_id) -> str | None`
      that walks the (live + tombstoned) teammate registry and delegates
      to `Teammate.get_tool_output`. Returns None for unknown teammate or
      evicted entry. Unit tests cover both miss paths and a live hit.
    dependsOn: [tool-output-store]
    acceptanceTests: []
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_broker_tool_output.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_broker_tool_output.py -v

  - name: ui-route
    description: |
      Register Starlette route GET /tool-output/{teammate_id}/{tool_use_id}
      in UiServer._make_app. Validate path params against
      `^[A-Za-z0-9_\-]+$` (400 on miss). Delegate to
      Broker.get_tool_output; return 200 {body, truncated,
      redaction_version} on hit, 404 {error:"not_found"} on miss, 500
      {error:"internal_error"} on unexpected exception (mirrors
      /wait-messages handler). Also extend _build_state's tool-event merge
      to include tool_use_id on the kind:"tool" record. httpx-based async
      tests cover AT-5, AT-6, AT-9.
    dependsOn: [broker-lookup]
    acceptanceTests: [5, 6, 9]
    taskTouches:
      - "claude_crew/ui_server.py"
      - "tests/test_ui_server_tool_output.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_ui_server_tool_output.py -v

  - name: dashboard-modal
    description: |
      Make the MiniMessage tool-row in claude_crew/ui/dashboard.html
      clickable when `msg.tool_use_id` is present. On click, fetch
      `/tool-output/<msg.from>/<msg.tool_use_id>`, open a modal in
      ConfigDetailPanel style showing the body in a <pre> with a Copy
      button (navigator.clipboard.writeText fallback to
      document.execCommand). 404 renders "Output no longer available
      (evicted from rolling buffer)". Playwright test loads the dashboard
      against a fixture UiServer with a seeded tool output and exercises
      the click → modal → text-visible path.
    dependsOn: [ui-route]
    acceptanceTests: [10]
    taskTouches:
      - "claude_crew/ui/dashboard.html"
      - "tests/test_dashboard_tool_output.py"
    implementationKind: behavior-change
    testCommand: |
      uv run playwright install chromium >/dev/null 2>&1 || true && \
        uv run pytest tests/test_dashboard_tool_output.py -v
```
