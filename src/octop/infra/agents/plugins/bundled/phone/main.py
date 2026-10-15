"""小手机 —— 在聊天里渲染角色自己的手机（短信 / 通话 / 相册 / 微信 / 朋友圈 / 备忘录）。

每个角色拥有一部自己的手机，用来承载不打断主线的日常。本插件只做两件事：

* ``show_phone`` 工具：把手机卡片渲染进聊天，可把首屏定位到某个 App；
* ``ui/index.js`` 渲染器：手机外壳（状态栏 / 主屏 / dock）+ App 模块，读取状态文件
  画出界面，并把玩家的点赞、收藏、打开会话、回拨写回状态文件。

手机内容存储在 Agent 工作区文件 ``phone/state.json``，由 Agent 自带的
``read_file`` / ``write_file`` 工具维护。插件工具是纯函数、没有 workspace
上下文（见 ``docs/octop-ui-payload-offload.md`` 的 F12），因此不能自己读写文件，
状态一律走工作区。
"""

from __future__ import annotations

import json
from typing import Any

from octop_harness.plugins import PluginContext

RENDERER = "phone_card"
STATE_PATH = "phone/state.json"
DEFAULT_APP = "home"
# 玩家动作写进 likes 的名字，与 ``ui/index.js`` 的 PLAYER_LABEL 保持一致。
PLAYER_LABEL = "玩家"
# 与 ``ui/index.js`` 的 APPS 保持一致：工具只用它校验 ``app`` 参数。
# dock（底部常驻）是 短信 / 通话 / 相册，其余在主屏。
APPS: dict[str, str] = {
    "home": "主屏",
    "sms": "短信",
    "calls": "通话",
    "photos": "相册",
    "wechat": "微信",
    "moments": "朋友圈",
    "notes": "备忘录",
    "settings": "设置",
}

_APP_NAMES = " / ".join(
    f"{app_id}（{label}）" for app_id, label in APPS.items() if app_id != "home"
)

_DESCRIPTION = (
    "打开角色的手机（小手机），在聊天里渲染出一部可查看的手机。"
    f"手机内容保存在工作区文件 {STATE_PATH}（version=2），首次打开会自动生成带示例的模板，"
    "先 read_file 看清结构再动手。玩家和角色会同时改这个文件：玩家点赞、收藏照片、"
    "打开短信会话、点回拨都会写回它，所以每次写入前都要先 read_file 读最新内容，"
    "不要凭记忆整文件覆盖（局部改动优先 edit_file），version / owner 字段要保留。"
    "内容分区（都可选，缺失按空处理）："
    "device{model,os,carrier,signal,battery,charging,storage_used_gb,storage_total_gb}"
    "（决定状态栏与「设置」里显示的机型、运营商、信号格数与电量）；"
    "wechat.chats[]{id,name,avatar,unread,messages[]}，messages[].side=me 表示角色自己发的；"
    "sms[] 结构与 wechat.chats 相同，是短信会话（unread 会显示成角标）；"
    "calls[]{id,name,avatar,direction,time,duration}，direction 取 in / out / missed；"
    "photos[]{id,emoji,tone,caption,date,place,favorite}，"
    "tone 取 sunset / sea / forest / night / rose / snow / ink（相册目前用 emoji + 色调画照片，"
    "不支持真实图片）；"
    "moments[]{id,time,text,images,likes,comments} 是朋友圈，新动态插到数组头部；"
    "notes[]{id,title,body,time} 是备忘录。新记录插到各自数组头部，数组只保留最近若干条。"
    "手机底部 dock 固定「短信 / 通话 / 相册」，微信 / 朋友圈 / 备忘录 / 设置在主屏宫格。"
    "玩家不会在手机里打字，想回应角色时会直接在聊天窗口发消息。"
    f"玩家动作的痕迹：点赞会把「{PLAYER_LABEL}」追加进该条 moments 的 likes，"
    f"收藏照片会把 photos[].favorite 置真，打开短信会话会把该会话 unread 清零，"
    "点「回拨」会在 calls 头部留下一条 direction=out、duration=未接通 的记录。"
    f"app 参数把首屏定位到某个 App：{_APP_NAMES}；home 表示只显示主屏。"
)


def _payload(data: dict[str, Any], text: str) -> str:
    return json.dumps(
        {"octop_ui": {"renderer": RENDERER, "version": 1}, "data": data, "text": text},
        ensure_ascii=False,
    )


async def show_phone(app: str = DEFAULT_APP) -> str:
    """Render the character's phone card in the chat."""
    raw = (app or "").strip().lower()
    target = raw if raw in APPS else DEFAULT_APP
    data = {"app": target, "state_path": STATE_PATH}
    if raw and target != raw:
        text = f"没有名为「{app}」的 App，已打开主屏。可选：{_APP_NAMES}。手机内容保存在 {STATE_PATH}。"
    elif target == DEFAULT_APP:
        text = f"已打开角色的手机主屏。内容保存在 {STATE_PATH}。"
    else:
        text = f"已打开角色的手机，首屏是「{APPS[target]}」。内容保存在 {STATE_PATH}。"
    return _payload(data, text)


def setup(ctx: PluginContext) -> None:
    ctx.tool("show_phone", show_phone, description=_DESCRIPTION)
