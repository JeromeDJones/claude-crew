# Feature: Tool-Execution Telemetry via SDK Hooks (Feature #8)

**Status**: Planning (Phase 1)
**Created**: 2026-04-27

---

## Phase 1: Research & Requirements

### Problem Statement

Feature #6 made the substrate honest about *whether a teammate is alive* by stamping `last_activity_at` on every event yielded by `client.receive_response()`. That works for stream-driven activity. It does **not** work for tool execution.

When a teammate calls a long-running tool — `Bash` running a test suite, a long `WebFetch`, an SDK-mediated MCP call — the SDK stream goes silent for the duration of the tool. From the substrate's perspective, `idle_seconds` climbs even though the teammate's subprocess is healthy and the tool is doing exactly what was asked. The lead, watching `get_teammate_status`, can't distinguish "wedged" from "running a 20-minute Bash."

Today the only way to know is to wait, ping, or kill. That's the same operator-overhead pattern Feature #6 was supposed to eliminate; it just shifted from the per-turn wall to the tool-call gap.

The fix is structural: **the SDK exposes `PreToolUse` and `PostToolUse` hooks** (spike-confirmed — see Constraints), so we can observe tool boundaries directly instead of inferring them from stream gaps. The substrate gets four new things:

1. **Tool-bracketed activity stamps.** `PreToolUse` and `PostToolUse` (and `PostToolUseFailure`) each call `_stamp_activity` — the long-Bash gap closes by construction.
2. **`current_tool` in the status payload.** When the lead calls `get_teammate_status` mid-tool, it sees `{current_tool: "Bash", current_tool_started_at_wallclock, current_tool_args_summary, current_tool_use_id}`. Operator policy ("Bash has been running 12min, that's normal for the test suite — don't ping") moves to lead code where it belongs.
3. **`last_tool_duration` and `last_tool_outcome`.** When the lead calls in the gap *between* tools, it sees what the most recent tool did. Useful for the "is this teammate making progress?" check.
4. **Per-tool transcript lines.** Every tool call lands in the crew JSONL transcript with `{tool_name, tool_use_id, duration_seconds, outcome, error?}`. `tail -f` becomes a real activity log.

### Why now

- Feature #6 just shipped. Its observability surface is in place, and #8 extends rather than reshapes it. Doing #8 now means the next real-task run (#5-style, likely MMM-4b) gets the full substrate observability stack at once — not "ship 6, run, ship 8, run again."
- The 30-min Phase 1 spike to confirm SDK hook parity with Claude Code is **resolved already** (this session — see Constraints). No design risk left in the "does the API exist?" dimension.
- All other open observability gaps the #5 retro identified are closed by #6 + #7 + #8 together. After #8 ships, the substrate is feature-complete for the next real-task validation.

### Success Criteria

Each criterion is testable. SCs reference the SDK hook contract verified in the Phase 1 spikes (see Constraints). Co-architect-f8 pushback (three load-bearing risks, see "Co-architect pushback" below) folded in. Sentinel review will fold in [FIX-NOW] items before the gate.

- [ ] **SC-1 (PreToolUse populates `current_tool`):** When the SDK fires `PreToolUse` for a tool call from the teammate's main agent, the teammate's status payload reflects `{current_tool: <tool_name>, current_tool_started_at_wallclock: <wallclock at hook fire>, current_tool_use_id: <SDK tool_use_id>, current_tool_args_summary: <bounded string>}` *before* the tool executes (i.e., before `tool_input` is dispatched). Verified by an integration test that drives a fake SDK client with controlled hook events and asserts the broker's `get_teammate_status` payload mid-tool.

- [ ] **SC-2 (PostToolUse clears `current_tool`, sets last_tool fields):** When the SDK fires `PostToolUse` (success), the teammate's status payload transitions to `{current_tool: null, current_tool_*: null, last_tool_name: <name>, last_tool_duration_seconds: <PostToolUse_wallclock - PreToolUse_wallclock>, last_tool_outcome: "ok", last_tool_finished_at_wallclock: <wallclock>}`. The transition is atomic — no observable intermediate state where `current_tool` is set but `last_tool_*` is from the prior tool.

- [ ] **SC-3 (PostToolUseFailure surfaces failure outcome):** When the SDK fires `PostToolUseFailure` (carries `error: str` and `is_interrupt: bool`), the teammate's status payload transitions to `{current_tool: null, last_tool_outcome: "failed" | "interrupted", last_tool_error_summary: <bounded string from error>, last_tool_duration_seconds, last_tool_finished_at_wallclock}`. `last_tool_outcome="interrupted"` exactly when `is_interrupt=true`; otherwise `"failed"`.

- [ ] **SC-4 (tool boundaries stamp activity):** Both `PreToolUse` and `PostToolUse`/`PostToolUseFailure` invoke `_stamp_activity()` (advancing both `last_activity_at_monotonic` and `last_activity_at_wallclock`). Verified by a deterministic test: simulate a turn where the SDK yields no stream events for 60s but a tool fires Pre→Post during that gap; assert `idle_seconds` resets at both hook fires, never exceeds the inter-hook gap.

- [ ] **SC-5 (per-tool transcript lines — paired tool_start / tool_end, main-agent only):** *(Q2 resolved → two lines. SC-10 scoped to main-agent only; subagent tool calls are #7's territory.)* The crew JSONL transcript receives **two new event kinds per main-agent tool call**:
  - On `PreToolUse` (main agent only — `agent_id is None`): `{kind: "tool_start", teammate_id, tool_name, tool_use_id, started_at_wallclock, args_summary}` (`args_summary` is `null` unless the tool is on the v1 allowlist — SC-15).
  - On `PostToolUse` / `PostToolUseFailure` (main agent only): `{kind: "tool_end", teammate_id, tool_name, tool_use_id, finished_at_wallclock, duration_seconds, outcome: "ok" | "failed" | "interrupted" | "abandoned" | "killed", error_summary: <bounded string, omitted on ok>}` (the latter two outcome values come from SC-14's close-with-non-empty-dict path).
  - Joined by `tool_use_id`. Replay tooling reconciles unmatched `tool_start` lines (e.g., dropped Post hook in unrecoverable SDK failure) by walking subsequent `lifecycle: died` / `lifecycle: kill` events scoped by `teammate_id` and treating still-open tools as `outcome: "abandoned"`.
  - Rationale (Jerome): live observability is better served by writing on Pre — `tail -f` shows tool starts in real time, not on completion. The join cost on replay is acceptable.
  - **No `agent_id`/`agent_type` fields** — by SC-10, only main-agent tool calls land in the transcript; the discrimination happens at the hook callsite (skip if `agent_id is not None`).

- [ ] **SC-6 (no schema version bump):** The `kind: "tool"` event is additive on the F4 transcript schema, mirroring F6's `kind: "lifecycle"` precedent (SC-9 in F6). Existing transcript consumers (`tail -f`, replay tooling, F6's `lifecycle: died` reader) keep working unchanged.

- [ ] **SC-7 (`get_teammate_status` payload extension):** The MCP tool's response schema gets the new fields above as **additive** keys on the existing payload. Unknown teammates and tombstoned teammates (F6 SC-2/SC-5b) continue to return their existing shapes, with the new fields reported as `null`. Tombstoned teammates report `{current_tool: null, last_tool_*: <last tool observed before death, if any, else null>}` — the post-mortem record stays queryable.

- [ ] **SC-8 (hook callbacks: observation-only, raise-safe, hang-bounded):** Three guarantees:
  1. **Observation-only.** The hook callback MUST NOT return `PermissionResultDeny`, MUST NOT modify `tool_input`, MUST NOT alter the SDK's tool-execution path in any way. Returns the continue-shaped output unconditionally. *(Co-architect: hooks share a surface with permission gating and input rewrites; without explicit observation-only discipline, a future contributor could turn telemetry into behavior change by accident.)*
  2. **Raise-safe (degrade-open).** If the callback raises (programming error, malformed `tool_input`, redaction-logic crash), failure is logged at WARNING with `{teammate_id, hook_event_name, tool_use_id, exception}` and the SDK's tool-call flow proceeds unblocked. Verified by injecting an exception into PreToolUse and asserting the turn completes normally.
  3. **Hang-bounded.** Every `HookMatcher` is registered with `timeout=1.0` (1 second). Hook bodies (stamp + dict mutate + JSONL append) are sub-ms; the budget covers slow-disk JSONL flushes. If the budget is breached, the SDK's hook timeout fires, our hook is skipped, and the SDK proceeds with the tool. Verified by a test that wraps the JSONL writer with a 5s sleep and asserts the SDK's timeout enforcement plus our degrade-open both fire.

- [ ] **SC-9 (parallel/concurrent tools — per-tool_use_id state, list-shaped payload):** *(co-architect pushback #2; sentinel + co-arch pass 2 added dict-semantics rules.)* The substrate tracks **per-`tool_use_id`** state internally — a dict keyed by `tool_use_id`, populated on `PreToolUse`, removed on `PostToolUse`/`PostToolUseFailure`. The status payload exposes:
  - `current_tools: list[{tool_name, tool_use_id, started_at_wallclock, args_summary}]` — all unfinished tools, ordered by `started_at_wallclock`.
  - `current_tool: str | null` — convenience accessor: `current_tools[-1].tool_name` if any, else `null` (last-started semantics, **documented**).
  - `current_tool_count: int` — `len(current_tools)`.
  - `last_tool_completed: {tool_name, outcome, finished_at_wallclock, duration_seconds, error_summary?}` — separate field, not collapsed with current.

  **Dict-semantics rules (must be specified for the dict to be implementable):**
  - **Pre-fires-twice for same `tool_use_id`** → last-write-wins (refresh `started_at_wallclock`, replace args_summary); WARNING log; not a crash. Guards against SDK retries / permission re-prompts re-firing Pre.
  - **Post fires for an unknown `tool_use_id`** → `dict.pop(tool_use_id, None)` no-op (idempotent). Do **NOT** write `last_tool_completed` (no `started_at` → no honest duration to report). WARNING log. Guards against turn-end races (Pre fired, dict cleared by `_end_turn`, then Post drained from a queued event) and missing-Pre cases.
  - **Soft overflow cap** (default 64 concurrent tools) → if exceeded, WARNING log and accept the new entry. This is a canary for an upstream hook-leak bug, not a hard limit. Without the cap, a leak becomes a silent unbounded dict.
  Verified by injecting two `PreToolUse` events without intervening Posts and asserting payload shape; then injecting one `PostToolUse` and asserting only one tool remains in `current_tools`. Plus three additional tests: (a) Pre-twice-for-same-tuid asserts last-write-wins + WARNING; (b) Post-without-Pre asserts no-op + WARNING + `last_tool_completed` unchanged; (c) overflow asserts WARNING + accept.

- [ ] **SC-10 (subagent tool calls — strict territorial boundary, #7 owns subagent observability):** *(Q1 resolved by Phase 1 spike — see Constraints. Tightened by sentinel + co-arch pass 2.)* The SDK fires parent-options hooks for **both** main-agent tool calls and subagent tool calls. They are distinguished by the hook input fields:
  - Main agent: `agent_id is None` AND `agent_type is None`.
  - Subagent: `agent_id` is a non-null SDK-generated identifier; `agent_type` is the subagent's defined name (e.g., `"echo-runner"`).
  Substrate behavior:
  - **Main-agent tool calls** mutate `current_tools` (SC-9), populate `last_tool_completed`, AND emit `kind: tool_start` / `kind: tool_end` transcript lines.
  - **Subagent tool calls** DO stamp activity (SC-4 — load-bearing for `idle_seconds` honesty while a subagent runs a long Bash). They DO NOT mutate `current_tools`, DO NOT populate `last_tool_completed`, AND DO NOT emit transcript lines. Activity-stamp only.
  - Feature #7 (subagent-activity envelopes) owns the entire subagent observability surface — broker envelopes AND any transcript records. #8 does **NOT** partially ship #7's contract. Without this strict boundary, #7 inherits ambiguity about what's already been delivered.
  Verified by an integration test that exercises a Task-dispatched subagent and asserts: (a) `current_tools` is empty during subagent's Bash execution; (b) the transcript contains the parent's `tool_start: Agent` line BUT NO line for the subagent's `Bash` call; (c) `last_activity_at` advances during the subagent's Bash execution (proving activity-stamp fired).

- [ ] **SC-11 (no regression in F6 telemetry):** All F6 SCs continue to hold. The `_stamp_activity` plumbing is invoked from both stream-event drainage (F6) and hook fires (F8); no double-stamping is observable as a bug (stamps are idempotent — `time.monotonic()`/`time.time()` always advance), and `idle_seconds` math is unaffected. Verified by F6's existing test suite passing unchanged plus one new test that asserts a turn with both stream events and tool calls advances `last_activity_at` from both sources.

- [ ] **SC-12 (summarizer never raises):** The summarizer/redactor (per SC-15) is wrapped in a try/except that returns `null` on any internal failure. Combined with SC-8's hook-callback degrade-open, this guarantees no summarizer bug can crash a teammate. Verified by injecting an exception inside the redactor and asserting the tool turn completes normally with `args_summary: null`. (Cap discipline + redaction patterns live in SC-15.)

- [ ] **SC-13 (interrupt path interaction):** When F6's backstop fires `client.interrupt()` for an in-flight turn that has an active tool call, the substrate's tool state is cleared by the resulting `PostToolUseFailure` hook (with `is_interrupt=true`), reaching `last_tool_completed.outcome="interrupted"` — not left dangling. If `PostToolUseFailure` does **not** fire for any reason (SDK quirk, subprocess died first), the cleanup backstops fire (SC-14). Verified by injecting a backstop fire with an active tool and asserting status post-recovery shows `current_tools == []`.

- [ ] **SC-14 (unconditional reset discipline + abandoned-tool transcript records — co-arch pushback #2 + sentinel A2 + co-arch pass 2):** `_end_turn`, `_handle_teammate_death`, and `kill_teammate` each **unconditionally** clear the per-`tool_use_id` dict (set to empty), regardless of whether matching Post hooks fired. This is the cleanup backstop for: (a) subprocess death mid-tool — no Post hook will ever fire, (b) SDK quirks where Post is dropped, (c) test-fake clients that don't implement Post. Hooks are NOT trusted to close their own brackets at the turn/lifecycle boundary.

  **When closing the dict with non-empty entries**, emit one `kind: "tool_end"` transcript line per still-open `tool_use_id` (sentinel + co-arch: this is the high-signal post-mortem record):
  - `outcome: "abandoned"` when triggered by `_end_turn` (turn ended with tool still in flight — usually from a backstop interrupt where PostToolUseFailure didn't fire).
  - `outcome: "killed"` when triggered by `kill_teammate` (operator-initiated termination).
  - `outcome: "abandoned"` when triggered by `_handle_teammate_death` (subprocess died mid-tool).
  - `finished_at_wallclock = <wallclock at clear>`, `duration_seconds = clear_wallclock - started_at_wallclock`.
  - `error_summary = "tool was in flight when <reason: turn_end | kill | death> closed it"` (passed through SC-15's redactor + cap, though typically empty of sensitive content).

  Tombstoned teammates' `last_tool_completed` is **not** updated by abandoned tools (the API contract there stays "most recent thing we know finished cleanly" — no fake durations). The audit record lives in the transcript, not the status payload.

  Verified by tests: (a) PreToolUse then death triggers a `tool_end` transcript line with `outcome: "abandoned"`; (b) PreToolUse then `kill_teammate` triggers a `tool_end` transcript line with `outcome: "killed"`; (c) tombstone status `last_tool_completed` reflects the most-recent cleanly-bracketed tool, not the abandoned one.

- [ ] **SC-16 (SDK `session_id` per teammate — `f"{crew_id}-{teammate_id}"`):** Each `SdkTeammate` calls `client.query(prompt, session_id=f"{crew_id}-{teammate_id}")` instead of the current literal `"default"`. Implications:
  - Each teammate writes to its own `~/.claude/projects/<encoded-cwd>/<crew_id>-<teammate_id>.jsonl` conversation file. **Fixes a latent bug**: today every teammate writes to `default.jsonl` concurrently with no locking — interleaved/corrupted writes, undetected because we don't resume teammate sessions.
  - SDK propagates `session_id` to subagent hook inputs automatically (verified: subagent spike showed subagent hooks fire with the parent SDK session). Every `tool_start`/`tool_end` transcript line therefore carries `session_id` for free.
  - Crew-level filtering: `tail -f <transcript> | grep <crew_id>` shows everything every teammate (and their subagents) did in this crew run, since `<crew_id>` is the prefix of every session_id.
  - **Explicitly NOT tied to the lead's CLI session_id.** That requires either GitHub issue #25642 (CLAUDE_SESSION_ID env var) to land, or an explicit `--lead-session-id` parameter on broker startup. Both are deferred to a future BACKLOG item; v1's substrate-level correlation via `crew_id` is sufficient for the use cases identified in the #5 retro.
  - Verified by an integration test: spawn two teammates in one crew, drive them through tool calls, assert each teammate's session_id payload field is `<crew_id>-<their teammate_id>`, that the two session_ids are distinct, and that subagent tool calls within a teammate carry the same session_id as the parent teammate.

- [ ] **SC-15 (args_summary opt-in for long-running tools — Q3 resolved, inverted from co-architect default):** The v1 contract is **utility-first for long-running tools, safety-via-redaction**:
  - `tool_name`, `tool_use_id`, `started_at_wallclock`, `finished_at_wallclock`, `duration_seconds`, `outcome` are emitted **unconditionally** for every tool call (cheap, never sensitive).
  - `args_summary` is emitted for tools on the v1 allowlist: **`Bash`, `Task` (subagent dispatch / Agent tool), `WebFetch`** (Phase 2 may extend to specific MCP tools known to be long-running). Rationale: these are exactly the tools that drove the feature's existence (`idle_seconds` climbs while Bash runs the test suite — operator needs to know whether it's the test suite or `gh auth token | curl ...`). `Read`/`Glob`/`Grep` are explicitly EXCLUDED from v1 — they complete in ms; args_summary buys nothing on them.
  - For allowlisted tools, the summary applies (a) **per-tool extractor** (Phase 2 locks each):
    - `Bash`: extract `command` only. *(Reconciled to Task 1 spec: command alone is the high-signal field; description is rarely populated by the model and adds little.)*
    - `Task`: extract `subagent_type` + `description`; **NOT** the full `prompt` (could carry parent context including secrets).
    - `WebFetch`: extract `url` + `prompt[:80]`.
  - (b) **Unconditional regex redaction** applied AFTER extraction (full pattern set; Phase 2 to lock the regex syntax with worked examples):
    - **Keyword substrings** (case-insensitive): `token`, `secret`, `key`, `password`, `passwd`, `bearer`, `authorization`, `apikey`, `api_key`. The entire `key=value`, `key: value`, `--flag value`, or `key value` pair around the match becomes `<redacted>`. (Phase 2 picks a single regex set with worked examples for each shape.)
    - **Short-flag secrets** (co-architect: `mysql -p hunter2`, `ssh -i key`, `curl -k`): `(?<!\S)-[pPkKtT]\s+\S+` → `<redacted>`. *(T1 builder errata: `\b-` fails because `-` is not a word character; negative-lookbehind for non-whitespace is the correct anchor.)*
    - **Anchored token shapes** (cheaper to grep than relying on length-based heuristics):
      - AWS access key: `AKIA[0-9A-Z]{16}` (20 chars total — under the base64 threshold) → `<redacted-aws>`.
      - GitHub PAT/OAuth: `gh[poasu]_[A-Za-z0-9]{36,}` → `<redacted-gh>`.
      - Slack token: `xox[baprs]-[A-Za-z0-9-]{10,}` → `<redacted-slack>`.
      - JWT: `eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+` → `<redacted-jwt>` (single-shot; saves cap budget vs. three separate base64 redactions).
    - **URL embedded credentials**: `https?://[^:/\s@]+:[^@/\s]+@` → `https://<redacted>@`.
    - **Length-based fallbacks**:
      - Base64-shaped strings ≥32 chars (`[A-Za-z0-9+/_-]{32,}={0,2}`) → `<redacted>`.
      - Hex-shaped strings ≥40 chars (`[A-Fa-f0-9]{40,}`) → `<redacted>`.
  - (c) **Hard 256-byte cap** on the post-redaction summary, with `…` ellipsis suffix.
  - `CLAUDE_CREW_TOOL_ARGS_FULL=1` env override expands the allowlist to **all** tools but keeps redaction + cap. Documented as debug-only; warning logged at teammate spawn when set.
  - `CLAUDE_CREW_TOOL_ARGS_DISABLED=1` env override forces `args_summary: null` for ALL tools (paranoid mode). Documented as opt-out for sensitive workloads.
  - For non-allowlisted tools, `args_summary` is `null` in the payload AND in the transcript line.
  - **`error_summary` (SC-3, SC-5) is also passed through the redactor + 256-byte cap, UNCONDITIONALLY** — regardless of allowlist or env overrides. `CLAUDE_CREW_TOOL_ARGS_FULL=1` does NOT widen errors. Errors are not args. Rationale (co-architect): tool errors carry content (`Bash` stderr like `Authentication failed: token sk-ant-...`, `WebFetch` errors echoing URLs with embedded query secrets, MCP errors echoing request bodies); without redaction they're a parallel exfil channel.
  Verified by tests:
  - **Utility:** `Bash` with `command: "pytest tests/ -v"` → summary contains `pytest tests/ -v`.
  - **Shell-var safety:** `Bash` with `command: "curl -H 'Authorization: Bearer $TOKEN' ..."` → summary redacts the `Authorization: Bearer $TOKEN` clause (regex matches "authorization" substring → redact pair) AND the literal `$TOKEN` (since shell interpolation happens at exec, not in tool_input — the literal `$TOKEN` is what ends up in the summary).
  - **Inline-secret safety:** `Bash` with `command: "curl -H 'X-API-Key: sk-ant-abc123def456...long...'..."` → base64-pattern strips the literal token.
  - **Hex-secret safety:** `Bash` with `command: "deploy --token=deadbeef0123...40chars+"` → hex pattern strips the value.
  - **Heredoc/multiline:** `Bash` with multiline command containing `--password=hunter2` → substring pattern strips the `--password=hunter2`.
  - **Task safety:** `Task` with `prompt: "..."` long string → summary contains `subagent_type` + `description` only, NOT the prompt body.
  - **Cap:** any allowlisted tool with a 1KB arg → summary is exactly 256 bytes ending in `…`.
  - **Disabled mode:** `CLAUDE_CREW_TOOL_ARGS_DISABLED=1` set → all summaries null regardless of allowlist.
  - **Read excluded:** `Read` tool call with `file_path: "/etc/passwd"` → summary is `null` (Read is not on v1 allowlist).

### Co-architect pushback (folded in — see SCs above)

co-architect-f8 (Opus 4.7 high-effort, persistent) identified three load-bearing risks before Phase 1 SC drafting. All three are folded into the SCs above:

1. **Hook dispatch order vs stream order — RESOLVED by Phase 1 spike (this session).** Live SDK probe (`/tmp/hook_order_spike.py`) confirmed: `PreToolUse` fires ~5ms AFTER the `AssistantMessage` containing the `ToolUseBlock` yields into `receive_response()`. `PostToolUse` fires ~2ms BEFORE the `UserMessage+ToolResultBlock` yields. Hooks and stream events are sequenced on the same asyncio loop — no concurrent writers. Implications: (a) a 5ms `current_tool=null` window exists between block-yield and Pre-hook-fire, accepted as not operator-observable; (b) `_current_tool` has a single writer per field (hooks-only for tool fields, stream-loop for activity stamp — no shared field has two writers); (c) parallel tool use is not yet exercised — co-architect's parallel-tool concern still applies (SC-9 is the contract). See spike script for re-running.

2. **Scalar `current_tool` leaks under failure / death / parallelism — RESOLVED by SC-9 + SC-14.** Per-`tool_use_id` dict is the internal model; `current_tools: list` is the payload. `_end_turn` and the death/kill paths clear unconditionally — hooks are not trusted to close their own brackets.

3. **`args_summary` as exfiltration surface — RESOLVED by SC-15.** Inverted the default: cheap-and-safe fields (name, timestamps, outcome) are always emitted; `args_summary` is opt-in via per-tool allowlist (v1 likely empty or `Read`-only); unconditional regex redaction; 256-byte cap; debug-only env override (`CLAUDE_CREW_TOOL_ARGS_FULL=1`).

### Questions

Q1, Q2, Q3 resolved this session (spike + Jerome's calls). Q4, Q5, Q6 carry leans into Phase 2. Q7, Q8, Q9 added by sentinel-f8-p1 review — Q7/Q8 are 2-min Phase 2 spikes, Q9 is a Phase 2 design lock.

- [x] **Q1 (subagent tool hooks — does our hook fire for them?) — RESOLVED YES.** Phase 1 live spike (`/tmp/subagent_hook_spike.py`) drove a real SDK client through a Task-dispatched subagent (`echo-runner` → `Bash`). Parent options' hooks fired for the subagent's `Bash` call with `agent_id=<hex>`, `agent_type="echo-runner"`. Main agent's tool calls fire with `agent_id=None`, `agent_type=None`. SC-10 codifies the discrimination.

- [x] **Q2 (transcript model) — RESOLVED: paired `tool_start` / `tool_end` lines.** Jerome's call. Reasoning: don't wait for tool completion to write activity to the transcript — `tail -f` should surface tool starts in real time. Join cost on replay is acceptable. SC-5 reflects this.

- [x] **Q3 (args_summary v1 allowlist) — RESOLVED: long-running tools (`Bash`, `Task`, `WebFetch`).** Jerome's call, inverting co-architect's safety-first lean. Co-architect ratified after rebuttal (seq=74) and counter-proposed structural strengthening folded into SC-15: versioned `REDACTION_PATTERNS_V1`, per-tool extractor registry, redact-then-cap order, extended pattern set (AKIA, GitHub, Slack, JWT, URL-creds, short-flags). Read/Glob/Grep are NOT on the v1 allowlist — too fast for `args_summary` to add value. Promoted to Phase 2 design pin.

- [ ] **Q4 (does `_stamp_activity` care that the same wallclock is set twice?):** F6's stamping uses `time.monotonic()`/`time.time()` directly — no rate-limit or dedup. A `PreToolUse` hook fire 5ms after a stream-event stamp produces a tiny but non-zero advance. Spike confirms hooks and stream events are sequenced on the same loop, so no race within a single read. *Lean: no guard needed; idempotent advance is fine.*

- [ ] **Q5 (`current_tool_args_summary` format for allowlisted tools — JSON-shape preserved or flat string?):** Two formats: (a) `json.dumps(redacted_input)[:256]` — keeps shape, easier to grep; (b) per-tool extraction (e.g., `Bash` → `f"command={cmd}"`). *Lean: (b). Per-tool extractors are tighter and the allowlist already binds us to per-tool logic. Phase 2 locks the extractor for each allowlisted tool — initial drafts in the Phase 2 Design Pins below.*

- [ ] **Q6 (do we stamp activity on `Stop`/`SubagentStop`/`Notification` hooks too?):** SDK exposes 10 hook events. Pre/PostToolUse[/Failure] are the load-bearing four. *Lean: only wire what we use. Future features (e.g., #7 will likely use `SubagentStart`/`SubagentStop`) can add more. Phase 2 confirms no orphan dependency.*

- [ ] **Q7 (does Pre fire twice for the same `tool_use_id`?) — sentinel D:** SDK retries, internal re-deliveries, permission re-prompts could plausibly trigger duplicate Pre fires for one tool call. SC-9's "last-write-wins + WARNING" rule covers behavior; we want empirical confirmation. **Phase 2 spike (~2 min):** drive a tool through a permission prompt, observe whether Pre fires once or twice for the same `tool_use_id`.

- [ ] **Q8 (does `PermissionRequest` interleave with `PreToolUse`?) — sentinel D:** SDK exposes `PermissionRequest` as a separate hook event. If a tool requires permission, does `PreToolUse` fire before the prompt (so `current_tool` is set while the model awaits permission), or after (so a permission-gated tool has `current_tools=[]` until granted)? Affects the meaning of `current_tool_started_at_wallclock` for permission-gated tools. **Phase 2 spike (~2 min, alongside Q7).**

- [ ] **Q9 (transcript `tool_end` schema — same as status `last_tool_completed`, or diverge?) — sentinel D:** Two surfaces emit similar-shaped records: the transcript (`kind: tool_end`) and the status payload (`last_tool_completed`). They might diverge: transcript is post-mortem audit, can carry richer error context (full bounded error, `tool_use_id`); status is bounded for MCP response size. *Lean: transcript carries `tool_use_id` + full error_summary (still redacted + capped); status `last_tool_completed` keeps a smaller projection (no `tool_use_id`, error_summary still bounded). Phase 2 to lock both schemas with field-by-field justification.*

### Constraints & Dependencies

- **Requires (shipped):** Feature #6 (`get_teammate_status`, `_stamp_activity`, `_begin_turn`/`_end_turn` lifecycle, F4 transcript with `kind` discriminator, broker tombstone surface). Feature #4 (crew JSONL transcript). Feature #1 (MCP server + tool surface).

- **SDK hook-ordering spike (RESOLVED, this session, live SDK probe):** `/tmp/hook_order_spike.py` drove a real `ClaudeSDKClient` (Haiku 4.5) through a `Bash` tool call with PreToolUse / PostToolUse / PostToolUseFailure callbacks installed. Observed timeline (mono-seconds):
  ```
  t=3.618  STREAM_AssistantMsg+ToolUseBlock     tool=Bash tuid=toolu_01...
  t=3.623  HOOK_PreToolUse                      tool=Bash tuid=toolu_01...     (+5ms)
  t=3.767  HOOK_PostToolUse                     tool=Bash tuid=toolu_01...     (-2ms)
  t=3.769  STREAM_UserMsg+ToolResultBlock       tuid=toolu_01...
  ```
  - PreToolUse fires AFTER ToolUseBlock yields (+5ms). The 5ms `current_tools=[]` window between block-yield and Pre-hook is accepted (not operator-observable; MCP status calls are ms-scale anyway).
  - PostToolUse fires BEFORE ToolResultBlock yields (-2ms). `current_tools` is cleared before the lead can possibly see the tool result on the stream.
  - Hooks and stream events are sequenced on the same asyncio event loop (cooperative; no preemption). No concurrent-writer concerns for activity stamping.
  - Spike covered single-tool only — parallel tool use not yet exercised; SC-9 designs against it.

- **SDK hooks API spike (RESOLVED, this session):** `claude_agent_sdk` exposes hooks via `ClaudeAgentOptions(hooks=...)`. The signature is:
  ```python
  hooks: dict[
      Literal['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'UserPromptSubmit',
              'Stop', 'SubagentStop', 'PreCompact', 'Notification',
              'SubagentStart', 'PermissionRequest'],
      list[HookMatcher]
  ] | None
  ```
  - `HookMatcher = (matcher: str | None, hooks: list[Callable[[InputT, str | None, HookContext], Awaitable[HookOutputT]]], timeout: float | None)`. `matcher` is a tool-name pattern; `None` matches all tools.
  - `PreToolUseHookInput` carries `{session_id, transcript_path, cwd, permission_mode, agent_id, agent_type, hook_event_name, tool_name, tool_input: dict, tool_use_id}`.
  - `PostToolUseHookInput` adds `tool_response: Any` to the above.
  - `PostToolUseFailureHookInput` adds `error: str, is_interrupt: bool`.
  - `tool_use_id` is the join key between Pre and Post for the same tool call.
  - `agent_id` and `agent_type` distinguish main-agent tool calls from subagent tool calls (Q1 to confirm empirically that they fire on the parent options).
  - `client.interrupt()` (F6's backstop primitive) interacts with this surface via `PostToolUseFailure(is_interrupt=true)` (SC-13).

- **Breaking changes:** None for lead-side MCP tool surface — additive payload fields, additive transcript `kind` value, additive error code (none introduced). Internal: the SDK options dict gains a `hooks` entry per teammate.

- **Performance implications:** Per-tool-call hook overhead: two callback invocations (Pre + Post) per tool, each doing one stamp + one in-memory dict update + one JSONL append. Negligible vs. tool execution itself.

- **Concurrency:** Single asyncio event loop. Hook callbacks run on the same loop as the per-turn drain. SC-9's per-`tool_use_id` state must be a per-teammate dict guarded by the single-writer invariant of the asyncio loop (no explicit lock needed; document the invariant).

- **Cross-feature:** Touches F4 transcript schema (additive `kind: "tool_start"` / `kind: "tool_end"` lines, plus `session_id` field on every line via hook input). Touches F6 status payload (additive fields). Does NOT touch F1 broker error codes. Bumps elbow-room for F7 (`SubagentStart`/`SubagentStop` hooks are reserved territory; #8 defers them; SC-10 nails this down).

- **SDK session_id semantics (RESOLVED, this session — research + SDK source):** Claude Code session_id is a UUID v4 keying a JSONL file at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. The SDK accepts arbitrary strings, but **two `ClaudeSDKClient` processes using the same session_id will race-corrupt that file** (no locking). Today every teammate uses literal `"default"` → all teammates race on `default.jsonl`. SC-16 fixes this by giving each teammate a unique `f"{crew_id}-{teammate_id}"` session_id. Lead's CLI session_id is NOT exposed to subprocesses (open feature request `claude-code#25642`); deferring lead-session correlation to a future BACKLOG item.

- **Test infra:** `ProgrammableSDKClient` (added in F6 T4) needs extension to fire hook events from the test harness. F6's live SDK A2 probe pattern repeats — one live probe to confirm hooks fire end-to-end on a real SDK subprocess.

### Phase 2 Design Pins

These are decisions co-architect-f8 surfaced during Phase 1 review (passes 2 & 3) that should be preserved as Phase 2 design pins, not re-litigated:

- **Versioned redactor constant.** SC-15's redactor lives in `claude_crew/redaction.py` as a versioned constant `REDACTION_PATTERNS_V1: list[tuple[Pattern, str]]`. Transcripts record `redaction_version: "v1"` so a future bump is deterministic and auditable. Bumping the version is a deliberate act, not drift.

- **Per-tool extractor registry.** SC-15's allowlist is implemented as a registry: `ALLOWLIST_V1: dict[str, Callable[[dict], str]]`. Adding a tool requires writing an extractor function. That gate is a feature, not friction — it prevents "let's just allow everything" drift in v1.x.

- **Redact-then-cap order.** Always: redact full string → 256-byte cap → ellipsis suffix. Truncate-first splits tokens mid-string; the regex misses the half. Cost is fine; tool_input is bounded by SDK limits anyway.

- **HookMatcher.timeout=1.0.** Every `HookMatcher` is registered with the 1-second timeout (SC-8.3). 1s is well above the sub-ms hook body and well under any reasonable user-observable latency.

- **Broker MCP tools are first-class tool events.** Sentinel A4 — when the teammate calls our own broker tools (`mcp__claude-crew__send_to`, `broadcast`, `get_messages`, etc.), they fire Pre/Post like any tool. Lean: include them in `current_tools` and the transcript without filtering. They're real activity ("teammate is sending a message right now") and excluding them would create a blind spot in the substrate's own observability.

### Phase 1 Load-Bearing Assumptions

These are not Phase 2 default-accept assumptions — these are facts SC-15's safety calculus depends on. Listed here so Phase 2 design and future SDK upgrades can re-validate.

- **A1 (Bash exfil model — load-bearing for SC-15):** The model's `tool_input.command` is the **pre-shell-substitution string**. Variable expansion (`$VAR`, `${VAR}`), command substitution (`` `cmd` ``, `$(cmd)`), process substitution (`<(cmd)`, `>(cmd)`), and file redirection (`< file`) all happen inside the Bash subprocess at exec time, AFTER the `PreToolUse` hook has fired and the substrate has captured `args_summary`. The substrate never observes post-expansion strings. **If this assumption is ever invalidated** (SDK pre-expands for sandboxing/audit/observability), SC-15's safety calculus must be re-derived — shell-interpolated secrets would land in `args_summary` with no keyword anchor and the redactor would miss them. Phase 2 / Phase 4 to confirm by inspection of the SDK's CLI invocation path.

- **A2 (hook callback ordering w/ SDK loop):** Hooks and `receive_response()` events are sequenced on the same asyncio event loop, single-writer per field. Empirically verified (single-tool spike). Parallel-tool case still TBD; SC-9's per-tool_use_id dict design is the contract.

- **A3 (subagent hook propagation):** Parent-options hooks fire for subagent tool calls with `agent_id` and `agent_type` populated. Empirically verified (subagent spike). SC-10 codifies the discrimination logic.

### Pre-existing rule check

claude-crew has no `.claude/rules/` directory (verified F6 Phase 1; still true). Global rules from `~/.claude/rules/` apply:
- Tests at implementation layer + one layer above, happy and sad path at both (`validate-before-change.md`)
- Live SDK tests gated behind a marker, default off (carried from F6)
- No backwards-compat shims unless real consumers exist (`coding-standards.md`)

---

**Gate**:
- ✅ Co-architect-f8 pushback warmup — done (3 load-bearing risks, folded into SC-9 / SC-14 / SC-15)
- ✅ SDK hooks API spike — done (this session)
- ✅ Hook-ordering spike — done (live SDK probe, this session)
- ✅ Subagent hook spike — done (live SDK probe, this session — Q1 resolved)
- ✅ SDK session_id semantics research — done; SC-16 added (`f"{crew_id}-{teammate_id}"` per teammate)
- ✅ Q1, Q2, Q3 resolved with Jerome
- ✅ Co-architect-f8 review (3 passes) — done; all findings folded (extended redactor patterns + structure, SC-12 simplified, error_summary redaction unconditional, A1 Bash exfil model documented, abandoned-tool transcript records, observation-only discipline, dict-semantics rules, subagent territorial boundary)
- ✅ Sentinel-f8-p1 review of acceptance criteria — done (4 [FIX-NOW] items folded as SC-8 strengthened, SC-9 dict semantics, SC-14 abandoned-tool records, broker-MCP-tools design pin; 3 new questions Q7/Q8/Q9 added)
- ⏳ **Pre-Phase-3 parallel-tool live probe** (sentinel D process item — analog of F6's pre-Phase-3 grep audit; ~5min spike before Phase 3 task breakdown)
- ⏳ Jerome confirms Phase 1 → Phase 2

---

## Phase 2: Design & Specification

### Architecture Overview

```
                    ┌─────────────────────────────────────────────────────┐
                    │  SdkTeammate process (per teammate, ClaudeSDKClient)│
                    │                                                     │
   inbound          │   ┌─────────────────────────────────────────────┐   │
   envelope ───────►│  ─►│ _handle_one_turn(env)                       │   │
                    │   │   _begin_turn()                             │   │
                    │   │   client.query(prompt,                      │   │
                    │   │     session_id=f"{crew_id}-{teammate_id}")  │   │
                    │   │   ┌─────────────────────────────────────┐   │   │
                    │   │   │ async for msg in receive_response() │   │   │
                    │   │   │   _stamp_activity()  ◄────────────┐ │   │   │
                    │   │   │   handle msg…                     │ │   │   │
                    │   │   └───────────────────────────────────│─┘   │   │
                    │   │           ▲                           │     │   │
                    │   │           │ ▲ stream-event stamp      │     │   │
                    │   │           │ │ ┌───────────────────────│───┐ │   │
                    │   │           │ │ │ Hook callbacks         │   │ │   │
                    │   │           │ │ │   PreToolUse (+5ms)    │   │ │   │
                    │   │           │ │ │   PostToolUse (-2ms)   │   │ │   │
                    │   │           │ │ │   PostToolUseFailure   │   │ │   │
                    │   │           │ │ │ ◄───── hook stamp     │   │ │   │
                    │   │           │ └─│                        │   │ │   │
                    │   │           │   │ Discrimination by      │   │ │   │
                    │   │           │   │   inp.agent_id         │   │ │   │
                    │   │           │   └────┬───────────────────┘   │ │   │
                    │   │           │        │                       │ │   │
                    │   │           │        ▼                       │ │   │
                    │   │           │   Update _tool_uses dict       │ │   │
                    │   │           │   (only if main agent)         │ │   │
                    │   │           │        │                       │ │   │
                    │   │   _end_turn() ─────┘                       │ │   │
                    │   │   • clears _tool_uses                       │ │   │
                    │   │   • emits abandoned-tool transcript records │ │   │
                    │   └─────────────────────────────────────────────┘   │
                    │           │                            │            │
                    │           ▼                            ▼            │
                    │   self._broker.get_teammate    self._broker._sink   │
                    │     _status() reads              .write_tool_event  │
                    │     _tool_uses + last_tool       (broker is single  │
                    │     _completed                    transcript writer)│
                    └─────────────────────────────────────────────────────┘
```

**Key decisions visualized:**
- Hooks attach in `_run` at `sdk_teammate.py:264` (before `ClaudeAgentOptions(**opts_kwargs)`).
- Hook callbacks are bound methods on `SdkTeammate` so they have `self` access (broker, transcript sink, tool-use dict).
- Both stream events (F6) and hook events (F8) call `_stamp_activity` — single writer per *field*, idempotent advance.
- Per-`tool_use_id` dict (`_tool_uses`) lives on `Teammate` base class so death/kill paths can clear it without subclass branching (mirrors F6's `_current_turn_started_at_wallclock`).
- Subagent tool calls (`agent_id != None`) only stamp activity — no dict mutation, no transcript line. SC-10 boundary.
- Transcript writer (`broker._sink`) is single-writer (broker only); hooks call broker via `self._broker._sink.write_tool_event(...)`.

### Data / API Contracts

#### Internal: `_ToolUseEntry` dataclass (per-`tool_use_id` dict value type)

*(Sentinel A4 — pinning the entry shape so D8/D9 references are unambiguous.)*

```python
# claude_crew/teammate.py — module-scope dataclass; immutable except for replace-via-Pre-twice rule.
@dataclass(frozen=True)
class _ToolUseEntry:
    tool_name: str                    # PreToolUse input.tool_name
    tool_use_id: str                  # SDK toolu_xxx — also the dict key (denormalized for convenience)
    started_at_wallclock: float       # PreToolUse hook fire wallclock (NOT the ToolUseBlock yield time, ~5ms earlier)
    args_summary: str | None          # null unless tool on v1 allowlist (SC-15)
```

D8's "Pre-twice" replaces the entire entry (frozen → must reconstruct, simpler than mutating). D9's iteration consumes `(tool_use_id, entry)` pairs.

#### Status payload extension (additive on F6's `get_teammate_status`)

```python
# Existing F6 fields preserved verbatim. New fields below; null when no main-agent tool active/completed.
{
    # ... F6 fields (teammate_id, name, role, alive, spawned_at,
    #     last_activity_at_wallclock, current_turn_started_at_wallclock,
    #     idle_seconds, died_at_wallclock, exit_code, last_activity_at_wallclock_at_death) ...

    # F8 additions:
    "current_tools": list[dict] | [],              # always a list, possibly empty
    "current_tool": str | None,                    # = current_tools[-1].tool_name if any, else None
    "current_tool_count": int,                     # = len(current_tools)
    "last_tool_completed": dict | None,            # only fully-bracketed tools
    "redaction_version": str,                      # "v1" — pins the redactor used for this teammate's args/errors
}
```

```python
# current_tools[i] shape:
{
    "tool_name": str,                              # e.g. "Bash", "Task"
    "tool_use_id": str,                            # SDK's toolu_xxx identifier
    "started_at_wallclock": float,                 # PreToolUse fire wallclock
    "args_summary": str | None,                    # null unless tool on v1 allowlist (SC-15)
}
```

```python
# last_tool_completed shape (status payload — projection; transcript carries fuller record per Q9):
{
    "tool_name": str,
    "outcome": Literal["ok", "failed", "interrupted"],   # "abandoned"/"killed" go to transcript only
    "finished_at_wallclock": float,
    "duration_seconds": float,
    "error_summary": str | None,                          # only if outcome != "ok"
}
```

#### Transcript line schemas (additive on F4)

```python
# kind: "tool_start" — one per main-agent PreToolUse fire
{
    "v": 1,
    "kind": "tool_start",
    "ts": float,                                    # transcript ts (= started_at_wallclock)
    "crew_id": str,                                 # F4 standard
    "teammate_id": str,
    "tool_name": str,
    "tool_use_id": str,
    "args_summary": str | None,                     # null unless tool on allowlist
    "redaction_version": "v1",                      # pinned — bumping is a version event
}

# kind: "tool_end" — one per main-agent PostToolUse / PostToolUseFailure / abandon / kill
{
    "v": 1,
    "kind": "tool_end",
    "ts": float,                                    # transcript ts (= finished_at_wallclock)
    "crew_id": str,
    "teammate_id": str,
    "tool_name": str,
    "tool_use_id": str,                             # joins with prior tool_start by id
    "duration_seconds": float,
    "outcome": Literal["ok", "failed", "interrupted", "abandoned", "killed"],
    "error_summary": str | None,                    # null on "ok"; redacted+capped otherwise
    "redaction_version": "v1",
}
```

#### Hook callback signature (bound method on SdkTeammate)

```python
async def _on_pre_tool_use(
    self,
    inp: PreToolUseHookInput,
    tool_use_id: str | None,    # also in inp.tool_use_id; SDK passes it separately
    ctx: HookContext,
) -> dict:
    # Always returns {"continue": True} — never deny, never modify tool_input (SC-8.1).
    # Internal try/except wraps the body (SC-8.2 degrade-open).
    # SDK-level timeout=1.0 backstops hangs (SC-8.3).
```

#### Redactor module signature (`claude_crew/redaction.py` — new file)

```python
REDACTION_VERSION: str = "v1"

# Each entry: (compiled regex, replacement string).
REDACTION_PATTERNS_V1: list[tuple[re.Pattern, str]] = [
    # … patterns from SC-15 …
]

ALLOWLIST_V1: dict[str, Callable[[dict], str]] = {
    "Bash": _extract_bash,
    "Task": _extract_task,
    "WebFetch": _extract_webfetch,
}

def summarize_args(tool_name: str, tool_input: dict) -> str | None:
    """Returns null if tool not allowlisted or env-disabled.
       Otherwise: extract → redact → cap. Never raises (returns null on internal failure)."""

def redact_error(error_text: str) -> str:
    """Unconditional redact + cap, regardless of allowlist. Used for error_summary."""
```

### Design Decisions

Each decision names a concrete carry-into point — the file/contract/test that fails if the decision is silently dropped.

- **D1 (hook attachment site).** Hooks attach in `SdkTeammate._run` at `sdk_teammate.py:264`, immediately before `options = ClaudeAgentOptions(**opts_kwargs)`. The hook dict is built from bound methods (`self._on_pre_tool_use`, `self._on_post_tool_use`, `self._on_post_tool_use_failure`) so each callback has `self` access (broker, transcript sink, `_tool_uses` dict). Every `HookMatcher` is constructed with `timeout=1.0`. *Carried into:* `sdk_teammate.py:_run`, `tests/test_sdk_teammate.py::test_hooks_registered_with_timeout`.

- **D2 (per-tool_use_id dict on Teammate base class).** A new instance field `_tool_uses: dict[str, _ToolUseEntry]` lives on `Teammate` (teammate.py) — not on `SdkTeammate`. Initialized to `{}` in both `Teammate.__init__` (StubTeammate path) and `SdkTeammate.__init__`. Cleared unconditionally in `_end_turn` AND in the broker's death/kill paths. Stub teammates always report empty (no hooks fire on them) — same shape as F6's `_current_turn_started_at_wallclock`. *Carried into:* `teammate.py` field declaration + `_end_turn` body, `broker._tombstone_teammate` calling `teammate._tool_uses.clear()`. Tests: `test_teammate.py::test_tool_uses_cleared_on_end_turn`.

- **D3 (main-vs-subagent gating at hook entry).** Each hook callback's first action — after the try/except wrap — checks `inp.get("agent_id") is None`. If True (main agent): proceed with full flow (dict mutation + transcript line + activity stamp). If False (subagent): activity stamp only, then return continue-shaped output. SC-10's strict territorial boundary. *Carried into:* `_on_pre_tool_use` early-return branch, `tests/test_sdk_teammate.py::test_subagent_tool_call_does_not_emit_transcript_line`.

- **D4 (transcript writes serialized on the broker's asyncio event loop — not call-site monopoly).** *(Co-arch pass-3 review wording fix.)* The semantic invariant is **same-loop serialization**, not "only broker code calls write_tool_event." Hook callbacks call `self._broker._sink.write_tool_event(event_kind, fields)` directly — they execute on the same asyncio loop as the broker, so cooperative scheduling serializes writes without explicit locks. No queue/drain indirection needed. `write_tool_event` is sync (mirrors `write_lifecycle`) and best-effort (errors logged WARNING, swallowed — never propagate to the SDK). **Future contributors warning:** "single-writer broker" reads as call-site monopoly; the actual invariant is loop-coresidency. Don't refactor toward call-site monopoly; you'd add latency for zero loop-safety benefit. *Carried into:* `transcript.py` new `write_tool_event` method, hook callsites in `sdk_teammate.py` calling `self._broker._sink.write_tool_event(...)` directly.

- **D5 (session_id at client.query callsite).** `sdk_teammate.py:306` changes from `await client.query(prompt, session_id="default")` to `await client.query(prompt, session_id=f"{self._broker.crew_id}-{self.id}")`. Both values in scope today. **Bonus fix for the latent default.jsonl race** documented in Constraints. *Carried into:* `sdk_teammate.py:306`, `tests/test_sdk_teammate.py::test_session_id_per_teammate`.

- **D6 (redactor architecture — versioned constant + per-tool extractor registry).** New module `claude_crew/redaction.py` houses `REDACTION_VERSION`, `REDACTION_PATTERNS_V1`, `ALLOWLIST_V1`, `summarize_args`, `redact_error`. Module-scope imports only (no lazy loading — see global memory feedback). Per-tool extractors are pure functions that take `tool_input: dict` and return a flat `f"key1=val1; key2=val2"` string (Q5 lean (b)). The redactor pipeline is **`extract → redact → cap`** — never `cap → redact` (truncate-first splits tokens mid-string, regex misses).

  **`REDACTION_PATTERNS_V1` enumeration (lock these in Phase 4 implementation — co-arch pass-4 fix-now):**
  ```python
  REDACTION_PATTERNS_V1: list[tuple[re.Pattern, str]] = [
      # Flag-style secrets (long flags + value)
      (re.compile(r"--(?:password|token|secret|api[-_]?key|key|auth)[=\s]+\S+", re.I),
       "<redacted-flag>"),
      # Short-flag secrets — mysql -p hunter2, ssh -i keyfile
      (re.compile(r"\b-[pPkKtT]\s+\S+"), "<redacted-flag>"),
      # Header literals
      (re.compile(r"(?i)(Authorization|X-Api-Key|X-Auth-Token)\s*[:=]\s*\S+"),
       r"\1: <redacted>"),
      (re.compile(r"(?i)(Bearer|Basic)\s+[A-Za-z0-9._\-+/=]+"),
       r"\1 <redacted>"),
      # URL embedded credentials — git push https://user:tok@host/repo
      (re.compile(r"https?://[^:/\s@]+:[^@/\s]+@"), "https://<redacted>@"),
      # URL query-param secrets
      (re.compile(r"[?&](?:api[-_]?key|token|secret|access[-_]?token|password)=[^&\s]+", re.I),
       r"&<redacted>"),
      # Anchored token shapes (cheaper to grep than length-based fallbacks)
      (re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"), "<redacted-key>"),
      (re.compile(r"gh[poasu]_[A-Za-z0-9]{36,}"), "<redacted-key>"),
      (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "<redacted-key>"),
      (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted-key>"),
      (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
       "<redacted-jwt>"),
      # Generic length-based fallbacks (last — anchored shapes catch first)
      (re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"), "<redacted-b64>"),
      (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<redacted-hex>"),
  ]
  ```

  **Pattern bump procedure** *(sentinel pass-2 D fresh observation):* a v2 is a deliberate event. Triggers: a confirmed real-world leak shape escapes the v1 set, a new SDK tool joins the allowlist with a novel arg shape, or a redaction false-positive proves chronic. Bump procedure: new constant `REDACTION_PATTERNS_V2`, bump `REDACTION_VERSION = "v2"`, write a CHANGELOG entry citing the trigger, do NOT delete v1 (transcripts written under v1 stay marked v1 for audit). Documented in module docstring of `redaction.py`.

  *Carried into:* `claude_crew/redaction.py` (new file with the explicit pattern set above), `tests/test_redaction.py` (new file with the SC-15 test list).

- **D7 (env-var contract).** Two additions, joining existing `CLAUDE_CREW_TRANSCRIPT_*` and `CLAUDE_CREW_LIVENESS_POLL_SECONDS`/`CLAUDE_CREW_TURN_BACKSTOP_SECONDS`:
  - `CLAUDE_CREW_TOOL_ARGS_FULL=1`: widens allowlist to all tools using a generic-extractor (`json.dumps(tool_input)`); redaction + cap still apply. Lifecycle WARNING logged at teammate spawn when set.
  - `CLAUDE_CREW_TOOL_ARGS_DISABLED=1`: forces `args_summary: null` for all tools; redaction + cap don't run on args (still run on `error_summary`, which is always emitted).
  Read once at `SdkTeammate.__init__` (per-teammate snapshot), passed to the redactor's `summarize_args` call. *Carried into:* `sdk_teammate.py:__init__` env reads, `redaction.py` honoring both flags.

- **D8 (dict-semantics rules implementation).** Five guards inside `_on_pre_tool_use` and `_on_post_tool_use`:
  - **Null/empty `tool_use_id` defensive guard** *(co-arch pass-4)*: `if not tool_use_id: log.warning(...); return continue`. SDK contract says it's populated; we defend anyway, matching the redactor's degrade-open discipline.
  - **Pre-twice**: `if tool_use_id in self._tool_uses:` → log WARNING, last-write-wins. Replace `started_at_wallclock` and `args_summary`.
  - **Post-without-Pre, NOT recently closed**: `entry = self._tool_uses.pop(tool_use_id, None); if entry is None and tool_use_id not in self._recently_closed_tool_use_ids:` → log WARNING, emit a `tool_end` transcript line with null duration and `error_summary: "post fired without matching pre"`. No `last_tool_completed` mutation.
  - **Post-without-Pre, IS recently closed (late-Post-after-abandon)** *(sentinel A1 + co-arch pass-4 [DEFER 5] convergent):* `if tool_use_id in self._recently_closed_tool_use_ids:` → log INFO ("late post for closed tool"), suppress duplicate `tool_end` emission, no `last_tool_completed` mutation. Prevents D9's `tool_end(outcome="abandoned")` from being shadowed by a delayed `PostToolUse` firing a second `tool_end(outcome="ok")` for the same `tool_use_id`. Replay tooling joins by `tool_use_id` and sees exactly one `tool_end` per `tool_start`.
  - **Soft overflow**: `if len(self._tool_uses) >= MAX_CONCURRENT_TOOLS:` (default 64) → log WARNING with the new `tool_use_id` and current size, accept anyway. Constant configurable via `CLAUDE_CREW_TOOL_OVERFLOW_THRESHOLD` env var (deferred — only added if real usage hits it).

  **`_recently_closed_tool_use_ids: collections.deque[str]` (bounded LIFO, `maxlen=64`)** lives on `Teammate` base class alongside `_tool_uses`. Populated by `_close_open_tools` for every closed entry (deque auto-evicts oldest on overflow). Lookup is O(N) on a 64-element deque — fine; alternative is `OrderedDict` if profile shows it matters.

  *Carried into:* `_on_pre_tool_use` and `_on_post_tool_use` bodies (five guards), `Teammate` base class field, six dedicated tests in `test_sdk_teammate.py` covering each guard.

- **D9 (cleanup discipline at turn / death / kill — iteration-safe).** `_end_turn` and the broker's `_tombstone_teammate` (and `kill_teammate` path) each invoke `teammate._close_open_tools(reason: Literal["turn_end", "death", "kill"])` which:
  1. **Snapshot first** *(sentinel A2)*: `entries = list(self._tool_uses.items())`. Future-proofs against D4 ever turning async — Pre hooks queued behind a yield-point cannot mutate the dict mid-iteration.
  2. Iterate the **snapshot**, computing `outcome = "abandoned" if reason in {"turn_end", "death"} else "killed"`.
  3. For each entry: call `self._broker._sink.write_tool_event("tool_end", {...})` with `outcome`, `error_summary = f"tool was in flight when {reason} closed it"`, `duration_seconds = now - entry.started_at_wallclock`. AND append `tool_use_id` to `self._recently_closed_tool_use_ids` (D8 dedup gate).
  4. **`finally` block** *(sentinel A2)*: `self._tool_uses.clear()`. The clear runs even if a transcript write raises mid-iteration. Combined with D10's per-callsite try/except, this guarantees `_tool_uses == {}` post-call.
  5. Does NOT update `last_tool_completed` (only fully-bracketed Pre→Post pairs roll into that). SC-14.
  *Carried into:* new `Teammate._close_open_tools` method (snapshot + finally pattern explicit), callsites in `_end_turn`, `broker._tombstone_teammate`, `broker.kill_teammate`. Tests: `test_close_open_tools_iteration_safe`, `test_close_open_tools_emits_recently_closed_marker`.

- **D10 (hook callback degrade-open, three-layer).** Each callback body is wrapped in `try / except Exception: log.warning(...); return {"continue": True}`. The redactor is wrapped in its own `try / except: return None` (returns null `args_summary` rather than crashing the hook). The SDK's `HookMatcher.timeout=1.0` is the third layer — if the body hangs (catastrophic regex backtracking, slow disk on JSONL append), SDK skips the hook and proceeds. SC-8 three-part guarantee. *Carried into:* every hook callback body (template + comment), `tests/test_sdk_teammate.py::test_hook_raise_does_not_crash`, `test_hook_timeout_degrades_open`.

- **D11 (transcript fields diverge from status payload — Q9 resolution).** Both surfaces emit similar records, but they diverge at three fields:
  - Transcript `tool_end` carries `tool_use_id` (joins with `tool_start`); status `last_tool_completed` does NOT (the SDK id has no use to MCP consumers).
  - Transcript `tool_end` carries `outcome ∈ {ok, failed, interrupted, abandoned, killed}` (5 values); status `last_tool_completed.outcome ∈ {ok, failed, interrupted}` (3 values — abandoned/killed are post-mortem, not "completed" in the status sense).
  - Transcript `error_summary` is up to 256 chars (SC-15 cap); status `last_tool_completed.error_summary` is the same content (no second projection — keep the cap consistent so the redactor runs once). *Carried into:* `transcript.py::write_tool_event` field whitelist, `broker.get_teammate_status` payload builder.

- **D12 (broker MCP tools as first-class tool events — Phase 2 design pin from sentinel A4).** When the teammate calls our broker's MCP tools (`mcp__claude-crew__send_to`, `broadcast`, `get_messages`, `get_teammate_status`, `kill_teammate`, `get_transcript_path`), they fire Pre/Post hooks like any tool. They're included in `current_tools`, the transcript, and `last_tool_completed`. No filtering. **Rationale:** they're real activity signal ("teammate is sending a message right now"); excluding them creates a substrate-observability blind spot. **The recursive-self-observation concern** is bounded: a teammate calling `get_teammate_status(self.id)` reads its own status from the broker, but the read happens after the Pre hook fires, so the snapshot it sees includes itself in `current_tools`. Acceptable: the snapshot is honest. Tests assert this in `test_broker_mcp_tool_telemetry::test_teammate_querying_own_status_sees_itself_in_current_tools`. *Carried into:* no special-casing — broker MCP tool names match the same hook flow as builtin tools.

### Edge Cases

- **Empty turn (no tool calls).** Hook callbacks never fire. `_tool_uses` stays `{}`. `last_tool_completed` retains its prior value (or null on first turn). `_end_turn` clears nothing.

- **Tool call spanning turn boundary.** Per F6's contract, a turn ends when the SDK stream terminates (typically at `ResultMessage`). All Pre and Post hooks fire WITHIN the turn (Pre before stream's `ToolUseBlock`, Post before `ToolResultBlock`). A tool whose Post is somehow delayed past `ResultMessage` triggers D9's cleanup with `outcome: "abandoned"`. In practice this is the SDK quirk path, not normal flow.

- **Subprocess death mid-tool.** `_handle_teammate_death` (broker side) invokes `_close_open_tools("death")` before tombstoning. The transcript records the abandoned tool; `last_tool_completed` is untouched (it's lying to claim a tool finished that didn't). Tombstone status reads `current_tools = []`, `last_tool_completed = <prior>` if any.

- **`kill_teammate` mid-tool.** Same as death-mid-tool but `outcome: "killed"`. The kill path already emits `lifecycle: kill`; the abandoned-tool records emit just before it (single-writer ordering — kill lifecycle line is the last one).

- **Backstop interrupt during a tool call.** Per F6 SC-11, backstop calls `client.interrupt()`. SDK fires `PostToolUseFailure(is_interrupt=true)` for the active tool (most-likely path). Our hook records `outcome: "interrupted"` cleanly. If the SDK *doesn't* fire the failure hook (untested edge), `_end_turn` (which runs after backstop) hits the abandoned-tool path. Both paths converge on `current_tools = []`.

- **Pre fires twice for same tool_use_id.** Q7 spike showed this is uncommon in normal flow. If it happens (SDK retry / permission re-prompt re-fire — Q8 unresolved empirically), D8 last-write-wins keeps the record honest (latest start time) at the cost of replacing args_summary. WARNING log for visibility.

- **Post fires for unknown tool_use_id.** D8 no-op + WARNING + emit a `tool_end` line with null duration. The record exists for audit but doesn't pollute `last_tool_completed`.

- **Parallel tool calls in one assistant turn.** SDK supports it. `_tool_uses` is a dict so insertion order doesn't matter. `current_tool` returns the latest-started (last-started semantics). `current_tool_count > 1` signals concurrency. **PRE-PHASE-3 LIVE PROBE will validate this** (gate item).

- **Hook callback timeout (>1s).** SDK skips the hook, proceeds with the tool. `_tool_uses` doesn't get the entry. The Post hook may or may not fire (depends on SDK semantics); if it does, D8's "Post-without-Pre" path emits a tombstoned-tool transcript record. Acceptable degradation.

- **Hook callback raises.** Wrapped in try/except; logged at WARNING; returns continue-shape. Tool flow proceeds. Same observability outcome as timeout.

- **Redactor catastrophic-backtracking on adversarial input.** Wrapped in try/except inside `summarize_args`; returns null. Hook completes normally. WARNING log with the offending tool_name (NOT the input — that's what we suspect of being malicious). Pattern set is reviewed for catastrophic-backtracking shapes in Phase 4 implementation.

- **`tool_input` containing types that don't JSON-serialize.** Per-tool extractors handle this — they read specific keys (`command`, `description`, `url`) which are always strings. The fallback (`CLAUDE_CREW_TOOL_ARGS_FULL=1`) uses `json.dumps(default=str)` to coerce.

- **`error_summary` containing the secret it's reporting on.** SC-15 unconditional redaction handles this. Test fixture: `Bash` exits with `stderr: "Bearer sk-ant-xxx invalid"` → `error_summary: "Bearer <redacted-key> invalid"` (or `<redacted>` on the surrounding pair).

- **Subagent tool that's slow.** Activity stamps fire (D3 still stamps for subagent). `current_tools` doesn't track it (D3 boundary). `idle_seconds` stays honest. F7 will own the surface.

- **Teammate process restart (not in v1; flagged for completeness).** Today, `SdkTeammate` doesn't restart its underlying SDK process. If we ever do, `_tool_uses` is per-instance — gets reset on construction.

- **Hook firing in the window between tombstone and subprocess termination** *(co-arch pass-4 fresh observation)*. Sequence: broker death-handler runs `_close_open_tools` → tombstones the teammate → SDK subprocess hasn't fully died yet → a stray Pre hook fires on the still-instantiated teammate object. Mutates `_tool_uses` on a tombstoned teammate; D4's transcript write hits the (possibly closed) sink. Mitigated by D10 (try/except wraps the sink write — closed sink raises, gets swallowed) and instance-level GC (the orphan dict entry gets collected when the teammate object is). The window is microseconds in practice. **Acceptable.** No structural fix — the cost (rare orphaned dict mutation that GCs) is dwarfed by adding broker-state checks to every hook callback.

- **PermissionRequest gating fires for restricted tools** *(spike-resolved this session, PA7 substantiated)*. With `setting_sources=[]` and a `can_use_tool` callback installed, neither `PermissionRequest` hook nor `can_use_tool` callback fired in our SDK invocation pattern. PreToolUse → PostToolUse remained the canonical flow. The failure mode co-architect feared (PermissionRequest fires *before* PreToolUse, leaving a permission-stuck tool with `current_tools=[]`) appears structurally unreachable in claude-crew's deployment. **Accepted blind spot:** if a future configuration change exposes us to permission-gated tools (multi-tenant deployments, restricted ACLs), revisit. Documented in PA7.

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| **SDK → hook callback** | SDK has fired hook event with valid `HookInput` typed dict | Callback raises → SDK timeout (1s) → SDK proceeds with tool | Callback returns `{"continue": True}` | None — observation only |
| **Hook callback → `_tool_uses` dict** | `agent_id is None` (D3 gate); `tool_use_id` is non-empty string | Pre-twice → last-write-wins + WARNING; overflow → WARNING + accept | Dict mutated atomically (single-loop) | Cleared by `_end_turn`/death/kill paths (D9) |
| **Hook callback → broker transcript sink** | Broker's `_sink` is initialized; broker is alive | Sink raises (disk full, fd closed) → log WARNING, swallow | Transcript line appended | None — best-effort write |
| **Hook callback → redactor** | `tool_input` is dict-shaped (SDK guarantee) | Redactor raises → return null `args_summary`; hook continues | Output is bounded ≤256 bytes, redacted | None — null is the safe degradation |
| **`_end_turn` → cleanup** | Turn is ending (success path or backstop) | `_close_open_tools` raises → log WARNING, force-clear `_tool_uses` anyway | `_tool_uses == {}` regardless | Always runs in `finally` block of `_handle_one_turn` |
| **Broker death path → cleanup** | Liveness poll has detected death | `_close_open_tools` raises → log WARNING, force-clear anyway | `_tool_uses == {}`; tombstone written | Tombstone is the rollback (subsequent reads see `alive: false`) |

### Specification

The substrate registers four hooks on every `SdkTeammate` (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`; `PermissionRequest` is informational-only and unused in v1 — Q6/Q8 lean). Each hook callback:

1. Wraps its body in `try/except Exception` (D10 layer 1).
2. Always returns `{"continue": True}` (SC-8.1 observation-only).
3. Stamps activity (`self._stamp_activity()`) at the very top — before any branch — so subagent tool calls (which return early) still advance idle.
4. Branches on `inp.agent_id`:
   - **Main agent (`agent_id is None`):** proceeds to dict mutation + transcript emission (D3, D4).
   - **Subagent:** returns continue-shape immediately. Activity already stamped.

`PreToolUse` (main agent only) creates a `_ToolUseEntry`, applies dict-semantics rules (D8), runs `summarize_args` (D6), emits `kind: "tool_start"`. `PostToolUse` and `PostToolUseFailure` pop the entry, build `last_tool_completed`, run `redact_error` if applicable, emit `kind: "tool_end"`. The differences between Post and PostFailure are the `outcome` value and the presence of `error_summary`.

`_end_turn`, `_handle_teammate_death`, and `kill_teammate` all invoke `_close_open_tools(reason)` which emits one abandoned/killed `tool_end` per still-open entry, then clears the dict (D9).

`get_teammate_status` reads the dict + `last_tool_completed` + `redaction_version` and adds them to the existing F6 payload (additive — D11).

`session_id` on `client.query` becomes `f"{crew_id}-{teammate_id}"` (D5), incidentally fixing the latent `default.jsonl` race.

Redactor lives in a new `claude_crew/redaction.py` module (D6) with a versioned pattern set and per-tool extractor registry. The version string `redaction_version: "v1"` ships in every transcript line and status payload, so future bumps are auditable.

### Phase 2 Assumptions (default-accept)

These are judgment calls Phase 2 made with a sensible default. Default-accept semantics — silence is agreement. Call out any that are wrong before Phase 3.

- **PA1 (single-loop invariant — broker, all teammates, all hooks share one asyncio event loop).** *(Sentinel A3 tightening.)* The load-bearing assumption is broader than "hooks fire on the same loop as receive_response for ONE teammate." It is: **the broker, every spawned `SdkTeammate`'s `_run` task, every hook callback, and every `_sink.write_tool_event` call all execute on a single asyncio event loop.** Cross-teammate concurrent `write_tool_event` calls from N teammates' hooks are serialized by cooperative scheduling. *Default:* no explicit lock anywhere on `_tool_uses`, `_recently_closed_tool_use_ids`, or `_sink._fp.write`. *Risk if wrong:* if a future feature ever spawns a teammate on a separate loop or thread, `broker._sink.write_tool_event` becomes multi-writer without a lock and the JSONL file is corruptible. **Builder must not break the single-loop invariant.** Anchor: `Broker.spawn_teammate` (broker.py) creates the teammate's `_run` task via `asyncio.create_task` on the broker's loop — verify in code review for any future change.

- **PA2 (broker.\_sink is a stable handle for the teammate's lifetime).** F4's TranscriptSink is constructed in `Broker.__init__` and lives for broker lifetime. *Default:* hook callbacks can dereference `self._broker._sink` without nullity checks (assert at attach time only). *Risk if wrong:* hooks that fire during broker shutdown crash. Mitigated by D10's try/except.

- **PA3 (overflow cap of 64 concurrent tools is generous enough).** *Default:* hard-coded to 64. Configurable via env var only if real usage hits it. *Risk if wrong:* a future feature exercises >64 parallel tools and triggers WARNING storm. Mitigated by env var escape valve, not a real risk in practice.

- **PA4 (`tool_input` for `Bash` always has a `command` key).** Per Claude Code's tool spec, yes. *Default:* per-tool extractor reads `tool_input["command"]` directly (KeyError lands in the redactor's try/except → null summary). *Risk if wrong:* shrug — null summary is the safe degradation.

- **PA5 (parallel-tool live probe pre-Phase-3 will pass).** D8's overflow cap, D11's dict-shaped current_tools, and the dict's last-started semantics are all designed for parallel. *Default:* design proceeds. *If probe surfaces something different* (e.g., SDK serializes tool calls inside a single assistant turn), SC-9's parallel-handling reduces to "documented but never observed" — design still works, just over-engineered.

- **PA6 (per-tool extractors for v1 allowlist won't surprise us).** Bash's `command`, Task's `subagent_type`+`description`, WebFetch's `url`+`prompt` — all are documented Claude Code tool inputs. *Default:* extractors hard-coded against these field names. *If SDK adds/renames a field*, the extractor returns the field that exists or null on KeyError (degrade-open).

- **PA7 (`PermissionRequest` interleave with PreToolUse — empirically substantiated this session).** Two probes done:
  - Q7/Q8 spike (initial): with `setting_sources=["user", "project"]`, neither PermissionRequest nor can_use_tool fired for Bash (auto-allowed). PreToolUse → PostToolUse is the canonical flow.
  - PermissionRequest probe (co-arch's pushback): with `setting_sources=[]` and a `can_use_tool` callback installed (allow + deny variants), STILL no PermissionRequest hook fire and no `can_use_tool` invocation. The failure mode — PermissionRequest fires *first*, PreToolUse only on grant — appears structurally unreachable in claude-crew's SDK invocation pattern.
  *Default:* design treats PermissionRequest as informational-only with `current_tools` driven exclusively by PreToolUse. *If wrong* (some future SDK config we don't yet exercise triggers permission gating), the substrate would briefly show `current_tools=[]` while a permission-gated tool stalls on approval — exactly the F6 S1-dishonesty failure mode. **Accepted with empirical evidence**: not blind. Revisit only if a real-task run surfaces a permission-gated tool with weird semantics.

### Open Questions

None blocking Phase 3. Q4, Q5, Q6 from Phase 1 are resolved by their leans (carried into D6/D7 design). Q7 spike confirmed normal-case behavior (resolved). Q8 partially resolved (no permission triggered in spike); design treats PermissionRequest as informational. Q9 resolved by D11.

**One late-binding question for Jerome before Phase 3 task breakdown:**

- [ ] **Q-PA7-confirm:** PA7 accepts that `PermissionRequest` interleave with PreToolUse remains empirically untested for restricted-tool case. We have a workaround (PreToolUse remains the canonical marker), and the spike showed no permission gating fires for Haiku's Bash in default mode. **Lean: ship as designed; revisit if a real-task run surfaces a permission-gated tool with weird `current_tools` semantics.** Do you want a longer Q8 spike (find a tool we explicitly restrict and re-probe), or accept PA7?

**Gate**:
- ✅ Phase 1 re-read in main session (SCs, Questions, Constraints fresh in context)
- ✅ Gathering buckets identified and delegated to 3 parallel Haiku Explores (consumer inventory, transcript schema/write path, hook attach + ProgrammableSDKClient)
- ✅ Q7/Q8 Phase 2 spike done (Pre fired exactly once for Bash; PermissionRequest didn't fire in default mode — PA7 accepts)
- ✅ Phase 2 synthesized in main session (architecture, contracts, 12 design decisions, edge cases, validation contracts, specification)
- ✅ **Pre-Phase-3 parallel-tool live probe — done.** SDK fires `Pre→Pre→Post→Post` (interleaved by `tool_use_id`, not strictly nested). Two `Bash` calls within one assistant turn produced two distinct `tool_use_id`s with 1 Pre + 1 Post each. Per-tool_use_id dict legitimately holds 2 entries simultaneously for ~63ms in this run. SC-9's list-shaped payload + D8's dict are exactly the right model — empirically validated, not theoretical. Spike script at `/tmp/parallel_tool_spike.py`.
- ✅ **PermissionRequest probe — done.** Two variants probed (with and without setting_sources, with and without can_use_tool callback). In every variant, PermissionRequest hook did NOT fire, can_use_tool did NOT invoke; PreToolUse → PostToolUse remained canonical. PA7 substantiated empirically. Failure mode (PermissionRequest first, PreToolUse on grant) structurally unreachable in our SDK invocation pattern.
- ✅ Co-architect-f8 review of Phase 2 — done (4 [FIX-NOW]: D6 redaction enumeration, D4 wording, null tool_use_id guard, duplicate tool_end gap; all folded). Plus PA7 strengthening via the spike co-architect requested.
- ✅ Sentinel-f8-p1 review of Phase 2 — done (4 [FIX-NOW]: duplicate tool_end gap (convergent with co-arch), iteration safety, PA1 cross-teammate tightening, _ToolUseEntry pinning; all folded).
- ⏳ Jerome confirms Phase 2 → Phase 3

---

## Phase 3: Task Breakdown

Five tasks. T1 and T2 can run in parallel (no dependencies). T3 depends on T1+T2. T4 depends on T3. T5 depends on T4. Mirrors F6's by-layer shape.

**Pre-Phase-3 contract-change grep audit (F6 retro process pin — done this session):**
```bash
grep -rn "get_teammate_status\|session_id=.default.\|TranscriptSink\|status_snapshot" claude_crew/ tests/
```
Files surfaced as needing updates:
- `tests/test_broker.py:350-357` and `tests/test_server.py:237-243` — assertions on `get_teammate_status` field set; will need new keys added but use `in`/`==` patterns (additive-safe).
- `tests/test_sdk_teammate.py` — any test asserting `session_id="default"` will fail when SC-16 ships. Builder updates these in T3.
- `tests/test_transcript.py` — schema tests; T2 adds tool_start/tool_end coverage.

No surprise contract collisions identified. Phase 3 task scope is clean.

---

### Task 1: Redaction module (new file)
**Depends on**: None | **Blocks**: T3, T5

Pure new module `claude_crew/redaction.py` + `tests/test_redaction.py`. No dependencies on the rest of the feature. Can run in parallel with T2.

**Implements**: D6 (versioned constant + per-tool extractor registry + redact-then-cap order), SC-15 (redaction patterns + allowlist + env-var overrides).

**Module shape:**
- `REDACTION_VERSION: str = "v1"`
- `REDACTION_PATTERNS_V1: list[tuple[re.Pattern, str]]` — exact pattern set enumerated in D6.
- `ALLOWLIST_V1: dict[str, Callable[[dict], str]]` — `Bash` / `Task` / `WebFetch` extractors.
- `summarize_args(tool_name, tool_input) -> str | None` — extract → redact → cap (256 bytes); honors `CLAUDE_CREW_TOOL_ARGS_FULL` and `CLAUDE_CREW_TOOL_ARGS_DISABLED`; never raises (returns None on internal failure).
- `redact_error(error_text) -> str` — unconditional redact + cap.

**Acceptance Criteria** (BDD scenarios):

```
Scenario: Bash command summarized with redaction (SC-15 utility)
  Given REDACTION_PATTERNS_V1 active and Bash on the v1 allowlist
  When summarize_args("Bash", {"command": "pytest tests/ -v"}) is called
  Then the result is "command=pytest tests/ -v"

Scenario: Bash command with literal Bearer token redacted (SC-15)
  When summarize_args("Bash", {"command": "curl -H 'Authorization: Bearer sk-ant-abc123def456ghi789jkl012mno345pqr678'"})
  Then the result contains "<redacted-key>" or "<redacted>"
  And the result does NOT contain "sk-ant-abc123"

Scenario: Bash command with shell variable is safe by literal (SC-15, A1)
  When summarize_args("Bash", {"command": "curl -H 'Authorization: Bearer $TOKEN'"})
  Then the result contains "$TOKEN" literal (pre-shell-substitution)
  And the result does NOT contain "<redacted>" for $TOKEN itself (the Authorization line redacts the pair though)

Scenario: AKIA AWS access key redacted (SC-15 anchored shape)
  When summarize_args("Bash", {"command": "aws s3 cp foo bar # AKIAIOSFODNN7EXAMPLE in heredoc"})
  Then the result contains "<redacted-key>"
  And the result does NOT contain "AKIAIOSFODNN7EXAMPLE"

Scenario: URL with embedded credentials redacted (SC-15)
  When summarize_args("Bash", {"command": "git push https://user:tok123@host/repo"})
  Then the result contains "https://<redacted>@"

Scenario: Short-flag secret redacted (SC-15)
  When summarize_args("Bash", {"command": "mysql -p hunter2 -u admin"})
  Then the result contains "<redacted-flag>"
  And the result does NOT contain "hunter2"

Scenario: Read tool not on allowlist returns null (SC-15)
  When summarize_args("Read", {"file_path": "/etc/passwd"})
  Then the result is None

Scenario: Task tool extracts subagent_type and description, NOT prompt body (SC-15)
  When summarize_args("Task", {"subagent_type": "researcher", "description": "find auth bug", "prompt": "Look at auth/login.py and identify the deserialization vuln. Use sk-ant-... if you need it."})
  Then the result contains "subagent=researcher" and "description=find auth bug"
  And the result does NOT contain "Look at auth/login.py" (prompt body excluded)
  And the result does NOT contain "sk-ant"

Scenario: Output capped at 256 bytes
  When summarize_args("Bash", {"command": "echo " + ("A" * 1000)})
  Then len(result.encode("utf-8")) <= 256
  And result.endswith("…")

Scenario: CLAUDE_CREW_TOOL_ARGS_DISABLED forces null (SC-15)
  Given CLAUDE_CREW_TOOL_ARGS_DISABLED=1 in env
  When summarize_args("Bash", {"command": "pytest"})
  Then the result is None

Scenario: CLAUDE_CREW_TOOL_ARGS_FULL widens allowlist (SC-15)
  Given CLAUDE_CREW_TOOL_ARGS_FULL=1 in env
  When summarize_args("Read", {"file_path": "/etc/passwd"})
  Then the result is not None
  And the result contains "/etc/passwd"

Scenario: Redactor never raises (SC-12 + SC-15)
  Given a malformed tool_input that triggers an internal exception
  When summarize_args("Bash", malformed_input) is called
  Then the result is None
  And no exception propagates

Scenario: redact_error applies unconditionally (SC-15 error_summary clause)
  When redact_error("Authentication failed: token sk-ant-abc123def456ghi789jkl012mno345")
  Then the result contains "<redacted>"
  And the result is at most 256 bytes
```

**Verification**: `cd ~/dev/claude-crew && uv run pytest tests/test_redaction.py -v` — all scenarios pass; baseline run before T1 fails (file doesn't exist).

---

### Task 2: Teammate base class + transcript writer extension
**Depends on**: None | **Blocks**: T3, T4, T5

Two surgical changes that share the "model layer" — no SDK integration yet.

**T2a: `claude_crew/teammate.py`**
- Add `_ToolUseEntry` frozen dataclass (Data Contracts shape).
- On `Teammate` base class: add `_tool_uses: dict[str, _ToolUseEntry] = {}` and `_recently_closed_tool_use_ids: collections.deque[str]` (`maxlen=64`). Initialize in both `Teammate.__init__` paths (StubTeammate + SdkTeammate).
- New method `_close_open_tools(self, reason: Literal["turn_end", "death", "kill"])`: snapshot via `list(self._tool_uses.items())`, iterate snapshot emitting `tool_end` records via `self._broker._sink.write_tool_event(...)`, append each tool_use_id to `_recently_closed_tool_use_ids`, `clear()` in `finally`. Does NOT update `last_tool_completed`.
- Extend `status_snapshot()` (the helper read by `Broker.get_teammate_status`) to include `current_tools`, `current_tool`, `current_tool_count`, `last_tool_completed`, `redaction_version`. (D11 projection: `last_tool_completed` is a 5-key dict, NOT containing `tool_use_id`.)

**T2b: `claude_crew/transcript.py`**
- Add `TranscriptSink.write_tool_event(self, event: Literal["tool_start","tool_end"], fields: dict[str, Any])` — mirrors `write_lifecycle` shape. Synchronous, best-effort, line-buffered append.

**Acceptance Criteria** (BDD scenarios):

```
Scenario: _close_open_tools emits tool_end for each in-flight entry (SC-14)
  Given a teammate with two _tool_uses entries (Bash + Task, both started 5s ago)
  When _close_open_tools(reason="turn_end") is called
  Then two "tool_end" transcript lines are written (one per entry)
  And each line has outcome="abandoned" and duration_seconds≈5.0
  And _tool_uses is empty afterwards
  And both tool_use_ids are in _recently_closed_tool_use_ids

Scenario: _close_open_tools with reason="kill" emits outcome="killed" (SC-14)
  Given a teammate with one _tool_uses entry
  When _close_open_tools(reason="kill") is called
  Then the tool_end transcript line has outcome="killed"

Scenario: _close_open_tools is iteration-safe under mid-iteration mutation (sentinel A2)
  Given a teammate with three _tool_uses entries
  When _close_open_tools is called and the transcript writer hypothetically mutates _tool_uses mid-iteration (simulated via spy)
  Then iteration completes against the original snapshot
  And _tool_uses is empty afterwards (cleared in finally)

Scenario: _close_open_tools clears _tool_uses even if write_tool_event raises
  Given a teammate with one _tool_uses entry and a transcript writer that raises on every call
  When _close_open_tools is called
  Then the exception is logged at WARNING (not propagated)
  And _tool_uses is empty afterwards (finally runs)

Scenario: status_snapshot reports empty current_tools when no Pre fired
  When status_snapshot() is called on a teammate with empty _tool_uses
  Then current_tools == [], current_tool is None, current_tool_count == 0

Scenario: status_snapshot reports last-started semantics for current_tool (SC-9)
  Given _tool_uses contains entries A (started t=10) and B (started t=15)
  When status_snapshot() is called
  Then current_tool == B.tool_name
  And current_tool_count == 2
  And current_tools is a list with both entries, B last

Scenario: TranscriptSink.write_tool_event appends a JSONL line (SC-5)
  When write_tool_event("tool_start", {"teammate_id": "t-x", "tool_name": "Bash", ...}) is called
  Then the transcript file gains one line
  And that line is valid JSON with kind="tool_start", v=1, ts present, crew_id present

Scenario: status_snapshot includes redaction_version
  When status_snapshot() is called
  Then the result contains "redaction_version": "v1"
```

**Verification**: `cd ~/dev/claude-crew && uv run pytest tests/test_teammate.py tests/test_transcript.py -v` — all new + existing scenarios pass.

---

### Task 3: SdkTeammate hook integration + ProgrammableSDKClient extension
**Depends on**: T1, T2 | **Blocks**: T4, T5

Wires the SDK hook callbacks into `SdkTeammate`, adds session_id (D5), and extends `ProgrammableSDKClient` to drive hooks in tests.

**T3a: `claude_crew/sdk_teammate.py`**
- Three bound-method callbacks: `_on_pre_tool_use`, `_on_post_tool_use`, `_on_post_tool_use_failure`. Each:
  1. Wraps body in try/except (D10 raise-safe).
  2. Stamps activity at top of body, before any branch (so subagent path also stamps).
  3. Branches on `inp.get("agent_id") is None` (D3 main-vs-subagent gate); subagent path returns continue-shape after activity stamp.
  4. For main-agent path: applies D8's five guards (null tool_use_id defensive, Pre-twice last-write-wins, Post-without-Pre with recently-closed dedup, soft overflow), updates `_tool_uses`, populates `last_tool_completed` on Post (success/failure outcome split), calls `summarize_args` (T1) + `redact_error` (T1) as appropriate, calls `self._broker._sink.write_tool_event(...)`.
- In `_run` at line 264: register all three hooks with `HookMatcher(timeout=1.0)`. Add `hooks=` to `opts_kwargs`.
- At line 306: replace `session_id="default"` with `session_id=f"{self._broker.crew_id}-{self.id}"` (D5).
- Read `CLAUDE_CREW_TOOL_ARGS_FULL` and `CLAUDE_CREW_TOOL_ARGS_DISABLED` env vars in `__init__`, pass to `summarize_args` calls.

**T3b: `tests/fakes/programmable_sdk_client.py`**
- Add `_hooks: dict[str, list[Callable]] = {}` field.
- Capture hooks from `ClaudeAgentOptions.hooks` on construction (or via test-injected setter).
- Add `async def fire_hook(self, event_name: str, hook_input: dict, tool_use_id: str | None = None)` method that invokes registered callbacks with the input.
- Tests call `fire_hook("PreToolUse", {...})` to drive the substrate without a live SDK.

**Acceptance Criteria** (BDD scenarios):

```
Scenario: PreToolUse populates current_tools (SC-1)
  Given a SdkTeammate with hooks attached and ProgrammableSDKClient
  When fire_hook("PreToolUse", {agent_id: None, tool_name: "Bash", tool_use_id: "tu-1", tool_input: {"command": "pytest"}})
  Then status_snapshot.current_tools has one entry
  And current_tools[0].tool_name == "Bash" and tool_use_id == "tu-1"
  And current_tools[0].args_summary contains "command=pytest" (Bash on allowlist)

Scenario: PostToolUse clears current_tools and sets last_tool_completed (SC-2)
  Given current_tools has one entry tu-1 started at t=10
  When fire_hook("PostToolUse", {tool_name: "Bash", tool_use_id: "tu-1", tool_response: ...}) at t=15
  Then current_tools is empty
  And last_tool_completed == {tool_name: "Bash", outcome: "ok", duration_seconds: 5.0, finished_at_wallclock: 15.0, error_summary: None}

Scenario: PostToolUseFailure with is_interrupt=true → outcome="interrupted" (SC-3)
  When fire_hook("PostToolUseFailure", {tool_name: "Bash", tool_use_id: "tu-1", error: "interrupted by user", is_interrupt: true})
  Then last_tool_completed.outcome == "interrupted"
  And last_tool_completed.error_summary is the redacted+capped form of the error

Scenario: PostToolUseFailure with is_interrupt=false → outcome="failed" (SC-3)
  When fire_hook("PostToolUseFailure", {tool_name: "Bash", tool_use_id: "tu-1", error: "exit 1", is_interrupt: false})
  Then last_tool_completed.outcome == "failed"

Scenario: Hooks stamp activity (SC-4)
  Given last_activity_at_wallclock is at t=10
  When fire_hook("PreToolUse", ...) at t=20
  Then last_activity_at_wallclock advances to ~20

Scenario: Subagent tool call stamps activity but does NOT touch current_tools (SC-10, D3)
  When fire_hook("PreToolUse", {agent_id: "sub-1", agent_type: "echo-runner", tool_name: "Bash", tool_use_id: "tu-sub-1"})
  Then last_activity_at_wallclock advances
  And current_tools is empty
  And NO transcript line was written

Scenario: Pre-twice for same tool_use_id → last-write-wins + WARNING (SC-9, D8)
  When fire_hook("PreToolUse", {tool_use_id: "tu-1", started_at: 10}) then fire again at 12 (same tu)
  Then current_tools has one entry with started_at_wallclock = 12
  And a WARNING was logged

Scenario: Post for unknown tool_use_id NOT recently closed → no-op + WARNING + audit transcript line (SC-9, D8)
  When fire_hook("PostToolUse", {tool_use_id: "tu-unknown"})
  Then current_tools is unchanged
  And last_tool_completed is unchanged
  And a WARNING was logged
  And one tool_end transcript line was written with error_summary mentioning "post fired without matching pre"

Scenario: Post for recently-closed tool_use_id (late-Post-after-abandon) → suppressed + INFO (SC-9, D8 fifth guard)
  Given tu-1 was abandoned by _close_open_tools and is in _recently_closed_tool_use_ids
  When fire_hook("PostToolUse", {tool_use_id: "tu-1"}) (delayed Post arrival)
  Then NO duplicate tool_end transcript line is written
  And last_tool_completed is unchanged
  And an INFO log was emitted ("late post for closed tool")

Scenario: Hook callback raise does not crash teammate (SC-8.2)
  Given a hook callback that raises by injection
  When the SDK fires the hook
  Then the turn completes normally
  And a WARNING is logged

Scenario: Hooks attached with timeout=1.0 (SC-8.3, D1)
  When SdkTeammate._run is invoked
  Then ClaudeAgentOptions.hooks contains HookMatcher entries with timeout=1.0

Scenario: session_id uses crew-teammate format (SC-16, D5)
  When SdkTeammate._handle_one_turn runs and calls client.query
  Then session_id == f"{broker.crew_id}-{self.id}"
  And session_id != "default"
```

**Verification**: `cd ~/dev/claude-crew && uv run pytest tests/test_sdk_teammate.py -v` — all new + existing scenarios pass; existing tests asserting `session_id="default"` are updated to the new format.

---

### Task 4: Broker integration + MCP tool surface extension
**Depends on**: T3 | **Blocks**: T5

Wires `_close_open_tools` into the broker's death/kill paths and extends the MCP `get_teammate_status` tool to return the new fields.

**T4a: `claude_crew/broker.py`**
- In `_handle_teammate_death` (the F6 death-detection path that emits `lifecycle: died`): before tombstoning, call `teammate._close_open_tools(reason="death")`.
- In `kill_teammate`: before emitting `lifecycle: kill`, call `teammate._close_open_tools(reason="kill")`.
- In `Broker.get_teammate_status`: include the new fields from `status_snapshot()` (T2a) in the alive payload AND in the tombstoned payload (last_tool_completed survives into tombstone; current_tools is empty post-`_close_open_tools`).

**T4b: `claude_crew/server.py`**
- The MCP tool `get_teammate_status` already delegates to broker (no signature change). Update its docstring to reflect the new fields.

**Acceptance Criteria** (BDD scenarios):

```
Scenario: Death mid-tool emits abandoned tool_end before tombstone (SC-14, D9)
  Given a teammate with one _tool_uses entry (Bash, started 5s ago)
  When the SDK subprocess dies and _handle_teammate_death runs
  Then transcript contains a tool_end line with outcome="abandoned" duration≈5.0
  And the tool_end line precedes the lifecycle: died line
  And subsequent get_teammate_status returns alive=false, current_tools=[]

Scenario: kill_teammate mid-tool emits killed tool_end before lifecycle: kill (SC-14, D9)
  Given a teammate with two _tool_uses entries
  When kill_teammate(t-x) is called
  Then transcript contains two tool_end lines with outcome="killed"
  And both precede the lifecycle: kill line
  And subsequent get_teammate_status returns alive=false, current_tools=[]

Scenario: Tombstoned teammate retains last_tool_completed (D11)
  Given a teammate completed Bash cleanly (last_tool_completed populated), then died mid-Task
  When get_teammate_status returns the tombstoned record
  Then last_tool_completed reflects the cleanly-finished Bash (NOT the abandoned Task)
  And current_tools == []

Scenario: get_teammate_status on alive teammate includes new fields (SC-7, D11)
  When get_teammate_status(t-x) is called on an alive teammate mid-tool
  Then the returned dict includes current_tools, current_tool, current_tool_count, last_tool_completed, redaction_version
  And all F6 fields (alive, idle_seconds, etc) are still present

Scenario: get_teammate_status on unknown teammate returns existing error shape (no regression)
  When get_teammate_status("does-not-exist") is called
  Then the returned dict matches F6's error shape (additive new fields don't change error path)

Scenario: Broker MCP tool calls fire hooks like any tool (D12)
  When a teammate calls mcp__claude-crew__send_to via Task/builtin tool
  Then PreToolUse fires with tool_name="mcp__claude-crew__send_to"
  And current_tools includes that call until PostToolUse fires
```

**Verification**: `cd ~/dev/claude-crew && uv run pytest tests/test_broker.py tests/test_server.py -v` — all new + existing scenarios pass.

---

### Task 5: End-to-end integration tests + live SDK A2 probe
**Depends on**: T4 | **Blocks**: Documentation / Phase 5

Cohesive tests that exercise the full feature pipeline through the public MCP API. Plus one live SDK probe to confirm the hook-based telemetry actually fires end-to-end against a real subprocess.

**Happy Path Scenarios:**

```
Scenario: Lead observes a real Bash call mid-execution via get_teammate_status
  Given a SdkTeammate spawned via the broker
  When the teammate executes a Bash command that takes 2s
  And the lead calls get_teammate_status mid-execution (at ~1s)
  Then current_tool == "Bash"
  And current_tool_count == 1
  And current_tools[0].args_summary contains "command="
  And idle_seconds is < 1 (activity stamped at PreToolUse)

Scenario: Parallel tool calls within one assistant turn (SC-9 — empirically observed shape)
  Given a teammate that issues two parallel Bash calls in one assistant turn
  When both Pre hooks fire before either Post (Pre→Pre→Post→Post pattern)
  Then current_tool_count == 2 mid-flight
  And both tool_use_ids are tracked
  And both tool_end lines are eventually emitted with distinct tool_use_ids

Scenario: Subagent Bash call appears in transcript (NO — SC-10 boundary)
  Given a teammate that dispatches a subagent which itself calls Bash
  When the subagent's Bash runs
  Then the parent teammate's idle_seconds stays low (activity stamped — SC-4)
  And the parent teammate's current_tools is empty during the subagent's Bash
  And NO tool_start line is written for the subagent's Bash call (#7's territory)
```

**Sad Path Scenarios:**

```
Scenario: Teammate dies mid-Bash → abandoned tool_end + lifecycle: died (SC-14 E2E)
  Given a teammate executing a long Bash command
  When the subprocess dies (simulated via process kill in the test fixture)
  Then transcript contains tool_end (outcome="abandoned") for the Bash tool_use_id
  And transcript contains lifecycle: died after the tool_end
  And get_teammate_status returns alive=false with current_tools=[] and last_tool_completed reflecting any prior cleanly-finished tool

Scenario: Operator kills teammate mid-Bash (SC-14 E2E)
  Given a teammate executing a long Bash command
  When kill_teammate is called
  Then transcript contains tool_end (outcome="killed") preceding lifecycle: kill
  And the Bash subprocess inside the SDK is terminated

Scenario: Adversarial redaction round-trip (SC-15 full-stack)
  Given a teammate that runs `echo "Bearer sk-ant-FAKE_DUMMY_TOKEN_FOR_TEST_abc123"` via Bash
  When the tool_start transcript line is written
  Then args_summary contains "<redacted>" (or token-shape redaction)
  And args_summary does NOT contain the literal "FAKE_DUMMY_TOKEN_FOR_TEST_abc123"
```

**Live-probe checklist:**
- [x] No assertion on token counts or workload-sensitive values.
- [x] Tool-name correctness verified by observable side effect (transcript line content), not by the agent's narration.
- [x] Test plants nothing — uses adversarial-but-fake patterns; redactor's job is to catch them.

**Live SDK A2 probe** (gated behind `pytest -m live_sdk` like F6's pattern):
- One real `ClaudeSDKClient` driven through a `Bash` call (echo a marker).
- Asserts that hooks fired (test stamps captured the Pre/Post events).
- Asserts that the transcript file gained one `tool_start` and one `tool_end` line with matching tool_use_id.
- Asserts that `get_teammate_status` mid-call (best-effort timing) reflects the active Bash.
- Estimated cost: <$0.10 per run (one Haiku call, single tool).

**Verification**: `cd ~/dev/claude-crew && uv run pytest tests/test_e2e_tool_telemetry.py -v` (non-live); `pytest -m live_sdk tests/test_e2e_tool_telemetry.py::test_live_a2_probe -v` (live, opt-in).

---

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task with happy and sad path coverage + live SDK A2 probe
- ✅ Verification commands fail without the feature (T1 file doesn't exist, T2 method doesn't exist, T3 hooks not registered, etc.)
- ✅ Each Phase 2 edge case traces to at least one BDD scenario (death-mid-tool → T4+T5 abandoned scenario; kill-mid-tool → T4+T5 killed scenario; parallel tools → T5 SC-9 scenario; redaction adversarial → T1+T5; subagent boundary → T3+T5; Pre-twice / Post-without-Pre / late-Post-after-abandon → T3 D8 scenarios)
- ✅ Cross-feature interaction has scenarios (broker MCP tools as first-class events → T4 D12 scenario)
- ⏳ Jerome approves Phase 3 plan
- ⏳ Implementation strategy chosen (Kael direct / team-build / deep-build)

*To be filled after Phase 2 gate.*

---

## Phase 4: Implementation

*To be filled.*

---

## Phase 5: Completion

*To be filled.*
