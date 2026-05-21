"""Playwright test for AT-10: tool-event row clickable → modal with body + Copy button.

Loads the Mission Control dashboard against a fixture UIServer that has one
tool event with a stored body ``"file contents"``. Exercises the full click →
modal → text-visible path.

Run prerequisite: uv run playwright install chromium
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from claude_crew.broker import Broker, TeammateInfo
from claude_crew.redaction import REDACTION_VERSION
from claude_crew.teammate import StubTeammate, ToolEvent
from claude_crew.ui_server import UIServer


# ── constants ─────────────────────────────────────────────────────────────────

TOOL_USE_ID = "toolu_test1234"
TOOL_BODY = "file contents"


# ── fixture helpers ───────────────────────────────────────────────────────────


def _build_tool_output_broker() -> tuple[Broker, str]:
    """Return (broker, teammate_id) seeded with a completed ToolEvent + stored output."""
    broker = Broker()
    tid = f"t-tool-{uuid.uuid4().hex[:10]}"

    # Register a live StubTeammate
    tm = StubTeammate(id=tid, name="builder", role="builder")
    broker._teammates[tid] = tm
    broker._inboxes[tid] = asyncio.Queue()
    broker._info[tid] = TeammateInfo(
        id=tid,
        name="builder",
        role="builder",
        spawned_at=time.time(),
        alive=True,
    )
    broker._configs[tid] = {
        "tools": ["Read"],
        "skills": [],
        "permission_mode": "default",
        "disallowed_tools": [],
        "mcp_servers": [],
        "system_prompt": "Test agent.",
        "model": "claude-sonnet-4-6",
    }

    # Inject a completed ToolEvent so it appears in the dashboard stream
    now = time.time()
    ev = ToolEvent(
        teammate_id=tid,
        tool_name="Read",
        tool_use_id=TOOL_USE_ID,
        started_at_wallclock=now - 1.0,
        finished_at_wallclock=now,
        duration_seconds=1.0,
        outcome="ok",
        args_summary="/some/file.py",
        error_summary=None,
        redaction_version=REDACTION_VERSION,
    )
    tm._completed_tool_events.append(ev)

    # Seed the tool output body
    tm.store_tool_output(TOOL_USE_ID, TOOL_BODY)

    return broker, tid


def _start_server(broker: Broker) -> tuple[str, uvicorn.Server, threading.Thread]:
    """Spin up a UIServer on a free port; return (base_url, server, thread)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port)
    app = ui._make_app()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("UIServer did not start within 10 seconds")

    return f"http://127.0.0.1:{port}", server, t


@pytest.fixture(scope="module")
def tool_output_server_url():
    """UIServer with a seeded tool event and stored output body; yield base URL."""
    broker, _tid = _build_tool_output_broker()
    url, server, t = _start_server(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_at10_tool_row_click_opens_modal_with_body_and_copy(tool_output_server_url, page):
    """AT-10: clicking a tool-event row opens a modal showing the stored body + Copy button."""
    page.goto(tool_output_server_url)

    # Wait for the WS state to arrive and the clickable tool row to render.
    # After our change, tool rows with tool_use_id carry CSS class "tool-output-row".
    tool_row = page.locator(".tool-output-row").first
    tool_row.wait_for(state="visible", timeout=15000)

    # Click the tool row — triggers fetch + modal open
    tool_row.click()

    # The modal backdrop must appear
    backdrop = page.locator(".tm-detail-backdrop")
    backdrop.wait_for(state="visible", timeout=5000)

    # The panel must appear inside the backdrop
    modal = page.locator(".tm-detail-panel")
    modal.wait_for(state="visible", timeout=3000)

    # The body must be visible in the pre element (fetch completes)
    pre = page.locator(".tm-detail-prompt")
    pre.wait_for(state="visible", timeout=10000)
    pre_text = pre.inner_text()
    assert TOOL_BODY in pre_text, (
        f"Expected {TOOL_BODY!r} in modal pre; got: {pre_text[:300]}"
    )

    # A button labeled "Copy" must be present
    copy_btn = modal.locator("button", has_text="Copy")
    assert copy_btn.count() > 0, "Expected 'Copy' button in modal"
    copy_btn.first.wait_for(state="visible", timeout=3000)
