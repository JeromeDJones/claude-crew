"""Tests for ArtifactRegistry — unit layer.

Covers: store/retrieve happy path, size cap rejection, count cap eviction
(evicted ids resolve to None), UTF-8 rejection, opaque id uniqueness, path
label never used as fetch key.
"""

from __future__ import annotations

import pytest

from claude_crew.artifact_registry import (
    ArtifactNotText,
    ArtifactRegistry,
    ArtifactTooLarge,
    _MAX_ARTIFACTS,
    MAX_BODY_BYTES,
)


def _reg(crew_id: str = "crew-test") -> ArtifactRegistry:
    return ArtifactRegistry(crew_id=crew_id)


def _store(reg: ArtifactRegistry, *, title: str = "T", path: str = "/p", body: str = "# hello") -> str:
    return reg.store(
        path_label=path,
        title=title,
        surfacing_teammate="planner",
        body_bytes=body.encode(),
    )


class TestStoreAndRetrieve:
    def test_happy_path_returns_artifact_id(self):
        reg = _reg()
        aid = _store(reg)
        assert isinstance(aid, str) and len(aid) == 32

    def test_retrieve_stored_record(self):
        reg = _reg()
        aid = _store(reg, title="My Doc", path="/docs/spec.md", body="# Spec\ncontent")
        rec = reg.get(aid)
        assert rec is not None
        assert rec.artifact_id == aid
        assert rec.title == "My Doc"
        assert rec.path_label == "/docs/spec.md"
        assert rec.body == "# Spec\ncontent"
        assert rec.crew_id == "crew-test"
        assert rec.surfacing_teammate == "planner"
        assert rec.timestamp_utc > 0

    def test_get_unknown_id_returns_none(self):
        reg = _reg()
        assert reg.get("doesnotexist") is None

    def test_get_returns_none_not_raises(self):
        reg = _reg()
        result = reg.get("x" * 32)
        assert result is None


class TestOpaqueness:
    def test_ids_are_not_derived_from_path(self):
        reg = _reg()
        aid = _store(reg, path="/secret/path.md")
        rec = reg.get(aid)
        assert rec is not None
        assert "/secret/path.md" not in aid
        assert "secret" not in aid

    def test_ids_are_unique_per_call(self):
        reg = _reg()
        ids = {_store(reg, body=f"body {i}") for i in range(10)}
        assert len(ids) == 10

    def test_path_label_is_display_only(self):
        reg = _reg()
        aid = _store(reg, path="/foo/bar.md")
        rec = reg.get(aid)
        assert rec is not None
        assert rec.path_label == "/foo/bar.md"
        assert reg.get("/foo/bar.md") is None


class TestSizeCap:
    def test_exact_limit_accepted(self):
        reg = _reg()
        body = b"x" * MAX_BODY_BYTES
        aid = reg.store(
            path_label="/p",
            title="T",
            surfacing_teammate="planner",
            body_bytes=body,
        )
        assert reg.get(aid) is not None

    def test_one_byte_over_rejected(self):
        reg = _reg()
        body = b"x" * (MAX_BODY_BYTES + 1)
        with pytest.raises(ArtifactTooLarge, match="limit is"):
            reg.store(
                path_label="/p",
                title="T",
                surfacing_teammate="planner",
                body_bytes=body,
            )

    def test_too_large_does_not_mutate_registry(self):
        reg = _reg()
        _store(reg, title="Before")
        with pytest.raises(ArtifactTooLarge):
            reg.store(
                path_label="/p",
                title="Big",
                surfacing_teammate="planner",
                body_bytes=b"x" * (MAX_BODY_BYTES + 1),
            )
        assert len(reg._records) == 1


class TestCountCapEviction:
    def test_eviction_at_cap(self):
        reg = _reg()
        first_id = _store(reg, title="First", body="first")
        for i in range(_MAX_ARTIFACTS - 1):
            _store(reg, title=f"Doc {i}", body=f"body {i}")
        # Registry is now full; one more push evicts oldest
        _store(reg, title="Overflow", body="overflow")
        assert len(reg._records) == _MAX_ARTIFACTS
        assert reg.get(first_id) is None

    def test_evicted_id_returns_none(self):
        # An evicted id is indistinguishable from one that never existed:
        # get() returns None for both, which drives a clean 404 at the endpoint.
        reg = _reg()
        first_id = _store(reg, title="First", body="first")
        for i in range(_MAX_ARTIFACTS):
            _store(reg, body=f"body {i}")
        assert reg.get(first_id) is None

    def test_non_evicted_id_still_resolves(self):
        reg = _reg()
        aid = _store(reg)
        assert reg.get(aid) is not None

    def test_registry_length_never_exceeds_cap(self):
        reg = _reg()
        for i in range(_MAX_ARTIFACTS + 20):
            _store(reg, body=f"body {i}")
        assert len(reg._records) == _MAX_ARTIFACTS

    def test_eviction_order_is_oldest_first(self):
        reg = _reg()
        ids = [_store(reg, body=f"b{i}") for i in range(_MAX_ARTIFACTS)]
        overflow_id = _store(reg, body="overflow")
        assert reg.get(ids[0]) is None         # oldest evicted
        assert reg.get(ids[1]) is not None     # second-oldest still present
        assert reg.get(overflow_id) is not None


class TestUtf8Validation:
    def test_valid_utf8_accepted(self):
        reg = _reg()
        aid = reg.store(
            path_label="/p",
            title="T",
            surfacing_teammate="planner",
            body_bytes="# Héllo Wörld\n".encode("utf-8"),
        )
        rec = reg.get(aid)
        assert rec is not None
        assert "Héllo" in rec.body

    def test_invalid_utf8_raises(self):
        reg = _reg()
        with pytest.raises(ArtifactNotText, match="UTF-8"):
            reg.store(
                path_label="/p",
                title="T",
                surfacing_teammate="planner",
                body_bytes=b"\xff\xfe binary garbage",
            )

    def test_invalid_utf8_does_not_mutate_registry(self):
        reg = _reg()
        _store(reg, title="Before")
        with pytest.raises(ArtifactNotText):
            reg.store(
                path_label="/p",
                title="Bad",
                surfacing_teammate="planner",
                body_bytes=b"\xff\xfe",
            )
        assert len(reg._records) == 1


class TestMetadataList:
    def test_metadata_newest_first(self):
        reg = _reg()
        a = _store(reg, title="First")
        b = _store(reg, title="Second")
        c = _store(reg, title="Third")
        meta = reg.metadata_list()
        assert [m["artifact_id"] for m in meta] == [c, b, a]

    def test_metadata_has_no_body_field(self):
        reg = _reg()
        _store(reg)
        meta = reg.metadata_list()
        assert len(meta) == 1
        assert "body" not in meta[0]

    def test_metadata_has_crew_id(self):
        reg = _reg(crew_id="crew-xyz")
        _store(reg)
        meta = reg.metadata_list()
        assert meta[0]["crew_id"] == "crew-xyz"

    def test_empty_registry_returns_empty_list(self):
        reg = _reg()
        assert reg.metadata_list() == []
