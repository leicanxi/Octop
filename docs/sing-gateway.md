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
| `SING_VOICES_CATALOG_FILE` | `~/.octop/sing-voices-catalog.json` | Scraped public catalog (see [Voice catalog](#voice-catalog)) |
| `SING_VOICE_PLAZA_URL` | `https://voice-models.flowbee.top` | Voice plaza link returned by the guide endpoints |
| `SING_BILI_COOKIES` | — | Path to a Netscape `cookies.txt` for Bilibili (see HTTP 412 below) |
| `SING_PROXY` | — | Optional proxy (`http://host:port`) for Bilibili search/download |
| `SING_NCM_API` | — | Base URL of a running ncm-api sidecar (see below); unlocks VIP tracks |

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

### Expanding the NetEase catalog (ncm-api sidecar)

Anonymous NetEase APIs only issue audio links for free tracks. The community
[ncm-api](https://github.com/moeyy01/ncm-api) project (NeteaseCloudMusicApi
Enhanced, Node.js) maintains "unblock" parameter tricks that often get VIP
tracks too — sampled hit rate was 11/11: some full 320kbps tracks, most a
~45s free-trial clip (enough for short covers). Run it as a sidecar:

```bash
docker run -d --name ncm-api --restart always \
  -p 127.0.0.1:3010:3000 moefurina/ncm-api:latest
export SING_NCM_API=http://127.0.0.1:3010
```

The gateway resolves audio URLs through the sidecar first and falls back to
the bare endpoint (free tracks only) when it fails, so a broken/updated
sidecar degrades gracefully. Trial clips are trimmed within their own
duration automatically.

Caveats: the unlock tricks are community-maintained attempts against
NetEase's unofficial API — they can stop working at any time (the fallback
path keeps free tracks working); trial clips are typically ~45 seconds.

## Voices

Two JSON files, both re-read on lookup (mtime-cached), so edits apply without
a restart. Lookups check the whitelist first, then the public catalog.

**Whitelist** — curated voices (built-in defaults + admin-approved custom),
shared by the gateway and the Octop plugin:

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

A plain string value (`"keine": "https://..."`) is accepted as the URL.

**Public catalog** — machine-scraped community models, browsable on the voice
plaza and selectable by name:

```
~/.octop/sing-voices-catalog.json   # generated by scripts/scrape_voice_catalog.py
```

- `sing_song(voice="cirno")` resolves whitelist names first, then catalog names.
- An empty name uses the whitelist's `keine` entry (or `SING_VOICE_URL` on the
  gateway when it is set).
- An unknown name returns `400 unknown voice '...'` **without** listing the
  available names: whitelist entries (including future per-user custom voices)
  rely on staying unknown, and the plaza is the only public browsable surface.
- Users cannot pass a raw RVC URL. New custom voices must be reviewed by the
  admin and added to this file, because `.pth` checkpoints are untrusted
  code/data and arbitrary URLs also create SSRF risk.
- `GET /v1/voices` and the `sing-voices` chat model only return a guide block
  pointing at the plaza (`{"type": "guide", "plaza_url": ...}`) — they no
  longer enumerate voices.

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

Populate the catalog on the gateway host (Hugging Face is reachable from
overseas VPS; only direct HF zip links are kept, Google Drive sources are
skipped):

```bash
uv run python scripts/scrape_voice_catalog.py            # full catalog, ~640 pages
uv run python scripts/scrape_voice_catalog.py --limit 200
```

## Configure new-api

1. Create a **custom / OpenAI-compatible** channel with:
   - Base URL: `http://<octop-host>:9001/v1`
   - Key: the same value as `SING_GATEWAY_TOKEN`
2. Add model `sing-song` to the channel.
3. Add model `sing-voices` to the channel too and price it at **0**: clients
   use it to fetch the voice plaza link through new-api (see
   [Voice catalog](#voice-catalog)). It never runs a generation; the gateway
   only returns the guide block.
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

Voice names are picked on the voice plaza (`SING_VOICE_PLAZA_URL`, default
`https://voice-models.flowbee.top`) and passed back by name. The endpoints
below only return the plaza link so every client (Octop plugin, AstrBot
plugin, DSH, …) can show the same guidance:

- Direct gateway access: `GET /v1/voices` with the gateway token returns
  `{"object": "list", "data": [], "note": "...", "plaza_url": "..."}`.
- Behind new-api: unknown paths like `GET /v1/voices` are not relayed. Post
  `/v1/chat/completions` with `"model": "sing-voices"` instead (include a
  dummy user message — new-api rejects empty `messages` lists before
  forwarding). The assistant content is a guide block:

  ```json
  {
    "type": "guide",
    "text": "音色列表已迁移到音色广场：打开 plaza_url 挑选音色，点歌时把音色名传给 voice。…",
    "plaza_url": "https://voice-models.flowbee.top"
  }
  ```

Keep `sing-voices` priced at 0 on new-api — the response `usage` is all zeros,
so the call is free either way.

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

- Song-level failures — a NetEase VIP track with no anonymous audio link, or a
  query that finds nothing — return HTTP **400** with the reason, not 502.
  Relays like new-api treat 4xx as non-retryable and pass the message to the
  user; a 502 would burn channel retries and end in a misleading
  "no channel available" error.

- Returned audio URLs are Replicate delivery URLs (public), so QQ can fetch
  them directly. Cache hits return the original public delivery URL stored in
  the cache entry (validated with a HEAD request; stale or pre-upgrade entries
  regenerate once and refresh the stored URL).
- Cache hits still count as one new-api call unless you create a separate
  lower-priced model for them.
- Keep `sing-song` at or above 0.7 per call under the 1:1 convention; typical
  cost is $0.016–0.054 and the cache hit cost is near zero.
