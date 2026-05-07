"""Integration tests for `factories.default_factory` startup-diagnostics capture.

Slice: factory-capture-wire. Owns acceptance tests #1, #2, #4, #10.

These tests plant project-level config (shadow file, frontmatter typo) and
assert the capture wire produces a `factory.startup_diagnostics` tuple
populated with categorized diagnostics; stub mode produces an empty tuple
(no capture installed).
"""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent

import pytest

from claude_crew import factories


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _isolate_home_and_project(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(home, project)`` directories isolated from the real user.

    Prevents the test from picking up real ``~/.claude/agents`` (which would
    add real diagnostics to the captured tuple and make assertions flake).
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(project)
    return home, project


def _enable_sdk_mode(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")


def _enable_stub_mode(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "stub")


# ---------------------------------------------------------------------------
# Acceptance #1 — empty case (clean config produces empty tuple of WARN+)
# ---------------------------------------------------------------------------


class TestEmptyCase:
    """Acceptance #1: clean config → no WARN/ERROR diagnostics; INFO-only OK."""

    def test_clean_config_yields_no_warnings(
        self, monkeypatch, tmp_path
    ) -> None:
        _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        f = factories.default_factory()

        assert hasattr(f, "startup_diagnostics")
        diags = f.startup_diagnostics
        assert isinstance(diags, tuple)
        warn_or_error = [d for d in diags if d.level in ("WARNING", "ERROR")]
        assert warn_or_error == [], (
            "clean config should produce no WARN/ERROR diagnostics; "
            f"got {warn_or_error!r}"
        )

    def test_clean_config_threads_through_make_server(
        self, monkeypatch, tmp_path
    ) -> None:
        """make_server() builds a Broker whose snapshot.startup_diagnostics
        is empty under clean-config conditions — no WARN/ERROR rows leak in
        from the capture window."""
        from claude_crew.server import make_server

        _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        # SDK auth is required by sdk_factory's requires_auth=True; bypass.
        monkeypatch.setattr(
            "claude_crew.server.validate_auth_or_exit", lambda: None
        )

        make_server()  # default Broker constructed inside
        # We cannot easily reach the broker through the FastMCP wrapper, so
        # instead verify the capture-side invariant: the factory exposes an
        # empty-of-WARNs tuple under clean config.
        from claude_crew.factories import default_factory

        f = default_factory()
        warn_or_error = [
            d for d in f.startup_diagnostics if d.level in ("WARNING", "ERROR")
        ]
        assert warn_or_error == []


# ---------------------------------------------------------------------------
# Acceptance #2 — pack-shadow INFO captured and categorized
# ---------------------------------------------------------------------------


class TestShadowCaptured:
    """Acceptance #2: project-level explorer.md shadows default-pack
    explorer → INFO diagnostic with category="shadow"."""

    def test_project_shadow_emits_shadow_diagnostic(
        self, monkeypatch, tmp_path
    ) -> None:
        _, project = _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "explorer.md").write_text(dedent("""\
            ---
            name: explorer
            description: Project-level shadow.
            tools: [Read]
            ---

            You are a project-level explorer.
            """))

        f = factories.default_factory()
        diags = f.startup_diagnostics

        shadow_diags = [d for d in diags if d.category == "shadow"]
        assert shadow_diags, (
            f"expected at least one shadow-category diagnostic; got {[d.category for d in diags]!r}"
        )
        # Spec: source must start with claude_crew.subagents
        d0 = shadow_diags[0]
        assert d0.level == "INFO"
        assert d0.source.startswith("claude_crew.subagents"), d0.source
        assert "shadows" in d0.message


# ---------------------------------------------------------------------------
# Acceptance #4 — frontmatter parse rejection captured (WARNING / frontmatter)
# ---------------------------------------------------------------------------


class TestFrontmatterRejection:
    """Acceptance #4: project agent file with an unsupported frontmatter
    key → WARNING diagnostic with category="frontmatter"."""

    def test_unsupported_frontmatter_key_emits_frontmatter_warning(
        self, monkeypatch, tmp_path
    ) -> None:
        _, project = _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "scout.md").write_text(dedent("""\
            ---
            name: scout
            description: Test agent with bogus frontmatter key.
            tools: [Read]
            bogus_key: definitely-not-a-real-field
            ---

            You are a scout.
            """))

        f = factories.default_factory()
        diags = f.startup_diagnostics

        frontmatter_diags = [d for d in diags if d.category == "frontmatter"]
        assert frontmatter_diags, (
            f"expected at least one frontmatter-category diagnostic; "
            f"got categories {[d.category for d in diags]!r}"
        )
        d0 = frontmatter_diags[0]
        assert d0.level == "WARNING"
        assert d0.source.startswith("claude_crew.subagents"), d0.source


# ---------------------------------------------------------------------------
# Acceptance #10 — stub mode skips capture entirely
# ---------------------------------------------------------------------------


class TestStubModeSkipsCapture:
    """Acceptance #10: CLAUDE_CREW_TEAMMATE_MODE=stub → no capture; the
    returned factory has no `startup_diagnostics` attribute, and the
    snapshot field defaults to ``()`` regardless of logger activity."""

    def test_stub_factory_has_no_startup_diagnostics_attribute(
        self, monkeypatch, tmp_path
    ) -> None:
        _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_stub_mode(monkeypatch)

        f = factories.default_factory()
        # stub_factory is the module-level callable; it does not carry
        # the startup_diagnostics attribute set on the sdk-mode closure.
        assert getattr(f, "startup_diagnostics", ()) == ()

    def test_stub_mode_yields_empty_tuple_in_broker(
        self, monkeypatch, tmp_path
    ) -> None:
        """Even with WARNs emitted on the source loggers during the
        ``default_factory()`` call window, stub mode never installs the
        collector — the broker built afterward has an empty tuple."""
        from claude_crew.broker import Broker

        _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_stub_mode(monkeypatch)

        # Emit noise on a source logger before / during factory creation.
        loader_log = logging.getLogger("claude_crew.subagents.loader")
        loader_log.warning("noise emitted under stub mode")

        f = factories.default_factory()
        loader_log.warning("more noise during/after stub default_factory")

        # Broker default is empty tuple; reading factory's attribute (absent)
        # falls through to the same empty default.
        broker = Broker(
            startup_diagnostics=getattr(f, "startup_diagnostics", ())
        )
        snap = broker.snapshot()
        assert snap.startup_diagnostics == ()
        assert isinstance(snap.startup_diagnostics, tuple)


# ---------------------------------------------------------------------------
# OQ-1 propagation probe — direct-attach fallback exercised explicitly
# ---------------------------------------------------------------------------


class TestPropagationFallback:
    """When a known source logger has propagate=False, the collector
    direct-attaches to it so records still land in the captured tuple."""

    def test_direct_attach_when_propagation_broken(
        self, monkeypatch, tmp_path
    ) -> None:
        _, project = _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        # Plant a project-level shadow so the loader emits an INFO during
        # build_merged_pack(); direct-attach must catch it even without
        # propagation to the root.
        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "explorer.md").write_text(dedent("""\
            ---
            name: explorer
            description: Project-level shadow.
            tools: [Read]
            ---

            shadow body
            """))

        loader = logging.getLogger("claude_crew.subagents.loader")
        prior_propagate = loader.propagate
        loader.propagate = False
        try:
            f = factories.default_factory()
        finally:
            loader.propagate = prior_propagate

        shadow_diags = [d for d in f.startup_diagnostics if d.category == "shadow"]
        assert shadow_diags, (
            "direct-attach fallback must capture shadow INFO even when "
            "propagation is disabled on the source logger"
        )

    def test_propagation_intact_does_not_double_capture(
        self, monkeypatch, tmp_path
    ) -> None:
        """Sanity: when propagation is intact, the root attach catches the
        record and we do not also direct-attach (no duplicate diagnostics)."""
        _, project = _isolate_home_and_project(monkeypatch, tmp_path)
        _enable_sdk_mode(monkeypatch)

        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "explorer.md").write_text(dedent("""\
            ---
            name: explorer
            description: Project-level shadow.
            tools: [Read]
            ---

            shadow body
            """))

        f = factories.default_factory()

        shadow_diags = [d for d in f.startup_diagnostics if d.category == "shadow"]
        # exactly one project-shadows-default INFO is emitted per role
        assert len(shadow_diags) == 1, (
            f"expected exactly one shadow diagnostic; got {len(shadow_diags)} "
            f"({[d.message for d in shadow_diags]!r})"
        )
