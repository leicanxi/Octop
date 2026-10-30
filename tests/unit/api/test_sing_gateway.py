"""Contract tests for the OpenAI-compatible singing gateway."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from octop.api.gateway import sing


def _client() -> TestClient:
    # https base URL so Secure cookies are sent by the test client
    return TestClient(sing.app, base_url="https://testserver")


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
    monkeypatch.setenv("SING_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("SING_SSO_SECRET", "test-sso-secret")
    monkeypatch.setenv("SING_JWT_TTL_HOURS", "24")
    monkeypatch.setenv("SING_PLAZA_BASE", "https://plaza.example")
    monkeypatch.delenv("SING_REVIEW_BASE_URL", raising=False)
    monkeypatch.delenv("SING_REVIEW_API_KEY", raising=False)
    sing._recent_submissions.clear()


def _session(username: str = "alice", uid: int = 7) -> dict[str, str]:
    from octop.api.gateway import sso

    token = sso.issue_session(sso.Identity(uid=uid, username=username, name=username.title()))
    return {"sing_session": token}


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
        data={"label": "My Voice"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
        cookies=_session(),
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
    assert status.json()["flowbee_uid"] == 7


def test_submit_link_uses_source_url(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)

    def fake_download(url: str, dest, max_bytes: int) -> None:
        dest.write_bytes(_model_zip_bytes())

    monkeypatch.setattr(sing, "_download_zip", fake_download)
    response = _client().post(
        "/v1/voice-requests",
        json={"label": "Linked Voice", "url": "https://huggingface.co/a/b/resolve/main/m.zip"},
        cookies=_session("bob", 8),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert voices[body["name"]]["url"] == "https://huggingface.co/a/b/resolve/main/m.zip"
    assert voices[body["name"]]["added_by"] == "bob"


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
        data={"label": "Evil"},
        files={"file": ("model.zip", buffer.getvalue(), "application/zip")},
        cookies=_session("eve", 9),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "posix.system" in body["reason"]
    assert not (tmp_path / "voices.json").exists()


def test_submit_requires_login(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "No Login"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
    )
    assert response.status_code == 401


def test_submit_rejects_non_public_link(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        json={"label": "SSRF", "url": "http://127.0.0.1:9001/health"},
        cookies=_session("carol", 10),
    )
    assert response.status_code == 400
    assert "公网" in response.json()["detail"]


def test_status_endpoint_404(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().get("/v1/voice-requests/vr-missing")
    assert response.status_code == 404


def test_submit_upload_uses_huggingface_storage(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SING_UPLOAD_DIR", raising=False)
    monkeypatch.delenv("SING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SING_HF_REPO", "leicanxi/rvc-models")
    monkeypatch.setenv("SING_HF_TOKEN", "hf_test")
    captured: dict[str, str] = {}

    def fake_upload(local_path, *, repo_id: str, path_in_repo: str, token: str) -> str:
        captured.update(
            {
                "repo_id": repo_id,
                "path_in_repo": path_in_repo,
                "token": token,
                "exists": str(local_path),
            }
        )
        return f"https://huggingface.co/{repo_id}/resolve/main/{path_in_repo}"

    monkeypatch.setattr(sing, "hf_upload", fake_upload)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "HF Voice"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
        cookies=_session("dave", 11),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["name"].startswith("hf-voice-")
    assert captured["repo_id"] == "leicanxi/rvc-models"
    assert captured["path_in_repo"] == f"uploads/{body['name']}.zip"
    assert captured["token"] == "hf_test"
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert voices[body["name"]]["source"] == "hf"
    assert voices[body["name"]]["url"].startswith("https://huggingface.co/")


def test_submit_file_requires_storage(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SING_UPLOAD_DIR", raising=False)
    monkeypatch.delenv("SING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("SING_HF_REPO", raising=False)
    monkeypatch.delenv("SING_HF_TOKEN", raising=False)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "No Storage"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
        cookies=_session("erin", 12),
    )
    assert response.status_code == 503
    assert "存储" in response.json()["detail"]


def test_submit_upload_uses_modelscope_storage(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SING_UPLOAD_DIR", raising=False)
    monkeypatch.delenv("SING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("SING_HF_REPO", raising=False)
    monkeypatch.delenv("SING_HF_TOKEN", raising=False)
    monkeypatch.setenv("SING_MS_REPO", "leicanxi/rvc-voice-uploads")
    monkeypatch.setenv("SING_MS_TOKEN", "ms_test")
    captured: dict[str, str] = {}

    def fake_upload(local_path, *, repo_id: str, path_in_repo: str, token: str) -> str:
        captured.update({"repo_id": repo_id, "path_in_repo": path_in_repo, "token": token})
        return f"https://modelscope.cn/models/{repo_id}/resolve/master/{path_in_repo}"

    monkeypatch.setattr(sing, "modelscope_upload", fake_upload)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "MS Voice"},
        files={"file": ("model.zip", _model_zip_bytes(), "application/zip")},
        cookies=_session("frank", 13),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert captured["repo_id"] == "leicanxi/rvc-voice-uploads"
    assert captured["path_in_repo"] == f"uploads/{body['name']}.zip"
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert voices[body["name"]]["source"] == "ms"
    assert voices[body["name"]]["url"].startswith("https://modelscope.cn/")


def test_auth_login_redirects_to_sso(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().get("/v1/auth/login?next=/upload.html", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://flowbee.top/sso/octop?state=")
    state = location.split("state=", 1)[1]
    cookie = response.cookies.get("sing_sso")
    assert cookie == f"{state}.upload"


def test_auth_callback_issues_session(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    import time

    import jwt as pyjwt

    client = _client()
    login = client.get("/v1/auth/login?next=/upload.html", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    code = pyjwt.encode(
        {
            "sub": "7",
            "username": "alice",
            "name": "Alice",
            "exp": int(time.time()) + 300,
        },
        "test-sso-secret",
        algorithm="HS256",
    )
    callback = client.get(f"/v1/auth/callback?code={code}&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "https://plaza.example/upload.html"

    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {"uid": 7, "username": "alice", "name": "Alice"}


def test_auth_callback_rejects_state_mismatch(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    import time

    import jwt as pyjwt

    client = _client()
    client.get("/v1/auth/login?next=/upload.html", follow_redirects=False)
    code = pyjwt.encode(
        {"sub": "7", "username": "alice", "exp": int(time.time()) + 300},
        "test-sso-secret",
        algorithm="HS256",
    )
    callback = client.get(
        f"/v1/auth/callback?code={code}&state=wrong-state", follow_redirects=False
    )
    assert callback.status_code == 302
    assert "login=failed" in callback.headers["location"]


def test_auth_callback_rejects_expired_code(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    import time

    import jwt as pyjwt

    client = _client()
    login = client.get("/v1/auth/login?next=/upload.html", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    code = pyjwt.encode(
        {"sub": "7", "username": "alice", "exp": int(time.time()) - 3600},
        "test-sso-secret",
        algorithm="HS256",
    )
    callback = client.get(f"/v1/auth/callback?code={code}&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    assert "login=failed" in callback.headers["location"]


def test_me_requires_login(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    assert _client().get("/v1/auth/me").status_code == 401


def test_my_requests_filters_by_uid(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    from octop.infra.music import submissions as subs

    subs.save_request(
        subs.build_request_record(
            request_id="vr-a",
            name="a-aaaa",
            label="A",
            url="u",
            flowbee_user="alice",
            source="upload",
            approved=True,
            reason="ok",
            flowbee_uid=7,
        )
    )
    subs.save_request(
        subs.build_request_record(
            request_id="vr-b",
            name="b-bbbb",
            label="B",
            url="u",
            flowbee_user="bob",
            source="upload",
            approved=True,
            reason="ok",
            flowbee_uid=8,
        )
    )
    response = _client().get("/v1/voice-requests/mine", cookies=_session())
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["submissions"]] == ["vr-a"]


def test_delete_requires_owner(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    from octop.infra.music import submissions as subs

    subs.save_request(
        subs.build_request_record(
            request_id="vr-b",
            name="b-bbbb",
            label="B",
            url="u",
            flowbee_user="bob",
            source="upload",
            approved=True,
            reason="ok",
            flowbee_uid=8,
        )
    )
    response = _client().delete("/v1/voice-requests/vr-b", cookies=_session())
    assert response.status_code == 404


def test_delete_removes_whitelist_and_file(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    from octop.infra.music import submissions as subs

    name = "mine-1a2b"
    (tmp_path / "voices.json").write_text(
        json.dumps({name: {"label": "Mine", "url": "u"}}), encoding="utf-8"
    )
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / f"{name}.zip").write_bytes(b"zip")
    subs.save_request(
        subs.build_request_record(
            request_id="vr-a",
            name=name,
            label="Mine",
            url="u",
            flowbee_user="alice",
            source="upload",
            approved=True,
            reason="ok",
            flowbee_uid=7,
        )
    )
    response = _client().delete("/v1/voice-requests/vr-a", cookies=_session())
    assert response.status_code == 200
    assert response.json()["name"] == name
    assert name not in json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert not (uploads / f"{name}.zip").exists()
    record = subs.get_request("vr-a")
    assert record is not None and record["status"] == "deleted"


def test_origin_check_blocks_foreign_host(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _submission_env(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/voice-requests",
        data={"label": "X"},
        headers={"origin": "https://evil.example"},
        cookies=_session(),
    )
    assert response.status_code == 403
