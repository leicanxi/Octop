"""Cache hits must return a publicly playable audio URL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from octop.infra.music import service
from octop.infra.music.config import SingConfig


def _cfg(tmp_path: Path) -> SingConfig:
    return SingConfig(cache_root=tmp_path, replicate_token="r8_test")


def _patch_full_render(monkeypatch: MonkeyPatch) -> None:
    def fake_download(url: str, workdir: Path) -> tuple[Path, str, float]:
        source = workdir / "source.mp3"
        source.write_bytes(b"src")
        return source, "title", 60.0

    def fake_trim(source: Path, target: Path, start: int, duration: int) -> None:
        target.write_bytes(b"clip")

    def fake_fetch(output_url: str, workdir: Path) -> Path:
        generated = workdir / "generated.mp3"
        generated.write_bytes(b"gen")
        return generated

    monkeypatch.setattr(service.netease, "is_netease_url", lambda url: False)
    monkeypatch.setattr(service.bilibili, "download_audio", fake_download)
    monkeypatch.setattr(service.media, "trim_audio", fake_trim)
    monkeypatch.setattr(
        service.replicate,
        "upload_file",
        lambda base, token, path: "https://replicate.delivery/clip",
    )
    monkeypatch.setattr(
        service.replicate,
        "predict_cover",
        lambda base, token, audio, cfg: "https://replicate.delivery/out.mp3",
    )
    monkeypatch.setattr(service.media, "fetch_output", fake_fetch)


def test_cache_hit_returns_stored_public_url(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    entry = tmp_path / "hit.mp3"
    entry.write_bytes(b"cached")
    meta = {"filename": "t.mp3", "audio_url": "https://replicate.delivery/a.mp3"}
    monkeypatch.setattr(service.cache, "cache_lookup", lambda root, key, ttl: (entry, meta))
    monkeypatch.setattr(service, "_url_reachable", lambda *args, **kwargs: True)

    def forbid_upload(*args: object, **kwargs: object) -> str:
        raise AssertionError("a playable hit must not re-upload")

    monkeypatch.setattr(service.replicate, "upload_file", forbid_upload)
    result = service.render_song("https://music.163.com/song?id=1", _cfg(tmp_path))
    assert result.cached is True
    assert result.audio_url == "https://replicate.delivery/a.mp3"
    assert result.filename == "t.mp3"


@pytest.mark.parametrize(
    ("meta", "reachable"),
    [
        ({"filename": "old.mp3"}, True),  # legacy entry without audio_url
        ({"filename": "old.mp3", "audio_url": "https://replicate.delivery/gone.mp3"}, False),
    ],
    ids=["legacy", "stale"],
)
def test_hit_without_reachable_url_regenerates(
    tmp_path: Path, monkeypatch: MonkeyPatch, meta: dict, reachable: bool
) -> None:
    entry = tmp_path / "old.mp3"
    entry.write_bytes(b"cached")
    monkeypatch.setattr(service.cache, "cache_lookup", lambda root, key, ttl: (entry, dict(meta)))
    monkeypatch.setattr(service, "_url_reachable", lambda *args, **kwargs: reachable)
    _patch_full_render(monkeypatch)

    result = service.render_song("https://music.163.com/song?id=2", _cfg(tmp_path))
    assert result.cached is False
    assert result.audio_url == "https://replicate.delivery/out.mp3"

    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert stored["audio_url"] == "https://replicate.delivery/out.mp3"
