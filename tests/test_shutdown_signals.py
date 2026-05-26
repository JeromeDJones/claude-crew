"""Integration tests for signal-driven clean shutdown of claude-crew.

A SIGTERM (or SIGINT) to a running claude-crew process must:
  1. Deregister the InstanceRegistry entry.
  2. Cancel the task group (UIServer, leader-watcher, MCP stdio loop).
  3. Exit cleanly within a small budget.

Pre-fix behavior: SIGTERM only ran `registry.deregister`; UIServer kept the
event loop alive so the process never exited — orphan claude-crew processes
required SIGKILL to clear. This file pins the fixed behavior end-to-end by
spawning the real binary, signaling it, and asserting on the observable
outcomes (exit, registry).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXIT_BUDGET_SECONDS = 5.0


def _spawn_claude_crew(registry_dir: Path) -> subprocess.Popen:
    """Spawn claude-crew with a stub-mode env and an isolated registry dir.

    stdin is piped (so `run_stdio_async` blocks on read, mimicking a live MCP
    client). Without this the server would exit immediately on stdin EOF and
    the test wouldn't be exercising the signal path.
    """
    env = os.environ.copy()
    env["CLAUDE_CREW_TEAMMATE_MODE"] = "stub"
    env["CLAUDE_CREW_TRANSCRIPT_DISABLED"] = "1"
    env["CLAUDE_CREW_INSTANCE_REGISTRY_DIR"] = str(registry_dir)

    # Use `sys.executable -m claude_crew.cli` rather than `uv run claude-crew`
    # so signals reach the python process directly. `uv run` is a wrapper that
    # doesn't reliably forward signals to its child, which would mask the
    # behavior under test (the handler IS reached on direct kill of the python
    # pid — verified manually). Production launch via `uv run` carries the same
    # caveat; if forwarding ever becomes a concern, fix the launcher, not this
    # test.
    return subprocess.Popen(
        [sys.executable, "-m", "claude_crew.cli"],
        cwd=str(_REPO_ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_registry_entry(registry_dir: Path, deadline_seconds: float = 15.0) -> dict:
    """Poll until claude-crew has registered itself, then return its entry."""
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        if registry_dir.exists():
            entries = list(registry_dir.glob("*.json"))
            if entries:
                try:
                    return json.loads(entries[0].read_text())
                except json.JSONDecodeError:
                    pass  # mid-write; retry
        time.sleep(0.1)
    raise AssertionError(f"claude-crew did not register within {deadline_seconds}s")


def _assert_clean_shutdown(proc: subprocess.Popen, registry_dir: Path, sig: int) -> None:
    """Send `sig` and assert the process exits within budget AND the registry
    entry is removed (proves the shutdown ran deregister + cancel)."""
    proc.send_signal(sig)
    try:
        proc.wait(timeout=_EXIT_BUDGET_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        pytest.fail(
            f"claude-crew did not exit within {_EXIT_BUDGET_SECONDS}s after "
            f"signal {sig} — pre-fix behavior (process hangs alive)."
        )
    # Clean exit: 0 on signal-triggered cooperative shutdown OR -sig if the
    # signal raced past our handler. Anything else (1, 137 from SIGKILL) is a
    # regression.
    assert proc.returncode in (0, -sig), (
        f"unexpected exit code {proc.returncode}; stderr:\n{proc.stderr.read()}"
    )
    # Registry must be empty — our SIGTERM handler calls registry.deregister().
    remaining = list(registry_dir.glob("*.json"))
    assert remaining == [], (
        f"registry not deregistered on shutdown; leftover entries: "
        f"{[p.read_text() for p in remaining]}"
    )


class TestSignalShutdown:
    """End-to-end: signal a real claude-crew process, assert it dies cleanly."""

    def test_sigterm_triggers_clean_exit_and_deregister(self, tmp_path: Path) -> None:
        """Happy path: kill <pid> (SIGTERM) exits within budget + cleans registry."""
        registry_dir = tmp_path / "instances"
        proc = _spawn_claude_crew(registry_dir)
        try:
            _wait_for_registry_entry(registry_dir)
            _assert_clean_shutdown(proc, registry_dir, signal.SIGTERM)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_sigint_triggers_clean_exit_and_deregister(self, tmp_path: Path) -> None:
        """Sad/variant path: Ctrl+C (SIGINT) takes the same clean shutdown path.
        Pre-fix, SIGINT had no handler at all — the default would have raised
        KeyboardInterrupt inside anyio.run and exited via crash-style teardown.
        """
        registry_dir = tmp_path / "instances"
        proc = _spawn_claude_crew(registry_dir)
        try:
            _wait_for_registry_entry(registry_dir)
            _assert_clean_shutdown(proc, registry_dir, signal.SIGINT)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
