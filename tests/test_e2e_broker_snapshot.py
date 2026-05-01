"""E2E integration tests for Feature #18: Broker Snapshot + Dashboard Polish.

Exercises the full assembled pipeline: Broker.snapshot() → UIServer._build_local_instance()
→ dashboard payload. Three tests covering:
  1. Full pipeline with real Broker, real StubTeammates, and real git branch detection.
  2. F14 tombstone aggregate preserved through the snapshot pipeline (synthetic snapshot).
  3. _unreachable_instance shape preserved (SC-12 regression guard).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from claude_crew.broker import Broker, BrokerSnapshot, LiveTeammateInfo, TeammateInfo
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.ui_server import UIServer, _unreachable_instance


# ── helpers ────────────────────────────────────────────────────────────────────

_REPO_ROOT = str(Path(__file__).parent.parent)

_EXPECTED_INSTANCE_KEYS = {
    "id", "is_local", "label", "cwd", "branch",
    "uptime", "status", "cost", "tokens", "agents",
}


def _stub_factory(id: str, name: str, role: str, **_kwargs: Any):
    from claude_crew.teammate import StubTeammate
    return StubTeammate(id=id, name=name, role=role)


def _current_repo_branch() -> str:
    """Ask git for the current branch at test-time so assertions stay CI-safe."""
    try:
        result = subprocess.run(
            ["git", "-C", _REPO_ROOT, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5.0,
        )
        branch = result.stdout.strip()
        return branch if branch else "main"
    except Exception:
        return "main"


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_dashboard_payload_with_real_branch() -> None:
    """Full pipeline: Broker → snapshot → UIServer → dashboard payload.

    BDD (T5, Scenario 1):
      - Real Broker with 2 alive StubTeammates.
      - UIServer pointing at the actual claude-crew repo (so git branch detection fires).
      - _build_state(local_only=True) produces a valid payload.
      - instances[0] has exactly 2 agents (both alive StubTeammates).
      - branch reflects the real git branch (not the hardcoded "main").
      - All required instance keys present.
      - transcripts is a dict.
      - cost is a float (zero for StubTeammates, which is fine).
    """
    broker = Broker()
    try:
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        await broker.spawn_teammate(role="reviewer", name="reviewer", factory=_stub_factory)

        ui = UIServer(broker=broker, port=0, cwd=_REPO_ROOT)
        state = await ui._build_state(local_only=True)

        # Top-level shape
        assert isinstance(state["instances"], list)
        assert isinstance(state["transcripts"], dict)
        assert len(state["instances"]) == 1

        instance = state["instances"][0]

        # All required keys present
        assert _EXPECTED_INSTANCE_KEYS <= set(instance.keys()), (
            f"Missing keys: {_EXPECTED_INSTANCE_KEYS - set(instance.keys())}"
        )

        # 2 alive agents
        assert len(instance["agents"]) == 2, (
            f"Expected 2 alive agents, got {len(instance['agents'])}"
        )

        # Branch: real git branch, not the hardcoded "main" fallback.
        # Compute the expected branch at test-time so the assertion survives CI
        # (where the branch name may differ from "feature/broker-snapshot-dashboard-polish").
        expected_branch = _current_repo_branch()
        assert instance["branch"] == expected_branch, (
            f"Expected branch '{expected_branch}', got '{instance['branch']}'"
        )
        # Additional guard: if we're on any feature branch, it won't be "main"
        if expected_branch != "main":
            assert instance["branch"] != "main", (
                "branch should reflect real git branch, not hardcoded 'main'"
            )

        # cost is a float (StubTeammates produce zero cost, which is valid)
        assert isinstance(instance["cost"], float)

        # transcripts is keyed by crew_id
        assert broker.crew_id in state["transcripts"]

    finally:
        await broker.shutdown_all()


def test_e2e_tombstone_aggregate_preserved() -> None:
    """F14 cost aggregation survives the snapshot pipeline end-to-end.

    BDD (T5, Scenario 2):
      - Synthetic BrokerSnapshot with 1 alive teammate ($0.05) and 1 dead ($0.20).
      - _build_local_instance produces instance cost == $0.25 (sum of both).
      - agents[] contains only the alive teammate (dead excluded per D-10).

    Uses the synthetic-snapshot pattern from TestUIServerBrokerDecoupling to avoid
    the overhead of scripted SDK turns. The goal is to prove the aggregation path
    through the snapshot pipeline works, not to re-prove F14's token capture.
    """
    now = time.time()

    info_alive = TeammateInfo(
        id="t-alive", name="alice", role="builder",
        spawned_at=now - 60, alive=True,
    )
    info_dead = TeammateInfo(
        id="t-dead", name="bob", role="reviewer",
        spawned_at=now - 120, alive=False,
        total_cost_usd_at_death=0.20,
        total_input_tokens_at_death=200,
        total_output_tokens_at_death=80,
    )
    live_entry = LiveTeammateInfo(
        info=info_alive,
        status={
            "current_tool_count": 0,
            "current_turn_started_at_wallclock": None,
            "total_cost_usd": 0.05,
            "total_input_tokens": 50,
            "total_output_tokens": 20,
            "current_tools": [],
            "current_tool": None,
            "last_activity_at_wallclock": None,
        },
        model="claude-sonnet-4-6",
    )
    snapshot = BrokerSnapshot(
        crew_id="crew-f14",
        teammates=(info_alive, info_dead),
        live=(live_entry,),
        log=(),
    )

    broker = Broker()
    ui = UIServer(broker=broker, port=0)
    instance, _ = ui._build_local_instance(snapshot)

    # Alive teammate is in agents[]
    assert len(instance["agents"]) == 1, (
        f"Expected 1 alive agent, got {len(instance['agents'])}"
    )
    assert instance["agents"][0]["id"] == "t-alive"

    # Dead teammate excluded from agents[] (D-10)
    agent_ids = [a["id"] for a in instance["agents"]]
    assert "t-dead" not in agent_ids

    # F14 aggregate: alive ($0.05) + tombstone ($0.20) = $0.25
    assert abs(instance["cost"] - 0.25) < 1e-9, (
        f"Expected instance cost 0.25 (alive $0.05 + dead $0.20), got {instance['cost']}"
    )

    # Alive agent cost matches what was in the snapshot status
    assert abs(instance["agents"][0]["cost"] - 0.05) < 1e-9, (
        f"Expected agent cost 0.05, got {instance['agents'][0]['cost']}"
    )


def test_e2e_unreachable_instance_shape_preserved() -> None:
    """SC-12 regression guard: _unreachable_instance shape unchanged after refactor.

    BDD (T5, Scenario 3):
      - Calls _unreachable_instance("crew-test") directly.
      - branch == "main" (NOT replaced by git detection — only local instances get the real branch).
      - is_local == False.
      - status == "unreachable".
      - cost == 0.0.
      - All expected keys present.

    If a future refactor accidentally wires _unreachable_instance to call _get_branch(),
    the branch assertion catches it immediately.
    """
    result = _unreachable_instance("crew-test")

    # All required keys present
    assert _EXPECTED_INSTANCE_KEYS <= set(result.keys()), (
        f"Missing keys: {_EXPECTED_INSTANCE_KEYS - set(result.keys())}"
    )

    # SC-12: unreachable instances always have branch="main"
    # (we cannot determine the remote instance's branch from outside)
    assert result["branch"] == "main", (
        f"_unreachable_instance must keep branch='main' (SC-12), got '{result['branch']}'"
    )

    # Identity fields
    assert result["id"] == "crew-test"
    assert result["is_local"] is False
    assert result["status"] == "unreachable"

    # Numeric zeros — nothing contributed
    assert result["cost"] == 0.0
    assert result["tokens"] == {"in": 0, "out": 0}
    assert result["agents"] == []
