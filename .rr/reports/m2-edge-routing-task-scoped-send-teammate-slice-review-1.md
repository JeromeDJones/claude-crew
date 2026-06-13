# Slice Review: m2-edge-routing task=scoped-send-teammate (cycle 1 — RE-REVIEW)

**Verdict:** PASS
**Cycle:** 1 (re-review of REWORK; prior cycle-0 verdict was REQUEST-CHANGES)
**Task index:** 2 — owns AT#8, AT#9, AT#10 (+ the now-built `send_to` tool deliverable)

The single cycle-0 High (`slice.incomplete` — missing in-process `send_to` MCP tool) is
**RESOLVED**. All five required-fix sub-points confirmed. ATs green, non-regression holds.

---

## Cycle-0 High resolution — confirmed on all five points

1. **Tool registered.** `SdkTeammate._build_send_to_mcp_server()` (sdk_teammate.py:1304) uses
   `@sdk_tool("send_to", …, {"recipient": str, "payload": dict})` and returns
   `create_sdk_mcp_server(_SEND_TO_MCP_SERVER_NAME, tools=[send_to_impl])`. Wired into `_run()`
   at lines 1483–1501. ✓
2. **Handler delegates + surfaces rejection.** Handler calls
   `await broker.send_scoped(self_ref.id, args["recipient"], args.get("payload") or {})`
   (sdk_teammate.py:1336); `UnauthorizedEdgeError` → `{is_error: True, "send_to rejected: …"}`;
   `broker is None` → `{is_error: True, "broker not available"}`. ✓
3. **Production caller exists.** `grep -rn send_scoped claude_crew/` now returns the broker
   definition **plus** the production callsite at sdk_teammate.py:1336 — no longer tests-only.
   The cycle-0 "zero production callers" finding is reversed. ✓
4. **Green-suite gap closed.** `TestSendToToolRegistration` (5 stub-level tests) asserts server
   shape (`type=="sdk"`, name, `_send_to_tool.name=="send_to"`), handler delegation with exact
   args, unauthorized→error, broker-none→error, and the `mcp__<server>__send_to` id constant —
   all via `SdkTeammate.__new__` (no live SDK, no pack load). Live verification stays
   `CLAUDE_CREW_LIVE_TESTS`-gated per Out-of-Scope, as specified. ✓
5. **Out-edge gating sound — does NOT drop the tool for teammates that have out-edges.**
   `_has_out_edges = any(n.get("direction") == "out" for n in (self._neighbors or []))`
   (sdk_teammate.py:1487). When true, the `crew-send` server is added to `mcp_servers`, the tool
   id is unioned into `allowed_tools` (pre-approval), and into `tools` **only when the catalog is
   already restricted** (`if "tools" in opts_kwargs`) — correct, because an unrestricted catalog
   already surfaces MCP tools via `allowed_tools`, while a restricted `--tools` catalog must name
   the MCP tool explicitly or the model won't see it. Teammates with no out-edges (sink nodes /
   non-topology spawns) correctly receive no `crew-send` entry — this is what fixed the 14
   regressions, and it does not starve any out-edge teammate. ✓

The cycle-0 incoherence (teammate_prompt.py advertising `send_to` to a teammate with no such
tool) is now resolved: the advertised tool exists and is granted exactly when the prompt's
out-edges section is populated.

## Check 1 — Slice adherence (AT#8, AT#9, AT#10 + send_to deliverable)

- **AT#8** neighbor injection — retained from cycle 0, `TestNeighborInjection` green.
- **AT#9** scoped authorize/reject — `broker.send_scoped` + now the teammate-facing tool that
  invokes it; `TestScopedSendAuthorize` green.
- **AT#10** direct ping-pong off lead — `TestDirectPingPong` green.
- **send_to deliverable** — built, wired, stub-tested (above). The moat invariant ("every
  cross-teammate message routes through `broker.send_scoped`, no off-broker channel") now has a
  real teammate entry point, and it is the *only* one.

## Check 2 — Non-regression (independent re-run)

```
uv run pytest tests/test_scoped_send.py tests/test_edge_routing.py tests/test_circuit_breaker.py
→ 58 passed in 0.41s
```
22 slice tests (17 retained + 5 new) + 36 sibling tests, all green. Corroborates coordinator
ground-truth (scoped 22, siblings 36, full suite 1520/0). No sibling-task source regressions.

## Check 3 — Code-quality smoke (changed files)

- `_build_send_to_mcp_server` is clean: closure over `self_ref`, structured error returns, no
  traceback leakage to the model, `_send_to_tool` side-effect documented for test introspection.
- Injection block is minimal and correctly placed after `mcp_servers` assembly; dedup via
  `dict.fromkeys`; catalog-vs-allowlist distinction handled correctly.
- **Low (non-blocking):** `TestSendToToolRegistration` uses function-local imports
  (`from claude_crew.sdk_teammate import …`, `from unittest.mock import AsyncMock`) inside each
  test. CLAUDE.md's test conventions flag inline imports as a code smell ("Imports at module
  top"). Cosmetic, does not affect correctness or the verdict — backlog-worthy tidy.

## Invariant-1 adjudication (carried forward, not re-litigated)

`factories.py` outside declared `taskTouches` = `breakout.scope.under-declared` (**Info**) —
adjudicated cycle 0 as a legitimate minimal signature-thread for the `neighbors=` kwarg. Carried
forward per instruction; does not affect the verdict.

## Severity tally
No Critical, no High. One Low (inline test imports), one Info (carried-forward scope
under-declaration). Cycle-0 High resolved.

---

RR-VERDICT: PASS m2-edge-routing 1 /home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-scoped-send-teammate-slice-review-1.md
