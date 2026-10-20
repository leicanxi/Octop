"""Admin-managed RVC voice whitelist shared by the gateway and plugin.

The JSON file is intentionally small and re-read on every lookup, so an admin
can add or retitle a voice without restarting Octop or the gateway:

    ~/.octop/sing-voices.json

    {
      "keine": {"label": "Keine（默认）", "url": "https://.../keine.zip"},
      "cirno": {"label": "Cirno（更甜）", "url": "https://.../cirno.zip"}
    }

A plain string value (``"keine": "https://..."``) is accepted as the URL.
Users never pass a raw URL: they select one of these names, which lets an admin
review the model before it is loaded.
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
DEFAULT_VOICE_NAME = "keine"


@dataclass(frozen=True)
class VoiceSpec:
    name: str
    label: str
    url: str


def voices_path() -> Path:
    override = os.environ.get(VOICES_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("OCTOP_HOME") or (Path.home() / ".octop"))
    return home / "sing-voices.json"


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


def load_voices(path: Path | None = None) -> dict[str, VoiceSpec]:
    """Return the whitelist; a missing/invalid file falls back to built-in Keine."""
    target = path or voices_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_voices()
    if not isinstance(raw, dict):
        return _default_voices()
    voices: dict[str, VoiceSpec] = {}
    for name, value in raw.items():
        key = str(name).strip().lower()
        if not key:
            continue
        spec = _parse_entry(key, value)
        if spec is not None:
            voices[key] = spec
    return voices or _default_voices()


def resolve_voice(name: str, path: Path | None = None) -> VoiceSpec | None:
    """Resolve a whitelist name; an empty name selects the default entry."""
    voices = load_voices(path)
    key = (name or "").strip().lower()
    if key:
        return voices.get(key)
    return voices.get(DEFAULT_VOICE_NAME) or next(iter(voices.values()), None)


def voice_catalog(path: Path | None = None) -> list[dict[str, str]]:
    return [{"name": spec.name, "label": spec.label} for spec in load_voices(path).values()]
