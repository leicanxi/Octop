"""Orchestrate search, clip extraction, cover generation and caching."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx

from octop.infra.music import bilibili, cache, media, netease, replicate
from octop.infra.music.config import (
    MUSIC_SOURCES,
    PITCH_MODES,
    ClientSongError,
    SingConfig,
    SingResult,
    SongCandidate,
)

logger = logging.getLogger(__name__)


def _normalized(cfg: SingConfig) -> SingConfig:
    return replace(
        cfg,
        start=max(0, min(3600, int(cfg.start))),
        duration=max(5, min(30, int(cfg.duration))),
        pitch=max(-24, min(24, int(cfg.pitch))),
        pitch_mode=cfg.pitch_mode if cfg.pitch_mode in PITCH_MODES else "no-change",
        music_source=cfg.music_source if cfg.music_source in MUSIC_SOURCES else "bilibili",
        index_rate=max(0.0, min(1.0, float(cfg.index_rate))),
        filter_radius=max(0, min(7, int(cfg.filter_radius))),
        rms_mix_rate=max(0.0, min(1.0, float(cfg.rms_mix_rate))),
        protect=max(0.0, min(0.5, float(cfg.protect))),
        prediction_timeout=max(30.0, float(cfg.prediction_timeout)),
        cache_ttl_hours=max(0.0, float(cfg.cache_ttl_hours)),
        cache_max_entries=max(0, min(500, int(cfg.cache_max_entries))),
    )


def _resolve_token(cfg: SingConfig) -> str:
    token = (cfg.replicate_token or os.environ.get("REPLICATE_API_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("Replicate token is not configured")
    return token


def _url_reachable(url: str, timeout: float = 10.0) -> bool:
    """Check that a cached public audio URL still serves before returning it."""
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"Range": "bytes=0-0"}
        ) as client:
            response = client.head(url)
            if response.status_code in (405, 501):
                response = client.get(url)
            return response.status_code < 400
    except Exception:
        return False


def search_songs(query: str, limit: int = 3, source: str = "bilibili") -> list[SongCandidate]:
    """Search the configured music source and return up to ``limit`` candidates."""
    limit = max(1, min(5, int(limit)))
    if source == "netease":
        return netease.search_netease(query, limit)
    return bilibili.search_bilibili(query, limit)


def render_song(url: str, cfg: SingConfig) -> SingResult:
    """Generate one short cover for an already-resolved Bilibili/audio URL.

    Cache hits only re-upload the cached mp3 to Replicate Files, so they return
    a fresh public URL in about a second without running the model.
    """
    cfg = _normalized(cfg)
    token = _resolve_token(cfg)
    base_url = cfg.replicate_base_url.rstrip("/")
    root = cache.cache_root(cfg)
    key = cache.cache_key(url, cfg.start, cfg.duration, cfg)
    cached = cache.cache_lookup(root, key, cfg.cache_ttl_hours)
    if cached is not None:
        audio, meta = cached
        # Hits must return a publicly playable URL: the original delivery link
        # is stored in the cache meta. Replicate Files URLs need auth, and old
        # entries predate the field — both regenerate below.
        public_url = str(meta.get("audio_url") or "").strip()
        if public_url and _url_reachable(public_url):
            logger.info("sing cache hit %s", key)
            return SingResult(public_url, str(meta.get("filename") or "cover.mp3"), True)
        logger.info("sing cache entry has no playable url; regenerating %s", key)
        audio.unlink(missing_ok=True)
        audio.with_suffix(".json").unlink(missing_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix="octop-sing-"))
    try:
        if netease.is_netease_url(url):
            source, title, total = netease.download_audio(url, workdir)
        else:
            source, title, total = bilibili.download_audio(url, workdir)
        start = cfg.start
        if total > 0:
            start = min(start, max(0, int(total) - cfg.duration - 1))
        clip = workdir / "clip.mp3"
        media.trim_audio(source, clip, start, cfg.duration)
        audio_url = replicate.upload_file(base_url, token, clip)
        output_url = replicate.predict_cover(base_url, token, audio_url, cfg)
        filename = media.safe_filename(title)
        try:
            generated = media.fetch_output(output_url, workdir)
            cache.cache_store(
                root,
                key,
                generated,
                {"filename": filename, "audio_url": output_url},
                cfg.cache_max_entries,
            )
        except Exception:
            logger.warning("sing cache store failed", exc_info=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return SingResult(output_url, filename, cached=False)


def render_song_by_query(song: str, cfg: SingConfig) -> SingResult:
    """Search for *song* and generate the first usable result."""
    candidates = search_songs(song, 1, cfg.music_source)
    if not candidates:
        raise ClientSongError(f"no song found for {song!r}")
    return render_song(candidates[0].url, cfg)
