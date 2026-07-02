"""AT 20 — doc staleness guard for M3.5 reshape-crew feature.

Greps doc/ARCHITECTURE.md to assert:
  1. The NAMED LITERAL `_has_out_edges` does NOT appear anywhere in the file
     (deletion-detector: re-introducing the spawn-time gate writes the literal
     back and fails this test).
  2. The NAMED LITERAL `reshape_crew` IS present in the file
     (presence-detector: removing the tool from documentation fails this test).

Also greps CLAUDE.md to assert:
  3. The NAMED LITERAL `_has_out_edges` does NOT appear anywhere in the file
     (same deletion-detector: CLAUDE.md must not carry stale _has_out_edges prose).
"""

import re
from pathlib import Path

ARCH_DOC = Path(__file__).parent.parent / "doc" / "ARCHITECTURE.md"
CLAUDE_MD = Path(__file__).parent.parent / "CLAUDE.md"


def _read_arch_doc() -> str:
    return ARCH_DOC.read_text(encoding="utf-8")


def _read_claude_md() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def test_has_out_edges_not_in_architecture_md() -> None:
    """_has_out_edges must not appear in doc/ARCHITECTURE.md.

    The D0 contract flip (M3.5) removes the spawn-time neighbor-list gate and
    wires send_to unconditionally for every SdkTeammate. Any surviving prose
    referencing the old _has_out_edges guard is stale and misleading.
    """
    content = _read_arch_doc()
    assert "_has_out_edges" not in content, (
        "doc/ARCHITECTURE.md contains the literal '_has_out_edges' — "
        "this prose predates the D0 contract flip (M3.5) and must be removed. "
        "send_to is now wired unconditionally; authorize_send is the security boundary."
    )


def test_has_out_edges_not_in_claude_md() -> None:
    """_has_out_edges must not appear in CLAUDE.md.

    The D0 contract flip (M3.5) removes the spawn-time neighbor-list gate and
    wires send_to unconditionally for every SdkTeammate. Any surviving prose
    referencing the old _has_out_edges guard in CLAUDE.md is stale and misleading.
    """
    content = _read_claude_md()
    assert "_has_out_edges" not in content, (
        "CLAUDE.md contains the literal '_has_out_edges' — "
        "this prose predates the D0 contract flip (M3.5) and must be removed. "
        "send_to is now wired unconditionally; authorize_send is the security boundary."
    )


def test_reshape_crew_documented_in_architecture_md() -> None:
    """reshape_crew must appear in doc/ARCHITECTURE.md.

    The M3.5 feature adds the reshape_crew MCP tool as the 18th tool in
    server.py. Documentation must reflect this.
    """
    content = _read_arch_doc()
    assert "reshape_crew" in content, (
        "doc/ARCHITECTURE.md does not mention 'reshape_crew' — "
        "the M3.5 live-crew-reshape tool (18th MCP tool) is undocumented. "
        "Add it to the server.py tool table and the M3.5 section."
    )
