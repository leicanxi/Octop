"""Voice submission review, records and whitelist writes."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from pytest import MonkeyPatch

from octop.infra.music import submissions as subs


def _zip_with(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def test_review_approves_simple_rvc_zip(tmp_path: Path) -> None:
    archive = _zip_with(tmp_path / "model.zip", {"model.pth": b"stub-weights"})
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert result.approved
    assert result.summary["pth_count"] == 1
    assert result.summary["index_count"] == 0


def test_review_counts_index_files(tmp_path: Path) -> None:
    archive = _zip_with(
        tmp_path / "model.zip",
        {"model.pth": b"stub", "added_IVF256.index": b"stub-index"},
    )
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert result.approved
    assert result.summary["index_count"] == 1


def test_review_rejects_missing_pth(tmp_path: Path) -> None:
    archive = _zip_with(tmp_path / "model.zip", {"readme.txt": b"hello"})
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert not result.approved
    assert ".pth" in result.reason


def test_review_rejects_script_members(tmp_path: Path) -> None:
    archive = _zip_with(tmp_path / "model.zip", {"model.pth": b"stub", "run.py": b"print(1)"})
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert not result.approved
    assert "run.py" in result.reason


def test_review_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _zip_with(tmp_path / "model.zip", {"../evil.pth": b"stub"})
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert not result.approved
    assert "非法路径" in result.reason


def test_review_rejects_oversize(tmp_path: Path) -> None:
    archive = _zip_with(tmp_path / "model.zip", {"model.pth": b"x" * 2048})
    result = subs.review_archive(archive, max_bytes=1024)
    assert not result.approved
    assert "大小限制" in result.reason


def test_scan_pickle_flags_dangerous_globals() -> None:
    assert subs.scan_pickle(b"cposix\nsystem\n.") is not None
    assert subs.scan_pickle(b"csubprocess\nPopen\n.") is not None
    assert subs.scan_pickle(b"ctorch._utils\n_rebuild_tensor_v2\n.") is None
    assert subs.scan_pickle(b"") is None


def test_review_rejects_dangerous_torch_payload(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as payload:
        payload.writestr("archive/data.pkl", b"cposix\nsystem\n.")
    archive = _zip_with(tmp_path / "model.zip", {"model.pth": inner.getvalue()})
    result = subs.review_archive(archive, max_bytes=10 * 1024 * 1024)
    assert not result.approved
    assert "posix.system" in result.reason


def test_llm_review_disabled_without_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(subs.REVIEW_BASE_ENV, raising=False)
    monkeypatch.delenv(subs.REVIEW_KEY_ENV, raising=False)
    decision, reason = subs.llm_review({"label": "x"})
    assert decision is None
    assert "未配置" in reason


def test_request_records_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(subs.REQUESTS_FILE_ENV, str(tmp_path / "requests.json"))
    record = subs.build_request_record(
        request_id="vr-1",
        name="mine-1a2b",
        label="Mine",
        url="https://example.com/m.zip",
        flowbee_user="alice",
        source="upload",
        approved=True,
        reason="ok",
    )
    subs.save_request(record)
    stored = subs.get_request("vr-1")
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["flowbee_user"] == "alice"
    assert subs.get_request("missing") is None


def test_add_voice_preserves_other_entries(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    target = tmp_path / "voices.json"
    target.write_text(json.dumps({"keine": "https://example.com/k.zip"}), encoding="utf-8")
    monkeypatch.setenv("SING_VOICES_FILE", str(target))
    subs.add_voice("mine-1a2b", "Mine", "https://example.com/m.zip", extra={"added_by": "alice"})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["mine-1a2b"]["url"] == "https://example.com/m.zip"
    assert data["mine-1a2b"]["added_by"] == "alice"
    assert data["keine"] == "https://example.com/k.zip"


def test_generate_name_has_random_suffix_and_avoids_taken() -> None:
    name = subs.generate_name("我的音色", set())
    base, suffix = name.rsplit("-", 1)
    assert base == "我的音色"
    assert len(suffix) == 4
    assert subs.generate_name("x", set()) != subs.generate_name("x", set())


def test_valid_flowbee_user() -> None:
    assert subs.valid_flowbee_user("alice_01")
    assert subs.valid_flowbee_user("user@example.com")
    assert not subs.valid_flowbee_user("")
    assert not subs.valid_flowbee_user("bad name")
    assert not subs.valid_flowbee_user("x" * 65)
