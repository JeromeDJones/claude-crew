"""Unit tests for TranscriptSink."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_crew.transcript import TranscriptSink, resolve_transcript_dir


@pytest.fixture
def enable_transcripts(monkeypatch, tmp_path):
    """Override the conftest default-disabled fixture for tests that
    actually exercise the transcript file writes."""
    monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
    return tmp_path


# ---------- resolve_transcript_dir ----------


class TestResolveTranscriptDir:
    def test_uses_xdg_state_home_when_set(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DIR", raising=False)
        result = resolve_transcript_dir()
        assert result == tmp_path / "claude-crew" / "transcripts"

    def test_falls_back_to_local_state_when_xdg_unset(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = resolve_transcript_dir()
        assert result == tmp_path / ".local" / "state" / "claude-crew" / "transcripts"

    def test_explicit_env_var_overrides_xdg(self, monkeypatch, tmp_path) -> None:
        override = tmp_path / "custom"
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(override))
        monkeypatch.setenv("XDG_STATE_HOME", "/should/not/be/used")
        result = resolve_transcript_dir()
        assert result == override


# ---------- TranscriptSink lifecycle and writes ----------


class TestTranscriptSinkLifecycle:
    def test_open_creates_directory_and_file(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path / "t"))
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        sink = TranscriptSink(crew_id="abc12345")
        try:
            assert not sink.disabled
            assert sink.path is not None
            assert sink.path.exists()
            assert sink.path.parent == tmp_path / "t"
            assert "abc12345" in sink.path.name
        finally:
            sink.close()

    def test_filename_format_includes_utc_timestamp_and_crew_id(
        self, enable_transcripts,
    ) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        try:
            name = sink.path.name
            # Format: YYYYMMDD-HHMMSSZ-<8hex>.jsonl
            assert name.endswith("-abc12345.jsonl")
            stem = name.removesuffix("-abc12345.jsonl")
            # 20260425-170000Z (length 16)
            assert len(stem) == 16
            assert stem[8] == "-"
            assert stem.endswith("Z")
        finally:
            sink.close()

    def test_disabled_via_env_var(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", "1")
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        sink = TranscriptSink(crew_id="abc12345")
        assert sink.disabled
        assert sink.path is None
        # Writes are silent no-ops.
        sink.write_envelope({"id": "m-1", "seq": 1})
        sink.write_lifecycle("spawn", {"teammate_id": "t-x"})
        sink.close()

    def test_init_failure_disables_self(
        self, monkeypatch, tmp_path, capsys,
    ) -> None:
        # Point at a path that exists as a file, not a dir — mkdir will fail.
        bad = tmp_path / "blocker"
        bad.write_text("i am a file")
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(bad / "subdir"))
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        sink = TranscriptSink(crew_id="abc12345")
        assert sink.disabled
        assert sink.path is None
        err = capsys.readouterr().err
        assert "[claude-crew]" in err


# ---------- Write semantics ----------


class TestTranscriptSinkWrites:
    def test_write_envelope_appends_line(self, enable_transcripts) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_envelope({
                "id": "m-1", "seq": 1, "sender": "lead",
                "recipient": "t-x", "timestamp": 100.0, "payload": "hi",
            })
            content = sink.path.read_text()
            lines = [l for l in content.splitlines() if l]
            assert len(lines) == 1
            obj = json.loads(lines[0])
            assert obj["v"] == 1
            assert obj["kind"] == "envelope"
            assert obj["crew_id"] == "abc12345"
            assert obj["id"] == "m-1"
            assert obj["seq"] == 1
            assert obj["payload"] == "hi"
            assert "ts" in obj
        finally:
            sink.close()

    def test_write_lifecycle_appends_line(self, enable_transcripts) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_lifecycle("spawn", {
                "teammate_id": "t-x", "name": "alice",
                "role": "planner", "model": "claude-sonnet-4-6",
            })
            obj = json.loads(sink.path.read_text().strip())
            assert obj["v"] == 1
            assert obj["kind"] == "lifecycle"
            assert obj["crew_id"] == "abc12345"
            assert obj["event"] == "spawn"
            assert obj["teammate_id"] == "t-x"
            assert obj["role"] == "planner"
            assert "ts" in obj
        finally:
            sink.close()

    def test_writes_flush_immediately_for_tail_f(
        self, enable_transcripts,
    ) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_envelope({
                "id": "m-1", "seq": 1, "sender": "lead",
                "recipient": "t-x", "timestamp": 0.0, "payload": "x",
            })
            # Read the file from a different fd — no close yet.
            content = sink.path.read_text()
            assert "m-1" in content
        finally:
            sink.close()

    def test_multiple_writes_each_one_line(
        self, enable_transcripts,
    ) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        try:
            for i in range(5):
                sink.write_envelope({
                    "id": f"m-{i}", "seq": i + 1, "sender": "lead",
                    "recipient": "t-x", "timestamp": 0.0, "payload": i,
                })
            lines = [l for l in sink.path.read_text().splitlines() if l]
            assert len(lines) == 5
            for i, line in enumerate(lines):
                obj = json.loads(line)
                assert obj["seq"] == i + 1
        finally:
            sink.close()

    def test_write_after_close_is_silent_no_op(
        self, enable_transcripts,
    ) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        # Sanity: file was actually opened before we close.
        assert sink._fp is not None
        sink.close()
        # Should not raise.
        sink.write_envelope({"id": "m-1", "seq": 1})
        sink.write_lifecycle("kill", {"teammate_id": "t-x"})

    def test_close_is_idempotent(self, enable_transcripts) -> None:
        sink = TranscriptSink(crew_id="abc12345")
        assert sink._fp is not None
        sink.close()
        sink.close()  # Should not raise.

    def test_transient_write_failure_logs_but_does_not_disable(
        self, enable_transcripts, capsys,
    ) -> None:
        # Promised behavior: a single write failure logs to stderr and
        # leaves the sink alive. The next write may succeed.
        sink = TranscriptSink(crew_id="abc12345")
        try:
            assert not sink.disabled

            calls = {"count": 0}
            real_write = sink._fp.write

            def flaky_write(s):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError("simulated transient")
                return real_write(s)

            sink._fp.write = flaky_write

            sink.write_envelope({"id": "m-1", "seq": 1})  # raises internally
            assert not sink.disabled, "transient failure must not disable"
            err = capsys.readouterr().err
            assert "transcript write failed" in err

            sink.write_envelope({"id": "m-2", "seq": 2})  # should succeed now
            content = sink.path.read_text()
            assert "m-2" in content
            assert "m-1" not in content
        finally:
            sink._fp.write = real_write
            sink.close()

    def test_disabled_sink_writes_no_file(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", "1")
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_envelope({"id": "m-1", "seq": 1})
            assert list(tmp_path.iterdir()) == []
        finally:
            sink.close()

    def test_write_tool_event_appends_jsonl_line(self, enable_transcripts) -> None:
        """SC-5: write_tool_event appends a well-formed JSONL line.

        BDD scenario: TranscriptSink.write_tool_event appends a JSONL line.
          When write_tool_event("tool_start", {...}) is called
          Then the transcript file gains one line
          And that line is valid JSON with kind="tool_start", v=1, ts present, crew_id present
        """
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_tool_event("tool_start", {
                "teammate_id": "t-x",
                "tool_name": "Bash",
                "tool_use_id": "toolu_01abc",
                "started_at_wallclock": 1000.0,
                "args_summary": "command=pytest tests/ -v",
                "redaction_version": "v1",
            })
            content = sink.path.read_text()
            lines = [ln for ln in content.splitlines() if ln]
            assert len(lines) == 1
            obj = json.loads(lines[0])
            assert obj["v"] == 1
            assert obj["kind"] == "tool_start"
            assert obj["crew_id"] == "abc12345"
            assert "ts" in obj
            assert obj["teammate_id"] == "t-x"
            assert obj["tool_name"] == "Bash"
            assert obj["tool_use_id"] == "toolu_01abc"
            assert obj["args_summary"] == "command=pytest tests/ -v"
            assert obj["redaction_version"] == "v1"
        finally:
            sink.close()

    def test_write_tool_end_event(self, enable_transcripts) -> None:
        """write_tool_event works for tool_end events with outcome/duration fields."""
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_tool_event("tool_end", {
                "teammate_id": "t-x",
                "tool_name": "Bash",
                "tool_use_id": "toolu_01abc",
                "finished_at_wallclock": 1005.0,
                "duration_seconds": 5.0,
                "outcome": "ok",
                "error_summary": None,
                "redaction_version": "v1",
            })
            obj = json.loads(sink.path.read_text().strip())
            assert obj["kind"] == "tool_end"
            assert obj["outcome"] == "ok"
            assert obj["duration_seconds"] == 5.0
            assert obj["error_summary"] is None
        finally:
            sink.close()

    def test_write_tool_event_disabled_is_silent_no_op(
        self, monkeypatch, tmp_path,
    ) -> None:
        """write_tool_event honours CLAUDE_CREW_TRANSCRIPT_DISABLED=1."""
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", "1")
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        sink = TranscriptSink(crew_id="abc12345")
        try:
            sink.write_tool_event("tool_start", {"teammate_id": "t-x", "tool_name": "Bash"})
            assert list(tmp_path.iterdir()) == []
        finally:
            sink.close()
