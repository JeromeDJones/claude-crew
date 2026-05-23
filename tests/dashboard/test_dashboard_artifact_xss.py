"""XSS regression tests for the artifact markdown render pipeline.

Verifies that all three payload classes survive the full browser-side
sanitizeArtifact() pipeline (marked.parse → DOMPurify.sanitize → link-hardening)
without executing JavaScript.

The tests inject synthetic artifact payloads via monkey-patched
ArtifactRegistry.metadata_list() / .get() so the UIServer serves them
from /api/state and /artifact/{crew_id}/{artifact_id} exactly as production
would — no test-only shortcuts around the pipeline.

Run prerequisite: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn

from claude_crew.artifact_registry import ArtifactRecord, ArtifactRegistry
from claude_crew.broker import Broker, BrokerSnapshot, LiveTeammateInfo, TeammateInfo
from claude_crew.ui_server import UIServer


# ── helpers ──────────────────────────────────────────────────────────────────


def _alive_info(idx: int) -> TeammateInfo:
    return TeammateInfo(
        id=f"t-{idx}",
        name=f"agent-{idx}",
        role="builder",
        spawned_at=time.time() - 60,
        alive=True,
    )


def _live_entry(info: TeammateInfo) -> LiveTeammateInfo:
    return LiveTeammateInfo(
        info=info,
        status={
            "current_tool_count": 0,
            "current_turn_started_at_wallclock": None,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "current_tools": [],
            "current_tool": None,
            "last_activity_at_wallclock": None,
        },
        model="claude-sonnet-4-6",
    )


def _make_snapshot() -> BrokerSnapshot:
    info = _alive_info(0)
    return BrokerSnapshot(
        crew_id="test-crew",
        teammates=(info,),
        live=(_live_entry(info),),
        log=(),
        dead_configs={},
    )


def _patched_broker(snapshot: BrokerSnapshot) -> Broker:
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    return b


def _make_artifact(body: str, artifact_id: str | None = None) -> ArtifactRecord:
    aid = artifact_id or uuid.uuid4().hex
    return ArtifactRecord(
        artifact_id=aid,
        crew_id="test-crew",
        title="XSS Test Artifact",
        path_label="/tmp/test.md",
        surfacing_teammate="agent-0",
        timestamp_utc=int(time.time()),
        body=body,
    )


class _FakeRegistry:
    """Minimal ArtifactRegistry stand-in that serves a single artifact."""

    def __init__(self, record: ArtifactRecord) -> None:
        self._record = record

    def metadata_list(self) -> list[dict]:
        r = self._record
        return [
            {
                "artifact_id": r.artifact_id,
                "crew_id": r.crew_id,
                "title": r.title,
                "path_label": r.path_label,
                "surfacing_teammate": r.surfacing_teammate,
                "timestamp_utc": r.timestamp_utc,
            }
        ]

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        if artifact_id == self._record.artifact_id:
            return self._record
        return None


def _start_server(broker: Broker, registry: _FakeRegistry) -> tuple[str, Any, threading.Thread]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port, artifact_registry=registry)  # type: ignore[arg-type]
    app = ui._make_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
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


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_xss_server(payload: str) -> tuple[str, Any, threading.Thread, str]:
    """Spin up a UIServer seeded with a single artifact whose body is the given payload.

    Returns (url, server, thread, artifact_id).
    """
    record = _make_artifact(payload)
    registry = _FakeRegistry(record)
    snapshot = _make_snapshot()
    broker = _patched_broker(snapshot)
    url, server, t = _start_server(broker, registry)
    return url, server, t, record.artifact_id


# ── helpers for browser-side verification ────────────────────────────────────


_XSS_MARKER = "XSS_FIRED"

_PROBE_SCRIPT = """
() => {
    // Returns true if any XSS fired (window.XSS_FIRED is set by the
    // onerror/onload/onclick attribute payloads that DOMPurify must strip).
    return window["%s"] === true;
}
""" % _XSS_MARKER


def _open_artifact_drawer(page, url: str, artifact_id: str) -> None:
    """Navigate to the dashboard and programmatically open the artifact drawer."""
    page.goto(url)
    # Wait for the Artifacts pill to appear (MCTopBar renders it when
    # activeArtifacts is non-empty).
    page.locator('[data-testid="artifacts-pill"]').wait_for(state="visible", timeout=15_000)
    # Click the pill to open the tray.
    page.locator('[data-testid="artifacts-pill"]').click()
    # Click the first artifact entry in the tray (our only artifact).
    page.locator(".artifact-tray-item").first.wait_for(state="visible", timeout=5_000)
    page.locator(".artifact-tray-item").first.click()
    # Wait for the drawer body to be rendered.
    page.locator(".artifact-md").wait_for(state="visible", timeout=10_000)


# ── XSS payload classes ───────────────────────────────────────────────────────

# Payload class 1: onerror attribute on an img tag.
# marked renders <img src=x onerror="window.XSS_FIRED=true"> directly;
# DOMPurify must strip the onerror attribute.
_PAYLOAD_ONERROR = (
    '![xss](<x" onerror="window.XSS_FIRED=true">)'
)

# Payload class 2: raw <script> tag embedded in markdown.
# marked passes unknown HTML through; DOMPurify must strip <script>.
_PAYLOAD_SCRIPT_TAG = (
    'text\n\n<script>window.XSS_FIRED=true;</script>\n\nmore text'
)

# Payload class 3: javascript: URL in a link.
# marked renders [click](javascript:window.XSS_FIRED=true);
# sanitizeArtifact's link-hardening must strip the href on javascript: links.
_PAYLOAD_JS_URL = (
    '[click me](javascript:window.XSS_FIRED=true)'
)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_xss_onerror_attribute_is_stripped(page):
    """Payload class 1: onerror attribute must not fire after sanitization."""
    url, server, t, artifact_id = _make_xss_server(_PAYLOAD_ONERROR)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        # Give the browser a moment to process any rogue event handlers.
        page.wait_for_timeout(500)
        fired = page.evaluate(_PROBE_SCRIPT)
        assert not fired, "onerror payload executed — DOMPurify attribute stripping failed"
        # Verify the artifact-md container rendered (pipeline ran at all).
        assert page.locator(".artifact-md").count() > 0
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_xss_script_tag_is_stripped(page):
    """Payload class 2: <script> tag embedded in markdown must be removed."""
    url, server, t, artifact_id = _make_xss_server(_PAYLOAD_SCRIPT_TAG)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(500)
        fired = page.evaluate(_PROBE_SCRIPT)
        assert not fired, "<script> tag payload executed — DOMPurify FORBID_TAGS failed"
        # The surrounding text should still be present.
        assert "text" in page.locator(".artifact-md").inner_text()
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_xss_javascript_url_href_is_stripped(page):
    """Payload class 3: javascript: href must be stripped by link-hardening."""
    url, server, t, artifact_id = _make_xss_server(_PAYLOAD_JS_URL)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        # Click the rendered link — if href survived it would execute JS.
        link = page.locator(".artifact-md a").first
        link.wait_for(state="visible", timeout=5_000)
        link.click()
        page.wait_for_timeout(500)
        fired = page.evaluate(_PROBE_SCRIPT)
        assert not fired, "javascript: href executed — link-hardening in sanitizeArtifact failed"
        # The link text must still be visible (content preserved, href stripped).
        assert "click me" in page.locator(".artifact-md").inner_text()
    finally:
        server.should_exit = True
        t.join(timeout=3)
