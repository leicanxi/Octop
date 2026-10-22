"""Music source dispatch in the singing service."""

from __future__ import annotations

from pytest import MonkeyPatch

from octop.infra.music import bilibili, netease, service


def test_search_songs_dispatches_by_source(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(bilibili, "search_bilibili", lambda query, limit: ["bili"])
    monkeypatch.setattr(netease, "search_netease", lambda query, limit: ["netease"])
    assert service.search_songs("q", 1) == ["bili"]
    assert service.search_songs("q", 1, "bilibili") == ["bili"]
    assert service.search_songs("q", 1, "netease") == ["netease"]
