"""Singing tools for chat agents — an HTTP client for a ``sing-gateway`` instance.

Two routing modes:

- ``flowbee`` — an OpenAI-compatible ``sing-song`` model on new-api / FlowBee,
  billed per call on the acting user's own key (the key comes from the user's
  personal model settings, injected by Octop for this run).
- ``selfhosted`` — a ``sing-gateway`` you (or an operator) run yourself, talked
  to with a base URL and the gateway token.

The plugin never touches Replicate, the music sources, the voice whitelist or
the public voice plaza: the gateway owns all of that. User-facing phrasing
belongs to the agent persona; this module only returns data and audio blocks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langgraph.config import get_config
from octop_harness.plugins import PluginContext
from octop_harness.plugins.tools import get_tool_config

logger = logging.getLogger("octop.plugins.sing_song")

_SING_MODEL = "sing-song"
_SEARCH_MODEL = "sing-search"
_MODES = {"flowbee", "selfhosted"}
_PITCH_MODES = ("no-change", "male-to-female", "female-to-male")
# A cold prediction can take several minutes; keep the client wait above it.
_REQUEST_TIMEOUT = 900.0
_DEFAULT_PLAZA_URL = "https://voice-models.flowbee.top"


def _cfg(tool_name: str) -> dict[str, Any]:
    """Per-invocation tool config; defaults are applied by the callers."""
    try:
        return dict(get_tool_config(tool_name) or {})
    except Exception:
        logger.debug("plugin tool config unavailable for %s", tool_name, exc_info=True)
        return {}


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _pitch_mode(value: Any) -> str:
    mode = str(value or "no-change").strip()
    return mode if mode in _PITCH_MODES else "no-change"


def _gateway_mode(cfg: dict[str, Any]) -> str:
    mode = str(cfg.get("billing_mode") or "flowbee").strip().lower()
    return mode if mode in _MODES else "flowbee"


def _plugin_user_credential() -> dict[str, str] | None:
    """Read the acting user's model credential injected by Octop for this run."""
    try:
        configurable = dict(get_config().get("configurable") or {})
    except RuntimeError:
        return None
    raw = configurable.get("plugin_user_credential")
    if not isinstance(raw, dict):
        return None
    api_key = str(raw.get("api_key") or "").strip()
    if not api_key:
        return None
    return {
        "base_url": str(raw.get("base_url") or "").strip(),
        "api_key": api_key,
    }


def _connection(cfg: dict[str, Any]) -> tuple[str, str]:
    """Resolve ``(base_url, token)`` for the configured routing mode."""
    mode = _gateway_mode(cfg)
    if mode == "selfhosted":
        base_url = str(cfg.get("gateway_base_url") or "").strip()
        token = str(cfg.get("gateway_token") or "").strip()
        if not base_url:
            raise RuntimeError("未配置自托管网关地址")
        if not token:
            raise RuntimeError("未配置网关令牌")
    else:
        credential = _plugin_user_credential()
        if credential is None:
            raise RuntimeError("请先在个人设置里配置 FlowBee API Key")
        base_url = str(cfg.get("flowbee_base_url") or credential["base_url"] or "").strip()
        token = credential["api_key"]
        if not base_url:
            raise RuntimeError("未配置 FlowBee API 地址")
    return base_url.rstrip("/"), token


def _describe_error(exc: BaseException) -> str:
    """Readable failure text; httpx timeouts stringify to an empty string."""
    text = str(exc).strip()
    if text:
        return text
    if "Timeout" in type(exc).__name__:
        return f"网关在 {int(_REQUEST_TIMEOUT)} 秒内没有返回结果，请稍后重试"
    return f"网关请求中断（{type(exc).__name__}），请稍后重试"


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if isinstance(detail, dict):
            detail = detail.get("message")
        if detail:
            return str(detail)[:300]
    return response.text[:300]


def _plaza_url(cfg: dict[str, Any]) -> str:
    return str(cfg.get("plaza_url") or _DEFAULT_PLAZA_URL).strip().rstrip("/")


async def _post(base_url: str, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a JSON body to the gateway and return the parsed JSON object."""
    endpoint = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"网关请求失败：HTTP {response.status_code} {_error_detail(response)}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("网关返回内容不是 JSON") from exc
    if not isinstance(body, dict):
        raise RuntimeError("网关返回内容格式不正确")
    return body


async def _chat(base_url: str, token: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call the OpenAI-compatible chat endpoint and unwrap the JSON content."""
    request = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    }
    body = await _post(base_url, token, "chat/completions", request)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("网关返回格式不正确") from exc
    if isinstance(content, str):
        try:
            block = json.loads(content)
        except ValueError as exc:
            raise RuntimeError("网关返回内容不是 JSON") from exc
    elif isinstance(content, dict):
        block = content
    else:
        raise RuntimeError("网关返回内容格式不正确")
    if not isinstance(block, dict):
        raise RuntimeError("网关返回内容格式不正确")
    return block


async def search_song(query: str, limit: int | None = None) -> str:
    """Search the music source for a song.

    Returns candidates with title, artist, duration and URL. Ask the user to
    confirm or choose one when the match is ambiguous.
    """
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "query is required", "candidates": []}, ensure_ascii=False)
    cfg = _cfg("search_song")
    limit = _as_int(limit if limit is not None else cfg.get("limit"), 3, minimum=1, maximum=5)
    try:
        base_url, token = _connection(cfg)
        if _gateway_mode(cfg) == "selfhosted":
            body = await _post(base_url, token, "songs/search", {"query": query, "limit": limit})
            candidates = body.get("data") or []
        else:
            block = await _chat(base_url, token, _SEARCH_MODEL, {"query": query, "limit": limit})
            candidates = block.get("data") or []
        return json.dumps(
            {
                "candidates": candidates,
                "next": (
                    "If the top result clearly matches the request, call sing_song(url=...). "
                    "If it is ambiguous, ask the user to confirm the title/artist first."
                ),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("search_song failed")
        return json.dumps({"error": _describe_error(exc), "candidates": []}, ensure_ascii=False)


async def sing_song(
    url: str = "",
    song: str = "",
    voice: str = "",
    start: int | None = None,
    duration: int | None = None,
    pitch: int | None = None,
    pitch_mode: str | None = None,
) -> str:
    """Generate a short singing clip in the configured voice.

    Pass ``url`` from ``search_song`` after the user confirms, or ``song`` to
    auto-pick the first search result. Keep the default clip length unless the
    user asks for a different section or duration.
    """
    cfg = _cfg("sing_song")
    url = (url or "").strip()
    song = (song or "").strip()
    voice_name = (voice or str(cfg.get("default_voice") or "")).strip()
    if not url and not song:
        return json.dumps({"error": "url or song is required"}, ensure_ascii=False)
    start_default = _as_int(cfg.get("start"), 60, minimum=0, maximum=3600)
    duration_default = _as_int(cfg.get("duration"), 25, minimum=5, maximum=60)
    payload: dict[str, Any] = {
        "start": _as_int(
            start if start is not None else start_default, 60, minimum=0, maximum=3600
        ),
        "duration": _as_int(
            duration if duration is not None else duration_default,
            25,
            minimum=5,
            maximum=60,
        ),
        "pitch": _as_int(
            pitch if pitch is not None else cfg.get("pitch"), 2, minimum=-24, maximum=24
        ),
        "pitch_mode": _pitch_mode(pitch_mode if pitch_mode is not None else cfg.get("pitch_mode")),
    }
    if url:
        payload["url"] = url
    else:
        payload["song"] = song
    if voice_name:
        payload["voice"] = voice_name
    try:
        base_url, token = _connection(cfg)
        block = await _chat(base_url, token, _SING_MODEL, payload)
        source = block.get("source")
        if block.get("type") != "audio" or not isinstance(source, dict) or not source.get("url"):
            raise RuntimeError(str(block.get("error") or "网关没有返回音频"))
        return json.dumps(block, ensure_ascii=False)
    except Exception as exc:
        logger.exception("sing_song failed")
        return json.dumps({"error": _describe_error(exc)}, ensure_ascii=False)


def list_voices() -> str:
    """Point users to the voice plaza; voices are picked there and used by name."""
    cfg = _cfg("list_voices")
    return json.dumps(
        {
            "guide": "请让用户到音色广场浏览挑选音色",
            "plaza_url": _plaza_url(cfg),
            "note": (
                "音色目录不通过接口返回：用户在广场挑好后，点歌时传 voice=音色名即可；"
                "未指定音色时使用插件配置的默认音色。自有 RVC 音色可在广场页登录后"
                "提交上传（自动审核），并在「我的音色」里管理；用户不能直接传链接。"
            ),
        },
        ensure_ascii=False,
    )


def setup(ctx: PluginContext) -> None:
    ctx.tool(
        "search_song",
        search_song,
        description=(
            "Step 1 of 2 for singing: search a song and return up to 3 candidates "
            "(title, artist, duration, url). If the top result clearly matches the request, "
            "you may call sing_song(url=...) directly; if it is ambiguous, show the top "
            "candidate and ask the user to confirm or choose. Do not search repeatedly."
        ),
        config_fields=[
            {
                "name": "limit",
                "label": "候选数量",
                "type": "number",
                "help": "默认 3，最多 5。",
            },
        ],
    )
    ctx.tool(
        "list_voices",
        list_voices,
        description=(
            "Return the voice plaza URL and usage guidance. Voice names are browsed and "
            "picked on the plaza; pass the chosen name to sing_song(voice=...). Users "
            "cannot pass raw model URLs."
        ),
        config_fields=[
            {
                "name": "plaza_url",
                "label": "音色广场地址",
                "help": "留空使用默认广场地址。",
            },
        ],
    )
    ctx.tool(
        "sing_song",
        sing_song,
        description=(
            "Step 2 of 2: generate a short singing clip in the configured RVC voice. "
            "Pass the url the user confirmed, or song only when the user asked to skip "
            "confirmation. `voice` is a voice name the user picked on the voice plaza "
            "(see list_voices for the URL). Defaults are tuned for a short clip "
            "(duration up to 60s); do not override start/duration/pitch unless the user "
            "asks. Returns an audio attachment; do not paraphrase its JSON payload."
        ),
        config_fields=[
            {
                "name": "billing_mode",
                "label": "计费模式",
                "help": (
                    "flowbee 走 new-api 的 sing-song 按次计费（用用户自己的 key）；"
                    "selfhosted 走自建网关。"
                ),
            },
            {
                "name": "flowbee_base_url",
                "label": "FlowBee API 地址",
                "help": "留空则使用用户在个人设置里配置的 Base URL。",
            },
            {
                "name": "gateway_base_url",
                "label": "自托管网关地址",
                "help": "selfhosted 模式使用，例如 http://127.0.0.1:9001/v1。",
            },
            {
                "name": "gateway_token",
                "label": "网关令牌",
                "type": "password",
                "help": "selfhosted 模式使用，即网关的 SING_GATEWAY_TOKEN。",
            },
            {
                "name": "default_voice",
                "label": "默认音色",
                "help": "音色名（音色广场里的名字，如 keine / cirno），留空使用网关默认。",
            },
            {
                "name": "pitch",
                "label": "音高（半音）",
                "type": "number",
                "help": "整体升降调，默认 2。",
            },
            {
                "name": "pitch_mode",
                "label": "人声音域",
                "help": "no-change / male-to-female / female-to-male，默认 no-change。",
            },
            {
                "name": "start",
                "label": "截取起点（秒）",
                "type": "number",
                "help": "默认 60。",
            },
            {
                "name": "duration",
                "label": "片段长度（秒）",
                "type": "number",
                "help": "默认 25，范围 5~60。",
            },
        ],
    )
