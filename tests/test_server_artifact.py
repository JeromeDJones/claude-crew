"""Integration-level tests for the surface_document MCP tool.

Wires a client to the server in-process via the mcp SDK memory harness.
Covers: happy path, and all file-access sad paths (not-found, not-regular,
permission-denied, ArtifactTooLarge, ArtifactNotText).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.artifact_registry import ArtifactRegistry, _MAX_BODY_BYTES
from claude_crew.broker import Broker
from claude_crew.server import make_server


def _content_text(result: Any) -> str:
    assert result.content, f"empty content: {result}"
    return result.content[0].text


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(_content_text(result))


def _client(reg: ArtifactRegistry | None = None, broker: Broker | None = None):
    b = broker or Broker()
    return create_connected_server_and_client_session(
        make_server(broker=b, artifact_registry=reg)
    )


@pytest.mark.anyio
class TestSurfaceDocumentHappyPath:
    async def test_returns_artifact_id_and_crew_id(self, tmp_path: Path) -> None:
        doc = tmp_path / "spec.md"
        doc.write_text("# My Spec\nSome content.")
        broker = Broker()
        reg = ArtifactRegistry(crew_id=broker.crew_id)
        async with _client(reg=reg, broker=broker) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": str(doc), "title": "My Spec"}
            )
        data = _content_json(result)
        assert "artifact_id" in data
        assert len(data["artifact_id"]) == 32
        assert data["crew_id"] == broker.crew_id

    async def test_stored_in_registry_with_snapshot_body(self, tmp_path: Path) -> None:
        doc = tmp_path / "spec.md"
        doc.write_text("# Original content")
        broker = Broker()
        reg = ArtifactRegistry(crew_id=broker.crew_id)
        async with _client(reg=reg, broker=broker) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": str(doc), "title": "Spec"}
            )
        data = _content_json(result)
        rec = reg.get(data["artifact_id"])
        assert rec is not None
        assert rec.body == "# Original content"
        assert rec.title == "Spec"

    async def test_path_stored_as_label_only(self, tmp_path: Path) -> None:
        doc = tmp_path / "plan.md"
        doc.write_text("content")
        broker = Broker()
        reg = ArtifactRegistry(crew_id=broker.crew_id)
        async with _client(reg=reg, broker=broker) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": str(doc), "title": "Plan"}
            )
        data = _content_json(result)
        rec = reg.get(data["artifact_id"])
        assert rec is not None
        assert str(doc) not in data["artifact_id"]

    async def test_relative_path_resolved_against_cwd(self, tmp_path: Path) -> None:
        doc = tmp_path / "readme.md"
        doc.write_text("# Readme")
        broker = Broker()
        reg = ArtifactRegistry(crew_id=broker.crew_id)
        with patch("claude_crew.server.Path.cwd", return_value=tmp_path):
            async with _client(reg=reg, broker=broker) as s:
                await s.initialize()
                result = await s.call_tool(
                    "surface_document", {"path": "readme.md", "title": "Readme"}
                )
        data = _content_json(result)
        assert "artifact_id" in data

    async def test_snapshot_immutable_after_file_changes(self, tmp_path: Path) -> None:
        doc = tmp_path / "spec.md"
        doc.write_text("Version 1")
        broker = Broker()
        reg = ArtifactRegistry(crew_id=broker.crew_id)
        async with _client(reg=reg, broker=broker) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": str(doc), "title": "Spec"}
            )
        data = _content_json(result)
        doc.write_text("Version 2 — edited after surface")
        rec = reg.get(data["artifact_id"])
        assert rec is not None
        assert rec.body == "Version 1"


@pytest.mark.anyio
class TestSurfaceDocumentNoRegistry:
    async def test_error_when_no_registry_configured(self, tmp_path: Path) -> None:
        doc = tmp_path / "spec.md"
        doc.write_text("content")
        async with _client(reg=None) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": str(doc), "title": "T"}
            )
        assert result.isError
        text = _content_text(result)
        assert "not available" in text.lower()


@pytest.mark.anyio
class TestSurfaceDocumentFileAccessSadPaths:
    async def _call(self, path: str, reg: ArtifactRegistry):
        async with _client(reg=reg) as s:
            await s.initialize()
            result = await s.call_tool(
                "surface_document", {"path": path, "title": "T"}
            )
        return result, _content_text(result)

    async def test_path_not_found(self, tmp_path: Path) -> None:
        reg = ArtifactRegistry(crew_id="c1")
        path = str(tmp_path / "nonexistent.md")
        result, text = await self._call(path, reg)
        assert result.isError
        assert "not found" in text.lower()

    async def test_directory_not_regular_file(self, tmp_path: Path) -> None:
        reg = ArtifactRegistry(crew_id="c1")
        result, text = await self._call(str(tmp_path), reg)
        assert result.isError
        assert "directory" in text.lower()

    async def test_symlink_to_dir_not_regular_file(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        dirlink = tmp_path / "dirlink"
        dirlink.symlink_to(sub)
        reg = ArtifactRegistry(crew_id="c1")
        result, text = await self._call(str(dirlink), reg)
        assert result.isError

    async def test_artifact_too_large(self, tmp_path: Path) -> None:
        doc = tmp_path / "big.md"
        doc.write_bytes(b"x" * (_MAX_BODY_BYTES + 1))
        reg = ArtifactRegistry(crew_id="c1")
        result, text = await self._call(str(doc), reg)
        assert result.isError
        assert "limit" in text.lower() or "large" in text.lower()

    async def test_artifact_not_text(self, tmp_path: Path) -> None:
        doc = tmp_path / "binary.bin"
        doc.write_bytes(b"\xff\xfe binary garbage \x00\x01\x02")
        reg = ArtifactRegistry(crew_id="c1")
        result, text = await self._call(str(doc), reg)
        assert result.isError
        assert "utf-8" in text.lower() or "text" in text.lower()

    async def test_permission_denied(self, tmp_path: Path) -> None:
        doc = tmp_path / "protected.md"
        doc.write_text("secret")
        doc.chmod(0o000)
        try:
            reg = ArtifactRegistry(crew_id="c1")
            result, text = await self._call(str(doc), reg)
            assert result.isError
            assert "permission" in text.lower()
        finally:
            doc.chmod(0o644)
