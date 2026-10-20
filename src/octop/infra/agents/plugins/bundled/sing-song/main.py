"""Thin singing tools over the shared ``octop.infra.music`` pipeline.

``direct`` mode talks to Replicate from the plugin process. ``flowbee`` mode
calls an OpenAI-compatible ``sing-song`` model (new-api/FlowBee) with the acting
user's own key, so billing stays on the user's account. User-facing phrasing
belongs to the agent persona; this module only returns data and audio blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from langgraph.config import get_config
from octop_harness.plugins import PluginContext
from octop_harness.plugins.tools import get_tool_config

from octop.infra.music import (
    DEFAULT_MODEL,
    DEFAULT_REPLICATE_BASE,
    DEFAULT_VOICE_URL,
    PITCH_MODES,
    SingConfig,
    render_song,
    resolve_voice,
    search_songs,
    voice_catalog,
)

logger = logging.getLogger("octop.plugins.sing_song")

_BILLING_MODES = {"direct", "flowbee"}


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


def _as_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _pitch_mode(value: Any) -> str:
    mode = str(value or "no-change").strip()
    return mode if mode in PITCH_MODES else "no-change"


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


def _sing_config(cfg: dict[str, Any], *, voice_model_url: str | None = None) -> SingConfig:
    return SingConfig(
        voice_model_url=voice_model_url or str(cfg.get("voice_model_url") or DEFAULT_VOICE_URL),
        model=str(cfg.get("model") or DEFAULT_MODEL),
        model_version=str(cfg.get("model_version") or ""),
        replicate_base_url=str(cfg.get("replicate_base_url") or DEFAULT_REPLICATE_BASE),
        replicate_token=str(cfg.get("replicate_token") or ""),
        pitch=_as_int(cfg.get("pitch"), 2, minimum=-24, maximum=24),
        pitch_mode=_pitch_mode(cfg.get("pitch_mode")),
        start=_as_int(cfg.get("start"), 60, minimum=0, maximum=3600),
        duration=_as_int(cfg.get("duration"), 25, minimum=5, maximum=30),
        index_rate=_as_float(cfg.get("index_rate"), 0.75, minimum=0.0, maximum=1.0),
        filter_radius=_as_int(cfg.get("filter_radius"), 3, minimum=0, maximum=7),
        rms_mix_rate=_as_float(cfg.get("rms_mix_rate"), 0.25, minimum=0.0, maximum=1.0),
        protect=_as_float(cfg.get("protect"), 0.33, minimum=0.0, maximum=0.5),
        prediction_timeout=_as_float(
            cfg.get("prediction_timeout"), 600.0, minimum=30.0, maximum=1800.0
        ),
        cache_ttl_hours=_as_float(cfg.get("cache_ttl_hours"), 168.0, minimum=0.0, maximum=8760.0),
        cache_max_entries=_as_int(cfg.get("cache_max_entries"), 50, minimum=0, maximum=500),
    )


async def _flowbee_render(
    *,
    url: str,
    song: str,
    voice: str,
    cfg: dict[str, Any],
    start: int,
    duration: int,
    pitch: int,
    pitch_mode: str,
) -> dict[str, Any]:
    credential = _plugin_user_credential()
    if credential is None:
        raise RuntimeError("请先在个人设置里配置 FlowBee API Key")
    base_url = str(cfg.get("flowbee_base_url") or credential["base_url"] or "").rstrip("/")
    if not base_url:
        raise RuntimeError("未配置 FlowBee API 地址")
    endpoint = (
        base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    )
    model = str(cfg.get("flowbee_model") or "sing-song").strip()
    payload = {
        "url": url,
        "song": song,
        "voice": voice,
        "start": start,
        "duration": duration,
        "pitch": pitch,
        "pitch_mode": pitch_mode,
    }
    request = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    }
    async with httpx.AsyncClient(timeout=360.0) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {credential['api_key']}"},
            json=request,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"FlowBee 请求失败: HTTP {response.status_code} {response.text[:300]}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("FlowBee 返回格式不正确") from exc
    if isinstance(content, str):
        try:
            block = json.loads(content)
        except ValueError as exc:
            raise RuntimeError("FlowBee 返回内容不是 JSON") from exc
    elif isinstance(content, dict):
        block = content
    else:
        raise RuntimeError("FlowBee 返回内容格式不正确")
    if not isinstance(block, dict):
        raise RuntimeError("FlowBee 返回内容格式不正确")
    source = block.get("source")
    if block.get("type") != "audio" or not isinstance(source, dict) or not source.get("url"):
        raise RuntimeError(str(block.get("error") or "FlowBee 没有返回音频"))
    return block


async def search_song(query: str, limit: int | None = None) -> str:
    """Search Bilibili for a song.

    Returns candidates with title, artist, duration and URL. Ask the user to
    confirm or choose one when the match is ambiguous.
    """
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "query is required", "candidates": []}, ensure_ascii=False)
    cfg = _cfg("search_song")
    limit = _as_int(limit if limit is not None else cfg.get("limit"), 3, minimum=1, maximum=5)
    try:
        candidates = await asyncio.to_thread(search_songs, query, limit)
        return json.dumps(
            {
                "candidates": [candidate.as_dict() for candidate in candidates],
                "next": (
                    "If the top result clearly matches the request, call sing_song(url=...). "
                    "If it is ambiguous, ask the user to confirm the title/artist first."
                ),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("search_song failed")
        return json.dumps({"error": str(exc), "candidates": []}, ensure_ascii=False)


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
    if start is not None:
        cfg["start"] = start
    if duration is not None:
        cfg["duration"] = duration
    if pitch is not None:
        cfg["pitch"] = pitch
    if pitch_mode is not None:
        cfg["pitch_mode"] = pitch_mode

    start_value = _as_int(cfg.get("start"), 60, minimum=0, maximum=3600)
    duration_value = _as_int(cfg.get("duration"), 25, minimum=5, maximum=30)
    pitch_value = _as_int(cfg.get("pitch"), 2, minimum=-24, maximum=24)
    pitch_mode_value = _pitch_mode(cfg.get("pitch_mode"))
    billing_mode = str(cfg.get("billing_mode") or "direct").strip().lower()
    if billing_mode not in _BILLING_MODES:
        billing_mode = "direct"
    url = (url or "").strip()
    song = (song or "").strip()
    voice_name = (voice or str(cfg.get("default_voice") or "")).strip()
    if not url and not song:
        return json.dumps({"error": "url or song is required"}, ensure_ascii=False)

    try:
        if billing_mode == "flowbee":
            block = await _flowbee_render(
                url=url,
                song=song,
                voice=voice_name,
                cfg=cfg,
                start=start_value,
                duration=duration_value,
                pitch=pitch_value,
                pitch_mode=pitch_mode_value,
            )
            return json.dumps(block, ensure_ascii=False)
        if not url:
            candidates = await asyncio.to_thread(search_songs, song, 1)
            if not candidates:
                return json.dumps({"error": "no song found"}, ensure_ascii=False)
            url = candidates[0].url
        if voice_name:
            voice_spec = resolve_voice(voice_name)
            if voice_spec is None:
                names = ", ".join(item["name"] for item in voice_catalog())
                return json.dumps(
                    {
                        "error": f"unknown voice {voice_name!r}; available: {names}",
                        "note": "自定义 RVC 音色需要管理员审核后加入白名单；用户不能直接传链接。",
                    },
                    ensure_ascii=False,
                )
            voice_url = voice_spec.url
        else:
            voice_url = str(cfg.get("voice_model_url") or "").strip()
            if not voice_url:
                voice_spec = resolve_voice("")
                voice_url = voice_spec.url if voice_spec is not None else DEFAULT_VOICE_URL
        config = _sing_config(cfg, voice_model_url=voice_url)
        result = await asyncio.to_thread(render_song, url, config)
        return json.dumps(result.as_block(), ensure_ascii=False)
    except Exception as exc:
        logger.exception("sing_song failed")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def list_voices() -> str:
    """List selectable voice names and the custom-voice policy."""
    return json.dumps(
        {
            "voices": voice_catalog(),
            "note": "自定义 RVC 音色需要管理员审核后加入白名单；用户不能直接传链接。",
        },
        ensure_ascii=False,
    )


def setup(ctx: PluginContext) -> None:
    ctx.tool(
        "search_song",
        search_song,
        description=(
            "Step 1 of 2 for singing: search Bilibili and return up to 3 candidates "
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
            "List selectable singing voice names. Custom RVC models must be reviewed by an "
            "admin and added to the whitelist; users cannot pass raw model URLs."
        ),
    )
    ctx.tool(
        "sing_song",
        sing_song,
        description=(
            "Step 2 of 2: generate a short singing clip in the configured RVC voice. "
            "Pass the url the user confirmed, or song only when the user asked to skip "
            "confirmation. `voice` is a whitelist name from list_voices; custom voices "
            "require admin review. Defaults are tuned for a short clip (duration up to 30s); "
            "do not override start/duration/pitch unless the user asks. Returns an audio "
            "attachment; do not paraphrase its JSON payload."
        ),
        config_fields=[
            {
                "name": "billing_mode",
                "label": "计费模式",
                "help": "direct 直连 Replicate；flowbee 走 new-api 的 sing-song 按次计费，使用用户自己的 key。",
            },
            {
                "name": "flowbee_base_url",
                "label": "FlowBee API 地址",
                "help": "留空则使用用户在个人设置里配置的 Base URL。",
            },
            {
                "name": "flowbee_model",
                "label": "FlowBee 模型名",
                "help": "默认 sing-song。",
            },
            {
                "name": "default_voice",
                "label": "默认音色",
                "help": "白名单里的名字（如 keine / cirno），留空使用白名单默认项。",
            },
            {
                "name": "voice_model_url",
                "label": "音色模型 ZIP",
                "help": "RVC 模型的公网 zip 地址（含 .pth 和可选 .index）。",
            },
            {
                "name": "model",
                "label": "Replicate 模型",
                "help": "默认 zsxkib/realistic-voice-cloning。",
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
                "help": "默认 25，范围 5~30。",
            },
            {
                "name": "cache_ttl_hours",
                "label": "缓存有效期（小时）",
                "type": "number",
                "help": "默认 168（7 天），0 表示不过期。",
            },
            {
                "name": "cache_max_entries",
                "label": "缓存条数上限",
                "type": "number",
                "help": "默认 50，按最近使用淘汰。",
            },
            {
                "name": "index_rate",
                "label": "Index Rate",
                "type": "number",
                "help": "默认 0.75。",
            },
            {
                "name": "replicate_base_url",
                "label": "Replicate API 地址",
                "help": "direct 模式使用，默认 https://api.replicate.com/v1。",
            },
            {
                "name": "replicate_token",
                "label": "Replicate Token",
                "type": "password",
                "help": "direct 模式使用；推荐用环境变量 REPLICATE_API_TOKEN。",
            },
            {
                "name": "model_version",
                "label": "模型版本",
                "help": "留空自动使用最新版本。",
            },
            {
                "name": "prediction_timeout",
                "label": "超时（秒）",
                "type": "number",
                "help": "默认 600。",
            },
        ],
    )
