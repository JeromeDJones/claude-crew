"""Redaction module for tool-execution telemetry (Feature #8).

Provides versioned redaction patterns, a per-tool extractor registry,
and two public functions consumed by SdkTeammate hook callbacks:

  summarize_args(tool_name, tool_input) -> str | None
      Extract → redact → cap (256 bytes). Never raises. Returns None for
      non-allowlisted tools, on internal failure, or when the
      CLAUDE_CREW_TOOL_ARGS_DISABLED env var is set.

  redact_error(error_text) -> str
      Unconditional redact + cap (256 bytes). Always returns a string.
      Used for last_tool_error_summary and tool_end.error_summary.

Version bump procedure:
  A v2 is a deliberate event. Triggers: a confirmed real-world leak shape
  escapes the v1 set, a new SDK tool joins the allowlist with a novel arg
  shape, or a redaction false-positive proves chronic. Bump procedure:
  new constant REDACTION_PATTERNS_V2, bump REDACTION_VERSION = "v2", write
  a CHANGELOG entry citing the trigger, do NOT delete v1 (transcripts
  written under v1 stay marked v1 for audit). Document in this module.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version pin — shipped in every transcript line and status payload so future
# bumps are auditable without re-parsing the redacted content itself.
# ---------------------------------------------------------------------------

REDACTION_VERSION: str = "v1"

# ---------------------------------------------------------------------------
# Pattern set v1 (D6, sentinel-vetted — Phase 2 design pin).
#
# Order matters: apply anchored token shapes BEFORE length-based fallbacks
# so that a matched token is emitted as <redacted-key> rather than the more
# generic <redacted-b64>/<redacted-hex>.  Apply flag/header patterns early
# so that keyword-prefix pairs are caught even when the value itself
# wouldn't exceed the length threshold.
#
# Never reorder without re-running the SC-15 BDD scenarios.
# ---------------------------------------------------------------------------

REDACTION_PATTERNS_V1: list[tuple[re.Pattern, str]] = [
    # 1. Long-flag secrets — --password=hunter2, --token secret, --api-key=abc
    (
        re.compile(r"--(?:password|token|secret|api[-_]?key|key|auth)[=\s]+\S+", re.I),
        "<redacted-flag>",
    ),
    # 2. Short-flag secrets — mysql -p hunter2, ssh -i keyfile, curl -T tok
    #    Note: \b before '-' never fires because '-' is not a word character.
    #    (?<!\S) means "not preceded by a non-whitespace char" = "preceded by
    #    whitespace or at start of string" — the correct anchor for a CLI flag.
    (
        re.compile(r"(?<!\S)-[pPkKtT]\s+\S+"),
        "<redacted-flag>",
    ),
    # 3. Standard Authorization / X-Api-Key / X-Auth-Token header literals
    (
        re.compile(r"(?i)(Authorization|X-Api-Key|X-Auth-Token)\s*[:=]\s*\S+"),
        r"\1: <redacted>",
    ),
    # 4. Bearer / Basic value (catches the token after the scheme word)
    (
        re.compile(r"(?i)(Bearer|Basic)\s+[A-Za-z0-9._\-+/=]+"),
        r"\1 <redacted>",
    ),
    # 5. URL embedded credentials — git push https://user:tok@host/repo
    (
        re.compile(r"https?://[^:/\s@]+:[^@/\s]+@"),
        "https://<redacted>@",
    ),
    # 6. URL query-param secrets — ?token=abc&api_key=xyz
    (
        re.compile(r"[?&](?:api[-_]?key|token|secret|access[-_]?token|password)=[^&\s]+", re.I),
        "&<redacted>",
    ),
    # 7. Anthropic API keys — sk-ant-apiXX-..., sk-proj-...
    (
        re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),
        "<redacted-key>",
    ),
    # 8. GitHub PAT / OAuth tokens — ghp_..., gho_..., gha_..., ghs_..., ghu_...
    (
        re.compile(r"gh[poasu]_[A-Za-z0-9]{36,}"),
        "<redacted-key>",
    ),
    # 9. Slack tokens — xoxb-..., xoxp-..., xoxa-..., xoxr-..., xoxs-...
    (
        re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
        "<redacted-key>",
    ),
    # 10. AWS access key IDs — AKIA[0-9A-Z]{16} (20 chars total)
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "<redacted-key>",
    ),
    # 11. JWTs — eyJ<header>.<payload>.<signature>
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "<redacted-jwt>",
    ),
    # 12. Length-based fallback: base64-shaped strings >= 32 chars
    #     (after anchored patterns so known shapes get the specific label)
    (
        re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
        "<redacted-b64>",
    ),
    # 13. Length-based fallback: hex strings >= 32 chars (git SHAs, tokens)
    (
        re.compile(r"\b[0-9a-fA-F]{32,}\b"),
        "<redacted-hex>",
    ),
]

# ---------------------------------------------------------------------------
# Per-tool extractor registry (D6 Phase 2 design pin).
#
# Each extractor takes tool_input: dict and returns a flat summary string.
# KeyError / missing keys degrade to empty string (PA4 default-accept).
# Order of operation: extract → redact → cap (D6).  Never cap first.
#
# Per-tool budget caps on individual fields reduce the pre-redaction surface
# without depending on the post-extraction cap.
# ---------------------------------------------------------------------------

_SUBAGENT_TYPE_MAX = 32
_DESCRIPTION_MAX = 64
_PROMPT_PREVIEW_MAX = 64


def _extract_bash(tool_input: dict) -> str:
    """Extract Bash tool summary: command only (not description).

    The full command string is kept so the redactor can strip secrets inline.
    The 256-byte post-redaction cap bounds the final output.
    """
    cmd = tool_input.get("command", "")
    return f"command={cmd}"


def _extract_task(tool_input: dict) -> str:
    """Extract Task (subagent dispatch) summary.

    Deliberately excludes the prompt body — it could carry parent context
    including secrets (Jerome's explicit call, SC-15).
    """
    subagent_type = str(tool_input.get("subagent_type", ""))[:_SUBAGENT_TYPE_MAX]
    description = str(tool_input.get("description", ""))[:_DESCRIPTION_MAX]
    return f"subagent={subagent_type}; description={description}"


def _extract_webfetch(tool_input: dict) -> str:
    """Extract WebFetch summary: url + prompt prefix.

    URL is kept verbatim; the URL-cred and query-secret patterns in
    REDACTION_PATTERNS_V1 strip embedded credentials during the redact pass.
    """
    url = str(tool_input.get("url", ""))
    prompt = str(tool_input.get("prompt", ""))[:_PROMPT_PREVIEW_MAX]
    return f"url={url}; prompt={prompt}"


ALLOWLIST_V1: dict[str, Callable[[dict], str]] = {
    "Bash": _extract_bash,
    "Task": _extract_task,
    "WebFetch": _extract_webfetch,
}

# ---------------------------------------------------------------------------
# Cap helper
# ---------------------------------------------------------------------------


def _cap_utf8(s: str, max_bytes: int) -> str:
    """Truncate *s* to at most *max_bytes* UTF-8 bytes.

    If truncation is needed, appends U+2026 ELLIPSIS (3 UTF-8 bytes) as the
    final character.  Always returns a valid UTF-8 string.
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # Reserve 3 bytes for the ellipsis; decode with errors="ignore" so we
    # don't produce a truncated multi-byte character sequence.
    truncated = encoded[: max_bytes - 3].decode("utf-8", errors="ignore")
    return truncated + "…"


# ---------------------------------------------------------------------------
# Internal redaction helper
# ---------------------------------------------------------------------------


def _apply_patterns(text: str) -> str:
    """Apply REDACTION_PATTERNS_V1 to *text* in order."""
    for pattern, replacement in REDACTION_PATTERNS_V1:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarize_args(tool_name: str, tool_input: dict) -> str | None:
    """Produce a bounded, redacted summary of *tool_input* for *tool_name*.

    Contract:
      - Returns None if CLAUDE_CREW_TOOL_ARGS_DISABLED=1.
      - Returns None if *tool_name* is not on ALLOWLIST_V1 (unless
        CLAUDE_CREW_TOOL_ARGS_FULL=1 widens the allowlist to all tools).
      - Otherwise: extract → redact (REDACTION_PATTERNS_V1) → cap (256 bytes).
      - Never raises; returns None on any internal failure.
      - On failure, logs WARNING with tool_name only (NOT tool_input — the
        input may contain the secret we suspect).

    CLAUDE_CREW_TOOL_ARGS_FULL=1 is a debug-only escape hatch; the teammate
    side logs a WARNING at spawn time when it is set.
    """
    try:
        if os.environ.get("CLAUDE_CREW_TOOL_ARGS_DISABLED") == "1":
            return None

        if os.environ.get("CLAUDE_CREW_TOOL_ARGS_FULL") == "1":
            # Generic extractor — widens to all tools but keeps redaction + cap.
            extracted = json.dumps(tool_input, default=str)
        elif tool_name not in ALLOWLIST_V1:
            return None
        else:
            extracted = ALLOWLIST_V1[tool_name](tool_input)

        redacted = _apply_patterns(extracted)
        return _cap_utf8(redacted, 256)

    except Exception:
        logger.warning(
            "summarize_args: internal failure for tool_name=%r; returning None",
            tool_name,
        )
        return None


# ---------------------------------------------------------------------------
# Output-only redaction patterns
# Applied AFTER REDACTION_PATTERNS_V1 inside redact_output.  Not added to the
# shared V1 list (v1-is-frozen invariant; PEM blocks don't appear in CLI args).
# ---------------------------------------------------------------------------

_OUTPUT_ONLY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # O-1. PEM private-key blocks (multi-line; catches RSA/EC/ENCRYPTED/bare)
    (
        re.compile(
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted-pem>",
    ),
    # O-2. AWS session token keyword pair (40+ char base64 after keyword).
    #      Belt-and-suspenders complement to V1 pattern 12: catches tokens
    #      whose mixed base64 chars (+, /) break pattern 12's \b anchors.
    (
        re.compile(r"aws_session_token\s*[=:]\s*[A-Za-z0-9+/]{40,}={0,2}", re.I),
        "aws_session_token=<redacted-key>",
    ),
    # O-3. GitHub tokens — full gh*_ family. V1 pattern 8 only covers
    #      gh[poasu]_; this catches ghr_ (refresh) and ghe_ (Enterprise) plus
    #      any future gh<x>_ prefix. The underscore defeats V1 pattern 12's \b
    #      anchor, so the length fallback does NOT rescue these — an explicit
    #      pattern is required. (Output-only so V1's frozen contract is
    #      untouched; the args-side V1 gap is tracked for a future v2 bump.)
    (
        re.compile(r"gh[a-z]_[A-Za-z0-9]{30,}"),
        "<redacted-key>",
    ),
    # O-4. Stripe secret/restricted keys — sk_live_, sk_test_, rk_live_,
    #      rk_test_. V1 pattern 7 only matches the hyphen form (sk-); Stripe
    #      uses underscores, which again defeat pattern 12's \b anchor.
    (
        re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"),
        "<redacted-key>",
    ),
]

# Hard cap for stored tool output bodies (4 KiB)
_TOOL_OUTPUT_BYTE_CAP: int = 4096


def redact_output(text: str) -> str:
    """Redact tool output text before storage in the per-teammate output store.

    Applies REDACTION_PATTERNS_V1 in order, then the output-only additions
    (_OUTPUT_ONLY_PATTERNS: PEM private-key blocks and AWS session token
    keyword pairs).  Caps the final result to 4096 bytes via _cap_utf8.

    Contract:
      - Returns a redacted, capped string.
      - On any internal exception, re-raises so the caller can write the
        ``[REDACTION_FAILED: <ClassName>]`` sentinel.  Never silently stores
        raw text on failure.

    Security note: pattern coverage is the ONLY redaction defense. The 4096-byte
    cap is a memory bound, NOT a backup redactor — a secret in an unmatched
    format (a novel token type) survives capping. Adding a credential format to
    cover means adding a pattern here (or to REDACTION_PATTERNS_V1 via a v2
    bump), never relying on truncation.
    """
    result = _apply_patterns(text)
    for pattern, replacement in _OUTPUT_ONLY_PATTERNS:
        result = pattern.sub(replacement, result)
    return _cap_utf8(result, _TOOL_OUTPUT_BYTE_CAP)


def redact_error(error_text: str) -> str:
    """Redact and cap an error string for use as error_summary.

    Applies REDACTION_PATTERNS_V1 unconditionally, then caps to 256 bytes.
    Always returns a string (never None) — errors are always emitted
    (SC-15 unconditional clause, SC-3).

    On internal redaction failure, returns the original string truncated to
    256 bytes (patterns not applied, but capped so the output is bounded).
    This preserves the "always emits something" contract while safely
    degrading when the redactor itself malfunctions.
    """
    try:
        redacted = _apply_patterns(error_text)
        return _cap_utf8(redacted, 256)
    except Exception:
        logger.warning("redact_error: internal failure; returning truncated original")
        return _cap_utf8(error_text, 256)
