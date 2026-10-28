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
    catalog_path,
    load_catalog,
    load_voices,
    plaza_url,
    resolve_voice,
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
    "catalog_path",
    "load_catalog",
    "load_voices",
    "plaza_url",
    "render_song",
    "render_song_by_query",
    "resolve_voice",
    "search_songs",
    "voices_path",
]
