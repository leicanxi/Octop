# AI 唱歌（sing-song）

在聊天里点歌：搜索 B 站音频、截取一小段、用 RVC 音色唱出来，并作为音频附件发送。

## 安装

在 Dashboard 的「插件 → 插件市场」里安装 **AI 唱歌**，然后按 Agent 启用。

## 配置

推荐在 Octop 服务的环境变量里设置：

```bash
REPLICATE_API_TOKEN=r8_xxx
```

也可以在插件的工具设置里填写，但会明文保存在 Agent 配置中。其余配置项：

| 项 | 默认 | 说明 |
|----|------|------|
| `billing_mode` | `direct` | `direct` 直连 Replicate；`flowbee` 走 new-api 的 `sing-song` 按次计费 |
| `flowbee_base_url` | 空 | 留空则使用用户个人设置里的 Base URL |
| `flowbee_model` | `sing-song` | new-api 里的按次计费模型名 |
| `default_voice` | 空 | 默认音色名（白名单或音色广场目录里的名字），留空使用白名单默认项 |
| `voice_model_url` | 内置 Keine 音色 zip | RVC 模型 zip 的公网地址 |
| `model` | `zsxkib/realistic-voice-cloning` | AICoverGen 封装，内部完成主唱/伴唱分离 |
| `pitch` | `2` | 整体升降调（半音） |
| `pitch_mode` | `no-change` | `no-change` / `male-to-female` / `female-to-male` |
| `start` | `60` | 截取起点（秒） |
| `duration` | `25` | 片段长度（秒），范围 5~60 |
| `cache_ttl_hours` | `168` | 缓存有效期（小时），0 表示不过期 |
| `cache_max_entries` | `50` | 缓存条数上限，按最近使用淘汰 |
| `replicate_base_url` | `https://api.replicate.com/v1` | `direct` 模式使用 |

`flowbee` 模式使用**用户自己的模型 Key**，没有配置 Key 时直接报错，不回退到共享 Key。
new-api、网关和 AstrBot 的接入说明见 [`docs/sing-gateway.md`](../../../../../../../docs/sing-gateway.md)。

## 音色

音色分两个来源，查找时**白名单优先，目录兜底**：

**白名单**（管理员维护 `~/.octop/sing-voices.json`，可用 `SING_VOICES_FILE` 改路径）：

```json
{
  "keine": {"label": "Keine", "url": "https://.../keine.zip"},
  "cirno": {"label": "Cirno", "url": "https://.../cirno.zip"}
}
```

- 存放内置默认音色和审核过的自定义音色；条目不会出现在任何接口返回里，
  名字即凭证：知道音色名才能用。
- 用户**不能**直接传 RVC 链接；新音色必须由管理员审核后加入白名单。
- 服务器部署时把 Octop 服务和网关的 `SING_VOICES_FILE` 指向同一个绝对路径，
  例如 `/etc/octop/sing-voices.json`；不要依赖某个用户的本机目录。

**公开目录**（`~/.octop/sing-voices-catalog.json`，可用 `SING_VOICES_CATALOG_FILE`
改路径）：由 `scripts/scrape_voice_catalog.py` 从 easyaivoice 抓取的开源 RVC
模型集合（仅收录 Hugging Face 直链），供音色广场展示与选用。

- 音色广场（默认 `https://voice-models.flowbee.top`，可用 `SING_VOICE_PLAZA_URL`
  覆盖）是唯一的公开浏览入口；用户挑好音色后在聊天里报名字即可。
- `list_voices` 不再返回音色列表，只返回广场链接和使用说明，agent 可直接转述。
- 音色名不存在时报错只提示广场链接，不枚举任何名字。

## 交互

- `search_song(query)` 返回候选，agent 应先让用户确认再调用 `sing_song`。
- `sing_song(url=...)` 使用确认后的地址；`sing_song(song=...)` 自动取第一条，适合用户明确说“不用问，直接唱”。
- 插件不输出任何固定台词，所有措辞由 agent 人设决定。

## 说明

- 输入来源是 B 站音频，下载与翻唱涉及版权，请仅在授权范围内使用。
- 热启动约 70 秒，冷启动 2~4 分钟；工具会同步等待。
- 生成结果会按"歌曲 URL + 片段起点/长度 + 音高 + 音色 + 模型版本"缓存在
  `~/.octop/cache/sing-song/`。同一请求再次命中时只需重新上传缓存文件，通常 1~2 秒。
  缓存按需生成，不会预先生成热门歌曲。
- 返回的音频是 Replicate 的临时 URL，适合立即发送；本地缓存用于下次快速重放。
- 计费、限流和 FlowBee 计费接入尚未实现。
