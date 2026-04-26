"""Empirical probe: do top-level SDK-spawned teammates have access to
Claude Code's auto-memory subsystem?

`doc/research/sdk-memory.md` §3 concluded the SDK does not *activate*
auto-memory at the parent level, but the test was indirect (asked the
model what memories it had, didn't ask it to read/write the directory
directly). This probe closes that loop.

Two turns:
  1. Ask the teammate to read its auto-memory MEMORY.md file by path.
  2. Ask it to write a unique sentinel string into that file, then
     re-read to confirm.

Cost: ~$0.02. Gated on CLAUDE_CREW_LIVE_TESTS=1 (this hits the real API).

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/auto_memory_probe.py`

If positive: top-level teammates have a path to cross-session memory we
can build on cheaply. If negative: cross-session persistence is a
deliberate architectural lift, not a config flip.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _send_and_wait(
    broker: Broker, tid: str, prompt: str, expected_count: int,
    timeout: float = 180.0,
) -> Envelope:
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload=prompt,
    ))
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msgs = broker.get_messages(recipient=LEAD_ID)
        if len(msgs) >= expected_count:
            return msgs[-1]
        await asyncio.sleep(0.5)
    raise SystemExit(
        f"timed out waiting for {expected_count} replies; got {len(msgs)}"
    )


def _expected_memory_dir(cwd: Path) -> Path:
    """The auto-memory directory Claude Code uses for a given cwd.

    Encoding: replace path separators with hyphens, prefix with hyphen.
    `/home/jerome/dev/claude-crew` → `-home-jerome-dev-claude-crew`
    """
    encoded = "-" + str(cwd).strip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded / "memory"


async def main() -> int:
    _gated()

    cwd = Path.cwd()
    expected_dir = _expected_memory_dir(cwd)
    sentinel = f"auto-memory-probe-{uuid.uuid4().hex[:12]}"

    print(f"cwd:               {cwd}")
    print(f"expected dir:      {expected_dir}")
    print(f"  exists: {expected_dir.exists()}")
    print(f"sentinel:          {sentinel}")
    print()

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(
            role="memory-probe", name=None, factory=sdk_factory,
        )

        # Probe 1: can the teammate see / read its auto-memory file?
        print("=== Probe 1: read ===")
        reply = await _send_and_wait(
            broker, tid,
            (
                "I'm running an experiment about Claude Code's auto-memory "
                "subsystem. Please do exactly the following and report results:\n"
                "1. Run `ls -la ~/.claude/projects/` and report what's there.\n"
                f"2. Check whether this specific path exists: {expected_dir}\n"
                f"3. If it exists, run `ls -la {expected_dir}` and report contents.\n"
                f"4. If MEMORY.md exists in that dir, read it and quote the "
                "first 500 characters.\n"
                "Report everything plainly. If a step fails, say so and "
                "continue to the next."
            ),
            expected_count=1,
        )
        print(reply.payload.get("text", "<no text>"))
        print()

        # Probe 2: can the teammate write a sentinel and read it back?
        print("=== Probe 2: write+read ===")
        target = expected_dir / "MEMORY.md"
        reply = await _send_and_wait(
            broker, tid,
            (
                f"Now please attempt the following:\n"
                f"1. Append this exact line (with a leading newline) to the "
                f"file {target}:\n"
                f"   - PROBE: {sentinel}\n"
                f"2. Re-read the file and confirm whether the line appears.\n"
                f"3. Report exactly what happened — success, failure, or "
                "permission error. Quote any error messages verbatim.\n"
                "If you don't have permission to write or the path doesn't "
                "exist, that's a valid result — just report it clearly."
            ),
            expected_count=2,
        )
        print(reply.payload.get("text", "<no text>"))
        print()

        # Local verification — did the sentinel actually land?
        print("=== Local verification ===")
        if target.exists():
            content = target.read_text()
            if sentinel in content:
                print(f"✓ sentinel '{sentinel}' is present in {target}")
            else:
                print(f"✗ sentinel '{sentinel}' NOT found in {target}")
                print(f"  (file exists, last 500 chars:)")
                print(f"  ...{content[-500:]!r}")
        else:
            print(f"✗ {target} does not exist after probe")

    finally:
        await broker.shutdown_all()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
