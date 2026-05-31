"""AT#4 — Gated live test: real SdkTeammate writes a memory file on flush.

Skipped unless CLAUDE_CREW_LIVE_TESTS=1 is set.

PASS = a real SDK teammate spawned with a project memory scope writes or
appends a memory file under its role's project memory directory when
begin_graceful_termination is called, and the file is non-empty.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

LIVE_TESTS = os.environ.get("CLAUDE_CREW_LIVE_TESTS") == "1"


@pytest.mark.skipif(not LIVE_TESTS, reason="CLAUDE_CREW_LIVE_TESTS=1 not set")
class TestLiveGracefulFlush:
    """AT#4: real teammate writes a memory file when flushed via begin_graceful_termination."""

    @pytest.mark.asyncio
    async def test_flush_writes_memory_file(self, tmp_path: Path) -> None:
        """Spawn a real SdkTeammate, flush it, assert a non-empty memory file exists."""
        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.broker import Broker
        from claude_crew.sdk_teammate import SdkTeammate
        from claude_crew.teammate_memory import memory_dir

        role = "live-flush-tester"
        memory_scope = "project"

        agent_def = AgentDefinition(
            description=(
                "A test agent.  When asked to persist memories, "
                "write a brief note to your memory file using the Write tool."
            ),
            prompt=(
                "You are a test agent.  You have access to the Write tool to "
                "persist memories for future sessions.  When flushed, write a "
                "single-line memory note to your designated memory file."
            ),
            tools=["Write"],
            memory=memory_scope,  # type: ignore[arg-type]
        )

        tm = SdkTeammate(
            id="live-flush-tm",
            name="live-flush-tester",
            role=role,
            agents={role: agent_def},
            pack_bodies={role: agent_def.prompt},
            cwd=str(tmp_path),
        )

        assert tm.has_memory_surface(), (
            "SdkTeammate with memory='project' + Write must report has_memory_surface()=True"
        )

        broker = Broker()
        inbox: asyncio.Queue = asyncio.Queue()

        await tm.start(broker, inbox)
        try:
            # Wait for the liveness poll to signal readiness.
            await asyncio.wait_for(tm._poll_started.wait(), timeout=30.0)
            await asyncio.sleep(0.5)  # let the SDK subprocess start up

            # Flush the teammate with a generous timeout for a real SDK turn.
            flush_timeout = 90.0
            t0 = time.monotonic()
            await asyncio.wait_for(
                tm.begin_graceful_termination(timeout=flush_timeout),
                timeout=flush_timeout + 10.0,
            )
            elapsed = time.monotonic() - t0

            assert elapsed < flush_timeout, (
                f"flush took {elapsed:.1f}s, must be < {flush_timeout}s"
            )

            # Locate the memory directory for the role under the tmp project root.
            mem_dir = memory_dir(role, scope=memory_scope, project_root=tmp_path)
            mem_file = mem_dir / "MEMORY.md"

            assert mem_file.exists(), (
                f"memory file {mem_file} must exist after flush; "
                f"contents of {mem_dir}: {list(mem_dir.iterdir()) if mem_dir.exists() else 'dir missing'}"
            )
            content = mem_file.read_text(encoding="utf-8", errors="replace").strip()
            assert content, (
                f"memory file {mem_file} must be non-empty after flush; got empty file"
            )
        finally:
            await tm.shutdown()
