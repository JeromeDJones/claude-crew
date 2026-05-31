# Coordinator standing notes — graceful-termination-memory-flush

## ⚠️ LOCAL BACKEND ABANDONED (2026-05-31) — implementor now CLOUD

The local Qwen3-35B (llama-server :8080 via CCR :3456) crashed with a GPU OOM:
`HSA exception: MemoryRegion::BlockAllocator::alloc failed` — couldn't hold the ~41k-token
implementor prompt. Per user, the implementor switches to a REGULAR cloud team member.
=> Spawn rr-implementor with NO custom_endpoint, NO model="local" (pack default Sonnet 4.6 on
normal cloud auth) + CRG extra_tools. The local-routing notes below are HISTORICAL — do not use.

## CRG grant for ALL spawned teammates (user instruction 2026-05-31)

EVERY teammate spawned from here on gets the CRG tool set via `extra_tools`. MCP tool IDs in
`extra_tools` auto-wire the crg server — no separate mcpServers grant. Standing CRG set:

```
CRG_TOOLS = [
    "mcp__crg__get_minimal_context_tool",
    "mcp__crg__semantic_search_nodes_tool",   # degraded: 0 embeddings on the crew graph
    "mcp__crg__query_graph_tool",             # callers_of/callees_of/imports_of — works w/o embeddings
    "mcp__crg__get_impact_radius_tool",       # blast radius — works w/o embeddings
    "mcp__crg__traverse_graph_tool",
    "mcp__crg__get_review_context_tool",      # review bundle — apt for slice/feature reviewers
]
```

Applies to: slice-reviewer, feature-reviewer, implementor, documenter (all future spawns).
Already-spawned without CRG (do NOT respawn just to add it): planner (finished), plan-reviewer
(in-flight, spec-text review — CRG marginal). Rely on query_graph + get_impact_radius
(embedding-free); semantic_search is degraded.

## rr-implementor spawn config (EVERY implementor spawn, incl. build-cycle re-dispatch)

The implementor ALSO runs on the LOCAL backend. Every
`mcp__claude-crew__spawn_teammate(role="rr-implementor", ...)` call in the `implementing` phase
(Step PD3 step 3) and any re-dispatch MUST include BOTH:

```
model = "local"                                          # CRITICAL: routes CCR to its default (llama-cpp/Qwen3-35B).
                                                          # WITHOUT this, the teammate sends model=claude-sonnet-4-6,
                                                          # which CCR matches to its cloud `anthropic` provider (:3457
                                                          # oauth shim) -> CLOUD, not local. Verified via CCR log
                                                          # 2026-05-31: model name w/o comma + matching anthropic
                                                          # provider == cloud. "local" has no provider match -> default
                                                          # route (llama-cpp). The comma form "llama-cpp,Qwen3.6-35B-
                                                          # A3B-UD-Q4_K_M.gguf" also works (explicit provider,model).
custom_endpoint = {"base_url": "http://127.0.0.1:3456"}  # local CCR; no api_key (CCR has no APIKEY)
extra_tools = CRG_TOOLS                                   # (the standing set above)
```

Rationale:
- LOCAL backend: user wants the implementor dogfooding per-teammate-model-routing on the
  already-running local model. Only the implementor is local; all other roles stay on cloud.
- CRG tools: implementor edits the termination path (broker `_tombstone_teammate`/`kill_teammate`,
  sdk_teammate `_run`/`_handle_one_turn`). It must trace callers/impact before changing them
  (coding-standards "Know What You're Touching").

## CRG repo_root caveat for the implementor

The implementor's cwd is the per-task WORKTREE (`.rr-worktrees/<slug>/.rr-worktrees/<task>`), NOT
the registered graph root. CRG indexes `/home/jerome/dev/claude-crew`. Tell the implementor (or
verify) to pass `repo_root="/home/jerome/dev/claude-crew"` on crg queries, or confirm the worktree
resolves. VERIFY crg has claude-crew registered (list_repos_tool) before the implementing phase.

## Local backend health note

Initial probe of CCR :3456 timed out at 30s (likely Qwen cold-start). Expect slow first token and
possibly extra build<->slice-review cycles. Review gates (Opus) catch local-model misses.
