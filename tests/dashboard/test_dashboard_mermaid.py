"""Playwright tests for mermaid diagram rendering in the artifact viewer.

Verifies that fenced mermaid blocks in surfaced artifacts render as inline SVG,
that multiple blocks produce separate SVGs, that malformed blocks do not crash
the render, and that the render is scoped to artifact content only.

Follows the same harness pattern as test_dashboard_artifact_xss.py:
monkey-patches ArtifactRegistry via a _FakeRegistry, spins up a UIServer,
navigates with Playwright, and asserts on the DOM.

Run prerequisite: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from claude_crew.artifact_registry import ArtifactRecord
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
        title="Mermaid Test Artifact",
        path_label="/tmp/mermaid-test.md",
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


def _start_server(broker: Broker, registry: _FakeRegistry) -> tuple[str, object, threading.Thread]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port, artifact_registry=registry)  # type: ignore[arg-type]
    app = ui._make_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def run() -> None:
        import asyncio

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


def _make_mermaid_server(body: str) -> tuple[str, object, threading.Thread, str]:
    """Spin up a UIServer seeded with a single artifact whose body is the given markdown."""
    record = _make_artifact(body)
    registry = _FakeRegistry(record)
    snapshot = _make_snapshot()
    broker = _patched_broker(snapshot)
    url, server, t = _start_server(broker, registry)
    return url, server, t, record.artifact_id


def _open_artifact_drawer(page, url: str, artifact_id: str) -> None:
    """Navigate to the dashboard and programmatically open the artifact drawer."""
    page.goto(url)
    page.locator('[data-testid="artifacts-pill"]').wait_for(state="visible", timeout=15_000)
    page.locator('[data-testid="artifacts-pill"]').click()
    page.locator(".artifact-tray-item").first.wait_for(state="visible", timeout=5_000)
    page.locator(".artifact-tray-item").first.click()
    page.locator(".artifact-md").wait_for(state="visible", timeout=10_000)


# ── test payloads ─────────────────────────────────────────────────────────────

# AT 1: single valid mermaid block
_PAYLOAD_SINGLE = """# Plan

Here is the architecture:

```mermaid
graph TD
  A --> B
```

More text below.
"""

# AT 3: no mermaid blocks at all
_PAYLOAD_NO_MERMAID = """# Plan

Just regular markdown text.

- Bullet one
- Bullet two

And a [link](https://example.com).
"""

# AT 4: multiple mermaid blocks
_PAYLOAD_MULTIPLE = """# Plan

First diagram:

```mermaid
graph TD
  A --> B
```

Second diagram:

```mermaid
sequenceDiagram
  A ->> B: hello
  B ->> A: world
```

Third diagram:

```mermaid
pie title Shares
  "Apples" : 40
  "Oranges" : 60
```
"""

# AT 5: malformed mermaid block (invalid syntax)
_PAYLOAD_MALFORMED = """# Plan

This diagram has bad syntax:

```mermaid
graph TD
  A --> B -->
  invalid token here !!!
```
"""


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_single_mermaid_block_renders_svg(page):
    """AT 1: A single valid fenced mermaid block renders an <svg> inside .artifact-md."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_SINGLE)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        # Wait for async mermaid render to complete.
        page.wait_for_timeout(2_000)
        # Assert an <svg> element is present inside the artifact-md container.
        svg = page.locator(".artifact-md svg")
        svg.wait_for(state="visible", timeout=5_000)
        assert svg.count() == 1, f"Expected 1 SVG, found {svg.count()}"
        # Also assert the surrounding markdown text is present.
        assert "More text below" in page.locator(".artifact-md").inner_text()
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_drawer_resizes_by_dragging_left_handle(page):
    """The artifact drawer widens when its left-edge handle is dragged left, and the
    chosen width persists to localStorage (so wide diagrams have room to breathe)."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_SINGLE)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        handle = page.locator(".drawer-resize-handle")
        handle.wait_for(state="visible", timeout=5_000)
        panel = handle.locator("xpath=..")  # the drawer panel is the handle's parent
        start_w = panel.bounding_box()["width"]
        hb = handle.bounding_box()
        cy = hb["y"] + hb["height"] / 2
        # Right-anchored drawer: dragging the left-edge handle leftwards widens it.
        page.mouse.move(hb["x"] + hb["width"] / 2, cy)
        page.mouse.down()
        page.mouse.move(hb["x"] - 250, cy, steps=12)
        page.mouse.up()
        end_w = panel.bounding_box()["width"]
        assert end_w > start_w + 100, f"expected widen by >100px; start={start_w} end={end_w}"
        # Width persisted for next session.
        saved = page.evaluate("() => localStorage.getItem('artifactDrawerWidth')")
        assert saved is not None and float(saved) > start_w, f"width not persisted: {saved!r}"
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_no_mermaid_blocks_no_regression(page):
    """AT 3: An artifact with no mermaid blocks renders identically to today."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_NO_MERMAID)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(500)
        # Assert the markdown rendered correctly.
        text = page.locator(".artifact-md").inner_text()
        assert "Just regular markdown text" in text
        assert "Bullet one" in text
        assert "Bullet two" in text
        # Assert no SVGs (there are no mermaid blocks).
        assert page.locator(".artifact-md svg").count() == 0
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_multiple_mermaid_blocks_render_separate_svgs(page):
    """AT 4: Multiple mermaid blocks each render as a separate inline SVG."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_MULTIPLE)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(3_000)
        # Use a count assertion instead of wait_for to avoid Playwright strict mode
        # when the locator matches multiple elements.
        count = page.locator(".artifact-md svg").count()
        assert count == 3, f"Expected 3 SVGs, found {count}"
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_malformed_mermaid_no_crash(page):
    """AT 5: A malformed mermaid block does not crash the render; source text remains visible."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_MALFORMED)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(2_000)
        # The artifact-md container should still be present and contain text.
        text = page.locator(".artifact-md").inner_text()
        assert "This diagram has bad syntax" in text
        # The render should not have thrown — the container is still there.
        # On failure, the original <pre> block is left in place, so the raw
        # mermaid source text should still be visible.
        assert "graph TD" in text or "invalid token" in text
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_mermaid_render_scoped_to_artifact_md(page):
    """AT 9: The renderMermaidBlocks function is scoped to .artifact-md descendants only."""
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_SINGLE)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(2_000)
        # Verify the mermaid-rendered SVG is inside .artifact-md.
        artifact_svgs = page.locator(".artifact-md svg")
        assert artifact_svgs.count() == 1, f"Expected 1 SVG inside .artifact-md, found {artifact_svgs.count()}"
        # Verify no unconverted mermaid blocks remain (scope enforcement).
        # If renderMermaidBlocks ran correctly, all language-mermaid code blocks
        # inside .artifact-md should have been replaced by SVGs.
        unconverted = page.locator(".artifact-md pre > code.language-mermaid")
        assert unconverted.count() == 0, (
            f"Mermaid blocks were not converted inside .artifact-md: {unconverted.count()} remain"
        )
        # Verify that any mermaid SVGs (identified by id prefix) on the page
        # are only inside .artifact-md — not leaked outside.
        all_mermaid_svgs = page.evaluate("""() => {
          return [...document.querySelectorAll('svg[id^="mermaid-svg-"]')].map(s => s.id);
        }""")
        inside_svgs = page.evaluate("""() => {
          return [...document.querySelectorAll('.artifact-md svg[id^="mermaid-svg-"]')].map(s => s.id);
        }""")
        assert set(all_mermaid_svgs) == set(inside_svgs), (
            f"Some mermaid SVGs are outside .artifact-md: {all_mermaid_svgs} vs {inside_svgs}"
        )
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_mermaid_svg_carries_styling(page):
    """AT 10: The rendered mermaid SVG carries styling (style element + class attrs).

    Regression test for the "black rectangles" bug: DOMPurify's SVG profile
    strips <text>, <style>, and style= attributes by default, causing all
    diagram nodes to render as solid black with invisible labels.  The fix
    must preserve the <style> element and class/style attributes so the
    mermaid theme CSS can apply fill colours and text.
    """
    url, server, t, artifact_id = _make_mermaid_server(_PAYLOAD_SINGLE)
    try:
        _open_artifact_drawer(page, url, artifact_id)
        page.wait_for_timeout(2_000)

        # The SVG must contain a <style> element with mermaid theme rules.
        has_style = page.evaluate("""() => {
            const svg = document.querySelector('.artifact-md svg');
            if (!svg) return false;
            return svg.querySelectorAll('style').length > 0;
        }""")
        assert has_style, "Mermaid SVG is missing <style> element — theme CSS was stripped"

        # The <style> content must include fill rules for node rects.
        style_has_fill = page.evaluate("""() => {
            const style = document.querySelector('.artifact-md svg style');
            if (!style) return false;
            return /fill:/.test(style.textContent);
        }""")
        assert style_has_fill, "Mermaid <style> element has no fill rules — styling is broken"

        # The SVG must carry class attributes on elements (used by the CSS rules).
        has_classes = page.evaluate("""() => {
            const svg = document.querySelector('.artifact-md svg');
            if (!svg) return false;
            return svg.querySelectorAll('[class]').length > 0;
        }""")
        assert has_classes, "Mermaid SVG has no class attributes — CSS selectors cannot match"
    finally:
        server.should_exit = True
        t.join(timeout=3)
