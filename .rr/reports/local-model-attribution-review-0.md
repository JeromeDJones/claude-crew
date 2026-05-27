# Plan Review: local-model-attribution (cycle 0)

**Verdict:** PASS

**Spec:** `/home/jerome/dev/claude-crew/.rr-worktrees/local-model-attribution/.rr/specs/local-model-attribution.md`
**Reviewed:** 2026-05-26

## Summary

The spec cleanly diagnoses two bugs on the same surface (`sdk_teammate.py`) — zero-attribution of OpenAI-shape `usage` for local-backend teammates, and plugin-MCP leak through pack `tools:` allowlist — and proposes minimal, well-targeted fixes (dual-shape parse in `_extract_token_cost_from_rm`; `--strict-mcp-config` via SDK `extra_args` pass-through). All claims verified against codebase:

- SDK `extra_args: dict[str, str | None]` confirmed at `claude_agent_sdk/types.py:1476`; flag iteration at `subprocess_cli.py:340`. The `setdefault("extra_args", {})["strict-mcp-config"] = None` pattern will produce a bare `--strict-mcp-config` CLI flag as claimed.
- `_extract_token_cost_from_rm` confirmed nested in `_collect_response_text` at line 214; current logic reads only Anthropic keys (`input_tokens` + `cache_*`) per spec's bug description.
- `opts_kwargs["mcp_servers"]` write site at line 1297; `ClaudeAgentOptions(**opts_kwargs)` at 1304 — `extra_args` will flow through unchanged.
- `is_local` broker rule at `broker.py:775` matches spec's assertion (`bool(env) and "ANTHROPIC_BASE_URL" in env`).
- AT mapping: 9 ATs claimed exactly once across 5 tasks (1→e2e; 2,3,5,6→extract-helper; 4→thread-is-local; 7→non-regression; 8→e2e; 9→strict-mcp).

No Critical or High findings. Architecture doc absent — not blocking for this slice.

## Findings

### Critical

_None identified._

### High

_None identified._

### Medium

- [MEDIUM-01] `spec.architecture.call-site-undercount` — Architecture Overview / Call-site survey: The Call-site survey table lists `_handle_one_turn`'s `_collect_response_text` call as a single entry ("The only production caller; needs the new `is_local` arg threaded in"), but the function actually contains **three** call-sites inside `_handle_one_turn`: line 1350 (primary turn drain), line 1377 (post-interrupt drain; result discarded), and line 1474 (retry-after-empty path; result feeds `_last_turn_input_tokens` accumulation at 1480-1487). The breakout task `thread-is-local-from-handle-turn` and AT#4 do not specify whether all three sites must be wired or only the primary. Per the spec's own Design Decision ("`is_local` is a hint for diagnostic logging only; key-shape detection is the actual switch"), attribution still works without threading on all sites — but an implementor reading the spec literally has no rule for which sites to touch. Category: clarify-assumption. Fix: append to the breakout task description "thread `is_local` on the primary drain only (line ~1350); the post-interrupt drain and retry drain are diagnostic-logging-only and may be left unthreaded since shape-detection drives the actual switch" — or, alternatively, "thread on all three sites".

### Low

- [LOW-01] `spec.task-breakout.implementation-kind-mismatch` — `## Task Breakout` / non-regression-anthropic-path: Task is tagged `implementationKind: behavior-change` but the description says "No production-code edits, no fixture edits — the test command itself is the deliverable." Should be `validation` or `test-only`. Category: remove-contradiction.
- [LOW-02] `spec.acceptance-tests.at9-behavior-claim-untested` — Acceptance Tests / AT#9: The test asserts options-inspection only (`options.extra_args.get("strict-mcp-config", "MISSING") is None`), but the AT prose also claims "any attempted call to such a tool fails with the CLI's MCP-not-configured error" — which is not actually tested in the same AT and is explicitly marked "may be skipped if not feasible". The flag-set proxy is reasonable, but the prose overstates the assertion. Category: clarify-assumption.
- [LOW-03] `spec.scope.dual-feature-coupling` — Problem / Architecture Overview: Slice bundles two functionally distinct fixes (token-attribution and plugin-MCP suppression) that share a file but edit different blocks (helper inside `_collect_response_text` vs. options-construction at line ~1297). Spec justifies coupling as "same surface" but the surfaces are non-overlapping within the file. Acceptable per "batch by shared design surface" guidance since both touch `sdk_teammate.py`'s spawn-time options pipeline, but worth flagging that an implementor or reviewer must reason about two semi-orthogonal subsystems. Category: clarify-assumption.

## Persistent Findings

_None — cycle 0._

## Spec Quality

- **Clarity:** strong. Problem statement is specific, attribution gap quantified ("every Anthropic-key lookup defaults to 0"), code paths cited by file + line where applicable.
- **Completeness:** strong. Edge cases cover dict-shape variants, ambiguous dual-shape, mid-turn kill, pre-existing extra_args, no-skills no-op. Open Questions empty (and correctly so — all assumptions are stated as Assumptions with rationale).
- **Scope discipline:** good. Out-of-Scope explicitly defers synthetic cost, /slots fallback, dashboard rendering, per-pack opt-out.
- **Testability:** strong. Test Command is runnable as written; fakes-based strategy is appropriate for non-reproducible local backend; live probe is gated correctly per project convention (`CLAUDE_CREW_LIVE_*` pattern).
- **Decomposition (Task Breakout):** good. 5 tasks, no mega-tasks, dependencies sound (extract-helper → thread-is-local → e2e → non-regression; strict-mcp parallel). Each task has `taskTouches` and a runnable `testCommand`. The non-regression task is correctly gated on the prior three.

## Verdict Rule Applied

Zero Critical, zero High → **PASS**. Medium and Low findings are clarification/consistency requests, not blockers; implementor can proceed and address them inline or in a follow-up.

RR-VERDICT: PASS local-model-attribution 0 /home/jerome/dev/claude-crew/.rr-worktrees/local-model-attribution/.rr/reports/local-model-attribution-review-0.md
