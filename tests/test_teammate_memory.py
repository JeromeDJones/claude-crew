"""Tests for claude_crew/teammate_memory.py — Feature: Teammate Memory Persistence."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_crew.teammate_memory import (
    _sanitize_role,
    build_memory_section,
    memory_file_path,
    memory_index_path,
)
from claude_crew.teammate_prompt import SENTINEL_MEMORY


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestMemoryFilePath:
    def test_returns_correct_path_for_known_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cwd = str(tmp_path)
        encoded = "-" + cwd.strip("/").replace("/", "-")
        expected = Path.home() / ".claude" / "projects" / encoded / "memory" / "sentinel.md"
        assert memory_file_path("sentinel") == expected

    def test_role_appears_as_filename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert memory_file_path("rr-planner").name == "rr-planner.md"

    def test_rejects_unsafe_role(self):
        with pytest.raises(ValueError, match="not allowed"):
            memory_file_path("../../etc/passwd")

    def test_rejects_role_with_slash(self):
        with pytest.raises(ValueError):
            memory_file_path("foo/bar")

    def test_rejects_role_with_spaces(self):
        with pytest.raises(ValueError):
            memory_file_path("role with spaces")


class TestSanitizeRole:
    def test_accepts_kebab_case(self):
        assert _sanitize_role("rr-planner") == "rr-planner"

    def test_accepts_underscores(self):
        assert _sanitize_role("my_role") == "my_role"

    def test_accepts_alphanumeric(self):
        assert _sanitize_role("sentinel") == "sentinel"

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            _sanitize_role("../../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(ValueError):
            _sanitize_role("foo/bar")

    def test_rejects_dot(self):
        with pytest.raises(ValueError):
            _sanitize_role("foo.bar")


class TestMemoryIndexPath:
    def test_returns_memory_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert memory_index_path().name == "MEMORY.md"

    def test_same_parent_as_memory_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert memory_index_path().parent == memory_file_path("sentinel").parent


# ---------------------------------------------------------------------------
# build_memory_section
# ---------------------------------------------------------------------------


class TestBuildMemorySectionNoFile:
    def test_contains_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert SENTINEL_MEMORY in result

    def test_contains_memory_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert "sentinel.md" in result

    def test_no_sc3b_note_when_write_available(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert "Write tool is not in your tool list" not in result

    def test_contains_memory_index_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Write",))
        assert "MEMORY.md" in result

    def test_no_file_yet_message(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Write",))
        assert "No prior memory" in result


class TestBuildMemorySectionFileExists:
    def test_contains_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("prior observations here")
        result = build_memory_section("builder", ("Read", "Write"))
        assert SENTINEL_MEMORY in result

    def test_contains_file_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("prior observations here")
        result = build_memory_section("builder", ("Read", "Write"))
        assert "prior observations here" in result

    def test_contains_file_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some memory")
        result = build_memory_section("builder", ("Read", "Write"))
        assert str(path) in result

    def test_no_sc3b_note_when_write_available(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some memory")
        result = build_memory_section("builder", ("Read", "Write"))
        assert "Write tool is not in your tool list" not in result

    def test_contains_memory_index_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some memory")
        result = build_memory_section("builder", ("Write",))
        assert "MEMORY.md" in result


class TestBuildMemorySectionNoWriteTool:
    def test_sc3b_note_when_tools_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ())
        assert "Write tool is not in your tool list" in result

    def test_sc3b_note_when_tools_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", None)
        assert "Write tool is not in your tool list" in result

    def test_sc3b_note_when_tools_lacks_write(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ("Read", "Bash"))
        assert "Write tool is not in your tool list" in result

    def test_sentinel_still_present_with_sc3b(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = build_memory_section("sentinel", ())
        assert SENTINEL_MEMORY in result

    def test_sc3b_note_with_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("sentinel")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some memory")
        result = build_memory_section("sentinel", ())
        assert "Write tool is not in your tool list" in result
        assert "some memory" in result


class TestBuildMemorySectionTruncation:
    def test_large_file_truncated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 60_000)
        result = build_memory_section("builder", ("Write",))
        assert "truncated at 50 KB" in result

    def test_file_at_cap_not_truncated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"y" * 51_200)
        result = build_memory_section("builder", ("Write",))
        assert "truncated at 50 KB" not in result


class TestBuildMemorySectionIOError:
    def test_unreadable_file_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = memory_file_path("builder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret")
        path.chmod(0o000)
        try:
            result = build_memory_section("builder", ("Write",))
            assert SENTINEL_MEMORY in result
        finally:
            path.chmod(0o644)


class TestMemoryIndexNotMutated:
    def test_build_memory_section_does_not_create_memory_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        index = memory_index_path()
        assert not index.exists()
        build_memory_section("sentinel", ("Write",))
        assert not index.exists()

    def test_build_memory_section_does_not_modify_existing_memory_md(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        index = memory_index_path()
        index.parent.mkdir(parents=True, exist_ok=True)
        original = "- [Some memory](some.md) — existing entry\n"
        index.write_text(original)
        build_memory_section("sentinel", ("Write",))
        assert index.read_text() == original
