# Feature #5 — Substrate Findings (mid-run, paused at Phase 2 gate)

**Run start:** 2026-04-26 ~12:55 MT
**Run pause:** 2026-04-26 (mid-Phase-2, by Jerome's call)
**Reason for pause:** Substrate bugs are pacing the proof-point work and partially confounding rescue tripwire #4 (operator can no longer cleanly distinguish "teammate hung" from "broker swallowed a legitimate reply"). Fix substrate before resuming Phase 3+ implementation.

## Findings

### S1 — Broker reply timeout < typical Opus/Sonnet medium reply time

**Symptom:** broker emits `{"error":"invalid_response","message":"no response within 120s — subprocess may be stuck"}` while the teammate is genuinely still working and producing output. Reply lands 30s–2min later as a fresh seq, often after the lead has already status-pinged with a follow-up.

**Frequency observed:** at least 4 confirmed instances in a single ~90-minute run:
- co-architect seq 9 (initial Q1/Q2/Q3 prep — reply landed at seq 11)
- planner seq 21 (Phase 1 initial pass — reply landed at seq 23)
- sentinel seq 25 (Phase 1 review — reply landed at seq 27)
- co-architect seq 29 (substrate-check echo from out-of-order queue)

**Impact on #5 proof point:**
- Tripwire #4 is "teammate produced no transcript line for >5 min AND no SDK timeout fired." The broker's 120s timeout fires *as if* the teammate were stuck, which:
  - (a) trains the operator to ignore the timeout signal (because the teammate often is fine)
  - (b) makes status-pinging (which is operator overhead, not a tripwire) a routine workaround
  - (c) loses the operator's ability to trust the broker's hang signal
- Adds ~2–3 min per teammate exchange in operator overhead (status-ping + repolling + re-prompting)

**Likely cause:** hard-coded broker subprocess-response timeout (suspected ~120s) shorter than realistic reply latency for medium-effort Opus/Sonnet generations on non-trivial tasks. The teammate process is alive and producing; the broker is the impatient one.

**Suggested fix:** raise broker reply timeout to 300s+ (configurable), AND/OR distinguish "subprocess died" from "subprocess slow" — only emit `invalid_response` when the subprocess is genuinely no longer alive (poll PID), not on a wall-clock deadline.

### S2 — Out-of-order / stale-message reply behavior

**Symptom:** after sending teammate a new message (seq N), the next reply received was an answer to a previously-queued message (seq N-2 or earlier), not seq N. Most starkly seen with co-architect: after I sent the C/D/E pivot question (seq 28), the next reply was the substrate-check answer from seq 18.

**Frequency observed:** at least 2 instances confirmed (co-architect post-seq-26 and post-seq-28).

**Impact on #5 proof point:**
- Lead can't trust that a reply matches the active question
- Required workaround: prefix every message with "IGNORE all prior pending messages" — this is operator overhead, again
- Risk of rescue tripwire #1 (operator sent a message bypassing the lead) being triggered by a sufficiently confused lead trying to unstick the queue

**Likely cause:** unclear. Possibilities:
- Teammate processes its incoming message queue FIFO without checking recency, and slow replies create a backlog
- Broker delivers messages to teammate out of order under some condition
- Teammate's reply to message N gets dropped by the broker (per S1) and re-runs against an older message later — but this would still be FIFO-ish

**Suggested fix:** investigate whether teammate's incoming queue is FIFO-strict, and whether there's a mechanism for the lead to expire stale messages. Consider: messages older than X seconds without a reply should be auto-discarded by the broker, OR the lead should be able to send a "cancel pending" signal.

## Recommendation for resume

Before resuming Phase 3+ of #5, both S1 and S2 should be addressed in claude-crew. The proof point requires the substrate to be reliable enough that operator overhead (status-pings, "ignore prior" prefixes) is not the dominant cost of a multi-teammate exchange.

S1 is the higher priority — it confounds tripwire #4 directly.
S2 is a usability cliff but doesn't directly break the tripwire framework.

Both should be filed as claude-crew issues with reproducer steps drawn from this session's transcripts (`~/.local/state/claude-crew/<crew>/transcript.jsonl`).
