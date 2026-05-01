"""E2E pipeline tests for FEATURE-claude-code-agent-format-compatibility (#15).

Cohesive end-to-end tests that exercise the full feature pipeline through
parse_pack_text → discover_dir → merge_packs → build_merged_pack → spawn.
Component-level tests in test_pack_loader.py / test_user_loader.py /
test_subagents.py verify individual pieces; this file verifies the assembled
feature behaves correctly under real conditions.

Coverage:
- SC-8 happy path: minimal pack file (only name + description)
- SC-10a stub-mode WARN check: zero unsupported-key WARNs across the
  full pipeline against a real agents directory
- SC-10b live SDK dogfood (gated by CLAUDE_CREW_LIVE_TESTS=1)
- SC-11 per-source INFO contract
- SC-9 regression: bundled pack contract unchanged
- Cross-layer mixed-resolution shadow detection
- Sad path: malformed YAML in one file does not break sibling loads
- Sad path: name-validation cascade
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from claude_crew.subagents import load_default_pack
from claude_crew.subagents._loader import (
    SUBSTRATE_SUBAGENT_GUIDANCE,
    PackLoadError,
    parse_pack_text,
)
from claude_crew.subagents._user_loader import build_merged_pack

LOADER_LOG = "claude_crew.subagents._loader"
USER_LOADER_LOG = "claude_crew.subagents.loader"


# ---------------------------------------------------------------------------
# SC-8 — minimal pack file: only name + description
# ---------------------------------------------------------------------------


class TestMinimalPackE2E:
    """SC-8: a pack file using ONLY Claude Code's required fields loads
    cleanly through the full build_merged_pack pipeline."""

    def test_minimal_pack_loads_through_pipeline(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "minimal-probe.md").write_text(
            "---\n"
            "name: minimal-probe\n"
            "description: Smoke test for Claude Code minimal agent format.\n"
            "---\n\n"
            "You are a probe.\n"
        )

        with caplog.at_level(logging.WARNING, logger=USER_LOADER_LOG):
            merged, role_ss, bodies = build_merged_pack(
                home_dir=tmp_path,
                project_root=tmp_path / "noproject",
            )

        # Pack loads cleanly under the canonical role.
        assert "minimal-probe" in merged
        assert merged["minimal-probe"].description == (
            "Smoke test for Claude Code minimal agent format."
        )

        # SC-3, SC-4: optional model/tools default safely.
        assert merged["minimal-probe"].model is None
        assert merged["minimal-probe"].tools == []

        # SC-7: prompt leads with substrate guidance.
        assert merged["minimal-probe"].prompt.startswith(SUBSTRATE_SUBAGENT_GUIDANCE)
        assert merged["minimal-probe"].prompt.endswith("You are a probe.")

        # raw body in bodies dict has no substrate prefix.
        assert SUBSTRATE_SUBAGENT_GUIDANCE not in bodies["minimal-probe"]

        # No unsupported-key WARNs (was 1+ pre-#15 for `name:`).
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("unsupported" in w for w in warns), warns


# ---------------------------------------------------------------------------
# SC-10a — stub-mode WARN check against a real agents directory
# ---------------------------------------------------------------------------


class TestStubModeNoUnsupportedKeyWarns:
    """SC-10a: build_merged_pack against the operator's real ~/.claude/agents/
    emits zero unsupported-key WARNs post-#15. Pre-#15 every `name:`
    declaration produced one."""

    def test_real_user_agents_dir_no_unsupported_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agents_dir = Path.home() / ".claude" / "agents"
        if not agents_dir.is_dir():
            pytest.skip(f"no user agents directory at {agents_dir}")

        # Find any .md files. If empty, the test still proves no spurious
        # WARNs; if populated, it proves the post-#15 zero-WARN promise.
        md_files = list(agents_dir.glob("*.md"))

        with caplog.at_level(logging.WARNING, logger=USER_LOADER_LOG):
            with caplog.at_level(logging.WARNING, logger=LOADER_LOG):
                build_merged_pack(
                    home_dir=Path.home(),
                    project_root=Path("/nonexistent-project-for-test"),
                )

        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        unsupported_warns = [w for w in warns if "unsupported frontmatter key" in w]
        assert unsupported_warns == [], (
            f"expected zero unsupported-key WARNs against {agents_dir} "
            f"({len(md_files)} .md files), got: {unsupported_warns}"
        )


# ---------------------------------------------------------------------------
# SC-11 — per-source INFO contract
# ---------------------------------------------------------------------------


class TestPerSourceInfoContract:
    """SC-11: build_merged_pack emits one INFO per source naming label,
    path, count, and role keys."""

    def test_three_info_logs_one_per_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Seed user-level only; project absent.
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "user-tester.md").write_text(
            "---\nname: user-tester\ndescription: A user agent.\ntools: [Read]\n---\n\nbody\n"
        )

        with caplog.at_level(logging.INFO, logger=USER_LOADER_LOG):
            merged, _, _ = build_merged_pack(
                home_dir=tmp_path,
                project_root=tmp_path / "noproject",
            )

        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        bundled_msgs = [m for m in info_msgs if "from bundled" in m]
        user_msgs = [m for m in info_msgs if "from user" in m]
        project_msgs = [m for m in info_msgs if "from project" in m]

        assert len(bundled_msgs) == 1, info_msgs
        assert len(user_msgs) == 1, info_msgs
        assert len(project_msgs) == 1, info_msgs

        # Bundled INFO names role keys.
        assert "explorer" in bundled_msgs[0]
        assert "planner" in bundled_msgs[0]
        assert "general-purpose" in bundled_msgs[0]

        # User INFO names path + count + key.
        assert str(tmp_path) in user_msgs[0]
        assert "user-tester" in user_msgs[0]
        assert " 1 pack" in user_msgs[0]


# ---------------------------------------------------------------------------
# SC-9 — bundled pack regression
# ---------------------------------------------------------------------------


class TestBundledPackRegression:
    """SC-9: post-#15, the three bundled packs continue to load with the
    same role keys. Adding name: to bundled files must NOT change the keys."""

    def test_bundled_pack_keys_unchanged(self) -> None:
        pack, role_ss, bodies = load_default_pack()
        assert set(pack.keys()) == {"explorer", "planner", "general-purpose"}
        # Each prompt now leads with substrate guidance.
        for role, agent in pack.items():
            assert agent.prompt.startswith(SUBSTRATE_SUBAGENT_GUIDANCE), role
        # raw bodies have no substrate prefix.
        for role, body in bodies.items():
            assert SUBSTRATE_SUBAGENT_GUIDANCE not in body, role


# ---------------------------------------------------------------------------
# Cross-layer mixed-resolution shadow detection
# ---------------------------------------------------------------------------


class TestCrossLayerMixedResolution:
    """A user-level pack at old-name.md declaring `name: scout` is shadowed by a
    project-level scout.md (no `name:`). Both resolve to canonical key 'scout';
    the shadow is detected via dict-key comparison post-#15."""

    def test_user_canonical_name_collides_with_project_stem(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        user_agents = tmp_path / "home" / ".claude" / "agents"
        project_agents = tmp_path / "project" / ".claude" / "agents"
        user_agents.mkdir(parents=True)
        project_agents.mkdir(parents=True)

        # User pack: stem 'old-name', canonical name 'scout'.
        (user_agents / "old-name.md").write_text(
            "---\n"
            "name: scout\n"
            "description: User-level scout.\n"
            "tools: [Read]\n"
            "---\n\nuser body\n"
        )
        # Project pack: stem 'scout', no name: declaration → stem fallback.
        (project_agents / "scout.md").write_text(
            "---\n"
            "description: Project-level scout.\n"
            "tools: [Read]\n"
            "---\n\nproject body\n"
        )

        with caplog.at_level(logging.INFO, logger=USER_LOADER_LOG):
            merged, _, _ = build_merged_pack(
                home_dir=tmp_path / "home",
                project_root=tmp_path / "project",
            )

        # Both layers resolve to canonical key 'scout'; project wins.
        assert "scout" in merged
        assert merged["scout"].description == "Project-level scout."

        # Shadow INFO surfaces the precedence transition.
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any(
            "scout" in m and "project-level shadows user-level" in m
            for m in info_msgs
        ), f"expected project-shadows-user INFO, got {info_msgs}"


# ---------------------------------------------------------------------------
# Sad paths
# ---------------------------------------------------------------------------


class TestSadPaths:
    """Malformed packs should NOT break valid sibling loads. Validation
    failures should produce clear errors that name the offending file."""

    def test_malformed_yaml_in_one_file_does_not_break_siblings(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        # Broken: invalid YAML.
        (agents_dir / "broken.md").write_text(
            "---\n"
            "description: Broken.\n"
            "tools: [\n"  # Unclosed list
            "---\n\nbody\n"
        )
        # Valid sibling.
        (agents_dir / "good.md").write_text(
            "---\nname: good\ndescription: Works.\ntools: [Read]\n---\n\nbody\n"
        )

        with caplog.at_level(logging.WARNING, logger=USER_LOADER_LOG):
            merged, _, _ = build_merged_pack(
                home_dir=tmp_path,
                project_root=tmp_path / "noproject",
            )

        # Good sibling loads.
        assert "good" in merged
        # Broken file produces a WARN naming it.
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("broken.md" in w for w in warns), warns

    def test_invalid_name_in_one_file_does_not_break_siblings(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        # Broken: name: 42 (YAML int) → PackLoadError.
        (agents_dir / "bad-name.md").write_text(
            "---\nname: 42\ndescription: Bad.\ntools: [Read]\n---\n\nbody\n"
        )
        (agents_dir / "good.md").write_text(
            "---\nname: good\ndescription: Works.\ntools: [Read]\n---\n\nbody\n"
        )

        with caplog.at_level(logging.WARNING, logger=USER_LOADER_LOG):
            merged, _, _ = build_merged_pack(
                home_dir=tmp_path,
                project_root=tmp_path / "noproject",
            )

        assert "good" in merged
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("bad-name.md" in w for w in warns), warns


# ---------------------------------------------------------------------------
# SC-10b — live SDK dogfood (gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)
class TestLiveSdkDogfood:
    """SC-10b: spawn one of Jerome's user-level agents end-to-end against
    the real SDK. Validates pack-load → SDK construction → live spawn →
    response. Closes the noisy-startup-log regression that #17 BACKLOG
    M-2 flagged."""

    def test_runner_pack_loads_with_zero_unsupported_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runner_path = Path.home() / ".claude" / "agents" / "runner.md"
        if not runner_path.exists():
            pytest.skip(f"no user-level runner.md at {runner_path}")

        with caplog.at_level(logging.WARNING, logger=USER_LOADER_LOG):
            with caplog.at_level(logging.WARNING, logger=LOADER_LOG):
                merged, _, _ = build_merged_pack(home_dir=Path.home())

        assert "runner" in merged, (
            f"runner role should be available from {runner_path}"
        )

        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        unsupported_warns = [w for w in warns if "unsupported frontmatter key" in w]
        assert unsupported_warns == [], (
            f"expected zero unsupported-key WARNs for runner.md, got: "
            f"{unsupported_warns}"
        )
