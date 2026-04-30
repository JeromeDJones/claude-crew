"""Tests for claude_crew.instance_registry."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_crew.instance_registry import InstanceRegistry, resolve_registry_dir


# ── resolve_registry_dir ─────────────────────────────────────────────────────


class TestResolveRegistryDir:
    def test_explicit_env_var_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert resolve_registry_dir() == tmp_path

    def test_xdg_state_home_used_when_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert resolve_registry_dir() == tmp_path / "claude-crew" / "instances"

    def test_fallback_to_home_local_state(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        result = resolve_registry_dir()
        assert result == Path.home() / ".local" / "state" / "claude-crew" / "instances"


# ── register ─────────────────────────────────────────────────────────────────


class TestRegister:
    @pytest.fixture
    def reg(self, tmp_path, monkeypatch) -> InstanceRegistry:
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        return InstanceRegistry(crew_id="abc123", port=7821)

    def test_file_created(self, reg, tmp_path):
        reg.register()
        assert (tmp_path / "abc123.json").exists()

    def test_file_contains_correct_fields(self, reg, tmp_path):
        reg.register()
        data = json.loads((tmp_path / "abc123.json").read_text())
        assert data["crew_id"] == "abc123"
        assert data["port"] == 7821
        assert data["pid"] == os.getpid()
        assert isinstance(data["started_at"], float)

    def test_creates_directory_if_absent(self, tmp_path, monkeypatch):
        subdir = tmp_path / "nested" / "dir"
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(subdir))
        reg = InstanceRegistry(crew_id="xyz", port=9000)
        reg.register()
        assert (subdir / "xyz.json").exists()

    def test_no_tmp_file_left_after_register(self, reg, tmp_path):
        reg.register()
        assert not (tmp_path / "abc123.json.tmp").exists()

    def test_second_register_overwrites_first(self, reg, tmp_path):
        reg.register()
        reg2 = InstanceRegistry.__new__(InstanceRegistry)
        reg2.crew_id = "abc123"
        reg2.port = 9999
        reg2._dir = tmp_path
        reg2.register()
        data = json.loads((tmp_path / "abc123.json").read_text())
        assert data["port"] == 9999


# ── deregister ───────────────────────────────────────────────────────────────


class TestDeregister:
    @pytest.fixture
    def reg(self, tmp_path, monkeypatch) -> InstanceRegistry:
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        r = InstanceRegistry(crew_id="abc123", port=7821)
        r.register()
        return r

    def test_file_removed(self, reg, tmp_path):
        reg.deregister()
        assert not (tmp_path / "abc123.json").exists()

    def test_idempotent_on_missing_file(self, reg):
        reg.deregister()
        reg.deregister()  # should not raise

    def test_no_exception_on_permission_error(self, reg, tmp_path, monkeypatch):
        original_unlink = Path.unlink

        def _raise_perm(self, missing_ok=False):
            raise PermissionError("nope")

        monkeypatch.setattr(Path, "unlink", _raise_perm)
        reg.deregister()  # should not raise


# ── read_all ─────────────────────────────────────────────────────────────────


class TestReadAll:
    @pytest.fixture
    def reg(self, tmp_path, monkeypatch) -> InstanceRegistry:
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        return InstanceRegistry(crew_id="self", port=7821)

    def test_empty_when_dir_absent(self, tmp_path, monkeypatch):
        absent = tmp_path / "no-such-dir"
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(absent))
        reg = InstanceRegistry(crew_id="x", port=1)
        assert reg.read_all() == []

    def test_returns_own_entry_when_alive(self, reg, tmp_path):
        reg.register()
        entries = reg.read_all()
        assert len(entries) == 1
        assert entries[0]["crew_id"] == "self"

    def test_dead_pid_entry_removed_and_excluded(self, reg, tmp_path):
        # Write an entry with a guaranteed-dead PID
        dead_entry = {"crew_id": "dead", "port": 9999, "pid": 2**22, "started_at": 0.0}
        (tmp_path / "dead.json").write_text(json.dumps(dead_entry))

        entries = reg.read_all()
        ids = [e["crew_id"] for e in entries]
        assert "dead" not in ids
        assert not (tmp_path / "dead.json").exists()

    def test_corrupt_file_deleted_and_excluded(self, reg, tmp_path):
        (tmp_path / "corrupt.json").write_text("not json {{{")
        entries = reg.read_all()
        ids = [e["crew_id"] for e in entries]
        assert "corrupt" not in ids
        assert not (tmp_path / "corrupt.json").exists()

    def test_multiple_live_entries_returned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        pid = os.getpid()
        for cid in ("a", "b", "c"):
            entry = {"crew_id": cid, "port": 7820, "pid": pid, "started_at": 0.0}
            (tmp_path / f"{cid}.json").write_text(json.dumps(entry))
        reg = InstanceRegistry(crew_id="a", port=7820)
        entries = reg.read_all()
        assert {e["crew_id"] for e in entries} == {"a", "b", "c"}

    def test_ignores_non_json_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        (tmp_path / "readme.txt").write_text("not a registry entry")
        (tmp_path / "foo.json.tmp").write_text("{}")
        reg = InstanceRegistry(crew_id="x", port=1)
        # Should not raise and should not return the non-json files
        entries = reg.read_all()
        assert all("crew_id" in e for e in entries)

    def test_no_exception_when_dir_unreadable(self, reg, tmp_path, monkeypatch):
        # Simulate directory read failure gracefully by pointing at a file path
        (tmp_path / "notadir.json").write_text("{}")
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path / "notadir.json"))
        reg2 = InstanceRegistry(crew_id="x", port=1)
        # read_all() should return [] or entries without raising
        result = reg2.read_all()
        assert isinstance(result, list)


# ── concurrent write safety ───────────────────────────────────────────────────


class TestConcurrentWrites:
    def test_two_registrations_no_collision(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        r1 = InstanceRegistry(crew_id="crew1", port=7821)
        r2 = InstanceRegistry(crew_id="crew2", port=7822)
        r1.register()
        r2.register()
        assert (tmp_path / "crew1.json").exists()
        assert (tmp_path / "crew2.json").exists()
        d1 = json.loads((tmp_path / "crew1.json").read_text())
        d2 = json.loads((tmp_path / "crew2.json").read_text())
        assert d1["crew_id"] == "crew1"
        assert d2["crew_id"] == "crew2"
