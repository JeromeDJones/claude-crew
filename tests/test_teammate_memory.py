"""Tests for claude_crew/teammate_memory.py — Feature: Teammate Memory Persistence.

Memory location: ~/.claude/agent-memory/<role>/ (user-scoped, role-isolated).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_crew.teammate_memory import (
    _sanitize_role,
    build_memory_section,
    is_lead_project_memory_path,
    memory_dir,
    memory_index_path,
    write_guard_deny_message,
)
from claude_crew.teammate_prompt import SENTINEL_MEMORY


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestMemoryDir:
    def test_returns_user_scoped_role_dir(self):
        expected = Path.home() / ".claude" / "agent-memory" / "sentinel"
        assert memory_dir("sentinel") == expected

    def test_role_appears_as_directory_name(self):
        assert memory_dir("rr-planner").name == "rr-planner"

    def test_path_is_not_project_scoped(self):
        # Critical: must NOT contain "projects" segment — that was the v1 bug.
        result = memory_dir("sentinel")
        assert "projects" not in result.parts

    def test_rejects_unsafe_role(self):
        with pytest.raises(ValueError, match="not allowed"):
            memory_dir("../../etc/passwd")

    def test_rejects_role_with_slash(self):
        with pytest.raises(ValueError):
            memory_dir("foo/bar")


class TestMemoryDirScope:
    """Acceptance tests 1–4: scope keyword arms and ValueError guard."""

    def test_memory_dir_user_scope_matches_home(self):
        # AT-1: user scope returns home-based path (same as one-arg call)
        role = "sentinel"
        expected = Path.home() / ".claude" / "agent-memory" / role
        assert memory_dir(role, scope="user") == expected

    def test_memory_dir_user_scope_default(self):
        # AT-1: default scope is "user" — one-arg call still works
        role = "sentinel"
        assert memory_dir(role) == memory_dir(role, scope="user")

    def test_memory_dir_project_scope(self, tmp_path):
        # AT-2: project scope returns <root>/.claude/agent-memory/<role>
        role = "builder"
        result = memory_dir(role, scope="project", project_root=tmp_path)
        assert result == tmp_path / ".claude" / "agent-memory" / role

    def test_memory_dir_local_scope(self, tmp_path):
        # AT-3: local scope returns <root>/.claude/agent-memory.local/<role>
        role = "builder"
        result = memory_dir(role, scope="local", project_root=tmp_path)
        assert result == tmp_path / ".claude" / "agent-memory.local" / role

    def test_memory_dir_raises_for_project_scope_without_root(self):
        # AT-4: project scope without project_root raises ValueError
        with pytest.raises(ValueError, match="project_root"):
            memory_dir("builder", scope="project", project_root=None)

    def test_memory_dir_raises_for_local_scope_without_root(self):
        # AT-4: local scope without project_root raises ValueError
        with pytest.raises(ValueError, match="project_root"):
            memory_dir("builder", scope="local", project_root=None)


class TestSanitizeRole:
    def test_accepts_kebab_case(self):
        assert _sanitize_role("rr-planner") == "rr-planner"

    def test_accepts_alphanumeric(self):
        assert _sanitize_role("sentinel") == "sentinel"

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            _sanitize_role("../../etc/passwd")

    def test_rejects_dot(self):
        with pytest.raises(ValueError):
            _sanitize_role("foo.bar")


class TestMemoryIndexPath:
    def test_returns_memory_md_inside_role_dir(self):
        result = memory_index_path("sentinel")
        assert result.name == "MEMORY.md"
        assert result.parent == memory_dir("sentinel")


# ---------------------------------------------------------------------------
# build_memory_section
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.claude/agent-memory/ into tmp_path for isolation."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestGuidanceTextPerScope:
    """Acceptance tests 5–7: scope-specific guidance text in build_memory_section."""

    def test_user_scope_contains_cross_project_phrase(self, tmp_path, fake_home):
        # AT-5: user scope rendered text contains "apply across projects"
        result = build_memory_section("builder", ("Read", "Write"), scope="user")
        assert "apply across projects" in result

    def test_user_scope_no_project_specific_phrasing(self, tmp_path, fake_home):
        # AT-5: user scope does NOT contain project-scoped or local-scoped phrases
        result = build_memory_section("builder", ("Read", "Write"), scope="user")
        assert "project-scoped memory" not in result
        assert "local-scoped memory" not in result
        assert ".gitignore" not in result

    def test_project_scope_emphasizes_committed_shared_memory(self, tmp_path):
        # AT-6: project scope text emphasizes project-specific committed/shared memory
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="project", project_root=tmp_path
        )
        assert "project-scoped memory" in result
        assert "committed" in result
        assert "shared" in result or "team" in result

    def test_project_scope_warns_against_secrets(self, tmp_path):
        # AT-6: project scope warns against secrets and machine-specific detail
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="project", project_root=tmp_path
        )
        assert "secrets" in result.lower() or "credentials" in result.lower()
        assert "machine-specific" in result.lower()

    def test_project_scope_names_project_path(self, tmp_path):
        # AT-6: project scope names the project-scoped directory path
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="project", project_root=tmp_path
        )
        expected_dir = str(tmp_path / ".claude" / "agent-memory" / "builder")
        assert expected_dir in result

    def test_local_scope_describes_machine_local_memory(self, tmp_path):
        # AT-7: local scope describes machine-local non-shared memory
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="local", project_root=tmp_path
        )
        assert "local-scoped memory" in result
        assert "machine-local" in result or "not shared" in result or "not committed" in result

    def test_local_scope_mentions_experimental_notes(self, tmp_path):
        # AT-7: local scope mentions experimental notes
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="local", project_root=tmp_path
        )
        assert "experimental" in result.lower()

    def test_local_scope_recommends_gitignore_entry(self, tmp_path):
        # AT-7: local scope recommends the .gitignore entry .claude/agent-memory.local/
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="local", project_root=tmp_path
        )
        assert ".claude/agent-memory.local/" in result
        assert ".gitignore" in result

    def test_local_scope_names_local_path(self, tmp_path):
        # AT-7: local scope names the local-scoped directory path
        result = build_memory_section(
            "builder", ("Read", "Write"), scope="local", project_root=tmp_path
        )
        expected_dir = str(tmp_path / ".claude" / "agent-memory.local" / "builder")
        assert expected_dir in result


class TestBuildMemorySectionNoIndex:
    def test_contains_sentinel(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert SENTINEL_MEMORY in result

    def test_contains_role_directory_path(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert str(memory_dir("sentinel")) in result

    def test_contains_index_path(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert "MEMORY.md" in result

    def test_no_prior_memories_note(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert "No prior memories yet" in result

    def test_includes_boundary_from_project_memory(self, fake_home):
        """The memory section must explicitly mark project memory as
        off-limits and explain BOTH mechanisms that enforce it: the
        spawn-time env suppression AND the runtime write guard.

        Renamed from test_includes_disambiguation_from_project_memory
        2026-05-17 when the wording shifted from 'disambiguation against
        a visible-but-not-yours memory' to 'boundary against a
        suppressed-and-write-blocked memory' — auto-memory is no longer
        loaded for SDK teammates so the prior framing was wrong.
        """
        result = build_memory_section("sentinel", ("Write",))
        # Keyword on the new instruction header
        assert "Boundaries" in result
        # The protected path the teammate must not write to
        assert "~/.claude/projects/*/memory/" in result
        # Both enforcement mechanisms are named
        assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" in result
        assert "write guard" in result.lower()
        # The lead is named as the owner
        assert "lead session" in result.lower()

    def test_includes_what_to_save_section(self, fake_home):
        result = build_memory_section("sentinel", ("Write",))
        assert "What to save" in result
        assert "across projects" in result

    def test_includes_what_not_to_save_section(self, fake_home):
        result = build_memory_section("sentinel", ("Write",))
        assert "What NOT to save" in result
        assert "git log" in result
        assert "external" in result and "pointers" in result

    def test_includes_when_not_to_save_section(self, fake_home):
        result = build_memory_section("sentinel", ("Write",))
        assert "When NOT to save" in result
        assert "Default to no" in result

    def test_includes_how_to_save_section(self, fake_home):
        result = build_memory_section("sentinel", ("Write",))
        assert "How to save" in result
        assert "Two-step" in result
        assert "type: principle | pattern | gotcha | reference" in result


class TestBuildMemorySectionIndexExists:
    def test_index_content_appears_in_section(self, fake_home):
        index_path = memory_index_path("builder")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            "# MEMORY.md\n\n- [Some lesson](some_lesson.md) — what I learned\n"
        )
        result = build_memory_section("builder", ("Write",))
        assert "Some lesson" in result
        assert "what I learned" in result

    def test_index_truncated_at_200_lines(self, fake_home):
        index_path = memory_index_path("builder")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        # Build a 250-line file, ensure last 50 lines aren't included.
        lines = [f"- [entry-{i}](file-{i}.md) — line {i}" for i in range(250)]
        index_path.write_text("\n".join(lines))
        result = build_memory_section("builder", ("Write",))
        assert "entry-199" in result
        assert "entry-249" not in result
        assert "truncated at 200 lines" in result

    def test_no_prior_memories_note_absent_when_index_exists(self, fake_home):
        index_path = memory_index_path("builder")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("- [x](x.md) — y")
        result = build_memory_section("builder", ("Write",))
        assert "No prior memories yet" not in result


class TestBuildMemorySectionWriteToolMissing:
    def test_persistence_note_when_tools_empty(self, fake_home):
        result = build_memory_section("sentinel", ())
        assert "Write tool is not in your tool list" in result

    def test_persistence_note_when_tools_none(self, fake_home):
        result = build_memory_section("sentinel", None)
        assert "Write tool is not in your tool list" in result

    def test_persistence_note_when_tools_lacks_write(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Bash"))
        assert "Write tool is not in your tool list" in result

    def test_no_persistence_note_when_write_present(self, fake_home):
        result = build_memory_section("sentinel", ("Read", "Write"))
        assert "Write tool is not in your tool list" not in result

    def test_instructions_still_included_without_write(self, fake_home):
        # Even without Write, the agent should see the index and structure
        # so it knows what's been remembered.
        result = build_memory_section("sentinel", ())
        assert SENTINEL_MEMORY in result
        assert "What to save" in result


class TestBuildMemorySectionIOError:
    def test_unreadable_index_does_not_raise(self, fake_home):
        index_path = memory_index_path("builder")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("secret")
        index_path.chmod(0o000)
        try:
            result = build_memory_section("builder", ("Write",))
            assert SENTINEL_MEMORY in result
            assert "could not be read" in result
        finally:
            index_path.chmod(0o644)


class TestNoSpontaneousMutation:
    def test_does_not_create_directory(self, fake_home):
        directory = memory_dir("never-spawned")
        assert not directory.exists()
        build_memory_section("never-spawned", ("Write",))
        assert not directory.exists()

    def test_does_not_create_index(self, fake_home):
        index = memory_index_path("never-spawned")
        assert not index.exists()
        build_memory_section("never-spawned", ("Write",))
        assert not index.exists()


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------


class TestIsLeadProjectMemoryPath:
    def test_blocks_path_in_lead_project_memory(self, fake_home):
        path = fake_home / ".claude" / "projects" / "-foo-bar" / "memory" / "x.md"
        assert is_lead_project_memory_path(str(path)) is True

    def test_blocks_nested_path_in_lead_project_memory(self, fake_home):
        path = fake_home / ".claude" / "projects" / "-foo" / "memory" / "sub" / "x.md"
        assert is_lead_project_memory_path(str(path)) is True

    def test_allows_agent_memory_path(self, fake_home):
        path = fake_home / ".claude" / "agent-memory" / "sentinel" / "x.md"
        assert is_lead_project_memory_path(str(path)) is False

    def test_allows_unrelated_path(self, fake_home):
        path = fake_home / "some" / "project" / "memory" / "x.md"
        assert is_lead_project_memory_path(str(path)) is False

    def test_allows_projects_directory_without_memory(self, fake_home):
        path = fake_home / ".claude" / "projects" / "-foo" / "todos" / "x.json"
        assert is_lead_project_memory_path(str(path)) is False

    def test_does_not_false_positive_on_role_named_projects(self, fake_home):
        """Path with both 'projects' and 'memory' in parts but NOT under
        ~/.claude/projects/ must not be blocked. This is the H-2 bug."""
        path = fake_home / ".claude" / "agent-memory" / "projects" / "memory" / "x.md"
        assert is_lead_project_memory_path(str(path)) is False

    def test_does_not_false_positive_on_memory_in_wrong_position(self, fake_home):
        """~/.claude/projects/<slug>/foo/memory/x.md — memory not at parts[1]."""
        path = fake_home / ".claude" / "projects" / "-foo" / "subdir" / "memory" / "x.md"
        assert is_lead_project_memory_path(str(path)) is False

    def test_blocks_symlink_in_pointing_into_protected_zone(self, fake_home):
        """Caller-supplied path is a symlink that resolves into the zone."""
        target = fake_home / ".claude" / "projects" / "-foo" / "memory"
        target.mkdir(parents=True, exist_ok=True)
        (target / "real.md").write_text("x")
        symlink = fake_home / "evil_link.md"
        symlink.symlink_to(target / "real.md")
        assert is_lead_project_memory_path(str(symlink)) is True

    def test_blocks_symlink_out_protected_dir_pointing_to_safe(self, fake_home):
        """The path within the zone is itself a symlink to a safe location.
        The H-2 dual-check: expanded path is in the zone even if resolved escapes."""
        # Make ~/.claude/projects/-foo/memory itself a symlink to /tmp/safe.
        protected_parent = fake_home / ".claude" / "projects" / "-foo"
        protected_parent.mkdir(parents=True, exist_ok=True)
        safe_target = fake_home / "tmp_safe"
        safe_target.mkdir(parents=True, exist_ok=True)
        (protected_parent / "memory").symlink_to(safe_target)
        # Now writing to "~/.claude/projects/-foo/memory/x.md" resolves to
        # ~/tmp_safe/x.md — but the EXPANDED path is still in the protected zone.
        attempted = protected_parent / "memory" / "x.md"
        assert is_lead_project_memory_path(str(attempted)) is True

    def test_handles_relative_path_resolving_into_zone(self, fake_home, monkeypatch):
        """A relative path that resolves into the zone gets caught via .resolve()."""
        target = fake_home / ".claude" / "projects" / "-foo" / "memory"
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(target)
        # Relative path "x.md" resolves to <target>/x.md.
        assert is_lead_project_memory_path("x.md") is True

    def test_empty_string_does_not_match(self, fake_home):
        # Defensive — empty string shouldn't crash or match.
        assert is_lead_project_memory_path("") is False


class TestWriteGuardDenyMessage:
    def test_message_names_role_target(self, fake_home):
        msg = write_guard_deny_message("sentinel", "/x/y/z.md")
        assert "sentinel" in msg
        assert str(memory_dir("sentinel")) in msg

    def test_message_names_attempted_path(self, fake_home):
        msg = write_guard_deny_message("sentinel", "/some/attempted/path.md")
        assert "/some/attempted/path.md" in msg

    def test_message_explains_why(self, fake_home):
        msg = write_guard_deny_message("sentinel", "/x.md")
        assert "lead" in msg.lower()
        assert "blocked" in msg.lower()
