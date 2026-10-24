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
    payload = {"code": 200, "data": [{"id": 1, "url": None, "code": 404}]}
    monkeypatch.setattr(netease.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    with pytest.raises(ClientSongError, match="VIP"):
        netease.download_audio("https://music.163.com/song?id=1", tmp_path)
