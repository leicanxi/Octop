"""Types and defaults shared by the singing plugin and gateway."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_VOICE_URL = "https://huggingface.co/leicanxi/rvc-models/resolve/main/keine_genso_flat.zip"
DEFAULT_REPLICATE_BASE = "https://api.replicate.com/v1"
DEFAULT_MODEL = "zsxkib/realistic-voice-cloning"
PITCH_MODES = frozenset({"no-change", "male-to-female", "female-to-male"})


@dataclass(frozen=True)
class SongCandidate:
    """One Bilibili search result the agent can show or sing."""

    title: str
    artist: str
    duration_seconds: int
    url: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "duration_seconds": self.duration_seconds,
            "url": self.url,
        }


@dataclass(frozen=True)
class SingConfig:
    """Provider-neutral knobs for one cover generation."""

    voice_model_url: str = DEFAULT_VOICE_URL
    model: str = DEFAULT_MODEL
    model_version: str = ""
    replicate_base_url: str = DEFAULT_REPLICATE_BASE
    replicate_token: str = ""
    pitch: int = 2
    pitch_mode: str = "no-change"
    start: int = 60
    duration: int = 25
    index_rate: float = 0.75
    filter_radius: int = 3
    rms_mix_rate: float = 0.25
    protect: float = 0.33
    prediction_timeout: float = 600.0
    cache_ttl_hours: float = 168.0
    cache_max_entries: int = 50
    cache_root: Path | None = None


@dataclass(frozen=True)
class SingResult:
    audio_url: str
    filename: str
    cached: bool = False

    def as_block(self) -> dict[str, Any]:
        return {
            "type": "audio",
            "source": {"type": "url", "url": self.audio_url},
            "media_type": "audio/mpeg",
            "filename": self.filename,
        }
