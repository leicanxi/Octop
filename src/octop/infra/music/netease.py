"""NetEase Cloud Music source for short singing clips.

Uses the anonymous web APIs: search returns plain JSON and the player endpoint
issues direct mp3 links for free songs. VIP-only songs have no anonymous URL
and fail with an explicit error.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from octop.infra.music.config import SongCandidate

SEARCH_URL = "https://music.163.com/api/search/get"
PLAYER_URL = "https://music.163.com/api/song/enhance/player/url"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}
_SONG_URL_RE = re.compile(r"(?:song\?id=|/song/)(\d+)")


def _proxy() -> str | None:
    return os.environ.get("SING_PROXY", "").strip() or None


def is_netease_url(url: str) -> bool:
    return bool(_SONG_URL_RE.search(url or ""))


def _song_id(url: str) -> str:
    match = _SONG_URL_RE.search(url or "")
    if not match:
        raise RuntimeError(f"not a netease song url: {url}")
    return match.group(1)


def search_netease(query: str, limit: int = 3) -> list[SongCandidate]:
    """Search NetEase and return up to ``limit`` usable candidates."""
    query = (query or "").strip()
    if not query:
        return []
    with httpx.Client(
        timeout=20.0, headers=HEADERS, follow_redirects=True, proxy=_proxy()
    ) as client:
        response = client.get(SEARCH_URL, params={"s": query, "type": 1, "limit": limit})
        response.raise_for_status()
        payload = response.json()
    if int(payload.get("code") or 200) != 200:
        raise RuntimeError(
            payload.get("message") or f"netease search failed: {payload.get('code')}"
        )
    data = payload.get("result")
    songs = data.get("songs") if isinstance(data, dict) else None
    candidates: list[SongCandidate] = []
    for song in songs if isinstance(songs, list) else []:
        if not isinstance(song, dict):
            continue
        song_id = str(song.get("id") or "").strip()
        title = str(song.get("name") or "").strip()
        if not song_id or not title:
            continue
        artists = song.get("artists") or song.get("ar") or []
        artist = "/".join(
            str(item.get("name") or "")
            for item in artists
            if isinstance(item, dict) and item.get("name")
        )
        duration_ms = song.get("duration") or song.get("dt") or 0
        duration = int(duration_ms) // 1000 if isinstance(duration_ms, (int, float)) else 0
        candidates.append(
            SongCandidate(
                title=title,
                artist=artist,
                duration_seconds=duration,
                url=f"https://music.163.com/song?id={song_id}",
            )
        )
    return candidates


def download_audio(url: str, workdir: Path) -> tuple[Path, str, float]:
    """Download the mp3 stream for a NetEase song URL into *workdir*."""
    song_id = _song_id(url)
    with httpx.Client(
        timeout=30.0, headers=HEADERS, follow_redirects=True, proxy=_proxy()
    ) as client:
        response = client.get(PLAYER_URL, params={"ids": json.dumps([int(song_id)]), "br": 320000})
        response.raise_for_status()
        rows = response.json().get("data")
        row: dict[str, Any] = (
            rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        )
        direct = str(row.get("url") or "").strip()
        if not direct:
            raise RuntimeError(
                "网易云没有返回音频直链：该歌曲可能需要 VIP/数字专辑，或接口被风控，请换一首免费歌曲"
            )
        raw_ms = row.get("time")
        total_seconds = float(raw_ms) / 1000.0 if isinstance(raw_ms, (int, float)) else 0.0
        audio = client.get(direct)
        audio.raise_for_status()
    target = workdir / "source.mp3"
    target.write_bytes(audio.content)
    return target, f"netease-{song_id}", total_seconds
