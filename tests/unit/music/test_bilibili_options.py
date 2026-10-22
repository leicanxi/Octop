"""Env-driven yt-dlp options for Bilibili downloads (cookies / proxy)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from octop.infra.music import bilibili


def test_download_options_defaults(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SING_BILI_COOKIES", raising=False)
    monkeypatch.delenv("SING_PROXY", raising=False)
    options = bilibili._download_options(tmp_path)
    assert "cookies" not in options
    assert "proxy" not in options
    assert bilibili._proxy() is None


def test_download_options_with_cookies(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("SING_BILI_COOKIES", str(cookie_file))
    monkeypatch.delenv("SING_PROXY", raising=False)
    options = bilibili._download_options(tmp_path)
    assert options["cookies"] == str(cookie_file)


def test_download_options_missing_cookies_file(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SING_BILI_COOKIES", str(tmp_path / "nope.txt"))
    with pytest.raises(RuntimeError, match="SING_BILI_COOKIES"):
        bilibili._download_options(tmp_path)


def test_download_options_proxy(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SING_BILI_COOKIES", raising=False)
    monkeypatch.setenv("SING_PROXY", "http://127.0.0.1:7890")
    options = bilibili._download_options(tmp_path)
    assert options["proxy"] == "http://127.0.0.1:7890"
    assert bilibili._proxy() == "http://127.0.0.1:7890"
