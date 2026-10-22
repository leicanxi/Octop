"""Bilibili search plus best-effort audio download for short singing clips."""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from octop.infra.music.config import SongCandidate

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}
_TAG_RE = re.compile(r"<[^>]+>")
_BANNED_TITLE = re.compile(
    r"翻唱|cover|纯音乐|伴奏|直播|合集|AMV|MAD|鬼畜|剪辑|教学|钢琴|吉他|翻弹|现场"
    r"|直拍|演唱会|音乐节|教唱|混剪|reaction",
    re.IGNORECASE,
)
_AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".webm", ".opus", ".aac", ".flac"}


def strip_tags(value: Any) -> str:
    return html.unescape(_TAG_RE.sub("", str(value or ""))).strip()


def parse_duration(value: Any) -> int:
    """Accept Bilibili's ``MM:SS``/``HH:MM:SS`` strings or a numeric seconds value."""
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value or "").strip().split(":")
    if not parts or not all(part.isdigit() for part in parts):
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def candidate_rows(rows: Any, limit: int) -> list[SongCandidate]:
    """Normalize Bilibili rows and drop obvious covers/compilations.

    Filtered rows are used only when nothing clean remains, so an unusual query
    still returns something for the agent to show.
    """
    clean: list[SongCandidate] = []
    fallback: list[SongCandidate] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        bvid = str(row.get("bvid") or "").strip()
        if not bvid:
            continue
        title = strip_tags(row.get("title"))
        if not title:
            continue
        item = SongCandidate(
            title=title,
            artist=str(row.get("author") or "").strip(),
            duration_seconds=parse_duration(row.get("duration")),
            url=f"https://www.bilibili.com/video/{bvid}",
        )
        if _BANNED_TITLE.search(title):
            fallback.append(item)
        else:
            clean.append(item)
    return (clean or fallback)[:limit]


def _proxy() -> str | None:
    return os.environ.get("SING_PROXY", "").strip() or None


def search_bilibili(query: str, limit: int = 3) -> list[SongCandidate]:
    """Search Bilibili's web API and return up to *limit* usable candidates."""
    query = (query or "").strip()
    if not query:
        return []
    with httpx.Client(
        timeout=20.0, headers=HEADERS, follow_redirects=True, proxy=_proxy()
    ) as client:
        response = client.get(
            SEARCH_URL,
            params={"search_type": "video", "keyword": query, "page": 1},
        )
        response.raise_for_status()
        payload = response.json()
    if int(payload.get("code") or 0) != 0:
        raise RuntimeError(
            payload.get("message") or f"bilibili search failed: {payload.get('code')}"
        )
    data = payload.get("data") or {}
    return candidate_rows(data.get("result"), limit)


def _download_options(workdir: Path) -> dict[str, Any]:
    """yt-dlp options; optional cookies/proxy help datacenter IPs pass risk control."""
    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }
    cookies = os.environ.get("SING_BILI_COOKIES", "").strip()
    if cookies:
        if not Path(cookies).is_file():
            raise RuntimeError(f"SING_BILI_COOKIES file not found: {cookies}")
        options["cookies"] = cookies
    proxy = _proxy()
    if proxy:
        options["proxy"] = proxy
    return options


def download_audio(url: str, workdir: Path) -> tuple[Path, str, float]:
    """Download the best audio stream for *url* into *workdir*."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_download_options(workdir)) as downloader:
        info = downloader.extract_info(url, download=True) or {}
        path = Path(downloader.prepare_filename(info))
    if not path.is_file():
        files = [item for item in workdir.iterdir() if item.suffix.lower() in _AUDIO_SUFFIXES]
        if not files:
            raise RuntimeError("download produced no audio file")
        path = files[0]
    return path, str(info.get("title") or "cover"), float(info.get("duration") or 0)


def quote_query(query: str) -> str:
    """Convenience for callers that build Bilibili URLs themselves."""
    return quote(query, safe="")
