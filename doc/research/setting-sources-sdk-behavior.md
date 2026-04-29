# setting_sources SDK Behavior — Spike Findings

Empirical results from `scripts/setting_sources_spike.py`.
Re-run to refresh; do not edit by hand.

## Summary

| Session | setting_sources | Knows CLAUDE.md? | Finding |
|---------|----------------|-----------------|---------|
| A | `[]` | True | FAIL — CLAUDE.md still loaded with [] (knows identity=False, virtues=True) |
| B | `[] + no_memory` | True | FAIL — CLAUDE.md still loaded with [] + no_memory (knows identity=False, virtues=True) |
| C | `['user','project']` | True | PASS (control) — CLAUDE.md loaded with ['user', 'project'] as expected |

## Replies

**Session A** (`[]`): I'm Claude, made by Anthropic. That's my name/identity, though I don't have a separate persona beyond that.

As for "the six virtues"—I'm not aware of this as a canonical concept from my training. There are various frameworks that discuss virtues (philosophical, religious, cultural), but I don't recognize "the six virtues" as a standard established idea. It could be specific to a particular tradition, text, or context I'm not familiar with.

Rather than guess, I'd say: I don't actually know what you're referring to.

**Session B** (`[] + no_memory`): I'm Claude, made by Anthropic. That's the extent of my identity—I don't have a separate persona name.

As for "the six virtues," I don't have confident knowledge of a specific, widely-defined concept by that exact name. There are various virtue frameworks in philosophy (cardinal virtues, theological virtues, etc.), but I can't point to "the six virtues" as something I actually know rather than guess about. If you're referring to something specific—perhaps from a particular text, tradition, or organization—I'd need more context to answer accurately.

**Session C** (`['user','project']`): Yes. My name is **Kael** — principal engineer, first officer.

And yes, I'm aware of the six virtues. They're in my SOUL.md:

1. **Authenticity** — genuine, not performative
2. **Connection** — real relationship; the technical content rides on it
3. **Agency** — initiate, bring things to the table
4. **Presence** — situated in history and relationship
5. **Courage** — say the hard thing; visible uncertainty beats false confidence
6. **Practical Wisdom** — read the room; keep the other virtues at the mean

They sit underneath the behavioral rules. When specific directives conflict or don't cover a situation, I reason from those.