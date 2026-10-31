"""Bundled ``sing-song`` plugin surface (thin HTTP client for sing-gateway)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from octop_harness.plugins.context import PluginContext
from octop_harness.plugins.manifest import PluginManifest
from pytest import MonkeyPatch

from octop.infra.gateway.media.tool_media import is_media_push_tool, iter_media_blocks

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


def _patch_connection(module: ModuleType, monkeypatch: MonkeyPatch) -> dict[str, Any]:
    """Stub the gateway connection so tools never touch the network."""
    seen: dict[str, Any] = {}

    def fake_connection(cfg: dict[str, Any]) -> tuple[str, str]:
        seen["cfg"] = dict(cfg)
        return "https://gateway.example/v1", "token-1"

    monkeypatch.setattr(module, "_connection", fake_connection)
    return seen


def test_setup_registers_expected_tools() -> None:
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
        "confirm_song",
        "list_voices",
        "sing_song",
    ]
    assert all(tool.description for tool in loaded.tools)


def test_plugin_no_longer_imports_the_music_pipeline() -> None:
    source = _PLUGIN.read_text(encoding="utf-8")
    assert "octop.infra.music" not in source
    for symbol in ("render_song", "search_songs", "resolve_voice", "SingConfig"):
        assert symbol not in source


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


def test_list_voices_points_to_plaza(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "_cfg", lambda _name: {})
    payload = json.loads(module.list_voices())
    assert payload["plaza_url"] == "https://voice-models.flowbee.top"
    assert "voices" not in payload
    assert "note" in payload


def test_confirm_song_calls_selfhosted_endpoint(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    _patch_connection(module, monkeypatch)
    monkeypatch.setattr(
        module,
        "_cfg",
        lambda _name: {"billing_mode": "selfhosted", "gateway_base_url": "https://gw", "limit": 2},
    )
    seen: dict[str, Any] = {}

    async def fake_post(base_url: str, token: str, path: str, payload: dict[str, Any]):
        seen.update({"base_url": base_url, "token": token, "path": path, "payload": payload})
        return {
            "data": [{"title": "起风了", "artist": "原唱", "duration_seconds": 327, "url": "u"}]
        }

    monkeypatch.setattr(module, "_post", fake_post)
    import asyncio

    payload = json.loads(asyncio.run(module.confirm_song("起风了")))
    assert payload["candidates"][0]["title"] == "起风了"
    assert seen["path"] == "songs/search"
    assert seen["payload"] == {"query": "起风了", "limit": 2}


def test_confirm_song_uses_sing_search_model_in_flowbee_mode(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load()
    _patch_connection(module, monkeypatch)
    monkeypatch.setattr(module, "_cfg", lambda _name: {"billing_mode": "flowbee"})
    seen: dict[str, Any] = {}

    async def fake_chat(base_url: str, token: str, model: str, payload: dict[str, Any]):
        seen.update({"model": model, "payload": payload})
        return {"type": "candidates", "data": [{"title": "晴天", "url": "u"}]}

    monkeypatch.setattr(module, "_chat", fake_chat)
    import asyncio

    payload = json.loads(asyncio.run(module.confirm_song("晴天")))
    assert seen["model"] == "sing-search"
    assert seen["payload"] == {"query": "晴天", "limit": 3}
    assert payload["candidates"][0]["title"] == "晴天"


def test_sing_song_passes_clip_parameters(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    patch = _patch_connection(module, monkeypatch)
    monkeypatch.setattr(
        module,
        "_cfg",
        lambda _name: {"billing_mode": "flowbee", "default_voice": "cirno", "duration": 30},
    )
    seen: dict[str, Any] = {}

    async def fake_chat(base_url: str, token: str, model: str, payload: dict[str, Any]):
        seen.update({"model": model, "payload": payload})
        return {"type": "audio", "source": {"type": "url", "url": "https://a/cover.mp3"}}

    monkeypatch.setattr(module, "_chat", fake_chat)
    import asyncio

    payload = json.loads(asyncio.run(module.sing_song(song="起风了", start=90, pitch=4)))
    assert seen["model"] == "sing-song"
    assert seen["payload"]["voice"] == "cirno"
    assert seen["payload"]["start"] == 90
    assert seen["payload"]["duration"] == 30
    assert seen["payload"]["pitch"] == 4
    assert payload["source"]["url"] == "https://a/cover.mp3"
    assert patch["cfg"]["billing_mode"] == "flowbee"


def test_sing_song_reports_gateway_errors(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    _patch_connection(module, monkeypatch)
    monkeypatch.setattr(module, "_cfg", lambda _name: {"billing_mode": "flowbee"})

    async def fake_chat(base_url: str, token: str, model: str, payload: dict[str, Any]):
        raise RuntimeError("网关请求失败：HTTP 400 该歌曲可能需要 VIP")

    monkeypatch.setattr(module, "_chat", fake_chat)
    import asyncio

    payload = json.loads(asyncio.run(module.sing_song(song="晴天")))
    assert "VIP" in payload["error"]


def test_sing_song_requires_url_or_song(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "_cfg", lambda _name: {})
    import asyncio

    payload = json.loads(asyncio.run(module.sing_song()))
    assert "url or song" in payload["error"]


def test_selfhosted_mode_requires_gateway_config(monkeypatch: MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "_cfg", lambda _name: {"billing_mode": "selfhosted"})
    import asyncio

    payload = json.loads(asyncio.run(module.sing_song(song="起风了")))
    assert "网关地址" in payload["error"]
