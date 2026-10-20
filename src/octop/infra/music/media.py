"""ffmpeg helpers for trimming and normalizing generated audio."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from octop.infra.music.bilibili import strip_tags

logger = logging.getLogger(__name__)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", strip_tags(value)).strip("._")
    return (cleaned[:60] or "cover") + ".mp3"


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def trim_audio(source: Path, target: Path, start: int, duration: int) -> None:
    subprocess.run(
        [
            _ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def to_mp3(source: Path, target: Path) -> None:
    subprocess.run(
        [
            _ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def fetch_output(output_url: str, workdir: Path) -> Path:
    """Download the generated cover so it can be cached locally."""
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        response = client.get(output_url)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        suffix = Path(urlparse(output_url).path).suffix.lower()
        if "wav" in content_type:
            suffix = ".wav"
        elif "mpeg" in content_type or "mp3" in content_type:
            suffix = ".mp3"
        suffix = suffix or ".mp3"
        raw = workdir / f"generated{suffix}"
        raw.write_bytes(response.content)
    if suffix == ".mp3":
        return raw
    mp3 = workdir / "generated.mp3"
    to_mp3(raw, mp3)
    return mp3
