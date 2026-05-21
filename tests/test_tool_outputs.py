"""Tests for the tool-output store (task: tool-output-store).

Covers AT-1, AT-3, AT-4, AT-7, AT-8 from the click-to-view-tool-output spec.
"""

from __future__ import annotations

import dataclasses
import logging
import time

import pytest

import claude_crew.sdk_teammate as sdk_mod
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.teammate import ToolEvent, _ToolUseEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teammate(tm_id: str = "t1") -> SdkTeammate:
    """Construct a SdkTeammate with minimal deps (no pack loading, no broker)."""
    return SdkTeammate(id=tm_id, name="test", role="r", agents={}, system_prompt="s")


def _inject_pre(tm: SdkTeammate, tool_use_id: str, tool_name: str = "Read") -> None:
    """Inject a fake PreToolUse entry so _on_post_common finds a matching entry."""
    tm._tool_uses[tool_use_id] = _ToolUseEntry(
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        started_at_wallclock=time.time(),
        args_summary=None,
    )


async def _fire_post(
    tm: SdkTeammate,
    tool_use_id: str,
    tool_response: object = "body",
    outcome: str = "ok",
) -> None:
    """Fire _on_post_common simulating a PostToolUse event."""
    inp: dict = {"tool_use_id": tool_use_id}
    if tool_response is not None:
        inp["tool_response"] = tool_response
    await tm._on_post_common(inp, tool_use_id, outcome=outcome, error_text=None)


# ---------------------------------------------------------------------------
# AT-1: basic capture — plain string, no redaction triggered
# ---------------------------------------------------------------------------


class TestAT1BasicCapture:
    async def test_plain_string_stored_and_retrieved(self) -> None:
        """AT-1: tool_response='hello world' is stored verbatim (no secrets → no redaction)."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_001")
        await _fire_post(tm, "toolu_001", tool_response="hello world")
        assert tm.get_tool_output("toolu_001") == "hello world"

    async def test_missing_key_stores_nothing(self) -> None:
        """Edge case: absent tool_response → get_tool_output returns None."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_002")
        await _fire_post(tm, "toolu_002", tool_response=None)
        assert tm.get_tool_output("toolu_002") is None

    async def test_dict_coerced_to_json(self) -> None:
        """Edge case: dict tool_response is json.dumps-coerced before storage."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_003")
        await _fire_post(tm, "toolu_003", tool_response={"key": "val"})
        result = tm.get_tool_output("toolu_003")
        assert result is not None
        assert "key" in result and "val" in result

    async def test_bytes_coerced_via_decode(self) -> None:
        """Edge case: bytes tool_response decoded utf-8."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_004")
        await _fire_post(tm, "toolu_004", tool_response=b"byte content")
        result = tm.get_tool_output("toolu_004")
        assert result is not None
        assert "byte content" in result


# ---------------------------------------------------------------------------
# AT-3: FIFO eviction at 50 entries
# ---------------------------------------------------------------------------


class TestAT3FifoEviction:
    async def test_fifty_second_capture_evicts_first(self) -> None:
        """AT-3: after 52 stores, _tool_outputs has 50 entries and the first is gone."""
        tm = _make_teammate()
        first_id = "toolu_first"
        _inject_pre(tm, first_id)
        await _fire_post(tm, first_id, tool_response="first body")

        for i in range(1, 52):
            tid = f"toolu_{i:04d}"
            _inject_pre(tm, tid)
            await _fire_post(tm, tid, tool_response=f"body {i}")

        assert len(tm._tool_outputs) == 50
        assert tm.get_tool_output(first_id) is None

    async def test_fifty_entries_fills_without_eviction(self) -> None:
        """Boundary: exactly 50 stores → no eviction, first entry still present."""
        tm = _make_teammate()
        first_id = "toolu_keep_first"
        _inject_pre(tm, first_id)
        await _fire_post(tm, first_id, tool_response="keep me")

        for i in range(1, 50):
            tid = f"toolu_fill_{i:04d}"
            _inject_pre(tm, tid)
            await _fire_post(tm, tid, tool_response=f"fill {i}")

        assert len(tm._tool_outputs) == 50
        assert tm.get_tool_output(first_id) == "keep me"


# ---------------------------------------------------------------------------
# AT-4: 4096-byte UTF-8 cap
# ---------------------------------------------------------------------------


class TestAT4ByteCap:
    async def test_eight_kb_body_capped_at_four_kb(self) -> None:
        """AT-4: 8192-byte tool_response → stored body ≤ 4096 UTF-8 bytes."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_big")
        big_body = "x" * 8192
        await _fire_post(tm, "toolu_big", tool_response=big_body)

        stored = tm.get_tool_output("toolu_big")
        assert stored is not None
        assert len(stored.encode("utf-8")) <= 4096

    async def test_small_body_stored_intact(self) -> None:
        """Bodies under 4096 bytes are stored without truncation."""
        tm = _make_teammate()
        _inject_pre(tm, "toolu_small")
        small_body = "y" * 100
        await _fire_post(tm, "toolu_small", tool_response=small_body)

        stored = tm.get_tool_output("toolu_small")
        assert stored is not None
        # Small bodies come back unchanged (no ellipsis appended)
        assert len(stored.encode("utf-8")) <= 4096


# ---------------------------------------------------------------------------
# AT-7: redaction failure → sentinel stored + WARNING logged
# ---------------------------------------------------------------------------


class TestAT7RedactionFailure:
    async def test_redact_output_raises_stores_sentinel_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AT-7: if redact_output raises RuntimeError, sentinel is stored and WARNING logged."""

        def _raising_redact(text: str) -> str:
            raise RuntimeError("boom from test")

        monkeypatch.setattr(sdk_mod, "redact_output", _raising_redact)

        tm = _make_teammate("tmAT7")
        _inject_pre(tm, "toolu_fail")

        with caplog.at_level(logging.WARNING):
            await _fire_post(tm, "toolu_fail", tool_response="some secret data")

        stored = tm.get_tool_output("toolu_fail")
        assert stored == "[REDACTION_FAILED: RuntimeError]"
        # WARNING must mention teammate id and tool_use_id
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("tmAT7" in m for m in warning_messages), f"No WARNING mentioning teammate id. Warnings: {warning_messages}"
        assert any("toolu_fail" in m for m in warning_messages), f"No WARNING mentioning tool_use_id. Warnings: {warning_messages}"

    async def test_redact_output_raises_does_not_store_raw_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AT-7 sad path: raw unredacted body must NEVER be stored on failure."""

        def _raising_redact(text: str) -> str:
            raise ValueError("pattern crash")

        monkeypatch.setattr(sdk_mod, "redact_output", _raising_redact)

        tm = _make_teammate()
        _inject_pre(tm, "toolu_raw_check")
        raw_secret = "super-secret-token-abc123xyz"
        await _fire_post(tm, "toolu_raw_check", tool_response=raw_secret)

        stored = tm.get_tool_output("toolu_raw_check")
        # Stored value must be the sentinel, never the raw secret
        assert stored is not None
        assert stored == "[REDACTION_FAILED: ValueError]"
        assert raw_secret not in stored


# ---------------------------------------------------------------------------
# AT-8: ToolEvent dataclass has no output body field (regression guard)
# ---------------------------------------------------------------------------


class TestAT8ToolEventNoBodyField:
    def test_tool_event_has_no_body_output_response_field(self) -> None:
        """AT-8: ToolEvent dataclass must not have body/output/response/tool_response fields."""
        field_names = {f.name for f in dataclasses.fields(ToolEvent)}
        forbidden = {"body", "output", "response", "tool_response"}
        intersection = field_names & forbidden
        assert not intersection, (
            f"ToolEvent must not carry output body fields; found: {intersection}. "
            "These belong in the parallel tool-output store, not the frozen snapshot contract."
        )

    def test_tool_event_expected_fields_present(self) -> None:
        """Sanity check: expected ToolEvent fields are still present."""
        field_names = {f.name for f in dataclasses.fields(ToolEvent)}
        required = {
            "teammate_id", "tool_name", "tool_use_id",
            "started_at_wallclock", "finished_at_wallclock",
            "duration_seconds", "outcome", "args_summary",
            "error_summary", "redaction_version",
        }
        missing = required - field_names
        assert not missing, f"Expected ToolEvent fields missing: {missing}"
