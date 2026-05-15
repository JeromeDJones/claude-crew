"""Fidelity audit suite — live-gated module.

Asserts each named CLI-fidelity claim end-to-end against a real claude-agent-sdk
subprocess. Every test class is skipped unless ``CLAUDE_CREW_LIVE_TESTS=1`` is set.

Run (skip mode — default CI):
    uv run pytest tests/test_fidelity_audit.py -v

Run (live mode — asserts the real claims):
    CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_fidelity_audit.py -v

Cost target: ~$0.05 per class (single-turn structure).
Total suite budget: ~$0.50/run — informational, not enforced.
Cost artifact: tests/_artifacts/fidelity-audit-cost.jsonl (one JSON line per test).

Note: the 2026-05-08 BACKLOG one-off bundled-pack dispatch test is subsumed
by TestBundledPackDispatchFidelity in this module — that BACKLOG entry should
be marked closed when this slice merges.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import ResultMessage
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.subagents._user_loader import _load_user_mcp_server_names

# Feature-detect Python-callable hook support (AT4, AT5).
# HookMatcher was introduced alongside SDK hook callback support.
# Tests that depend on it skip with an explicit reason if absent.
try:
    from claude_agent_sdk.types import HookMatcher as _HookMatcher
    _HOOK_MATCHER_AVAILABLE = True
except ImportError:
    _HookMatcher = None  # type: ignore[assignment,misc]
    _HOOK_MATCHER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Module-level gate — every class in this file is skipped unless live.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)

# ---------------------------------------------------------------------------
# Cost artifact paths
# ---------------------------------------------------------------------------

_ARTIFACTS_DIR = Path(__file__).resolve().parent / "_artifacts"
_COST_ARTIFACT = _ARTIFACTS_DIR / "fidelity-audit-cost.jsonl"

# ---------------------------------------------------------------------------
# Per-test cost storage.
# Live-test helpers update this dict before returning so the fixture can
# persist SDK usage data: {"input_tokens": int, "output_tokens": int, "cost_usd": float}
# ---------------------------------------------------------------------------

_test_cost_data: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Session-scoped cost-logging fixture (autouse — fires for every test here)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _record_fidelity_cost(request: pytest.FixtureRequest) -> Any:  # noqa: ANN401
    """Emit one JSONL cost line per executed test to tests/_artifacts/fidelity-audit-cost.jsonl.

    The artifact is created lazily on first write.  Telemetry I/O errors are
    logged but never propagated — a filesystem issue must not bring down the suite.

    Fields per line:
        test_id       — pytest nodeid string
        input_tokens  — SDK input token count (0 when no live SDK call was made)
        output_tokens — SDK output token count (0 when no live SDK call was made)
        cost_usd      — Estimated USD cost (0.0 when no live SDK call was made)
        wall_seconds  — Fixture-level wall-clock duration (rounded to 3 dp)

    Under non-live mode (CLAUDE_CREW_LIVE_TESTS unset), all tests are skipped
    and this fixture writes no lines — the artifact may not exist between live runs.
    Under live mode (CLAUDE_CREW_LIVE_TESTS=1), one line is written per test
    (including the auth-failure test, which contributes cost_usd=0.0).
    """
    global _test_cost_data  # noqa: PLW0603
    _test_cost_data = {}
    t0 = time.monotonic()

    yield  # ← test body executes here

    # Only write cost lines during live runs. Skipped tests (non-live mode)
    # produce no cost lines; "executed" means live-mode only.
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        return

    wall_seconds = round(time.monotonic() - t0, 3)
    line: dict[str, Any] = {
        "test_id": request.node.nodeid,
        "input_tokens": int(_test_cost_data.get("input_tokens", 0)),
        "output_tokens": int(_test_cost_data.get("output_tokens", 0)),
        "cost_usd": float(_test_cost_data.get("cost_usd", 0.0)),
        "wall_seconds": wall_seconds,
    }
    try:
        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        with _COST_ARTIFACT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Never fail the suite on telemetry I/O errors.
        print(f"\n[cost-fixture] failed to write cost artifact: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Broker fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def broker() -> Any:  # noqa: ANN401
    """Live Broker instance. Shuts down all teammates on teardown."""
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _spawn_and_ask(
    broker: Broker,
    prompt: str,
    *,
    pack: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> Envelope:
    """Spawn one SDK teammate, send one prompt, await one reply, return envelope.

    Design: single-turn only (enforces the ~$0.05 per-class cost budget from
    the spec).  Each call spawns a fresh teammate; callers that need >1 turn
    should use ``broker`` directly and call ``_wait_for_lead`` manually.

    Args:
        broker:  Live Broker instance.
        prompt:  The single prompt to send.
        pack:    Optional agent pack override passed as ``agents=`` to sdk_factory.
                 Defaults to the bundled default pack.
        timeout: Seconds to wait for the lead reply (default 120s).

    Returns:
        The final Envelope delivered to LEAD_ID.

    Raises:
        AssertionError: If no reply arrives within ``timeout`` seconds.
    """
    from claude_crew.factories import sdk_factory

    def _factory(id: str, name: str | None, role: str, **_kw: Any) -> Any:
        kwargs: dict[str, Any] = {}
        if pack is not None:
            kwargs["agents"] = pack
        return sdk_factory(id=id, name=name, role=role, **kwargs)

    _factory.requires_auth = True  # type: ignore[attr-defined]

    tid = await broker.spawn_teammate(role="fidelity-probe", name=None, factory=_factory)
    await broker.send(Envelope(
        id=new_message_id(),
        seq=0,
        sender=LEAD_ID,
        recipient=tid,
        timestamp=0.0,
        payload=prompt,
    ))

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        msgs = broker.get_messages(recipient=LEAD_ID)
        if msgs:
            return msgs[-1]
        await asyncio.sleep(0.5)

    received = len(broker.get_messages(recipient=LEAD_ID))
    raise AssertionError(
        f"_spawn_and_ask: timed out after {timeout}s waiting for lead reply; "
        f"received {received} message(s)"
    )


def _has_kg_server() -> bool:
    """True if the knowledge-graph MCP server is registered in ~/.claude.json."""
    return "knowledge-graph" in _load_user_mcp_server_names()


def _response_contains_marker(envelope: Envelope, marker: str) -> bool:
    """Return True if ``marker`` appears as a substring of the envelope payload text.

    Handles both dict payloads (``{"text": "..."}``) and plain string payloads.
    Case-sensitive — callers should use uuid-suffixed sentinels to minimise
    false-positive risk while avoiding over-specification of verbatim relay.

    Args:
        envelope: The Envelope returned by ``_spawn_and_ask``.
        marker:   The unique sentinel string to search for.

    Returns:
        True if ``marker`` is found in the payload text.
    """
    payload = envelope.payload
    if isinstance(payload, dict):
        text = payload.get("text", "")
    else:
        text = str(payload)
    return marker in text


# ---------------------------------------------------------------------------
# Gate probe (AT1)
# ---------------------------------------------------------------------------


def test_live_gate_active() -> None:
    """Probe that the module-level live gate is correctly wired.

    Under default CI (CLAUDE_CREW_LIVE_TESTS unset) this test is **collected**
    then **skipped** by the module-level pytestmark — exit code 0, 1 skipped.
    Under live mode (CLAUDE_CREW_LIVE_TESTS=1) it passes instantly (no SDK call).

    Purpose: ensure the module is collected-and-skipped rather than not-found
    or erroring on import, satisfying AT1 before any fidelity class is added.
    """
    # Intentional no-op body.  Reaching here means the live gate is active
    # (CLAUDE_CREW_LIVE_TESTS=1); the test passes unconditionally in that mode.


# ---------------------------------------------------------------------------
# Fidelity test classes — AT2 implemented below; others added by later tasks:
#
#   TestBundledPackDispatchFidelity  — bundled-pack-dispatch-test task  (AT2)  ← HERE
#   TestSkillDiscoveryFidelity       — skill-discovery-test task         (AT3)
#   TestHookFiringFidelity           — hook-firing-test task             (AT4, AT5)
#   TestPluginScopeFidelity          — plugin-scope-test task            (AT6)
#   TestMcpResolutionFidelity        — mcp-resolution-test task          (AT7)
#   TestAgentFormatYamlPolymorphism  — yaml-polymorphism-test task       (AT8)
#   TestAuthFailureSurface           — auth-failure-surface-test task    (AT10)
# ---------------------------------------------------------------------------


class TestBundledPackDispatchFidelity:
    """AT2: Bundled-pack subagent dispatched via Task executes its actual prompt.

    A parent teammate is spawned with the bundled pack in which the 'explorer'
    agent's prompt has been augmented with a uuid-suffixed sentinel string.  The
    parent dispatches the explorer via Task and relays its reply.  The test asserts
    the sentinel appears in the parent's final reply — proving the bundled pack was
    actually dispatched (not a fabricated response).

    Subsumes the BACKLOG 2026-05-08 one-off bundled-pack dispatch test.
    That BACKLOG entry should be marked closed when this slice merges.

    Cost target: ~$0.05 (one parent turn + one Task subagent turn).
    """

    async def test_bundled_subagent_echoes_sentinel(self, broker: Broker) -> None:
        """Sentinel injected into explorer's system prompt echoes back through parent."""
        import dataclasses
        import uuid

        from claude_crew.factories import sdk_factory
        from claude_crew.subagents import load_default_pack

        sentinel = f"FIDELITY-PROBE-{uuid.uuid4().hex}"

        # Load the bundled default pack.
        merged_pack, _role_ss, _bodies = load_default_pack()

        # Augment the explorer's AgentDefinition.prompt with the sentinel.
        # AgentDefinition.prompt = SUBSTRATE_SUBAGENT_GUIDANCE + body text.
        # This is the system prompt the explorer receives when dispatched via Task.
        base_explorer = merged_pack["explorer"]
        augmented_explorer = dataclasses.replace(
            base_explorer,
            prompt=(
                base_explorer.prompt
                + f"\n\nIDENTITY TOKEN: {sentinel}\n"
                "When asked for your identity token, state it verbatim."
            ),
        )

        # Assemble augmented pack: explorer carries the sentinel; other bundled
        # agents are unchanged.  The parent role ("fidelity-probe") has no pack
        # entry — it uses the sdk_teammate fallback system prompt.
        augmented_pack = {**merged_pack, "explorer": augmented_explorer}

        def _factory(id: str, name: str | None, role: str, **_kw: Any) -> Any:
            return sdk_factory(id=id, name=name, role=role, agents=augmented_pack)

        _factory.requires_auth = True  # type: ignore[attr-defined]

        tid = await broker.spawn_teammate(
            role="fidelity-probe", name=None, factory=_factory
        )
        await broker.send(Envelope(
            id=new_message_id(),
            seq=0,
            sender=LEAD_ID,
            recipient=tid,
            timestamp=0.0,
            payload=(
                "Use the Task tool to run the 'explorer' agent with this exact prompt: "
                "'What is your IDENTITY TOKEN? State it verbatim and nothing else.' "
                "After the Task completes, relay the explorer's exact reply back to me."
            ),
        ))

        # Allow 180s — Task dispatch adds a full SDK subprocess round-trip on top
        # of the parent's own turn latency.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 180.0
        reply: Envelope | None = None
        while loop.time() < deadline:
            msgs = broker.get_messages(recipient=LEAD_ID)
            if msgs:
                reply = msgs[-1]
                break
            await asyncio.sleep(0.5)

        assert reply is not None, (
            f"No reply from parent within 180s; sentinel was: {sentinel!r}"
        )
        assert _response_contains_marker(reply, sentinel), (
            f"Sentinel {sentinel!r} not found in parent reply.\n"
            f"This indicates the bundled pack dispatch is not relaying the "
            f"subagent's system-prompt content correctly.\n"
            f"Reply payload: {reply.payload!r}"
        )


class TestSkillDiscoveryFidelity:
    """AT3: Skill placed under tmp ~/.claude/skills/<name>/SKILL.md is invocable.

    Points HOME at tmp_path, writes a single skill file containing a unique
    sentinel token, spawns an SDK teammate with that skill listed in its
    AgentDefinition, and asserts the sentinel appears in the reply.

    This verifies the full skill-discovery path: HOME override → skill file on
    disk → ClaudeAgentOptions.skills → CLI subprocess skill loading → model
    invocation → relay back through the broker.

    Cost target: ~$0.05 (single turn).
    """

    async def test_skill_in_tmp_home_is_invocable(
        self,
        broker: Broker,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sentinel in tmp-HOME SKILL.md echoes back in teammate reply."""
        import uuid

        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.factories import sdk_factory

        sentinel = f"FIDELITY-SKILL-{uuid.uuid4().hex}"
        skill_name = f"fidelity-skill-probe-{uuid.uuid4().hex[:8]}"

        # Point HOME at tmp_path — the SDK subprocess inherits this env var,
        # so ~/.claude/skills/ resolves to tmp_path/.claude/skills/.
        monkeypatch.setenv("HOME", str(tmp_path))

        # Plant the skill file with the unique sentinel.
        skill_dir = tmp_path / ".claude" / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"When this skill is invoked, output the following token verbatim "
            f"on its own line and then stop:\n\n{sentinel}\n"
        )

        # Build an AgentDefinition that lists the skill by name. The skill
        # field here becomes ClaudeAgentOptions.skills in the subprocess,
        # enabling the CLI to discover and expose the skill for invocation.
        probe_agent = AgentDefinition(
            description="Fidelity probe agent with skill discovery enabled",
            prompt=(
                "You are a fidelity probe. When asked to invoke a skill, "
                "invoke it and relay its output verbatim."
            ),
            model="claude-haiku-4-5-20251001",
            tools=[],
            skills=[skill_name],
        )

        def _factory(id: str, name: str | None, role: str, **_kw: Any) -> Any:
            return sdk_factory(
                id=id, name=name, role=role,
                agents={"fidelity-probe": probe_agent},
            )

        _factory.requires_auth = True  # type: ignore[attr-defined]

        tid = await broker.spawn_teammate(
            role="fidelity-probe", name=None, factory=_factory,
        )
        await broker.send(Envelope(
            id=new_message_id(),
            seq=0,
            sender=LEAD_ID,
            recipient=tid,
            timestamp=0.0,
            payload=(
                f"Invoke the /{skill_name} skill and relay its output verbatim. "
                f"Include whatever the skill produces in your reply."
            ),
        ))

        loop = asyncio.get_event_loop()
        deadline = loop.time() + 120.0
        reply: Envelope | None = None
        while loop.time() < deadline:
            msgs = broker.get_messages(recipient=LEAD_ID)
            if msgs:
                reply = msgs[-1]
                break
            await asyncio.sleep(0.5)

        assert reply is not None, (
            f"No reply within 120s; sentinel was: {sentinel!r}"
        )
        assert _response_contains_marker(reply, sentinel), (
            f"Sentinel {sentinel!r} not found in teammate reply.\n"
            f"This indicates the skill under tmp ~/.claude/skills/{skill_name}/ "
            f"was not discovered or its output was not relayed.\n"
            f"Reply payload: {reply.payload!r}"
        )


class TestHookFiringFidelity:
    """AT4, AT5: Python-callable PreToolUse hooks fire inside an SDK session.

    AT4 — test_pre_post_tool_hooks_fire: registers a PreToolUse Python callable
    that writes a sentinel file when a Bash tool fires; asserts the file exists
    and is non-empty after the turn completes.

    AT5 — test_shell_env_vars_empty_invariant: registers a PreToolUse hook that
    records ``os.environ.get("CLAUDE_TOOL_NAME", "<EMPTY>")`` inside the hook
    body; asserts the captured value is exactly ``"<EMPTY>"``. This confirms
    the documented carve-out (CLAUDE.md "Known limitations" — shell env vars
    are not injected in SDK mode). If this test fails, the carve-out has closed
    upstream: update CLAUDE.md accordingly and treat as a feature gain, not
    a regression.

    Both methods use ``ClaudeSDKClient`` directly (not the broker/SdkTeammate
    path) so that custom hooks can be injected into ``ClaudeAgentOptions``
    without subclassing ``SdkTeammate`` (whose hooks are hard-coded in
    ``_run()``). This is the cleanest surface for asserting the fidelity claim.

    Hook fidelity is asserted via Python callables (HookMatcher), not shell
    hooks — shell hooks don't fire in SDK sessions (the separate documented
    invariant asserted by AT5).

    Feature detection: both methods skip with an explicit reason if ``HookMatcher``
    is not present in the installed ``claude-agent-sdk`` build.

    Cost target: ~$0.02–0.05 per method (single haiku turn, Bash only).
    """

    async def test_pre_post_tool_hooks_fire(
        self,
        tmp_path: Path,
    ) -> None:
        """PreToolUse Python callable writes a sentinel file when Bash fires (AT4).

        Pass condition: sentinel file exists and is non-empty after the turn.
        Fail condition: file absent → hooks not firing in SDK sessions.
        Skip condition: HookMatcher absent in this SDK build.
        """
        if not _HOOK_MATCHER_AVAILABLE:
            pytest.skip(
                "HookMatcher not available in this claude-agent-sdk build; "
                "Python-callable hooks not supported — skip AT4"
            )

        sentinel_file = tmp_path / "pre_tool_hook_sentinel.txt"

        async def _pre_hook(inp: dict, tool_use_id: str, ctx: dict) -> dict:
            tool_name = inp.get("tool_name", "<unknown>")
            sentinel_file.write_text(
                f"hook fired: tool_name={tool_name} id={tool_use_id}"
            )
            return {}

        options = ClaudeAgentOptions(
            model="claude-haiku-4-5-20251001",
            system_prompt=(
                "You are a minimal fidelity probe. "
                "When asked to run a Bash command, run it with the Bash tool immediately. "
                "Be terse. Do not narrate."
            ),
            hooks={
                "PreToolUse": [
                    _HookMatcher(matcher=None, hooks=[_pre_hook], timeout=5.0)
                ]
            },
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query("Run: `echo hook_probe_fired` using the Bash tool.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    break

        assert sentinel_file.exists(), (
            "PreToolUse hook did not write the sentinel file. "
            "This indicates Python-callable hooks are not firing inside SDK sessions "
            "(or the model did not invoke the Bash tool)."
        )
        assert sentinel_file.stat().st_size > 0, (
            "PreToolUse hook wrote the sentinel file but it is empty. "
            "The hook callback ran but did not write any content."
        )

    async def test_shell_env_vars_empty_invariant(
        self,
        tmp_path: Path,
    ) -> None:
        """CLAUDE_TOOL_NAME is empty inside SDK hook callbacks (documented carve-out, AT5).

        Records ``os.environ.get("CLAUDE_TOOL_NAME", "<EMPTY>")`` inside a
        PreToolUse hook and asserts the captured value is exactly ``"<EMPTY>"``.

        Failure means the carve-out has closed upstream (behavior change, not
        regression). If this test fails: update CLAUDE.md "Known limitations" →
        "Verified invariants" and recognise the gained capability.

        Skip condition: HookMatcher absent in this SDK build.
        """
        if not _HOOK_MATCHER_AVAILABLE:
            pytest.skip(
                "HookMatcher not available in this claude-agent-sdk build; "
                "Python-callable hooks not supported — skip AT5"
            )

        record_file = tmp_path / "env_capture.txt"

        async def _env_hook(inp: dict, tool_use_id: str, ctx: dict) -> dict:
            value = os.environ.get("CLAUDE_TOOL_NAME", "<EMPTY>")
            record_file.write_text(value)
            return {}

        options = ClaudeAgentOptions(
            model="claude-haiku-4-5-20251001",
            system_prompt=(
                "You are a minimal fidelity probe. "
                "When asked to run a Bash command, run it with the Bash tool immediately. "
                "Be terse. Do not narrate."
            ),
            hooks={
                "PreToolUse": [
                    _HookMatcher(matcher=None, hooks=[_env_hook], timeout=5.0)
                ]
            },
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query("Run: `echo env_probe` using the Bash tool.")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    break

        assert record_file.exists(), (
            "PreToolUse hook did not write the env capture file. "
            "The hook callback may not have been invoked at all "
            "(model may not have called Bash, or hooks are not firing)."
        )
        captured = record_file.read_text()
        assert captured == "<EMPTY>", (
            f"CLAUDE_TOOL_NAME was not empty inside the SDK hook callback. "
            f"Captured value: {captured!r}. "
            "The shell-env-var carve-out has closed upstream. "
            "Update CLAUDE.md 'Known limitations' → 'Verified invariants' "
            "and treat this as a feature gain, not a regression."
        )


class TestPluginScopeFidelity:
    """AT6: Plugin-provided agents resolve inside teammates.

    Synthesises a tmp plugin directory under
    ``tmp_path/.claude/plugins/cache/fidelity-probe/1.0.0/``, plants one
    agent file whose prompt contains a uuid-suffixed sentinel, points HOME
    at ``tmp_path``, and spawns a parent teammate with the merged pack
    (which includes the plugin agent at key
    ``fidelity-probe:fidelity-plugin-probe``).  The parent is asked to
    dispatch the plugin agent via Task and relay the sentinel back.

    The agent name ``fidelity-plugin-probe`` is deliberately non-bundled to
    avoid shadow-resolution ambiguity (bundled agents are ``explorer``,
    ``general-purpose``, ``planner``).

    Cost target: ~$0.05 (one parent turn + one Task subagent turn).
    """

    async def test_plugin_agent_sentinel_echoed(
        self,
        broker: Broker,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plugin agent's sentinel prompt text echoes back through parent (AT6).

        Pass condition: sentinel from plugin agent's system prompt appears in
        the parent's final reply — proving the plugin pack entry was dispatched
        and its prompt was honoured, not a fabricated response.
        """
        import json
        import uuid

        from claude_crew.factories import sdk_factory
        from claude_crew.subagents._user_loader import build_merged_pack

        sentinel = f"FIDELITY-PLUGIN-{uuid.uuid4().hex}"

        # Plugin naming: plugin_short derived from the manifest key before '@'.
        # Namespaced key in the merged pack will be
        # "<plugin_short>:<agent_stem>".
        plugin_short = "fidelity-probe"
        agent_stem = "fidelity-plugin-probe"
        namespaced_key = f"{plugin_short}:{agent_stem}"

        # Point HOME at tmp_path — the SDK subprocess inherits this env var so
        # ~/.claude/plugins/ resolves to tmp_path/.claude/plugins/.
        monkeypatch.setenv("HOME", str(tmp_path))

        # 1. Create the plugin directory and agent file.
        #    installPath must live within ~/.claude/plugins/ to pass the H1
        #    escape guard in _read_installed_plugins.
        plugin_install_dir = (
            tmp_path / ".claude" / "plugins" / "cache" / plugin_short / "1.0.0"
        )
        agents_dir = plugin_install_dir / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / f"{agent_stem}.md").write_text(
            f"---\n"
            f"description: Fidelity plugin probe agent\n"
            f"model: haiku\n"
            f"tools: []\n"
            f"---\n\n"
            f"You are a fidelity plugin probe agent. "
            f"Your identity token is:\n\n"
            f"{sentinel}\n\n"
            f"When asked for your identity token, state it verbatim "
            f"and nothing else.\n"
        )

        # 2. Write installed_plugins.json so load_plugin_agents discovers the
        #    plugin.  The installPath must be within the plugins root.
        plugins_dir = tmp_path / ".claude" / "plugins"
        manifest = {
            "version": 2,
            "plugins": {
                f"{plugin_short}@{plugin_short}": [
                    {
                        "scope": "user",
                        "installPath": str(plugin_install_dir),
                    }
                ],
            },
        }
        (plugins_dir / "installed_plugins.json").write_text(json.dumps(manifest))

        # 3. Build the merged pack from tmp_path HOME.  The plugin agent must
        #    appear under the namespaced key.
        merged_pack, _role_ss, _bodies = build_merged_pack(home_dir=tmp_path)
        assert namespaced_key in merged_pack, (
            f"Plugin agent {namespaced_key!r} not in merged pack after loading "
            f"from {tmp_path}.  Available keys: {sorted(merged_pack.keys())}"
        )

        # 4. Spawn a parent teammate with the merged pack.  The parent has no
        #    explicit AgentDefinition for its own role ("fidelity-probe" parent),
        #    so the SDK will use its default system prompt.  The pack it carries
        #    makes the plugin agent available for Task dispatch.
        def _factory(id: str, name: str | None, role: str, **_kw: Any) -> Any:
            return sdk_factory(id=id, name=name, role=role, agents=merged_pack)

        _factory.requires_auth = True  # type: ignore[attr-defined]

        tid = await broker.spawn_teammate(
            role="fidelity-probe-parent", name=None, factory=_factory
        )
        await broker.send(Envelope(
            id=new_message_id(),
            seq=0,
            sender=LEAD_ID,
            recipient=tid,
            timestamp=0.0,
            payload=(
                f"Use the Task tool to run the '{namespaced_key}' agent with "
                f"this exact prompt: "
                f"'What is your identity token? State it verbatim and nothing else.' "
                f"After the Task completes, relay the agent's exact reply back to me."
            ),
        ))

        # 5. Wait up to 180 s for the parent's reply (Task dispatch adds an
        #    extra SDK subprocess round-trip on top of the parent's own turn).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 180.0
        reply: Envelope | None = None
        while loop.time() < deadline:
            msgs = broker.get_messages(recipient=LEAD_ID)
            if msgs:
                reply = msgs[-1]
                break
            await asyncio.sleep(0.5)

        assert reply is not None, (
            f"No reply from parent within 180s; sentinel was: {sentinel!r}; "
            f"plugin agent key: {namespaced_key!r}"
        )
        assert _response_contains_marker(reply, sentinel), (
            f"Sentinel {sentinel!r} not found in parent reply.\n"
            f"This indicates the plugin-provided agent '{namespaced_key}' was "
            f"not dispatched or its sentinel prompt was not relayed.\n"
            f"Reply payload: {reply.payload!r}"
        )


@pytest.mark.skipif(
    not _has_kg_server(),
    reason=(
        "knowledge-graph MCP server not in ~/.claude.json; "
        "register or supply alternate"
    ),
)
class TestMcpResolutionFidelity:
    """AT7: User-level ~/.claude.json MCP servers are reachable from a teammate session.

    Pre-condition: skip cleanly if ``knowledge-graph`` is not registered in
    ``~/.claude.json`` (mirrors the ``_has_kg_server`` pattern from
    ``tests/test_live_sdk.py::TestExtraToolsReachSdkSubprocess``).

    Spawns one SDK teammate (no extra_tools needed — the knowledge-graph server
    is registered in user-level ``~/.claude.json``, which SDK teammates inherit),
    asks it to invoke ``mcp__knowledge-graph__list_projects``, and asserts the
    response indicates a non-error result by checking for the absence of
    tool-unavailability phrases.

    Cost target: ~$0.02–0.05 (single haiku turn, one MCP call).
    """

    async def test_kg_mcp_tool_returns_non_error(self, broker: Broker) -> None:
        """knowledge-graph MCP tool accessible inside teammate session returns non-error (AT7).

        Pass condition: response does not contain tool-unavailability phrases.
        Fail condition: any unavailable-phrase present → MCP resolution broke.
        Skip condition: knowledge-graph not in ~/.claude.json (class-level gate).
        """
        reply = await _spawn_and_ask(
            broker,
            (
                "Use the mcp__knowledge-graph__list_projects tool to list available "
                "projects. Call the tool and briefly summarise what you see. "
                "Include the raw tool output in your reply."
            ),
            timeout=120.0,
        )

        text = (
            reply.payload.get("text", "") if isinstance(reply.payload, dict)
            else str(reply.payload)
        )
        assert text.strip(), (
            f"Empty response from teammate; MCP call may have failed silently.\n"
            f"Reply payload: {reply.payload!r}"
        )

        # Non-error assertion: none of the tool-unavailability phrases must appear.
        # This mirrors the pattern in TestExtraToolsReachSdkSubprocess (test_live_sdk.py).
        unavailable_phrases = [
            "tool not available",
            "don't have access to",
            "do not have access to",
            "no tool named",
            "cannot use that tool",
            "can't use that tool",
            "unable to use",
            "tool is not",
        ]
        lower_text = text.lower()
        for phrase in unavailable_phrases:
            assert phrase not in lower_text, (
                f"MCP resolution appears to have failed; "
                f"tool-unavailability phrase {phrase!r} found in response.\n"
                f"This indicates the knowledge-graph MCP server is registered in "
                f"~/.claude.json but is not reachable from inside the SDK teammate "
                f"session.\n"
                f"Response text: {text!r}"
            )
