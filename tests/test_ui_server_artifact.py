"""Tests for the /artifact route and artifacts[] field in /api/state.

Covers: 200 happy path, 404 unknown id, 404 evicted tombstone, 400 bad params,
proxy to owning follower instance (multi-instance), artifacts[] in state metadata
(no body in state), and the no-registry case.
"""

from __future__ import annotations

import httpx
import pytest

from claude_crew.artifact_registry import ArtifactRegistry, _MAX_ARTIFACTS
from claude_crew.broker import Broker
from claude_crew.ui_server import UIServer


def _make_ui(
    broker: Broker | None = None,
    artifact_registry: ArtifactRegistry | None = None,
) -> tuple[Broker, UIServer]:
    b = broker or Broker()
    ui = UIServer(b, port=0, artifact_registry=artifact_registry)
    return b, ui


def _client(ui: UIServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ui._make_app()),
        base_url="http://testserver",
    )


def _reg(crew_id: str) -> ArtifactRegistry:
    return ArtifactRegistry(crew_id=crew_id)


def _store(reg: ArtifactRegistry, title: str = "T", body: str = "# content") -> str:
    return reg.store(
        path_label="/doc.md",
        title=title,
        surfacing_teammate="planner",
        body_bytes=body.encode(),
    )


class _FakeRegistry:
    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def read_all(self) -> list[dict]:
        return self._entries


class _FakeResp:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeHttpClient:
    def __init__(self, resp: _FakeResp | None = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._resp = resp
        self._raise = raise_exc

    async def get(self, url: str) -> _FakeResp:
        self.calls.append(url)
        if self._raise is not None:
            raise self._raise
        assert self._resp is not None
        return self._resp


# ---------------------------------------------------------------------------
# Happy path: 200 with body
# ---------------------------------------------------------------------------


class TestArtifactEndpointHappyPath:
    async def test_200_returns_body_and_metadata(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        aid = _store(reg, title="My Spec", body="# Hello")
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/{aid}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["body"] == "# Hello"
        assert data["title"] == "My Spec"
        assert data["artifact_id"] == aid
        assert data["crew_id"] == broker.crew_id

    async def test_200_path_label_present(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        aid = reg.store(
            path_label="/some/path.md",
            title="T",
            surfacing_teammate="planner",
            body_bytes=b"content",
        )
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/{aid}")

        assert resp.status_code == 200
        assert resp.json()["path_label"] == "/some/path.md"

    async def test_body_not_in_api_state(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        _store(reg, body="secret body content")
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get("/api/state")

        data = resp.json()
        instance = data["instances"][0]
        assert "artifacts" in instance
        for art in instance["artifacts"]:
            assert "body" not in art

    async def test_artifacts_in_state_has_crew_id(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        _store(reg)
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get("/api/state")

        arts = resp.json()["instances"][0]["artifacts"]
        assert len(arts) == 1
        assert arts[0]["crew_id"] == broker.crew_id

    async def test_artifacts_newest_first_in_state(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        a = _store(reg, title="First")
        b = _store(reg, title="Second")
        c = _store(reg, title="Third")
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get("/api/state")

        arts = resp.json()["instances"][0]["artifacts"]
        assert [x["artifact_id"] for x in arts] == [c, b, a]


# ---------------------------------------------------------------------------
# Sad paths: 404 and 400
# ---------------------------------------------------------------------------


class TestArtifactEndpointSadPaths:
    async def test_404_unknown_artifact_id(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        _, ui = _make_ui(broker=broker, artifact_registry=reg)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/deadbeef1234")

        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    async def test_404_evicted_tombstoned_id(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        # Fill to cap + 1 to evict the first entry
        first_id = _store(reg, title="First")
        for i in range(_MAX_ARTIFACTS):
            _store(reg, body=f"body {i}")
        assert reg.is_tombstoned(first_id)

        _, ui = _make_ui(broker=broker, artifact_registry=reg)
        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/{first_id}")

        assert resp.status_code == 404

    async def test_404_when_no_registry(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker=broker, artifact_registry=None)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/someartifact")

        assert resp.status_code == 404

    async def test_400_invalid_crew_id(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker=broker)

        async with _client(ui) as client:
            resp = await client.get("/artifact/bad..crew/validaid")

        assert resp.status_code == 400

    async def test_400_invalid_artifact_id(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker=broker)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/bad..id")

        assert resp.status_code == 400

    async def test_state_artifacts_empty_when_no_registry(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker=broker, artifact_registry=None)

        async with _client(ui) as client:
            resp = await client.get("/api/state")

        arts = resp.json()["instances"][0]["artifacts"]
        assert arts == []


# ---------------------------------------------------------------------------
# Multi-instance proxy: leader → follower
# ---------------------------------------------------------------------------


class TestArtifactProxy:
    async def test_proxies_to_owning_instance(self) -> None:
        broker = Broker()
        remote_crew_id = "remote-crew-abc"
        fake_reg = _FakeRegistry([
            {"crew_id": remote_crew_id, "port": 9999, "pid": 1}
        ])
        fake_http = _FakeHttpClient(resp=_FakeResp(200, {
            "artifact_id": "abc123",
            "crew_id": remote_crew_id,
            "title": "Remote Spec",
            "path_label": "/remote.md",
            "surfacing_teammate": "planner",
            "timestamp_utc": 1234567890.0,
            "body": "remote content",
        }))
        ui = UIServer(broker, port=0, registry=fake_reg)
        ui._http_client = fake_http

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{remote_crew_id}/abc123")

        assert resp.status_code == 200
        assert resp.json()["body"] == "remote content"
        assert len(fake_http.calls) == 1
        assert fake_http.calls[0] == f"http://127.0.0.1:9999/artifact/{remote_crew_id}/abc123"

    async def test_proxy_404_when_crew_not_in_registry(self) -> None:
        broker = Broker()
        fake_reg = _FakeRegistry([])
        ui = UIServer(broker, port=0, registry=fake_reg)

        async with _client(ui) as client:
            resp = await client.get("/artifact/unknown-crew/someid")

        assert resp.status_code == 404

    async def test_proxy_404_when_no_instance_registry(self) -> None:
        broker = Broker()
        ui = UIServer(broker, port=0, registry=None)

        async with _client(ui) as client:
            resp = await client.get("/artifact/some-other-crew/someid")

        assert resp.status_code == 404

    async def test_proxy_502_when_remote_unreachable(self) -> None:
        broker = Broker()
        remote_crew_id = "unreachable-crew"
        fake_reg = _FakeRegistry([
            {"crew_id": remote_crew_id, "port": 9998, "pid": 1}
        ])
        fake_http = _FakeHttpClient(raise_exc=ConnectionRefusedError("refused"))
        ui = UIServer(broker, port=0, registry=fake_reg)
        ui._http_client = fake_http

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{remote_crew_id}/someid")

        assert resp.status_code == 502

    async def test_proxy_404_invalid_port_in_registry(self) -> None:
        broker = Broker()
        remote_crew_id = "bad-port-crew"
        fake_reg = _FakeRegistry([
            {"crew_id": remote_crew_id, "port": "notanint", "pid": 1}
        ])
        ui = UIServer(broker, port=0, registry=fake_reg)

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{remote_crew_id}/someid")

        assert resp.status_code == 404

    async def test_local_crew_served_directly(self) -> None:
        broker = Broker()
        reg = _reg(broker.crew_id)
        aid = _store(reg, body="local body")
        ui = UIServer(broker, port=0, artifact_registry=reg)
        fake_http = _FakeHttpClient()
        ui._http_client = fake_http

        async with _client(ui) as client:
            resp = await client.get(f"/artifact/{broker.crew_id}/{aid}")

        assert resp.status_code == 200
        assert resp.json()["body"] == "local body"
        assert fake_http.calls == []  # no proxy for local crew
