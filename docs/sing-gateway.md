# Singing gateway (new-api / FlowBee)

Exposes the Octop singing pipeline as an OpenAI-compatible `sing-song` model so
new-api can authenticate, meter and bill each generation. Octop's plugin and
future clients (for example an AstrBot plugin) only need a normal
`/v1/chat/completions` call.

```
Octop plugin / AstrBot / other client
  → new-api  /v1/chat/completions  model=sing-song  (user's own key)
    → singing gateway (this document)
      → Bilibili search/download → Replicate AICoverGen → disk cache
        → audio block
```

Only the Replicate prediction costs money; Bilibili search/download and ffmpeg
are local. AICoverGen already bundles vocal separation, RVC and mixing, so one
prediction is the whole paid step.

## Run the gateway

Install the optional runtime dependencies on the gateway host (the plugin's
install normally does this already):

```bash
uv pip install "yt-dlp>=2025.1.1" "imageio-ffmpeg>=0.5"
```

Start it next to Octop (same host or same Docker network):

```bash
export SING_GATEWAY_TOKEN='choose-a-long-random-secret'
export REPLICATE_API_TOKEN='r8_...'
export SING_VOICE_URL='https://huggingface.co/leicanxi/rvc-models/resolve/main/keine_genso_flat.zip'

uv run uvicorn octop.api.gateway.sing:app --host 127.0.0.1 --port 9001
```

`uv run octop-sing-gateway` works too; it reads `SING_GATEWAY_HOST` and
`SING_GATEWAY_PORT` (defaults `127.0.0.1:9001`).

### Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `SING_GATEWAY_TOKEN` | — | Required shared secret; new-api's channel key must match |
| `REPLICATE_API_TOKEN` | — | Required; the operator's Replicate account |
| `SING_VOICE_URL` | built-in Keine zip | RVC voice model zip |
| `SING_REPLICATE_MODEL` | `zsxkib/realistic-voice-cloning` | Cover model |
| `SING_REPLICATE_VERSION` | latest | Pin a Replicate version |
| `SING_REPLICATE_BASE_URL` | `https://api.replicate.com/v1` | Replicate API base |
| `SING_PITCH` | `2` | Default semitone shift |
| `SING_START` | `60` | Default clip start (seconds) |
| `SING_DURATION` | `25` | Default clip length (5–30) |
| `SING_MUSIC_SOURCE` | `bilibili` | Music source for search/download: `bilibili` or `netease` (payload `"source"` overrides per request) |
| `SING_CACHE_TTL_HOURS` | `168` | Disk cache TTL |
| `SING_CACHE_MAX_ENTRIES` | `50` | Disk cache entries |
| `SING_VOICES_FILE` | `~/.octop/sing-voices.json` | Admin-managed voice whitelist |
| `SING_BILI_COOKIES` | — | Path to a Netscape `cookies.txt` for Bilibili (see HTTP 412 below) |
| `SING_PROXY` | — | Optional proxy (`http://host:port`) for Bilibili search/download |

### Bilibili HTTP 412

Bilibili risk-controls datacenter IPs: on a cloud VPS every request — search
included — can fail with `HTTP Error 412 Precondition Failed` regardless of
headers.

**Easiest fix on an overseas host: set `SING_MUSIC_SOURCE=netease`.** NetEase's
anonymous APIs (plain search JSON + direct mp3 links for free songs) work from
datacenter IPs and need no cookies; VIP-only songs fail with an explicit
"needs VIP" error, so pick free tracks.

The Bilibili-specific alternatives: export cookies from a logged-in account
while visiting bilibili.com (browser extension such as "Get cookies.txt
LOCALLY"), place the file on the gateway host, and set `SING_BILI_COOKIES` to
its path. The file is re-read on every download, so refreshing an expired
cookie file takes effect without a restart. Alternatively set `SING_PROXY` to
route music traffic through a different exit node.

## Voice whitelist

The admin maintains selectable voices in one JSON file shared by the gateway
and the Octop plugin (same host, or a shared volume):

```
~/.octop/sing-voices.json
```

```json
{
  "keine": {
    "label": "Keine",
    "url": "https://huggingface.co/leicanxi/rvc-models/resolve/main/keine_genso_flat.zip"
  },
  "cirno": {
    "label": "Cirno",
    "url": "https://huggingface.co/Sergeantzero/Touhou_Project/resolve/main/Cirno%20%28Touhou%20Cannonball%29.zip"
  }
}
```

A plain string value (`"keine": "https://..."`) is accepted as the URL. The file
is re-read on every lookup, so adding a voice does not require a restart.

- `sing_song(voice="cirno")` selects a whitelist entry.
- An empty name uses the whitelist's `keine` entry (or `SING_VOICE_URL` on the
  gateway when it is set).
- An unknown name returns an error listing the available names.
- Users cannot pass a raw RVC URL. New voices must be reviewed by the admin and
  added to this file, because `.pth` checkpoints are untrusted code/data and
  arbitrary URLs also create SSRF risk.
- `GET /v1/voices` returns the catalog and the custom-voice policy note.

### Server deployment

Set `SING_VOICES_FILE` to an absolute path on the server and use the same value
for both the Octop service (plugin) and the gateway:

```bash
sudo mkdir -p /etc/octop
sudo cp deploy/sing-voices.example.json /etc/octop/sing-voices.json
export SING_VOICES_FILE=/etc/octop/sing-voices.json
```

Docker: mount the file and set the same environment variable, for example
`-v /srv/octop/sing-voices.json:/config/sing-voices.json -e SING_VOICES_FILE=/config/sing-voices.json`.

If `SING_VOICES_FILE` is unset, the fallback is `$OCTOP_HOME/sing-voices.json`,
then `~/.octop/sing-voices.json` (handy for local development).

## Configure new-api

1. Create a **custom / OpenAI-compatible** channel with:
   - Base URL: `http://<octop-host>:9001/v1`
   - Key: the same value as `SING_GATEWAY_TOKEN`
2. Add model `sing-song` to the channel.
3. Add model `sing-voices` to the channel too and price it at **0**: clients
   use it to pull the voice whitelist through new-api (see
   [Voice catalog](#voice-catalog)). It never runs a generation; the gateway
   only returns the catalog.
4. Set the `sing-song` price to **per-call billing** (`QuotaType=1`). With the
   1:1 RMB/USD convention, `0.7` per call covers the worst-case cold start
   (about $0.08 of T4 time) with a healthy margin.
5. Raise the relay timeout to **at least 300 seconds**: a warm generation is
   about 70 seconds and a cold start can take 2–4 minutes. Current new-api
   builds have no UI field for this — set env `RELAY_TIMEOUT=300` on the
   new-api container instead.

The gateway returns a standard chat completion whose assistant content is a
JSON audio block. `usage` is zero because billing is per-call.

## Voice catalog

Clients offer voice selection by name, so they need the current whitelist:

- Direct gateway access: `GET /v1/voices` with the gateway token returns
  `{"data": [{"id": ..., "label": ...}], "note": ...}`.
- Behind new-api: unknown paths like `GET /v1/voices` are not relayed. Post
  `/v1/chat/completions` with `"model": "sing-voices"` instead (include a
  dummy user message — new-api rejects empty `messages` lists before
  forwarding). The assistant content is a voices block:

  ```json
  {
    "type": "voices",
    "voices": [{"name": "keine", "label": "Keine"}],
    "note": "自定义 RVC 音色需要管理员审核后加入白名单；用户不能直接传链接。"
  }
  ```

Both read the whitelist file on every call, so an updated `SING_VOICES_FILE`
shows up on the next pull without restarting anything. Keep `sing-voices`
priced at 0 on new-api — the response `usage` is all zeros, so the call is
free either way.

## Configure the Octop plugin

Use the acting user's own FlowBee key (no shared-key fallback):

1. The user configures their model credential in Octop (Base URL such as
   `https://flowbee.top/v1` and their FlowBee key).
2. In the plugin's `sing_song` settings:
   - `billing_mode=flowbee`
   - `flowbee_model=sing-song`
   - `flowbee_base_url` can stay empty; the user's Base URL is used.
3. If the user has no key, `sing_song` returns a clear error instead of
   charging the operator's account.

Octop injects the resolved user credential into the run's internal tool context
(`plugin_user_credential`); it is not part of the prompt, tool output, or agent
config.

## AstrBot / other clients

Ready-made AstrBot plugin:
[`leicanxi/deepseek-sing-astrbot`](https://github.com/leicanxi/deepseek-sing-astrbot)
(config page supports FlowBee and self-hosted modes, voice list pull, and a
self-managed whitelist file).

Any client with an OpenAI-compatible provider can use the same model directly:

- Base URL: `https://flowbee.top/v1`
- Model: `sing-song`
- Key: the user's FlowBee key

The gateway accepts either a JSON user message (`{"song": "...", "start": 60}`)
or plain text (`雨爱`). Plain text is treated as the song name.

## Notes

- Returned audio URLs are Replicate delivery URLs (public), so QQ can fetch
  them directly. Cache hits re-upload the cached mp3 to Replicate Files.
- Cache hits still count as one new-api call unless you create a separate
  lower-priced model for them.
- Keep `sing-song` at or above 0.7 per call under the 1:1 convention; typical
  cost is $0.016–0.054 and the cache hit cost is near zero.
