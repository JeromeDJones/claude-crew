"""Pytest configuration shared across the suite.

Default-stub-mode fixture: existing tests (and most new ones) want
StubTeammate semantics from `make_server()` so they don't accidentally
hit the SDK factory. Tests that need SDK mode opt in by passing
`factory=` explicitly or by clearing the env var inside the test.

Playwright / pytest-asyncio interaction fix
-------------------------------------------
pytest-playwright's Playwright-session fixtures (``playwright``, ``browser``,
``browser_type``, ``launch_browser``, ``browser_context_args``) are
``scope="session"`` by default.  They keep Playwright's internal asyncio event
loop alive for the entire test session via a suspended greenlet.

Playwright's sync API explicitly calls ``asyncio._set_running_loop(loop)``
after every sync API call (``_sync_base._sync()``, line 114) so that code
immediately following a Playwright call can use ``asyncio.get_running_loop()``.
This leaves the C-level thread-local ``_running_loop`` set to Playwright's
loop *between* tests and *between* test modules.

When pytest-asyncio's function-scoped ``asyncio.Runner`` later tries to run
an async test in a different module, ``Runner.run(coro)`` checks
``asyncio._get_running_loop()`` and raises ``RuntimeError: Runner.run()
cannot be called from a running event loop``.  The teardown of the Runner
also fails because the Playwright loop is still "running" (``run_forever`` is
active inside the suspended greenlet).

Fix strategy
~~~~~~~~~~~~
Override all session-scoped Playwright fixtures to ``scope="module"``.  This
ensures Playwright shuts down completely (``pw.stop()`` → ``loop.close()``)
at the end of ``test_dashboard_render.py``'s module teardown, *before* the
``_reset_running_loop_between_modules`` autouse fixture clears any stale
thread-local.  After that, ``_running_loop`` is already None (cleared by
``run_forever``'s finally block), so the subsequent clear is a harmless no-op
and the transcript tests can create fresh ``asyncio.Runner`` instances safely.
"""

from __future__ import annotations

import asyncio
import asyncio.events
import os
import tempfile
from typing import Any, Callable, Dict, Generator, Optional

import pytest


@pytest.fixture(autouse=True)
def _default_stub_teammate_mode(monkeypatch):
    """Force `default_factory()` to return `stub_factory` for every test
    that doesn't override CLAUDE_CREW_TEAMMATE_MODE itself."""
    monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "stub")
    yield


@pytest.fixture(autouse=True)
def _disable_transcripts(monkeypatch):
    """Default-disable JSONL transcripts in tests so the user's state dir
    isn't polluted with one file per test run. Tests that exercise the
    transcript opt back in by setting CLAUDE_CREW_TRANSCRIPT_DIR to a
    tmp_path *and* unsetting CLAUDE_CREW_TRANSCRIPT_DISABLED."""
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", "1")
    yield


# ---------------------------------------------------------------------------
# Playwright fixtures — module-scoped overrides
# ---------------------------------------------------------------------------
# The block below replaces pytest-playwright's session-scoped Playwright
# fixtures with module-scoped equivalents so that the full Playwright teardown
# chain (including ``pw.stop()`` → ``loop.close()``) completes at the end of
# the test module that uses the browser, *before* ``_reset_running_loop_between_modules``
# clears the ``_running_loop`` thread-local.
#
# Only fixtures that pytest-playwright declares as ``scope="session"`` AND that
# are used (directly or transitively) by ``test_dashboard_render.py`` need to
# be overridden here.  Function-scoped fixtures (``page``, ``context``,
# ``new_context``, ``_artifacts_recorder``) are left unchanged because they
# are already narrower than module scope.

try:
    from playwright.sync_api import Browser, BrowserType, Playwright, sync_playwright

    @pytest.fixture(scope="module")
    def playwright() -> Generator[Playwright, None, None]:  # type: ignore[override]
        """Module-scoped Playwright instance (overrides session-scoped default)."""
        pw = sync_playwright().start()
        yield pw
        pw.stop()

    @pytest.fixture(scope="module")
    def browser_type(playwright: Playwright, browser_name: str) -> BrowserType:  # type: ignore[override]
        """Module-scoped browser type (overrides session-scoped default)."""
        return getattr(playwright, browser_name)

    @pytest.fixture(scope="module")
    def launch_browser(  # type: ignore[override]
        browser_type_launch_args: Dict,
        browser_type: BrowserType,
        connect_options: Optional[Dict],
    ) -> Callable[..., Browser]:
        """Module-scoped launch_browser (overrides session-scoped default)."""
        def launch(**kwargs: Any) -> Browser:
            import json
            launch_options = {**browser_type_launch_args, **kwargs}
            if connect_options:
                browser = browser_type.connect(
                    **(
                        {
                            **connect_options,
                            "headers": {
                                "x-playwright-launch-options": json.dumps(launch_options),
                                **(connect_options.get("headers") or {}),
                            },
                        }
                    )
                )
            else:
                browser = browser_type.launch(**launch_options)
            return browser
        return launch

    @pytest.fixture(scope="module")
    def browser(launch_browser: Callable[[], Browser]) -> Generator[Browser, None, None]:  # type: ignore[override]
        """Module-scoped browser (overrides session-scoped default)."""
        b = launch_browser()
        yield b
        b.close()

    @pytest.fixture(scope="module")
    def browser_context_args(  # type: ignore[override]
        pytestconfig: Any,
        playwright: Playwright,
        device: Optional[str],
        base_url: Optional[str],
        _pw_artifacts_folder: "tempfile.TemporaryDirectory[str]",
    ) -> Dict:
        """Module-scoped browser_context_args (overrides session-scoped default)."""
        context_args: Dict = {}
        if device:
            context_args.update(playwright.devices[device])
        if base_url:
            context_args["base_url"] = base_url
        video_option = pytestconfig.getoption("--video")
        capture_video = video_option in ["on", "retain-on-failure"]
        if capture_video:
            context_args["record_video_dir"] = _pw_artifacts_folder.name
        return context_args

except ImportError:
    pass  # playwright not installed; plugin fixtures are not available


# ---------------------------------------------------------------------------
# Cross-module event-loop hygiene
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _reset_running_loop_between_modules():
    """Clear stale ``_running_loop`` after each test module tears down.

    After the module-scoped Playwright fixtures tear down (``pw.stop()`` →
    ``loop.close()``), ``run_forever``'s finally block already sets
    ``_running_loop = None``.  This fixture is a safety-net: it ensures the
    thread-local is None even if Playwright's teardown left it set (e.g. a
    future version of Playwright that doesn't clear it), so that the next
    module's async tests can create ``asyncio.Runner`` instances freely.
    """
    yield
    asyncio.events._set_running_loop(None)
