# Debrief: multi-scope-agent-memory — implementor (task: ensure-write-tool-helper)

Verdict: PASS

## Transferable lesson

**Spec type annotations are aspirational, not contractual.** When a spec sketches `tools: tuple[str, ...] | None`, treat it as the *shape* of intent (ordered, finite, nullable) rather than the exact runtime type — verify against the SDK before writing tests or implementation. Here, the real `AgentDefinition.tools` was `list[str] | None`; had I coded to the spec's tuple literally, the identity short-circuit (`result is original`) would still pass but the equality assertions (`== ("Write",)`) would silently mismatch on the list the SDK stores. The discoverable signal is always the constructor signature, not the spec prose. The action: on any function that wraps a third-party dataclass, run `inspect.signature` or read the actual type stub *before* writing a single assertion — one shell call saves a failed cycle. The `tools=None` advisory was the second lesson: spec edge-case lists are often incomplete on the None/empty boundary; if a type is nullable, always add the None branch regardless of whether the spec calls it out explicitly.
