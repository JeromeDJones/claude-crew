"""Non-gated fidelity audit: frontmatter normalization.

Runs under default CI (no CLAUDE_CREW_LIVE_TESTS gate needed) because all
assertions run entirely in-process against the loader — no SDK subprocess,
no API spend.

AT9: Given CLAUDE_CREW_LIVE_TESTS unset, when TestFrontmatterNormalization
runs, then:
  - ``test_unix_lf`` passes: ``build_merged_pack`` returns the agent with a
    ``\\r``-free prompt body.
  - ``test_windows_crlf`` is marked xfail(strict=True) and the suite reports
    it as expected-fail.  The xfail flips to xpass (forced failure) the moment
    the underlying CRLF fix lands — that is the intended signal to unxfail in
    the same commit.

Implementation note on the CRLF test path:
    Python's ``Path.read_text()`` (used by ``parse_pack_file``) opens in
    text mode with ``newline=None`` (universal newlines), which silently
    translates ``\\r\\n`` → ``\\n`` on all platforms before the text reaches
    ``_split_frontmatter``.  The documented CRLF limitation therefore does
    NOT manifest through the ``parse_pack_file`` / ``build_merged_pack`` file-
    read path; it manifests when text is passed **directly** to
    ``parse_pack_text`` with ``\\r\\n`` intact (e.g., text retrieved over a
    network, embedded in a test fixture, or constructed programmatically).
    ``test_windows_crlf`` uses ``parse_pack_text`` directly to probe the
    actual bug site rather than the normalising file-read path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_crew.subagents._loader import PackLoadError, parse_pack_text
from claude_crew.subagents._user_loader import build_merged_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_AGENT_BODY = "You are a fidelity probe. Reply with FIDELITY_PROBE_OK."

_FRONTMATTER_LINES = [
    "description: Frontmatter normalization fidelity probe.",
    "model: haiku",
    "tools: [Read]",
]


def _build_agent_text(line_ending: str) -> str:
    """Compose a minimal valid pack file with the given line ending.

    Uses ``str.join`` so every separator — including between frontmatter
    delimiters and body — is exactly ``line_ending``.  No Python implicit
    newline normalisation occurs here.
    """
    lines = ["---", *_FRONTMATTER_LINES, "---", "", _MINIMAL_AGENT_BODY, ""]
    return line_ending.join(lines)


def _write_agent_file(agents_dir: Path, filename: str, text: str) -> Path:
    """Write raw UTF-8 bytes so the chosen line ending is preserved exactly."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / filename
    path.write_bytes(text.encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# TestFrontmatterNormalization
# ---------------------------------------------------------------------------


class TestFrontmatterNormalization:
    """AT9 — frontmatter normalization: Unix LF passes; Windows CRLF is xfail.

    ``test_unix_lf``:
        Writes a synthesized LF agent file into ``tmp_path/.claude/agents/``
        and calls ``build_merged_pack(home_dir=tmp_path)`` — the full
        loader pipeline.  Asserts the agent appears in the pack and the
        prompt body contains no ``\\r`` characters.

    ``test_windows_crlf``:
        Calls ``parse_pack_text`` directly with CRLF-terminated text.
        ``_split_frontmatter`` hard-codes ``"---\\n"`` as the opening
        delimiter; a ``\\r\\n``-terminated input starts with ``"---\\r\\n"``
        which does not match, so ``PackLoadError`` is raised and the test
        fails as expected.  The xfail flips to xpass (→ forced failure) when
        the underlying fix lands, prompting an unxfail in the same commit.

        Note: going through ``build_merged_pack`` / ``parse_pack_file`` would
        NOT expose the bug because Python's ``read_text()`` normalises CRLF.
        ``parse_pack_text`` is used here to probe the documented bug site.
    """

    def test_unix_lf(self, tmp_path: Path) -> None:
        """Unix LF (\\n) frontmatter loads cleanly via build_merged_pack; prompt body is \\r-free."""
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent_file(agents_dir, "fidelity-probe.md", _build_agent_text("\n"))

        pack, _role_ss, _bodies = build_merged_pack(
            home_dir=tmp_path,
            project_root=tmp_path / "nonexistent-project",
        )

        assert "fidelity-probe" in pack, (
            f"expected 'fidelity-probe' in pack; got keys: {sorted(pack)}"
        )
        agent = pack["fidelity-probe"]
        assert "\r" not in agent.prompt, (
            "Unix LF agent prompt must contain no \\r characters"
        )

    @pytest.mark.xfail(
        strict=True,
        raises=PackLoadError,
        reason=(
            "Windows CRLF frontmatter — _split_frontmatter hard-codes '---\\n' "
            "as delimiter; CRLF text passed directly raises PackLoadError. "
            "BACKLOG: frontmatter normalization fix slice. "
            "Note: this bug is NOT exposed through build_merged_pack/parse_pack_file "
            "because Python read_text() normalises \\r\\n → \\n before the loader "
            "sees the text."
        ),
    )
    def test_windows_crlf(self, tmp_path: Path) -> None:
        """Windows CRLF (\\r\\n) frontmatter passed directly raises PackLoadError.

        This test is expected to FAIL (xfail) because ``_split_frontmatter``
        hard-codes ``"---\\n"`` as the opening delimiter.  A string constructed
        with CRLF line endings starts with ``"---\\r\\n"``, which does not match,
        so ``PackLoadError`` is raised.

        When this test unexpectedly passes (xpass), ``strict=True`` turns that
        into a suite failure — the signal to unxfail this test in the same
        commit as the fix.

        If you see this test flip to xpass on your machine, it means the
        underlying fix has landed.  Update this test (remove the xfail mark)
        in the same commit that introduces the fix.
        """
        crlf_text = _build_agent_text("\r\n")
        path = tmp_path / "fidelity-probe-crlf.md"

        # parse_pack_text is called directly so that the CRLF characters
        # reach _split_frontmatter intact (bypassing read_text() normalisation).
        _, agent, _, _ = parse_pack_text(crlf_text, path)

        # If we somehow get here (bug fixed), assert no \r in the prompt.
        assert "\r" not in agent.prompt, (
            "Windows CRLF agent prompt must contain no \\r characters after loading"
        )
