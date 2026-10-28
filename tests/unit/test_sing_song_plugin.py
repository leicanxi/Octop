"""Shared singing helpers and the bundled ``sing-song`` plugin surface."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

from octop_harness.plugins.context import PluginContext
from octop_harness.plugins.manifest import PluginManifest
from pytest import MonkeyPatch

from octop.infra.gateway.media.tool_media import is_media_push_tool, iter_media_blocks
from octop.infra.music.bilibili import candidate_rows, parse_duration
from octop.infra.music.cache import cache_key, cache_lookup, cache_store
from octop.infra.music.config import SingConfig
from octop.infra.music.media import safe_filename
from octop.infra.music.voices import (
    load_catalog,
    load_voices,
    plaza_url,
    resolve_voice,
)

_PLUGIN = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "octop"
    / "infra"
    / "agents"
    / "plugins"
    / "bundled"
    / "sing-song"
    / "main.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("octop_test_sing_song", _PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_duration_accepts_bilibili_formats() -> None:
    assert parse_duration("5:27") == 327
    assert parse_duration("1:02:03") == 3723
    assert parse_duration(42) == 42
    assert parse_duration("") == 0


def test_candidate_rows_strip_tags_and_filter_covers() -> None:
    rows = [
        {
            "bvid": "BV1",
            "title": '<em class="keyword">起风了</em>',
            "author": "原唱",
            "duration": "5:27",
            "play": 10,
        },
        {
            "bvid": "BV2",
            "title": "起风了 翻唱",
            "author": "翻唱者",
            "duration": "4:00",
            "play": 999,
        },
        {
            "bvid": "BV3",
            "title": "起风了 演唱会直拍",
            "author": "现场",
            "duration": "5:00",
        },
    ]
    picked = candidate_rows(rows, 3)
    assert [item.url for item in picked] == ["https://www.bilibili.com/video/BV1"]
    assert picked[0].title == "起风了"
    assert picked[0].duration_seconds == 327


def test_candidate_rows_fall_back_when_everything_is_filtered() -> None:
    rows = [{"bvid": "BV9", "title": "起风了 伴奏", "duration": "3:00"}]
    picked = candidate_rows(rows, 3)
    assert [item.url for item in picked] == ["https://www.bilibili.com/video/BV9"]


def test_safe_filename_removes_path_separators() -> None:
    assert safe_filename("起风了 / live") == "起风了_live.mp3"
    assert safe_filename("") == "cover.mp3"


def test_setup_registers_both_tools() -> None:
    module = _load()
    manifest = PluginManifest(
        id="sing-song",
        version="0.1.0",
        name="AI 唱歌",
        kind="tool",
        entry="main.py",
    )
    ctx = PluginContext(manifest=manifest, source_path=_PLUGIN.parent)
    module.setup(ctx)
    loaded = ctx.to_loaded()
    assert [tool.name for tool in loaded.tools] == [
        "search_song",
        "list_voices",
        "sing_song",
    ]
    assert all(tool.description for tool in loaded.tools)


def test_voice_whitelist(tmp_path: Path) -> None:
    path = tmp_path / "voices.json"
    path.write_text(
        json.dumps(
            {
                "keine": "https://example.com/keine.zip",
                "cirno": {"label": "Cirno", "url": "https://example.com/cirno.zip"},
            }
        ),
        encoding="utf-8",
    )
    voices = load_voices(path)
    assert voices["keine"].url == "https://example.com/keine.zip"
    assert voices["cirno"].label == "Cirno"
    assert resolve_voice("CIRNO", path).url == "https://example.com/cirno.zip"
    assert resolve_voice("", path).name == "keine"
    assert resolve_voice("missing", path) is None


def test_resolve_voice_falls_back_to_catalog(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_VOICES_FILE", str(tmp_path / "voices.json"))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "hatsune-miku": {
                    "label": "Hatsune Miku",
                    "url": "https://huggingface.co/a/b/resolve/main/miku.zip",
                    "favorites": 59,
                    "weights_id": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SING_VOICES_CATALOG_FILE", str(catalog))
    spec = resolve_voice("hatsune-miku")
    assert spec is not None and spec.url == "https://huggingface.co/a/b/resolve/main/miku.zip"
    assert load_catalog()["hatsune-miku"].label == "Hatsune Miku"


def test_whitelist_wins_over_catalog(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    white = tmp_path / "voices.json"
    white.write_text(json.dumps({"miku": "https://example.com/white.zip"}), encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"miku": "https://huggingface.co/a/b/resolve/main/miku.zip"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SING_VOICES_FILE", str(white))
    monkeypatch.setenv("SING_VOICES_CATALOG_FILE", str(catalog))
    assert resolve_voice("miku").url == "https://example.com/white.zip"


def test_catalog_edits_apply_without_restart(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"miku": "https://example.com/v1.zip"}), encoding="utf-8")
    monkeypatch.setenv("SING_VOICES_FILE", str(tmp_path / "voices.json"))
    monkeypatch.setenv("SING_VOICES_CATALOG_FILE", str(catalog))
    assert resolve_voice("miku").url == "https://example.com/v1.zip"
    catalog.write_text(
        json.dumps({"miku": "https://example.com/second-version.zip"}), encoding="utf-8"
    )
    stat = catalog.stat()
    os.utime(catalog, ns=(stat.st_atime_ns + 10**9, stat.st_mtime_ns + 10**9))
    assert resolve_voice("miku").url == "https://example.com/second-version.zip"


def test_plaza_url_env_override(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SING_VOICE_PLAZA_URL", raising=False)
    assert plaza_url() == "https://voice-models.flowbee.top"
    monkeypatch.setenv("SING_VOICE_PLAZA_URL", "https://example.com/voices")
    assert plaza_url() == "https://example.com/voices"


def test_plugin_list_voices_points_to_plaza(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    monkeypatch.delenv("SING_VOICE_PLAZA_URL", raising=False)
    payload = json.loads(module.list_voices())
    assert payload["plaza_url"] == "https://voice-models.flowbee.top"
    assert "voices" not in payload
    assert "note" in payload


def test_sing_config_accepts_voice_override() -> None:
    module = _load()
    config = module._sing_config(
        {"voice_model_url": "https://example.com/base.zip"},
        voice_model_url="https://example.com/override.zip",
    )
    assert config.voice_model_url == "https://example.com/override.zip"


def test_sing_song_audio_block_is_pushable() -> None:
    block = {
        "type": "audio",
        "source": {"type": "url", "url": "https://example.com/cover.mp3"},
        "media_type": "audio/mpeg",
        "filename": "cover.mp3",
    }
    assert is_media_push_tool("sing_song")
    parsed = iter_media_blocks(json.dumps(block, ensure_ascii=False))
    assert parsed and parsed[0]["type"] == "audio"
    assert parsed[0]["source"]["url"] == "https://example.com/cover.mp3"


def test_cache_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    cfg = SingConfig(pitch=2, pitch_mode="no-change")
    key = cache_key("https://www.bilibili.com/video/BV1", 60, 25, cfg)
    root = tmp_path / "cache" / "sing-song"
    source = tmp_path / "generated.mp3"
    source.write_bytes(b"fake-mp3")
    cache_store(root, key, source, {"filename": "cover.mp3"}, 2)

    hit = cache_lookup(root, key, 168)
    assert hit is not None
    audio, meta = hit
    assert audio.read_bytes() == b"fake-mp3"
    assert meta["filename"] == "cover.mp3"


def test_cache_eviction_keeps_newest(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    root = tmp_path / "cache" / "sing-song"
    cfg = SingConfig(pitch=2, pitch_mode="no-change")
    for index in range(3):
        key = cache_key(f"https://example.com/{index}", 60, 25, cfg)
        source = tmp_path / f"source{index}.mp3"
        source.write_bytes(b"x")
        cache_store(root, key, source, {"filename": f"{index}.mp3"}, 2)
        os.utime(root / f"{key}.mp3", (1000 + index, 1000 + index))
    assert len(list(root.glob("*.mp3"))) == 2
