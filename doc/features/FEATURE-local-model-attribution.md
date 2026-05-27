# Feature: local-model-attribution

**Status:** done (2026-05-26)
**Spec:** `.rr/specs/local-model-attribution.md`
**Branch:** `repo-react/local-model-attribution`
**Validation:** 1278 passed / 0 failed (isolated run, 2026-05-26)

## Summary

Two independently-motivated fixes landing on the same design surface
(`claude_crew/sdk_teammate.py` spawn-and-drain blocks):

**1. Local-backend token attribution.** `_extract_token_cost_from_rm` extended
to detect and parse OpenAI-shaped usage dicts
(`prompt_tokens` / `completion_tokens` / `prompt_tokens_details.cached_tokens`)
alongside the existing Anthropic shape
(`input_tokens` / `output_tokens` / `cache_*`). Local-backend teammates
(routed via ccr → llama.cpp) now surface non-zero token counts through
`get_teammate_status` and the Mission Control dashboard. The detection is
key-presence-driven (not flag-driven) so a backend that re-routes
Anthropic-shape usage through a local URL continues to parse correctly.
`is_local` is threaded from `_handle_one_turn` to `_collect_response_text`
(all three call-sites: main drain, post-interrupt drain, retry) for diagnostic
logging only. Cost stays `$0.00` for local teammates — honest, flows through
whatever `ResultMessage.total_cost_usd` reports.

**2. Plugin-MCP isolation.** `--strict-mcp-config` added unconditionally to
every SDK teammate spawn via the SDK's `extra_args` pass-through:
`opts_kwargs.setdefault("extra_args", {})["strict-mcp-config"] = None`.
Closes the gap documented in `doc/sdk-teammate-wiring.md §4`: packs declaring
`skills:` trigger `setting_sources=["user","project"]` in the SDK, which
auto-loads plugin manifests and leaks `mcp__plugin_*` tools past the
deny-by-default `--mcp-config` allowlist. `--strict-mcp-config` tells the CLI
to ignore all MCP sources except the explicit `--mcp-config` payload
(already written as deny-by-default empty). Unconditional; no per-pack opt-out.

## Tasks

| Task | Acceptance Tests | Result |
|------|-----------------|--------|
| extract-helper-dual-shape | AT#2, AT#3, AT#5, AT#6 | PASS |
| thread-is-local-from-handle-turn | AT#4 | PASS |
| e2e-local-attribution | AT#1, AT#8 (live-gated) | PASS |
| non-regression-anthropic-path | AT#7 | PASS |
| strict-mcp-config-plugin-isolation | AT#9 | PASS |

## Key Design Decisions

- Polymorphic key-presence parser — actual switch is presence of `input_tokens`
  vs `prompt_tokens`; `is_local` is a hint for diagnostic logging only.
- OpenAI `prompt_tokens` = full per-turn input total (cached already included);
  no `cached_tokens` re-add to avoid double-counting.
- Cost stays `$0.00` for local teammates; flows through verbatim from
  `ResultMessage.total_cost_usd` — non-zero values are preserved (AT#5).
- Ambiguous dual-shape (both key families present) → prefer Anthropic + log INFO.
- `--strict-mcp-config` unconditional on every spawn; `setdefault` preserves
  any pre-existing `extra_args`.

## Files Changed

- `claude_crew/sdk_teammate.py` — dual-shape detection, `is_local` threading, `--strict-mcp-config`
- `tests/fakes/sdk.py` — `text_response_with_openai_usage` helper
- `tests/test_sdk_teammate_local_attribution.py` (new) — AT#2, #3, #5, #6
- `tests/test_e2e_local_token_attribution.py` (new) — AT#1, #8
- `tests/test_sdk_teammate_strict_mcp.py` (new) — AT#9

## Deferred Follow-ups (handled post-merge on master)

- Peak-invocation `AssistantMessage` log parity (INFO on ambiguous dual-shape)
- Tautological third test in `tests/test_sdk_teammate_strict_mcp.py`
- `test_shutdown_signals.py` stability under concurrent implementor dispatch

## Workflow-Retro Highlights

Four high-leverage SKILL/template changes surfaced by the persistent role
teammates at retro (full text in this slice's workflow-retro report):

1. **`bin/rr-flake-check.sh` + structured failure-attribution build-report
   fields** (slice-reviewer) — `failing_test` / `touches_slice_surface` /
   `reproduction_in_isolation` as required fields when an implementor cites
   a full-suite failure, so "pre-existing" becomes a measured claim rather
   than a rationalization.
2. **"Non-regression must be proven, not asserted" template clause**
   (feature-reviewer) — slice-review and feature-review templates require
   `git stash && pytest <test> && git stash pop` evidence pasted into the
   report when an implementor cites a failure as pre-existing.
3. **Call-site verification mandatory in plan-review** (plan-reviewer) —
   `skills/review-plan/SKILL.md` requires the reviewer to grep every symbol
   the spec's Call-site survey table claims and assert the row-count matches
   reality; any mismatch is at minimum a Medium finding.
4. **Surface Audit subsection in spec template** (planner) — a required
   `## Surface Audit` under `## Architecture Overview` (schema-checked) that
   forces the planner to name the exact file/block of the primary edit, list
   every other open issue / known contract gap touching that block, and
   explicitly decide "fold in" or "defer with reason." Would have made the
   `--strict-mcp-config` amendment turn unnecessary.

These four surface as deferred work on the repo-reactor plugin itself,
not in this claude-crew slice.
