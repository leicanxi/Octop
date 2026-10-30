"""RVC voice sources shared by the gateway and the sing-song plugin.

Two JSON files, both re-read on lookup with an mtime cache, so edits apply
without restarting Octop or the gateway:

~/.octop/sing-voices.json — curated whitelist (defaults + admin-approved
custom voices; also where per-user custom voices will live):

    {
      "keine": {"label": "Keine（默认）", "url": "https://.../keine.zip"},
      "cirno": "https://.../cirno.zip"
    }

~/.octop/sing-voices-catalog.json — machine-scraped public catalog maintained
by ``scripts/scrape_voice_catalog.py`` (extra keys such as ``favorites`` are
preserved on rewrite but ignored here):

    {
      "hatsune-miku": {"label": "Hatsune Miku", "url": "https://huggingface.co/.../miku.zip"}
    }

``resolve_voice`` checks the whitelist first, then the catalog. Voice names
travel through chat as plain strings and are never listed by any API: the
public catalog is browsable on the voice plaza, while custom whitelist entries
stay known only to whoever uses them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octop.infra.music.config import DEFAULT_VOICE_URL

logger = logging.getLogger(__name__)

VOICES_FILE_ENV = "SING_VOICES_FILE"
CATALOG_FILE_ENV = "SING_VOICES_CATALOG_FILE"
PLAZA_URL_ENV = "SING_VOICE_PLAZA_URL"
PLAZA_URL = "https://voice-models.flowbee.top"
DEFAULT_VOICE_NAME = "keine"

_CacheStamp = tuple[int, int]
_cache: dict[Path, tuple[_CacheStamp, dict[str, VoiceSpec]]] = {}


@dataclass(frozen=True)
class VoiceSpec:
    name: str
    label: str
    url: str


def _home() -> Path:
    return Path(os.environ.get("OCTOP_HOME") or (Path.home() / ".octop"))


def voices_path() -> Path:
    override = os.environ.get(VOICES_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return _home() / "sing-voices.json"


def catalog_path() -> Path:
    override = os.environ.get(CATALOG_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return _home() / "sing-voices-catalog.json"


def plaza_url() -> str:
    return os.environ.get(PLAZA_URL_ENV, "").strip() or PLAZA_URL


def _default_voices() -> dict[str, VoiceSpec]:
    return {
        DEFAULT_VOICE_NAME: VoiceSpec(
            name=DEFAULT_VOICE_NAME,
            label="Keine",
            url=DEFAULT_VOICE_URL,
        )
    }


def _parse_entry(name: str, value: Any) -> VoiceSpec | None:
    if isinstance(value, str) and value.strip():
        return VoiceSpec(name=name, label=name, url=value.strip())
    if isinstance(value, dict):
        url = str(value.get("url") or "").strip()
        if not url:
            return None
        label = str(value.get("label") or name).strip() or name
        return VoiceSpec(name=name, label=label, url=url)
    return None


def _read_specs(target: Path, fallback: dict[str, VoiceSpec]) -> dict[str, VoiceSpec]:
    """Parse a voices JSON; cache per (mtime, size) so edits apply immediately."""
    try:
        stat = target.stat()
        stamp: _CacheStamp | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if stamp is not None:
        cached = _cache.get(target)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    if not isinstance(raw, dict):
        return fallback
    specs: dict[str, VoiceSpec] = {}
    for name, value in raw.items():
        key = str(name).strip().lower()
        if not key:
            continue
        spec = _parse_entry(key, value)
        if spec is not None:
            specs[key] = spec
    parsed = specs or fallback
    if stamp is not None:
        _cache[target] = (stamp, parsed)
    return parsed


def load_voices(path: Path | None = None) -> dict[str, VoiceSpec]:
    """Return the whitelist; a missing/invalid file falls back to built-in Keine."""
    return _read_specs(path or voices_path(), _default_voices())


def load_catalog(path: Path | None = None) -> dict[str, VoiceSpec]:
    """Return the scraped public catalog; empty when the file is absent."""
    return _read_specs(path or catalog_path(), {})


def resolve_voice(name: str, path: Path | None = None) -> VoiceSpec | None:
    """Resolve a name against the whitelist, then the catalog.

    An empty name selects the whitelist default. An explicit ``path`` isolates
    the lookup to that single file (tests, tooling).
    """
    key = (name or "").strip().lower()
    if key:
        spec = _read_specs(path or voices_path(), _default_voices()).get(key)
        if spec is None and path is None:
            spec = _read_specs(catalog_path(), {}).get(key)
        return spec
    voices = _read_specs(path or voices_path(), _default_voices())
    return voices.get(DEFAULT_VOICE_NAME) or next(iter(voices.values()), None)


def remove_voice(name: str, path: Path | None = None) -> bool:
    """Remove a whitelist entry (used when a submitter deletes their voice)."""
    target = path or voices_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    key = str(name).strip().lower()
    if raw.pop(key, None) is None and raw.pop(str(name), None) is None:
        return False
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return True
