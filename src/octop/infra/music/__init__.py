"""Shared singing pipeline: Bilibili search, clip extraction, RVC via Replicate."""

from __future__ import annotations

from octop.infra.music.config import (
    DEFAULT_MODEL,
    DEFAULT_REPLICATE_BASE,
    DEFAULT_VOICE_URL,
    PITCH_MODES,
    ClientSongError,
    SingConfig,
    SingResult,
    SongCandidate,
)
from octop.infra.music.service import render_song, render_song_by_query, search_songs
from octop.infra.music.voices import (
    VoiceSpec,
    load_voices,
    resolve_voice,
    voice_catalog,
    voices_path,
)

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_REPLICATE_BASE",
    "DEFAULT_VOICE_URL",
    "PITCH_MODES",
    "ClientSongError",
    "SingConfig",
    "SingResult",
    "SongCandidate",
    "VoiceSpec",
    "load_voices",
    "render_song",
    "render_song_by_query",
    "resolve_voice",
    "search_songs",
    "voice_catalog",
    "voices_path",
]
