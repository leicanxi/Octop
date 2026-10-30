# Singing gateway (moved)

The singing gateway is now its own project: **sing-gateway** —
https://github.com/leicanxi/sing-gateway

It owns the music pipeline (Bilibili/NetEase search, clip extraction, RVC via
Replicate, disk cache), the voice whitelist/catalog, self-service voice uploads
with automatic review, the public voice plaza and FlowBee SSO login.

This repository's `sing-song` plugin is a thin HTTP client for it: pick either
`flowbee` (billed through new-api on the user's own key) or `selfhosted` (your
own gateway address + token) in the plugin settings.
