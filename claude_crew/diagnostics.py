"""Startup-diagnostic capture for claude-crew.

This module implements the *capture* stage of the startup-diagnostics
pipeline (spec: ``startup-diagnostics-dashboard``). It is pure domain
logic: a frozen :class:`StartupDiagnostic` dataclass, a
:class:`StartupDiagCollector` ``logging.Handler`` subclass that snapshots
records into immutable diagnostics, a :func:`classify` helper that maps
records to one of the six categories, and a
:func:`collect_startup_diagnostics` context manager that attaches the
handler to the root logger for the duration of the ``with`` block and
freezes it on exit.

The handler is purely additive: it never sets ``propagate=False`` on any
logger and never calls ``removeHandler`` on anything other than itself.
Existing stderr handlers continue to receive every record they would
have received without the collector installed.

Frozen-by-construction: once :meth:`StartupDiagCollector.freeze` is
called (or the context manager exits) the collector silently drops any
further records. This guards the spec's "startup-only" invariant — the
field on :class:`BrokerSnapshot` must not silently grow into a runtime
firehose.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal, Tuple

__all__ = [
    "DiagLevel",
    "DiagCategory",
    "StartupDiagnostic",
    "StartupDiagCollector",
    "classify",
    "collect_startup_diagnostics",
    "MAX_MESSAGE_CHARS",
]


DiagLevel = Literal["INFO", "WARNING", "ERROR"]
DiagCategory = Literal[
    "shadow",
    "unknown_skill",
    "unknown_mcp_server",
    "frontmatter",
    "plugin",
    "plugin_scope_miss",
    "other",
]

# Hard cap on stored message length. Messages above this cap are
# truncated with the ``…[truncated]`` suffix to bound payload size on
# the broker snapshot / ``/api/state`` payload.
MAX_MESSAGE_CHARS = 4096
_TRUNC_SUFFIX = "…[truncated]"


@dataclass(frozen=True)
class StartupDiagnostic:
    """Immutable record of a single startup-time log event."""

    level: DiagLevel
    message: str
    source: str
    timestamp: float
    category: DiagCategory


# ---------------------------------------------------------------------------
# Classifier table
# ---------------------------------------------------------------------------
#
# Per spec assumption A-8 ("primary key is logger name; secondary key is
# a prefix-startswith / substring match against ``record.getMessage()``").
# Today every emit site lives on ``claude_crew.subagents.loader``; we
# still scope by logger-name prefix so future sibling loggers
# (``claude_crew.factories``, plugin loader, etc.) inherit the same
# categorization seam without code change.
#
# Substring (not regex) — the loader's format strings are stable and
# audited per category. Order matters: most specific first.

_SUBAGENT_LOGGER_PREFIX = "claude_crew.subagents"
_FACTORIES_LOGGER_PREFIX = "claude_crew.factories"

# (substring, category) — checked in declaration order.
_MESSAGE_RULES: Tuple[Tuple[str, DiagCategory], ...] = (
    ("project-scope plugin", "plugin_scope_miss"),
    ("installPath", "plugin"),
    ("appears in plugin", "plugin"),
    ("declares unknown skills", "unknown_skill"),
    ("unknown skills", "unknown_skill"),
    ("declares unknown mcpServers", "unknown_mcp_server"),
    ("unknown mcpServers", "unknown_mcp_server"),
    ("unsupported frontmatter key", "frontmatter"),
    ("could not be loaded", "frontmatter"),
    ("raised", "frontmatter"),
    (" shadows ", "shadow"),
)


def classify(record: logging.LogRecord) -> DiagCategory:
    """Derive the diagnostic category for ``record``.

    Logger name is the primary key (it scopes us to startup-relevant
    loggers); message-substring rules act as the tiebreaker. Records
    from loggers outside the startup namespace fall through to
    ``"other"``.
    """

    name = record.name or ""
    if not (
        name.startswith(_SUBAGENT_LOGGER_PREFIX)
        or name.startswith(_FACTORIES_LOGGER_PREFIX)
    ):
        return "other"

    try:
        msg = record.getMessage()
    except Exception:
        return "other"

    for needle, category in _MESSAGE_RULES:
        if needle in msg:
            return category
    return "other"


def _coerce_level(levelno: int) -> DiagLevel:
    if levelno >= logging.ERROR:
        return "ERROR"
    if levelno >= logging.WARNING:
        return "WARNING"
    return "INFO"


def _cap_message(msg: str) -> str:
    if len(msg) <= MAX_MESSAGE_CHARS:
        return msg
    keep = MAX_MESSAGE_CHARS - len(_TRUNC_SUFFIX)
    if keep < 0:
        keep = 0
    return msg[:keep] + _TRUNC_SUFFIX


class StartupDiagCollector(logging.Handler):
    """Logging handler that snapshots records into ``StartupDiagnostic``.

    Designed to be attached at the root logger for the duration of a
    capture window (typically the ``build_merged_pack()`` call inside
    ``factories.default_factory``). After :meth:`freeze` (or context
    exit) further records are silently dropped — the collector preserves
    the spec's startup-only invariant.

    ``emit`` never raises: any exception during formatting or
    classification is routed through :meth:`logging.Handler.handleError`
    (which respects ``logging.raiseExceptions``) so a malformed record
    cannot break server startup.
    """

    def __init__(self, min_level: int = logging.INFO) -> None:
        super().__init__(level=min_level)
        self._records: list[StartupDiagnostic] = []
        self._frozen: bool = False
        self._frozen_tuple: Tuple[StartupDiagnostic, ...] | None = None

    # logging.Handler API ---------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        if self._frozen:
            return
        try:
            try:
                raw = record.getMessage()
            except Exception:
                # Defer to handleError below — this is a malformed
                # record (bad % args, etc.). Stdlib convention.
                raise
            level = _coerce_level(record.levelno)
            if level == "INFO" and self.level > logging.INFO:
                # Defensive: respect the handler-level threshold even
                # for INFO-classified records emitted by callers that
                # bypass the per-logger filter.
                return
            diag = StartupDiagnostic(
                level=level,
                message=_cap_message(raw),
                source=record.name,
                timestamp=record.created,
                category=classify(record),
            )
            self._records.append(diag)
        except Exception:
            # Stdlib-conventional safe-emit: never let a bad record kill
            # the process. ``handleError`` writes to stderr only when
            # ``logging.raiseExceptions`` is True (the default during
            # development; tests can flip it off if they need to assert
            # silence).
            self.handleError(record)

    # Public capture API ----------------------------------------------------

    def freeze(self) -> Tuple[StartupDiagnostic, ...]:
        """Stop accepting records and return the captured tuple.

        Idempotent: subsequent calls return the same tuple.
        """
        if self._frozen_tuple is None:
            self._frozen = True
            self._frozen_tuple = tuple(self._records)
        return self._frozen_tuple

    @property
    def frozen(self) -> bool:
        return self._frozen

    def snapshot(self) -> Tuple[StartupDiagnostic, ...]:
        """Peek at the current diagnostics without freezing."""
        return tuple(self._records)


@contextmanager
def collect_startup_diagnostics(
    min_level: int = logging.INFO,
    logger: logging.Logger | None = None,
) -> Iterator[StartupDiagCollector]:
    """Attach a :class:`StartupDiagCollector` for the ``with`` block.

    By default the handler attaches at the root logger so any
    propagating child logger is captured (assumption A-2). Tests can
    pass an explicit ``logger`` to scope capture (e.g., to assert
    propagation invariants without polluting the global root).

    On exit the handler is detached and the collector is frozen — even
    if the body raised — so callers can rely on::

        with collect_startup_diagnostics() as collector:
            do_startup_things()
        diagnostics = collector.freeze()  # safe; idempotent

    The handler is *additive*: it never sets ``propagate=False`` on any
    logger and never removes anything other than itself.
    """

    target = logger if logger is not None else logging.getLogger()
    handler = StartupDiagCollector(min_level=min_level)
    target.addHandler(handler)
    # Ensure the target logger actually lets ``min_level`` records
    # through. We don't lower it permanently — restore on exit.
    previous_level = target.level
    if target.level == logging.NOTSET or target.level > min_level:
        target.setLevel(min_level)
    try:
        yield handler
    finally:
        try:
            target.removeHandler(handler)
        finally:
            target.setLevel(previous_level)
            handler.freeze()
