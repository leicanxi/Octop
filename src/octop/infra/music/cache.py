"""On-demand disk cache for generated singing clips."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from octop.infra.music.config import DEFAULT_MODEL, DEFAULT_VOICE_URL, SingConfig


def cache_root(cfg: SingConfig) -> Path:
    if cfg.cache_root is not None:
        return cfg.cache_root
    home = Path(os.environ.get("OCTOP_HOME") or (Path.home() / ".octop"))
    return home / "cache" / "sing-song"


def cache_key(url: str, start: int, duration: int, cfg: SingConfig) -> str:
    payload = {
        "v": 1,
        "url": url,
        "start": start,
        "duration": duration,
        "pitch": cfg.pitch,
        "pitch_mode": cfg.pitch_mode,
        "voice": cfg.voice_model_url or DEFAULT_VOICE_URL,
        "model": cfg.model or DEFAULT_MODEL,
        "version": cfg.model_version or "latest",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def cache_lookup(root: Path, key: str, ttl_hours: float) -> tuple[Path, dict[str, Any]] | None:
    audio = root / f"{key}.mp3"
    meta_path = root / f"{key}.json"
    if not audio.is_file() or audio.stat().st_size <= 0 or not meta_path.is_file():
        return None
    age_hours = (time.time() - audio.stat().st_mtime) / 3600
    if ttl_hours > 0 and age_hours > ttl_hours:
        audio.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return audio, (meta if isinstance(meta, dict) else {})


def cache_store(root: Path, key: str, audio: Path, meta: dict[str, Any], max_entries: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio, root / f"{key}.mp3")
    (root / f"{key}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if max_entries <= 0:
        return
    entries = sorted(root.glob("*.mp3"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in entries[max_entries:]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".json").unlink(missing_ok=True)
