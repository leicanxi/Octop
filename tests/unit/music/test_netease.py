"""NetEase source parsing, search mapping and VIP error handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from octop.infra.music import netease
from octop.infra.music.config import ClientSongError


def test_song_url_recognition_and_id() -> None:
    url = "https://music.163.com/song?id=3379716579"
    assert netease.is_netease_url(url)
    assert netease._song_id(url) == "3379716579"
    assert netease._song_id("https://music.163.com/song/123") == "123"
    assert not netease.is_netease_url("https://www.bilibili.com/video/BV1xx411c7mD")
    with pytest.raises(RuntimeError, match="netease"):
        netease._song_id("https://example.com/x")


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def get(self, url: str, params: dict | None = None, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._payload)


def test_search_netease_parses_songs(monkeypatch: MonkeyPatch) -> None:
    payload = {
        "code": 200,
        "result": {
            "songs": [{"id": 1, "name": "歌", "artists": [{"name": "歌手"}], "duration": 250000}]
        },
    }
    monkeypatch.setattr(netease.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    rows = netease.search_netease("歌", 1)
    assert len(rows) == 1
    assert rows[0].url == "https://music.163.com/song?id=1"
    assert rows[0].artist == "歌手"
    assert rows[0].duration_seconds == 250


def test_download_vip_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SING_NCM_API", raising=False)
    payload = {"code": 200, "data": [{"id": 1, "url": None, "code": 404}]}
    monkeypatch.setattr(netease.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    with pytest.raises(ClientSongError, match="VIP"):
        netease.download_audio("https://music.163.com/song?id=1", tmp_path)


class _FakeAudioResponse:
    def __init__(self, content: bytes = b"") -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _RoutingClient:
    """Fake httpx client: player endpoints return JSON rows, CDN urls return bytes."""

    def __init__(self, *, sidecar: dict | None, bare: dict, content: bytes = b"mp3") -> None:
        self.sidecar = sidecar
        self.bare = bare
        self.content = content
        self.calls: list[str] = []

    def __enter__(self) -> _RoutingClient:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def get(
        self, url: str, params: dict | None = None, **kwargs: object
    ) -> _FakeResponse | _FakeAudioResponse:
        self.calls.append(url)
        if "song/url" in url:
            if self.sidecar is None:
                raise RuntimeError("sidecar down")
            return _FakeResponse(self.sidecar)
        if "player/url" in url:
            return _FakeResponse(self.bare)
        return _FakeAudioResponse(self.content)


def test_download_trial_clip_uses_sidecar(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_NCM_API", "http://127.0.0.1:3010")
    client = _RoutingClient(
        sidecar={
            "code": 200,
            "data": [
                {
                    "id": 7,
                    "url": "http://cdn.example/x.mp3",
                    "time": 269000,
                    "freeTrialInfo": {"start": 0, "end": 45},
                }
            ],
        },
        bare={"code": 200, "data": [{"id": 7, "url": None}]},
    )
    monkeypatch.setattr(netease.httpx, "Client", lambda **kwargs: client)
    path, title, total = netease.download_audio("https://music.163.com/song?id=7", tmp_path)
    assert path.read_bytes() == b"mp3"
    assert title == "netease-7"
    assert total == 45.0
    assert any("song/url/v1" in u for u in client.calls)


def test_download_falls_back_to_bare_endpoint(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_NCM_API", "http://127.0.0.1:3010")
    client = _RoutingClient(
        sidecar=None,
        bare={
            "code": 200,
            "data": [{"id": 7, "url": "http://cdn.example/full.mp3", "time": 200000}],
        },
    )
    monkeypatch.setattr(netease.httpx, "Client", lambda **kwargs: client)
    path, title, total = netease.download_audio("https://music.163.com/song?id=7", tmp_path)
    assert total == 200.0
    assert any("player/url" in u for u in client.calls)
