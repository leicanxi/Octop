"""OpenAI-compatible gateway for the ``sing-song`` new-api model.

Run it next to Octop and point a new-api custom channel at ``/v1``:

    SING_GATEWAY_TOKEN=... REPLICATE_API_TOKEN=... \\
        octop-sing-gateway --host 127.0.0.1 --port 9001

new-api bills the user per call; this gateway only performs the generation and
returns the standard audio block as the assistant message content.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
import uuid
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from octop.infra.music import (
    DEFAULT_REPLICATE_BASE,
    SingConfig,
    render_song,
    render_song_by_query,
    resolve_voice,
    voice_catalog,
)

app = FastAPI(title="Octop Sing Gateway", version="0.1.0")

_DEFAULT_MODEL = "sing-song"


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""


class ChatCompletionRequest(BaseModel):
    model: str = _DEFAULT_MODEL
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False


def _gateway_token() -> str:
    return os.environ.get("SING_GATEWAY_TOKEN", "").strip()


def _require_token(authorization: str | None = Header(default=None)) -> None:
    expected = _gateway_token()
    if not expected:
        raise HTTPException(status_code=503, detail="SING_GATEWAY_TOKEN is not configured")
    supplied = (authorization or "").strip()
    if not hmac.compare_digest(supplied, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="invalid gateway token")


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _request_payload(body: ChatCompletionRequest) -> dict[str, Any]:
    user_messages = [message for message in body.messages if message.role == "user"]
    content = user_messages[-1].content.strip() if user_messages else ""
    if not content:
        raise HTTPException(status_code=400, detail="messages must contain a user message")
    if content.startswith("{"):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"song": content}


def _voice_url(payload: dict[str, Any]) -> str:
    requested = str(payload.get("voice") or "").strip()
    if requested:
        spec = resolve_voice(requested)
        if spec is None:
            available = ", ".join(item["name"] for item in voice_catalog())
            raise HTTPException(
                status_code=400,
                detail=f"unknown voice {requested!r}; available: {available}",
            )
        return spec.url
    configured = os.environ.get("SING_VOICE_URL", "").strip()
    if configured:
        return configured
    spec = resolve_voice("")
    if spec is None:
        raise HTTPException(status_code=503, detail="no voice model is configured")
    return spec.url


def _config(payload: dict[str, Any]) -> SingConfig:
    return SingConfig(
        voice_model_url=_voice_url(payload),
        model=os.environ.get("SING_REPLICATE_MODEL", "zsxkib/realistic-voice-cloning"),
        model_version=os.environ.get("SING_REPLICATE_VERSION", ""),
        replicate_base_url=os.environ.get("SING_REPLICATE_BASE_URL", DEFAULT_REPLICATE_BASE),
        pitch=_as_int(
            payload.get("pitch"),
            _as_int(os.environ.get("SING_PITCH"), 2, minimum=-24, maximum=24),
            minimum=-24,
            maximum=24,
        ),
        pitch_mode=str(payload.get("pitch_mode") or "no-change"),
        start=_as_int(
            payload.get("start"),
            _as_int(os.environ.get("SING_START"), 60, minimum=0, maximum=3600),
            minimum=0,
            maximum=3600,
        ),
        duration=_as_int(
            payload.get("duration"),
            _as_int(os.environ.get("SING_DURATION"), 25, minimum=5, maximum=30),
            minimum=5,
            maximum=30,
        ),
        cache_ttl_hours=_as_float(
            os.environ.get("SING_CACHE_TTL_HOURS"), 168.0, minimum=0.0, maximum=8760.0
        ),
        cache_max_entries=_as_int(
            os.environ.get("SING_CACHE_MAX_ENTRIES"), 50, minimum=0, maximum=500
        ),
    )


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    config = _config(payload)
    url = str(payload.get("url") or "").strip()
    song = str(payload.get("song") or "").strip()
    if url:
        result = render_song(url, config)
    elif song:
        result = render_song_by_query(song, config)
    else:
        raise HTTPException(status_code=400, detail="url or song is required")
    return result.as_block()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models", dependencies=[Depends(_require_token)])
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": _DEFAULT_MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "octop",
            }
        ],
    }


@app.get("/v1/voices", dependencies=[Depends(_require_token)])
def list_voices() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": item["name"], "label": item["label"]} for item in voice_catalog()],
        "note": "自定义 RVC 音色需要管理员审核后加入白名单；用户不能直接传链接。",
    }


@app.post("/v1/chat/completions", dependencies=[Depends(_require_token)])
async def chat_completions(body: ChatCompletionRequest) -> dict[str, Any]:
    payload = _request_payload(body)
    try:
        block = await asyncio.to_thread(_render, payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "id": f"chatcmpl-sing-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model or _DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(block, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def main() -> None:
    uvicorn.run(
        "octop.api.gateway.sing:app",
        host=os.environ.get("SING_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("SING_GATEWAY_PORT", "9001")),
        log_level=os.environ.get("SING_GATEWAY_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
