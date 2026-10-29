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


def _submission_env(monkeypatch: MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SING_VOICES_FILE", str(tmp_path / "voices.json"))
    monkeypatch.setenv("SING_VOICE_REQUESTS_FILE", str(tmp_path / "requests.json"))
    monkeypatch.setenv("SING_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SING_PUBLIC_BASE_URL", "https://plaza.example")
    monkeypatch.setenv("SING_UPLOAD_DAILY_LIMIT", "100")
    monkeypatch.delenv("SING_REVIEW_BASE_URL", raising=False)
    monkeypatch.delenv("SING_REVIEW_API_KEY", raising=False)
    sing._recent_submissions.clear()


def _model_zip_bytes() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model.pth", b"stub-weights")
    return buffer.getvalue()


def test_submit_upload_auto_approves_and_registers_voice(
    monkeypatch: MonkeyPatch, tmp_path
) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "My Voice", "flowbee_user": "alice"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["name"].startswith("my-voice-")
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    entry = voices[body["name"]]
    assert entry["label"] == "My Voice"
    assert entry["added_by"] == "alice"
    assert entry["url"] == f"https://plaza.example/uploads/{body['name']}.zip"
    assert (tmp_path / "uploads" / f"{body['name']}.zip").exists()

    status = _client().get(body["status_path"])
    assert status.status_code == 200
    assert status.json()["flowbee_user"] == "alice"


def test_submit_link_uses_source_url(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)

    def fake_download(url: str, dest, max_bytes: int) -> None:
        dest.write_bytes(_model_zip_bytes())

    monkeypatch.setattr(sing, "_download_zip", fake_download)
    response = _client().post(
        "/v1/voice-requests",
        json={
            "label": "Linked Voice",
            "flowbee_user": "bob",
            "url": "https://huggingface.co/a/b/resolve/main/m.zip",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert voices[body["name"]]["url"] == "https://huggingface.co/a/b/resolve/main/m.zip"


def test_submit_rejects_unsafe_pickle(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    import io
    import zipfile

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as payload:
        payload.writestr("archive/data.pkl", b"cposix\nsystem\n.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model.pth", inner.getvalue())
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "Evil", "flowbee_user": "eve"},
        files={"file": ("model.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "posix.system" in body["reason"]
    assert not (tmp_path / "voices.json").exists()


def test_submit_requires_flowbee_user(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "No User"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
    )
    assert response.status_code == 400
    assert "FlowBee" in response.json()["detail"]


def test_submit_rejects_non_public_link(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        json={
            "label": "SSRF",
            "flowbee_user": "carol",
            "url": "http://127.0.0.1:9001/health",
        },
    )
    assert response.status_code == 400
    assert "公网" in response.json()["detail"]


def test_status_endpoint_404(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().get("/v1/voice-requests/vr-missing")
    assert response.status_code == 404
