"""Tests for the projectPath prefix-match + scope-miss diagnostic.

Covers AT1–AT8 of spec ``plugin-projectpath-prefix-match``:
loosen the strict-equality projectPath filter to prefix containment
via ``is_relative_to`` and surface rejections as a ``plugin_scope_miss``
WARN that flows through the startup-diagnostics channel.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable

import pytest

from claude_crew.diagnostics import collect_startup_diagnostics
from claude_crew.subagents._user_loader import (
    _read_installed_plugins,
    build_merged_pack,
)


LOGGER_NAME = "claude_crew.subagents.loader"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(home: Path, plugins: dict) -> Path:
    cfg_dir = home / ".claude" / "plugins"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "installed_plugins.json"
    cfg_path.write_text(json.dumps({"version": 2, "plugins": plugins}))
    return cfg_path


def _write_agent(agents_dir: Path, filename: str = "rr-planner.md") -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / filename
    path.write_text(
        "---\n"
        "description: Test agent.\n"
        "model: haiku\n"
        "tools: [Read]\n"
        "---\n"
        "\n"
        "Body.\n"
    )
    return path


def _install_dir(home: Path, plugin_dir_name: str = "repo-reactor") -> Path:
    """Plugin install path under ~/.claude/plugins/ to pass H1 escape guard."""
    return home / ".claude" / "plugins" / "cache" / plugin_dir_name


def _scope_miss_records(records: Iterable[logging.LogRecord]) -> list[logging.LogRecord]:
    return [
        r
        for r in records
        if r.name == LOGGER_NAME
        and "project-scope plugin" in r.getMessage()
    ]


# ---------------------------------------------------------------------------
# AT1 — exact match still loads (regression)
# ---------------------------------------------------------------------------


def test_at1_exact_match_still_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    install = _install_dir(home, "repo-reactor")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "repo-reactor@repo-reactor": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(project),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=project)

    assert any(key == "repo-reactor@repo-reactor" for key, _ in pairs)
    assert _scope_miss_records(caplog.records) == []


# ---------------------------------------------------------------------------
# AT2 — subdirectory loads (new behavior)
# ---------------------------------------------------------------------------


def test_at2_subdirectory_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    home = tmp_path / "home"
    project_root_dir = tmp_path / "proj"
    nested = project_root_dir / "sub" / "nested"
    nested.mkdir(parents=True)
    home.mkdir()
    install = _install_dir(home, "repo-reactor")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "repo-reactor@repo-reactor": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(project_root_dir),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=nested)

    assert any(key == "repo-reactor@repo-reactor" for key, _ in pairs)
    assert _scope_miss_records(caplog.records) == []


# ---------------------------------------------------------------------------
# AT3 — out-of-tree cwd produces categorized diagnostic
# ---------------------------------------------------------------------------


def test_at3_out_of_tree_cwd_emits_plugin_scope_miss(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project_a = tmp_path / "projA"
    project_b = tmp_path / "projB"
    home.mkdir()
    project_a.mkdir()
    project_b.mkdir()
    install = _install_dir(home, "repo-reactor")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "repo-reactor@repo-reactor": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(project_a),
                }
            ]
        },
    )

    with collect_startup_diagnostics() as collector:
        merged, _, _ = build_merged_pack(home_dir=home, project_root=project_b)
    diagnostics = collector.freeze()

    assert "repo-reactor:rr-planner" not in merged

    misses = [d for d in diagnostics if d.category == "plugin_scope_miss"]
    assert len(misses) == 1, (
        f"expected exactly one plugin_scope_miss diag, got {misses!r}"
    )
    miss = misses[0]
    assert miss.level == "WARNING"
    # message contains plugin key, resolved projectPath, resolved cwd
    assert "repo-reactor@repo-reactor" in miss.message
    assert str(project_a.resolve()) in miss.message
    assert str(project_b.resolve()) in miss.message


# ---------------------------------------------------------------------------
# AT4 — sibling cwd rejected (string-prefix bug regression guard)
# ---------------------------------------------------------------------------


def test_at4_sibling_cwd_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    sibling = tmp_path / "proj-other"
    home.mkdir()
    proj.mkdir()
    sibling.mkdir()
    install = _install_dir(home, "p")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "p@p": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(proj),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=sibling)

    assert pairs == []
    misses = _scope_miss_records(caplog.records)
    assert len(misses) == 1


# ---------------------------------------------------------------------------
# AT5 — symlinked cwd under projectPath loads
# ---------------------------------------------------------------------------


def test_at5_symlinked_cwd_under_projectpath_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    home = tmp_path / "home"
    realproj = tmp_path / "realproj"
    sub = realproj / "sub"
    sub.mkdir(parents=True)
    home.mkdir()
    link = tmp_path / "linkproj"
    try:
        os.symlink(sub, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlink on this platform")

    install = _install_dir(home, "p")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "p@p": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(realproj),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=link)

    assert any(key == "p@p" for key, _ in pairs)
    assert _scope_miss_records(caplog.records) == []


# ---------------------------------------------------------------------------
# AT6 — parent cwd rejected
# ---------------------------------------------------------------------------


def test_at6_parent_cwd_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    home = tmp_path / "home"
    parent = tmp_path / "proj"
    inner = parent / "inner"
    inner.mkdir(parents=True)
    home.mkdir()
    install = _install_dir(home, "p")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "p@p": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(inner),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=parent)

    assert pairs == []
    assert len(_scope_miss_records(caplog.records)) == 1


# ---------------------------------------------------------------------------
# AT7 — scope: "user" installs unaffected
# ---------------------------------------------------------------------------


def test_at7_user_scope_unaffected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "anywhere"
    project.mkdir()
    home.mkdir()
    install = _install_dir(home, "p")
    _write_agent(install / "agents")
    _write_manifest(
        home,
        {
            "p@p": [
                {
                    "scope": "user",
                    "installPath": str(install),
                }
            ]
        },
    )

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pairs = _read_installed_plugins(home_dir=home, project_root=project)

    assert any(key == "p@p" for key, _ in pairs)
    assert _scope_miss_records(caplog.records) == []


# ---------------------------------------------------------------------------
# AT8 — installPath escape guard still fires; not classified as plugin_scope_miss
# ---------------------------------------------------------------------------


def test_at8_install_path_escape_guard_still_fires(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    # installPath OUTSIDE ~/.claude/plugins/ — escape guard must reject
    escape_install = tmp_path / "evil"
    _write_agent(escape_install / "agents")
    _write_manifest(
        home,
        {
            "p@p": [
                {
                    "scope": "local",
                    "installPath": str(escape_install),
                    # projectPath matches cwd — would otherwise pass the new
                    # prefix-containment check, so a regression that drops
                    # the H1 guard would surface here.
                    "projectPath": str(project),
                }
            ]
        },
    )

    with collect_startup_diagnostics() as collector:
        pairs = _read_installed_plugins(home_dir=home, project_root=project)
    diagnostics = collector.freeze()

    assert pairs == []
    # The H1 message says "installPath" — must classify as "plugin", NOT
    # plugin_scope_miss.
    install_path_diags = [d for d in diagnostics if "installPath" in d.message]
    assert install_path_diags, "expected installPath escape WARN"
    for d in install_path_diags:
        assert d.category == "plugin", (
            f"installPath escape must remain in 'plugin' bucket, got {d.category!r}"
        )
    assert [d for d in diagnostics if d.category == "plugin_scope_miss"] == []
