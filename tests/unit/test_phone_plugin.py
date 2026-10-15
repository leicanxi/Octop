"""小手机插件：工具输出契约、状态文件约定与常驻面板声明。"""

from __future__ import annotations

import importlib.util
import json
from typing import Any

from octop.infra.agents.plugins.bundled import default_bundled_plugins_root


def _load_phone() -> Any:
    path = default_bundled_plugins_root() / "phone" / "main.py"
    spec = importlib.util.spec_from_file_location("bundled_phone", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RecordingCtx:
    def __init__(self) -> None:
        self.tools: list[tuple[str, Any, str]] = []

    def tool(
        self,
        name: str,
        fn: Any,
        *,
        description: str = "",
        config_fields: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tools.append((name, fn, description))


def test_setup_registers_show_phone() -> None:
    mod = _load_phone()
    ctx = _RecordingCtx()
    mod.setup(ctx)
    assert [name for name, _fn, _desc in ctx.tools] == ["show_phone"]
    _name, fn, description = ctx.tools[0]
    assert fn is mod.show_phone
    # 描述是角色唯一的接口文档：状态文件路径、各内容分区、玩家会写回的字段
    # 都必须在里面，否则角色不知道往哪写、也会覆盖掉玩家的动作。
    assert mod.STATE_PATH in description
    for section in ("device", "wechat", "sms", "calls", "photos", "moments", "notes"):
        assert section in description, section
    assert mod.PLAYER_LABEL in description


def test_apps_cover_the_dock_trio_and_home_grid() -> None:
    """dock 固定「短信 / 通话 / 相册」，其余在主屏；两者都必须在 APPS 里可定位。"""
    mod = _load_phone()
    assert {"sms", "calls", "photos"} <= set(mod.APPS)
    assert {"home", "wechat", "moments", "notes", "settings"} <= set(mod.APPS)
    assert mod.DEFAULT_APP == "home"


async def test_show_phone_returns_phone_renderer_envelope() -> None:
    mod = _load_phone()
    payload = json.loads(await mod.show_phone("moments"))
    assert payload["octop_ui"] == {"renderer": "phone_card", "version": 1}
    assert payload["data"]["app"] == "moments"
    assert payload["data"]["state_path"] == "phone/state.json"
    assert payload["text"]


async def test_show_phone_defaults_to_home() -> None:
    mod = _load_phone()
    assert json.loads(await mod.show_phone())["data"]["app"] == "home"


async def test_show_phone_opens_dock_apps() -> None:
    mod = _load_phone()
    for app in ("sms", "calls", "photos"):
        payload = json.loads(await mod.show_phone(app))
        assert payload["data"]["app"] == app


async def test_show_phone_clamps_unknown_app_and_lists_valid_ones() -> None:
    mod = _load_phone()
    payload = json.loads(await mod.show_phone("weibo"))
    assert payload["data"]["app"] == "home"
    assert "weibo" in payload["text"]
    # 角色猜错 App 名时要能从报错里看到正确取值，不然会反复试错。
    for app_id in ("sms", "calls", "photos", "wechat"):
        assert app_id in payload["text"], app_id


def test_ui_manifest_declares_the_persistent_panel() -> None:
    """常驻入口靠 ui/manifest.json 的 panels 声明，renderer 必须与工具一致。"""
    manifest = json.loads(
        (default_bundled_plugins_root() / "phone" / "ui" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    renderers = {item["id"] for item in manifest["renderers"]}
    panels = manifest["panels"]
    assert len(panels) == 1
    assert panels[0]["id"] == "phone"
    assert panels[0]["renderer"] in renderers
    assert panels[0]["renderer"] == _load_phone().RENDERER
