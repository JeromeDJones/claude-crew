"""Unit tests for ``claude_crew.diagnostics``.

Covers the slice owned by the ``diagnostics-domain`` task of
``startup-diagnostics-dashboard``:

- Acceptance test #3: unknown-skill WARN classified as ``unknown_skill``
  via the classifier + collector contract (unit-form, per spec).
- Acceptance test #5: collector preserves stderr propagation — records
  emitted while the collector is attached still reach the existing
  handler set (asserted via ``caplog``).
- Classifier table parametrization (spec §Design Decisions:
  "Categorization at capture time").
- ``freeze`` semantics, idempotency.
- Emit-after-freeze drop (spec §Edge Cases: "Records emitted after
  freeze").
- 4096-char message cap.
- Safe ``emit`` swallows formatting errors via ``handleError``.
"""

from __future__ import annotations

import logging
import time

import pytest

from claude_crew.diagnostics import (
    MAX_MESSAGE_CHARS,
    StartupDiagCollector,
    StartupDiagnostic,
    classify,
    collect_startup_diagnostics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    msg: str,
    *,
    name: str = "claude_crew.subagents.loader",
    level: int = logging.WARNING,
    args: tuple = (),
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )


# ---------------------------------------------------------------------------
# Classifier table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg,expected",
    [
        # plugin
        ("plugin 'foo' installPath '/x' is outside /y; skipping", "plugin"),
        (
            "agent key 'k' appears in plugin 'a' at /x and plugin 'b' at /y; b wins",
            "plugin",
        ),
        # unknown skills
        (
            "agent 'a' declares unknown skills ['nope'] — not found in user or project",
            "unknown_skill",
        ),
        # unknown mcp servers
        (
            "agent 'a' declares unknown mcpServers ['nope'] — not registered in ~/.claude.json",
            "unknown_mcp_server",
        ),
        # frontmatter — three entry points
        (
            "agent file /x/explorer.md has unsupported frontmatter key(s) {'bogus_key'}; dropping",
            "frontmatter",
        ),
        ("agent file /x/explorer.md could not be loaded: bad yaml; skipping", "frontmatter"),
        ("agent file /x/explorer.md raised ValueError: bad; skipping", "frontmatter"),
        # shadow (INFO emit site)
        ("agent 'explorer' from project-level shadows default pack", "shadow"),
        ("agent 'explorer' from user-level shadows plugin", "shadow"),
        # other (no rule matches)
        ("agent file /x is 999999 bytes (cap 65536); skipping", "other"),
    ],
)
def test_classify_table(msg: str, expected: str) -> None:
    rec = _record(msg)
    assert classify(rec) == expected


def test_classify_other_for_foreign_logger() -> None:
    rec = _record("agent 'explorer' from project-level shadows default pack",
                  name="some.unrelated.module")
    assert classify(rec) == "other"


# ---------------------------------------------------------------------------
# Acceptance test #3 — unknown-skill WARN captured + categorized.
# ---------------------------------------------------------------------------


def test_unknown_skill_warn_captured_and_categorized() -> None:
    """Acceptance #3 — unit form."""
    collector = StartupDiagCollector(min_level=logging.INFO)
    rec = _record(
        "agent 'fancy' declares unknown skills ['nope-not-real'] — not found in "
        "user or project skill dirs at startup; teammate will fail to invoke",
        level=logging.WARNING,
    )
    collector.handle(rec)

    diags = collector.freeze()
    assert len(diags) == 1
    diag = diags[0]
    assert isinstance(diag, StartupDiagnostic)
    assert diag.level == "WARNING"
    assert diag.category == "unknown_skill"
    assert diag.source.startswith("claude_crew.subagents")


# ---------------------------------------------------------------------------
# Acceptance test #5 — propagation preserved (existing handlers still fire).
# ---------------------------------------------------------------------------


def test_handler_does_not_suppress_stderr_propagation(caplog) -> None:
    """Acceptance #5 — collector is purely additive."""
    loader_logger = logging.getLogger("claude_crew.subagents.loader")
    # Sanity: the source logger itself must propagate to root.
    assert loader_logger.propagate is True

    caplog.set_level(logging.INFO, logger="claude_crew.subagents.loader")
    with collect_startup_diagnostics() as collector:
        loader_logger.warning(
            "agent 'a' declares unknown skills ['nope']"
        )
    diagnostics = collector.freeze()

    # (a) collector saw it
    assert any(d.category == "unknown_skill" for d in diagnostics)
    # (b) the existing handler set (caplog) saw it too — propagation
    # was preserved
    assert any(
        "unknown skills" in r.getMessage() for r in caplog.records
    )
    # And the source logger still propagates
    assert loader_logger.propagate is True


# ---------------------------------------------------------------------------
# freeze() semantics
# ---------------------------------------------------------------------------


def test_collector_freezes_after_context_exit() -> None:
    loader_logger = logging.getLogger("claude_crew.subagents.loader")
    with collect_startup_diagnostics() as collector:
        loader_logger.warning("agent 'a' declares unknown skills ['x']")
    assert collector.frozen is True
    diags = collector.freeze()
    assert isinstance(diags, tuple)
    # Idempotent: returns same tuple
    assert collector.freeze() is diags


def test_emit_after_freeze_is_dropped() -> None:
    collector = StartupDiagCollector()
    collector.handle(_record("agent 'a' declares unknown skills ['x']"))
    frozen = collector.freeze()
    assert len(frozen) == 1

    # New record post-freeze: silently dropped, no exception
    collector.handle(_record("agent 'b' declares unknown skills ['y']"))
    assert collector.freeze() == frozen
    assert len(collector.freeze()) == 1


def test_freeze_returns_tuple_of_immutable_diagnostics() -> None:
    collector = StartupDiagCollector()
    collector.handle(_record("agent 'a' declares unknown skills ['x']"))
    diags = collector.freeze()
    assert isinstance(diags, tuple)
    with pytest.raises((AttributeError, Exception)):
        diags[0].level = "ERROR"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Message cap
# ---------------------------------------------------------------------------


def test_long_message_capped() -> None:
    collector = StartupDiagCollector()
    huge = "x" * (MAX_MESSAGE_CHARS + 5000)
    collector.handle(_record(huge))
    diag = collector.freeze()[0]
    assert len(diag.message) == MAX_MESSAGE_CHARS
    assert diag.message.endswith("…[truncated]")


def test_short_message_not_modified() -> None:
    collector = StartupDiagCollector()
    short = "agent 'a' declares unknown skills ['x']"
    collector.handle(_record(short))
    diag = collector.freeze()[0]
    assert diag.message == short


# ---------------------------------------------------------------------------
# Safe emit
# ---------------------------------------------------------------------------


def test_emit_swallows_formatting_errors(monkeypatch) -> None:
    """A malformed record (bad % args) must not crash the handler."""
    collector = StartupDiagCollector()

    handled: list[logging.LogRecord] = []

    def fake_handle_error(record: logging.LogRecord) -> None:
        handled.append(record)

    monkeypatch.setattr(collector, "handleError", fake_handle_error)

    # Bad: format string expects an arg, none provided
    bad = logging.LogRecord(
        name="claude_crew.subagents.loader",
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg="oops %s %s",
        args=("only-one",),
        exc_info=None,
    )
    # Must not raise
    collector.emit(bad)
    assert handled == [bad]
    # And no diagnostic was recorded
    assert collector.freeze() == ()


# ---------------------------------------------------------------------------
# Level coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "levelno,expected",
    [
        (logging.DEBUG, "INFO"),  # below INFO clamps up since handler floor is INFO
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "ERROR"),
    ],
)
def test_level_coercion(levelno: int, expected: str) -> None:
    collector = StartupDiagCollector(min_level=logging.DEBUG)
    rec = _record("agent 'a' declares unknown skills ['x']", level=levelno)
    collector.handle(rec)
    diag = collector.freeze()[0]
    assert diag.level == expected


# ---------------------------------------------------------------------------
# Timestamp + source pass-through
# ---------------------------------------------------------------------------


def test_timestamp_and_source_preserved() -> None:
    collector = StartupDiagCollector()
    rec = _record(
        "agent 'a' declares unknown skills ['x']",
        name="claude_crew.subagents.loader",
    )
    before = time.time()
    rec.created = before  # explicit
    collector.handle(rec)
    diag = collector.freeze()[0]
    assert diag.source == "claude_crew.subagents.loader"
    assert diag.timestamp == pytest.approx(before, abs=1.0)
