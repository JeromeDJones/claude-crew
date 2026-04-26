"""Live E2E for the user/project agent loader — Feature #3b SC-8.

Gated by ``CLAUDE_CREW_LIVE_TESTS=1``. Cost: ~$0.20 per run (one
teammate subprocess, two turns invoking two user-defined subagents).

Verifies the full #3b path against a real SDK: agents planted in fake
``~/.claude/agents/`` and fake ``<project>/.claude/agents/`` directories
flow through ``default_factory()`` → merged pack → ``SdkTeammate`` →
real subprocess → subagent invocation that produces a tool side-effect
on disk.

Live-probe checklist:
- The question prompt does NOT contain the file content the subagent
  is asked to write — we verify the side-effect via on-disk file
  contents, but we don't ask the subagent to repeat a planted secret.
  (Item 1 of TEMPLATE.md's live-probe checklist isn't directly
  applicable here — there is no planted secret being quoted back.)
- Tool-name correctness verified by observable side effect (file on
  disk after the turn), not by the agent's narration. (Item 2.)
- No assertions on token counts. (Item 3.)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from textwrap import dedent

import pytest

from claude_crew import factories
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


LIVE_TURN_TIMEOUT = 180.0


async def _wait_for_lead(broker: Broker, count: int, timeout: float = LIVE_TURN_TIMEOUT) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


async def _send_and_wait(
    broker: Broker, tid: str, prompt: str, expected_count: int,
) -> Envelope:
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload=prompt,
    ))
    await _wait_for_lead(broker, expected_count)
    msgs = broker.get_messages(recipient=LEAD_ID)
    return msgs[-1]


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


def _plant_agent(dir_: Path, name: str, description: str, body: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{name}.md").write_text(dedent(f"""\
        ---
        description: {description}
        model: haiku
        tools: [Write]
        ---

        {body}
        """))


class TestUserAndProjectAgentsReachRealSdk:
    """SC-8 — full #3b path against the real SDK with planted fixtures."""

    async def test_user_and_project_agents_invokable(
        self,
        broker: Broker,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="claude_crew.sdk_teammate")

        # Two side-effect targets — files we will assert on disk.
        user_target = tmp_path / "user-scribe-was-here.txt"
        project_target = tmp_path / "project-scribe-was-here.txt"

        # Plant a user-level agent and a project-level agent.
        fake_home = tmp_path / "home"
        fake_project = tmp_path / "project"
        _plant_agent(
            fake_home / ".claude" / "agents",
            name="user-scribe",
            description="Live-test user-level agent. Writes a file.",
            body=(
                "You are a user-level test agent. When invoked, use the Write "
                "tool to create the exact file path the operator asks for, "
                "with the content they specify. Then stop."
            ),
        )
        _plant_agent(
            fake_project / ".claude" / "agents",
            name="project-scribe",
            description="Live-test project-level agent. Writes a file.",
            body=(
                "You are a project-level test agent. When invoked, use the "
                "Write tool to create the exact file path the operator asks "
                "for, with the content they specify. Then stop."
            ),
        )

        # Point the closure factory at the planted dirs.
        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
        monkeypatch.chdir(fake_project)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        factory = factories.default_factory()
        # Sanity: the factory must require auth and be the closure form
        # (not the raw sdk_factory).
        assert getattr(factory, "requires_auth", False) is True

        tid = await broker.spawn_teammate(role="parent", name=None, factory=factory)

        # Turn 1: invoke the user-level agent. The subagent prompt asks
        # it to write `user_target`. The check is the file's existence
        # and content on disk (item 2 of the live-probe checklist).
        await _send_and_wait(
            broker, tid,
            "Use the Task tool to invoke the 'user-scribe' subagent. Send "
            "it this prompt verbatim:\n"
            f"Use the Write tool to create the file '{user_target}' with "
            "exactly this content (one line, no quotes):\n"
            "user-scribe ok\n"
            "Then stop. Do not write anything else.\n"
            "After the subagent finishes, tell me what file it wrote to.",
            expected_count=1,
        )
        assert user_target.exists(), (
            f"user-scribe never wrote {user_target} — user-level agent "
            "did not reach the merged pack via default_factory()"
        )
        assert "user-scribe ok" in user_target.read_text()

        # Turn 2: invoke the project-level agent.
        await _send_and_wait(
            broker, tid,
            "Use the Task tool to invoke the 'project-scribe' subagent. "
            "Send it this prompt verbatim:\n"
            f"Use the Write tool to create the file '{project_target}' "
            "with exactly this content (one line, no quotes):\n"
            "project-scribe ok\n"
            "Then stop. Do not write anything else.\n"
            "After the subagent finishes, tell me what file it wrote to.",
            expected_count=2,
        )
        assert project_target.exists(), (
            f"project-scribe never wrote {project_target} — project-level "
            "agent did not reach the merged pack"
        )
        assert "project-scribe ok" in project_target.read_text()

        # No subagent failure WARNINGs across the two turns.
        sdk_warnings = [
            r for r in caplog.records
            if r.name == "claude_crew.sdk_teammate"
            and r.levelname == "WARNING"
        ]
        assert sdk_warnings == [], (
            f"unexpected subagent failure warnings: "
            f"{[r.message for r in sdk_warnings]}"
        )
