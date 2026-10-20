"""Minimal Replicate client for the AICoverGen voice-conversion model."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from octop.infra.music.config import PITCH_MODES, SingConfig

logger = logging.getLogger(__name__)


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def upload_file(base_url: str, token: str, path: Path) -> str:
    with httpx.Client(timeout=180.0) as client, path.open("rb") as handle:
        response = client.post(
            f"{base_url}/files",
            headers={"Authorization": f"Bearer {token}"},
            files={"content": (path.name, handle, "audio/mpeg")},
        )
    response.raise_for_status()
    url = (response.json().get("urls") or {}).get("get")
    if not url:
        raise RuntimeError("replicate file upload returned no url")
    return str(url)


def _create_prediction(
    client: httpx.Client,
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> str:
    for _ in range(8):
        response = client.post(f"{base_url}/predictions", json=payload, headers=headers)
        if response.status_code == 429:
            wait = int(response.headers.get("retry_after") or 10)
            logger.info("replicate throttled, waiting %ss", wait)
            time.sleep(wait + 2)
            continue
        response.raise_for_status()
        return str(response.json()["id"])
    raise RuntimeError("replicate prediction stayed rate-limited")


def predict_cover(base_url: str, token: str, audio_url: str, cfg: SingConfig) -> str:
    """Run the cover model and return the generated audio URL."""
    version = cfg.model_version.strip()
    timeout = float(cfg.prediction_timeout)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pitch_mode = cfg.pitch_mode if cfg.pitch_mode in PITCH_MODES else "no-change"
    payload_input = {
        "song_input": audio_url,
        "rvc_model": "CUSTOM",
        "custom_rvc_model_download_url": cfg.voice_model_url,
        "pitch_change": pitch_mode,
        "pitch_change_all": _clamp_int(cfg.pitch, -24, 24),
        "index_rate": _clamp_float(cfg.index_rate, 0.0, 1.0),
        "filter_radius": _clamp_int(cfg.filter_radius, 0, 7),
        "rms_mix_rate": _clamp_float(cfg.rms_mix_rate, 0.0, 1.0),
        "protect": _clamp_float(cfg.protect, 0.0, 0.5),
        "main_vocals_volume_change": 0,
        "backup_vocals_volume_change": -60,
        "instrumental_volume_change": -60,
        "reverb_size": 0.15,
        "reverb_wetness": 0.0,
        "reverb_dryness": 1.0,
        "reverb_damping": 0.7,
        "output_format": "mp3",
    }
    with httpx.Client(timeout=120.0, headers=headers) as client:
        if not version:
            response = client.get(f"{base_url}/models/{cfg.model}")
            response.raise_for_status()
            version = str(response.json()["latest_version"]["id"])
        prediction_id = _create_prediction(
            client,
            base_url,
            {"version": version, "input": payload_input},
            headers,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(2)
            response = client.get(f"{base_url}/predictions/{prediction_id}")
            response.raise_for_status()
            job = response.json()
            status = str(job.get("status") or "")
            if status == "succeeded":
                output = job.get("output")
                url = output[-1] if isinstance(output, list) and output else output
                if not url:
                    raise RuntimeError("replicate returned no output")
                return str(url)
            if status in {"failed", "canceled"}:
                logs = str(job.get("logs") or "")[-800:]
                raise RuntimeError(job.get("error") or f"prediction {status}: {logs}")
    raise RuntimeError("prediction timed out")
