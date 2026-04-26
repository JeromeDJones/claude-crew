"""Tests for the default subagent pack (Feature #3a).

Covers Task 1 (loader + pack files + security doc) ACs from
`doc/features/FEATURE-default-subagent-pack.md` Phase 3 — i.e.,
SC-3 (per-subagent budgets pinned), SC-4 (default models), SC-5
(hermetic prompts), SC-7 (determinism), SC-11 (security section regex),
plus loader sad paths and merge_packs semantics.

Tasks 2–4 add their own classes to this file; this module is the
single home for #3a regression coverage.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition, ResultMessage

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.subagents import (
    PACK_MEMBERS,
    PackLoadError,
    load_default_pack,
    merge_packs,
)
from claude_crew.subagents._loader import PackFrontmatter, parse_pack_file
from tests.fakes.sdk import (
    FakeSDKClient,
    task_failure_response,
    task_failure_then_text,
    task_notification,
    text_response,
)


PACK_DIR = Path(__file__).parent.parent / "claude_crew" / "subagents"


class TestPackContents:
    """SC-3, SC-4, SC-7 — the bundled pack matches its declared contract."""

    def test_keys_are_exactly_the_three_pack_members(self) -> None:
        pack = load_default_pack()
        assert set(pack.keys()) == {"explorer", "planner", "general-purpose"}
        assert PACK_MEMBERS == ("explorer", "planner", "general-purpose")

    def test_explorer_contract(self) -> None:
        pack = load_default_pack()
        explorer = pack["explorer"]
        assert isinstance(explorer, AgentDefinition)
        assert explorer.model == "haiku"
        assert explorer.tools == ["Read", "Grep", "Glob"]
        assert explorer.effort == "low"
        assert explorer.maxTurns == 10

    def test_planner_contract(self) -> None:
        pack = load_default_pack()
        planner = pack["planner"]
        assert planner.model == "sonnet"
        assert planner.tools == ["Read", "Grep", "Glob", "Write"]
        assert planner.effort == "high"
        assert planner.maxTurns == 20
        # The structural scope-creep guard is the initialPrompt.
        assert planner.initialPrompt is not None
        assert "acceptance criteria" in planner.initialPrompt.lower()

    def test_general_purpose_contract(self) -> None:
        pack = load_default_pack()
        gp = pack["general-purpose"]
        assert gp.model == "sonnet"
        assert gp.effort == "medium"
        assert gp.maxTurns == 20
        # Network access yes, shell and recursion no.
        assert "WebFetch" in gp.tools
        assert "WebSearch" in gp.tools
        assert "Bash" not in gp.tools
        assert "Task" not in gp.tools

    def test_no_pack_member_has_task_tool(self) -> None:
        """Subagents are leaves. None of them get Task — locked by Phase 1."""
        pack = load_default_pack()
        for name, agent in pack.items():
            assert "Task" not in (agent.tools or []), (
                f"{name} must not have Task — subagents are leaves"
            )

    def test_all_pack_members_are_foreground(self) -> None:
        """Phase 1 contract: background=False for all three.

        Async subagents change the parent's reasoning model and are out
        of scope. Without an explicit assertion, the loader could ship
        background=None (SDK default) and silently break the contract.
        """
        pack = load_default_pack()
        for name, agent in pack.items():
            assert agent.background is False, (
                f"{name}.background must be False — async subagents are OOS"
            )

    def test_load_default_pack_is_deterministic(self) -> None:
        """SC-7 — two calls produce identical pack."""
        a = load_default_pack()
        b = load_default_pack()
        assert a == b


class TestPackHermeticity:
    """SC-5 — pack content is in-repo, prompt body is the literal file body."""

    def test_explorer_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "explorer.md")
        assert pack["explorer"].prompt == body

    def test_planner_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "planner.md")
        assert pack["planner"].prompt == body

    def test_general_purpose_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "general_purpose.md")
        assert pack["general-purpose"].prompt == body


class TestParsePackFile:
    """parse_pack_file happy paths and sad paths."""

    def test_happy_path_full_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "explorer.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read, Grep, Glob]
            effort: low
            maxTurns: 10
            initialPrompt: Begin by stating what you'll search for.
            ---

            # Role
            You are an explorer.
            """))
        key, agent = parse_pack_file(f)
        assert key == "explorer"
        assert agent.model == "haiku"
        assert agent.tools == ["Read", "Grep", "Glob"]
        assert agent.effort == "low"
        assert agent.maxTurns == 10
        assert agent.initialPrompt == "Begin by stating what you'll search for."
        assert "You are an explorer." in agent.prompt

    def test_filename_underscore_to_kebab_in_key(self, tmp_path: Path) -> None:
        f = tmp_path / "general_purpose.md"
        f.write_text(dedent("""\
            ---
            description: Catch-all.
            model: sonnet
            tools: [Read]
            ---

            body
            """))
        key, _ = parse_pack_file(f)
        assert key == "general-purpose"

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.md"
        f.write_text(dedent("""\
            ---
            description: Missing tools.
            model: haiku
            ---

            body
            """))
        with pytest.raises(PackLoadError) as exc:
            parse_pack_file(f)
        assert "tools" in str(exc.value)
        assert str(f) in str(exc.value)

    def test_empty_body_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read]
            ---

            """))
        with pytest.raises(PackLoadError) as exc:
            parse_pack_file(f)
        assert str(f) in str(exc.value)

    def test_no_frontmatter_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "no_fm.md"
        f.write_text("just some markdown\n")
        with pytest.raises(PackLoadError):
            parse_pack_file(f)

    def test_extra_unknown_frontmatter_field_is_ignored(self, tmp_path: Path) -> None:
        """Forward-compat: unknown fields are dropped, not errored."""
        f = tmp_path / "fwd.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read]
            future_field: some value
            ---

            body here
            """))
        key, agent = parse_pack_file(f)
        assert key == "fwd"
        assert agent.model == "haiku"


class TestMergePacks:
    """Phase 1 contract: per-key override at whole-AgentDefinition level."""

    def _agent(self, **overrides) -> AgentDefinition:
        defaults = {"description": "x", "prompt": "y", "model": "sonnet"}
        return AgentDefinition(**{**defaults, **overrides})

    def test_user_wins_on_collision_whole_definition(self) -> None:
        default = {"planner": self._agent(model="sonnet", maxTurns=20)}
        user = {"planner": self._agent(model="opus", maxTurns=5)}
        result = merge_packs(default, user)
        # User's full AgentDefinition replaces default's — no field merge.
        assert result["planner"].model == "opus"
        assert result["planner"].maxTurns == 5

    def test_user_adds_non_conflicting_key(self) -> None:
        default = {"explorer": self._agent()}
        user = {"reviewer": self._agent(description="reviews")}
        result = merge_packs(default, user)
        assert set(result.keys()) == {"explorer", "reviewer"}

    def test_none_user_returns_default(self) -> None:
        default = {"explorer": self._agent()}
        result = merge_packs(default, None)
        assert result == default
        # Always returns a fresh dict — caller mutations must not affect
        # the default pack.
        assert result is not default

    def test_empty_user_returns_default(self) -> None:
        default = {"explorer": self._agent()}
        result = merge_packs(default, {})
        assert result == default
        assert result is not default

    def test_result_mutation_does_not_affect_inputs(self) -> None:
        default = {"explorer": self._agent()}
        result = merge_packs(default, None)
        result["new"] = self._agent()
        assert "new" not in default


class TestSecurityDoc:
    """SC-11 — the pack README documents CLAUDE.md visibility."""

    def test_readme_has_security_section_heading(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text()
        # Heading regex per Phase 1: Security[: ].*CLAUDE\.md
        assert re.search(r"Security[: ].*CLAUDE\.md", text), (
            "Pack README must contain a section heading matching Security[: ].*CLAUDE\\.md"
        )

    def test_readme_names_network_capable_member(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text()
        assert "general-purpose" in text
        # Both network tools must be named — either alone is incomplete.
        assert "WebFetch" in text
        assert "WebSearch" in text

    def test_readme_explains_setting_sources_inheritance(self) -> None:
        """SC-11 sub-requirement (c): the section must state the mechanism
        (inherited setting_sources) by which subagents see CLAUDE.md."""
        readme = PACK_DIR / "README.md"
        text = readme.read_text()
        assert "setting_sources" in text, (
            "README must name the inheritance mechanism (setting_sources)"
        )

    def test_readme_recommends_audit(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text().lower()
        assert "audit" in text


def _read_body(path: Path) -> str:
    """Return the markdown body (everything after the closing `---`)."""
    text = path.read_text()
    # Frontmatter delimited by --- on its own line.
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise AssertionError(f"{path} does not have YAML frontmatter")
    return parts[2]


# ---------- Task 2: SdkTeammate integration ----------


def _agent(**overrides) -> AgentDefinition:
    defaults = {"description": "x", "prompt": "y", "model": "sonnet"}
    return AgentDefinition(**{**defaults, **overrides})


def _patch_sdk(monkeypatch, fake: FakeSDKClient):
    """Patch claude_crew.sdk_teammate.ClaudeSDKClient with a constructor
    that returns `fake` and stores the options on it."""
    captured: dict = {}

    def _ctor(options=None):
        captured["options"] = options
        fake.options = options
        return fake

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)
    return captured


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


async def _drive_one_noop_turn(
    broker: Broker, role: str, *, agents=None, system_prompt: str | None = None,
) -> None:
    """Spawn an SdkTeammate via the broker (using a closure factory) and
    deliver one envelope to completion."""
    def _factory(id, name, role, **_kwargs):
        kwargs = {}
        if agents is not None:
            kwargs["agents"] = agents
        if system_prompt is not None:
            kwargs["system_prompt"] = system_prompt
        return SdkTeammate(id=id, name=name, role=role, **kwargs)

    tid = await broker.spawn_teammate(role=role, name=None, factory=_factory)
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload="hello",
    ))
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        if broker.get_messages(recipient=LEAD_ID):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for teammate reply")


class TestSdkTeammateIntegration:
    """Task 2 — SC-2 (always-runs), SC-6, SC-9 (a) and (b)."""

    async def test_default_pack_auto_registered_on_spawn(
        self, monkeypatch, broker: Broker
    ) -> None:
        """SC-6: a teammate spawned with no override gets all three pack keys."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        await _drive_one_noop_turn(broker, role="planner")

        assert "options" in captured, "ClaudeSDKClient was never constructed"
        agents = captured["options"].agents
        assert set(agents.keys()) == {"explorer", "planner", "general-purpose"}

    async def test_internal_seam_custom_agents_dict(
        self, monkeypatch, broker: Broker
    ) -> None:
        """SC-9 case (a): explicit agents dict replaces default pack."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        custom = {"reviewer": _agent(description="reviews things")}
        await _drive_one_noop_turn(broker, role="reviewer", agents=custom)

        assert captured["options"].agents == custom
        assert "planner" not in captured["options"].agents

    async def test_internal_seam_explicit_empty_distinct_from_none(
        self, monkeypatch, broker: Broker
    ) -> None:
        """SC-9 case (b), A5: agents={} ≠ agents=None.

        Empty dict means "this teammate has no pack." The SDK receives
        the empty dict; the default pack is NOT loaded as a fallback.
        """
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        await _drive_one_noop_turn(broker, role="planner", agents={})

        assert captured["options"].agents == {}

    async def test_parent_system_prompt_does_not_leak_into_pack(
        self, monkeypatch, broker: Broker
    ) -> None:
        """SC-2 always-runs: parent's system_prompt must not appear in any
        AgentDefinition field of the registered pack."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        marker = "PARENT_MARKER_X9F2_DO_NOT_LEAK"
        await _drive_one_noop_turn(broker, role="planner", system_prompt=marker)

        for name, agent_def in captured["options"].agents.items():
            for field_name in (
                "description", "prompt", "model", "effort",
                "initialPrompt",
            ):
                value = getattr(agent_def, field_name)
                assert value is None or marker not in str(value), (
                    f"parent system_prompt leaked into {name}.{field_name}"
                )
            assert marker not in str(agent_def.tools or [])

    def test_sdk_factory_passes_agents_through(self) -> None:
        """Factory accepts agents kwarg and threads it to SdkTeammate."""
        custom = {"reviewer": _agent()}
        teammate = sdk_factory(
            "id-1", "alice", "planner", agents=custom,
        )
        assert isinstance(teammate, SdkTeammate)
        assert teammate._agents == custom

    def test_sdk_factory_no_agents_kwarg_loads_default(self) -> None:
        """Factory called without agents kwarg → SdkTeammate loads default pack."""
        teammate = sdk_factory("id-2", "bob", "explorer")
        assert isinstance(teammate, SdkTeammate)
        assert set(teammate._agents.keys()) == {
            "explorer", "planner", "general-purpose",
        }


# ---------- Task 3: Failure handling ----------


async def _drive_with_scripted(
    broker: Broker, monkeypatch, scripted: list,
) -> list:
    """Run one envelope through an SdkTeammate whose stream is `scripted`,
    return broker's lead-recipient messages."""
    fake = FakeSDKClient(scripted_responses=[scripted])
    _patch_sdk(monkeypatch, fake)

    def _factory(id, name, role, **_kwargs):
        return SdkTeammate(id=id, name=name, role=role)

    tid = await broker.spawn_teammate(role="planner", name=None, factory=_factory)
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload="hello",
    ))
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        msgs = broker.get_messages(recipient=LEAD_ID)
        if msgs:
            return msgs
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for teammate reply")


class TestFailureHandling:
    """Task 3 — SC-8(a), SC-8(b), multi-subagent (α) and (β)."""

    async def test_sc8a_graceful_failure_with_summary(
        self, monkeypatch, broker: Broker, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SC-8(a): failed task notif + empty parent text → synthesized envelope."""
        caplog.set_level("WARNING", logger="claude_crew.sdk_teammate")
        msgs = await _drive_with_scripted(
            broker, monkeypatch,
            task_failure_response("ran out of turns"),
        )
        assert len(msgs) == 1
        payload = msgs[0].payload
        assert payload["error"] == "invalid_response"
        assert "ran out of turns" in payload["message"]
        # WARNING log fired during the drain.
        assert any("subagent failure" in r.message for r in caplog.records)

    async def test_sc8a_graceful_failure_empty_summary_uses_default(
        self, monkeypatch, broker: Broker,
    ) -> None:
        """SC-8(a) edge: empty summary → default message in envelope."""
        msgs = await _drive_with_scripted(
            broker, monkeypatch,
            task_failure_response(""),  # empty summary
        )
        assert len(msgs) == 1
        payload = msgs[0].payload
        assert payload["error"] == "invalid_response"
        assert "subagent run did not complete" in payload["message"]

    async def test_sc8b_stream_level_exception(
        self, monkeypatch, broker: Broker, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SC-8(b): receive_response raises mid-stream → code=internal envelope."""
        caplog.set_level("WARNING", logger="claude_crew.sdk_teammate")

        # Hand-build a fake whose receive_response raises on first iteration.
        class RaisingFake(FakeSDKClient):
            async def receive_response(self):
                raise RuntimeError("subprocess crashed mid-stream")
                yield  # pragma: no cover  (make this a generator)

        fake = RaisingFake()
        _patch_sdk(monkeypatch, fake)

        def _factory(id, name, role, **_kwargs):
            return SdkTeammate(id=id, name=name, role=role)

        tid = await broker.spawn_teammate(role="planner", name=None, factory=_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hello",
        ))
        # Wait for envelope.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            msgs = broker.get_messages(recipient=LEAD_ID)
            if msgs:
                break
            await asyncio.sleep(0.01)
        assert len(msgs) == 1
        payload = msgs[0].payload
        assert payload["error"] == "internal"
        assert "subprocess crashed mid-stream" in payload["message"]
        # Worker task must still be alive — survives one bad turn.
        teammate = broker._teammates[tid]
        assert teammate._task is not None and not teammate._task.done()
        # WARNING logged.
        assert any(
            "stream-level failure" in r.message for r in caplog.records
        )

    async def test_multi_subagent_alpha_last_fails_empty_text(
        self, monkeypatch, broker: Broker,
    ) -> None:
        """(α): last subagent fails, parent never produced text → envelope from last summary."""
        # Two TaskNotificationMessages, the second failed; no AssistantMessage.
        scripted = [
            task_notification(status="completed", summary="first ok"),
            task_notification(status="failed", summary="second bad"),
            ResultMessage(
                subtype="success", duration_ms=0, duration_api_ms=0,
                is_error=False, num_turns=1, session_id="default",
            ),
        ]
        msgs = await _drive_with_scripted(broker, monkeypatch, scripted)
        assert len(msgs) == 1
        payload = msgs[0].payload
        assert payload["error"] == "invalid_response"
        # Last failure ("second bad") wins over an absent earlier one.
        assert "second bad" in payload["message"]

    async def test_multi_subagent_beta_failure_with_recovery(
        self, monkeypatch, broker: Broker, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(β): subagent fails, parent narrates over → normal envelope + WARNING log.

        Load-bearing for the recovery-wins contract: if a refactor only
        logs on no-recovery, this test fails because the WARNING isn't
        emitted on the recovery path.
        """
        caplog.set_level("WARNING", logger="claude_crew.sdk_teammate")
        msgs = await _drive_with_scripted(
            broker, monkeypatch,
            task_failure_then_text("bad subagent", "ok, here's the answer"),
        )
        assert len(msgs) == 1
        payload = msgs[0].payload
        # Recovery wins — payload is normal text, not error.
        assert "error" not in payload
        assert payload["text"] == "ok, here's the answer"
        # But the warning log fired regardless — operator visibility preserved.
        assert any("bad subagent" in r.message for r in caplog.records)

    async def test_successful_turn_no_warning(
        self, monkeypatch, broker: Broker, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No TaskNotificationMessage → no WARNING log, normal envelope."""
        caplog.set_level("WARNING", logger="claude_crew.sdk_teammate")
        msgs = await _drive_with_scripted(
            broker, monkeypatch, text_response("clean reply"),
        )
        assert len(msgs) == 1
        assert msgs[0].payload["text"] == "clean reply"
        # No warnings from our logger.
        sdk_warnings = [
            r for r in caplog.records
            if r.name == "claude_crew.sdk_teammate" and r.levelname == "WARNING"
        ]
        assert sdk_warnings == []
