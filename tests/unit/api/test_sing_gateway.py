"""Contract tests for the OpenAI-compatible singing gateway."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from octop.api.gateway import sing


def _client() -> TestClient:
    return TestClient(sing.app)


def test_health() -> None:
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lists_sing_song_model(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    response = _client().get("/v1/models", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids == ["sing-song", "sing-voices"]


def test_voices_endpoint_points_to_plaza(monkeypatch: MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"keine": "https://example.com/keine.zip"}), encoding="utf-8")
    monkeypatch.setenv("SING_VOICES_FILE", str(path))
    response = _client().get("/v1/voices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["plaza_url"] == "https://voice-models.flowbee.top"
    assert "音色广场" in body["note"]


def test_config_resolves_whitelist_voice(monkeypatch: MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "voices.json"
    path.write_text(
        json.dumps({"cirno": {"label": "Cirno", "url": "https://example.com/cirno.zip"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SING_VOICES_FILE", str(path))
    config = sing._config({"voice": "cirno"})
    assert config.voice_model_url == "https://example.com/cirno.zip"


def test_requires_gateway_token(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    response = _client().post(
        "/v1/chat/completions",
        json={"model": "sing-song", "messages": [{"role": "user", "content": "雨爱"}]},
    )
    assert response.status_code == 401


def test_generates_audio_block(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    captured: dict[str, object] = {}

    def fake_render(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {
            "type": "audio",
            "source": {"type": "url", "url": "https://example.com/a.mp3"},
            "media_type": "audio/mpeg",
            "filename": "a.mp3",
        }

    monkeypatch.setattr(sing, "_render", fake_render)
    response = _client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={
            "model": "sing-song",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps({"song": "雨爱", "duration": 25}, ensure_ascii=False),
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "sing-song"
    content = json.loads(body["choices"][0]["message"]["content"])
    assert content["type"] == "audio"
    assert content["source"]["url"] == "https://example.com/a.mp3"
    assert captured["song"] == "雨爱"
    assert captured["duration"] == 25


def test_plain_song_content(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    captured: dict[str, object] = {}

    def fake_render(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {
            "type": "audio",
            "source": {"type": "url", "url": "https://example.com/a.mp3"},
            "media_type": "audio/mpeg",
            "filename": "a.mp3",
        }

    monkeypatch.setattr(sing, "_render", fake_render)
    response = _client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"model": "sing-song", "messages": [{"role": "user", "content": "雨爱"}]},
    )
    assert response.status_code == 200
    assert captured["song"] == "雨爱"


def test_voices_model_returns_plaza_guide_without_rendering(
    monkeypatch: MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"keine": "https://example.com/keine.zip"}), encoding="utf-8")
    monkeypatch.setenv("SING_VOICES_FILE", str(path))

    def fail_render(payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("voices model must not render")

    monkeypatch.setattr(sing, "_render", fail_render)
    response = _client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"model": "sing-voices", "messages": []},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "sing-voices"
    assert body["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    content = json.loads(body["choices"][0]["message"]["content"])
    assert content["type"] == "guide"
    assert content["plaza_url"] == "https://voice-models.flowbee.top"
    assert "音色广场" in content["text"]


def test_unknown_voice_does_not_enumerate(monkeypatch: MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"cirno": "https://example.com/cirno.zip"}), encoding="utf-8")
    monkeypatch.setenv("SING_VOICES_FILE", str(path))
    response = _client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={
            "model": "sing-song",
            "messages": [
                {"role": "user", "content": json.dumps({"song": "雨爱", "voice": "miku"})}
            ],
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "unknown voice 'miku'" in detail
    assert "cirno" not in detail  # never enumerate the whitelist
    assert "voice-models.flowbee.top" in detail


def test_config_resolves_catalog_voice(monkeypatch: MonkeyPatch, tmp_path) -> None:
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
    config = sing._config({"voice": "hatsune-miku"})
    assert config.voice_model_url == "https://huggingface.co/a/b/resolve/main/miku.zip"


def test_music_source_env_and_payload(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_MUSIC_SOURCE", "netease")
    assert sing._config({}).music_source == "netease"
    assert sing._config({"source": "bilibili"}).music_source == "bilibili"
    monkeypatch.delenv("SING_MUSIC_SOURCE")
    assert sing._config({}).music_source == "bilibili"


def test_client_song_error_maps_to_400(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_GATEWAY_TOKEN", "secret")

    def fail_render(payload: dict[str, object]) -> dict[str, object]:
        raise sing.ClientSongError("网易云没有返回音频直链：该歌曲可能需要 VIP")

    monkeypatch.setattr(sing, "_render", fail_render)
    response = _client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"model": "sing-song", "messages": [{"role": "user", "content": "晴天"}]},
    )
    assert response.status_code == 400
    assert "VIP" in response.json()["detail"]
