"""Scrape the public RVC catalog into ``sing-voices-catalog.json``.

Source: easyaivoice.com (a.k.a. voice-models.com) ``run-model-library.php``,
a public JSON endpoint that lists community RVC models with their external
download links. Only direct Hugging Face zip links are imported — Google Drive
links cannot be fetched by Replicate, so they are skipped.

Re-running merges by the site's ``weightsId`` (idempotent): entries keep their
existing slug, labels/URLs/favorites refresh, and unrelated keys in the file
are preserved. Run it on the gateway host, where the file is read:

    uv run python scripts/scrape_voice_catalog.py                     # full catalog
    uv run python scripts/scrape_voice_catalog.py --limit 200         # newest 200
    uv run python scripts/scrape_voice_catalog.py --search miku --search touhou
    uv run python scripts/scrape_voice_catalog.py --out /etc/octop/sing-voices-catalog.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SOURCE_URL = "https://easyaivoice.com/run-model-library.php"
PAGE_SIZE = 48  # server-side maximum
USER_AGENT = "octop-voice-catalog/1.0 (+self-hosted RVC voice catalog)"
_TAG_RE = re.compile(r"\[[^\]]*\]|【[^】]*】")
_SLUG_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def default_out() -> Path:
    home = Path(os.environ.get("OCTOP_HOME") or (Path.home() / ".octop"))
    return home / "sing-voices-catalog.json"


def fetch_page(search: str, offset: int, *, timeout: float) -> dict[str, Any]:
    params = {"offset": str(offset), "limit": str(PAGE_SIZE)}
    if search:
        params["search"] = search
    url = f"{SOURCE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - retry any transport/JSON error
            last_error = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"fetch failed after retries: {url}: {last_error}")


def clean_label(name: str) -> str:
    text = _TAG_RE.sub(" ", name)
    text = re.sub(r"\s+", " ", text).strip(" -_.")
    return text or name.strip()


def slugify(name: str) -> str:
    text = _TAG_RE.sub(" ", name).lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    return text[:48].strip("-")


def normalize_url(raw: str) -> str:
    """Turn HF blob links into resolvable direct-download links."""
    url = raw.strip().split("?")[0]
    return url.replace("/blob/", "/resolve/")


def is_huggingface_zip(raw: str) -> bool:
    url = normalize_url(raw)
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return host == "huggingface.co" or host.endswith(".huggingface.co")


def load_existing(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


class SlugPool:
    """Allocate unique slugs, preferring the slug already used for a weights_id."""

    def __init__(self, existing: dict[str, Any]) -> None:
        self.taken: set[str] = set(existing)
        self.by_weights: dict[int, str] = {}
        for slug, value in existing.items():
            if isinstance(value, dict) and isinstance(value.get("weights_id"), int):
                self.by_weights[value["weights_id"]] = slug

    def for_weights(self, weights_id: int, name: str) -> str:
        known = self.by_weights.get(weights_id)
        if known:
            return known
        base = slugify(name) or f"voice-{weights_id}"
        slug = base
        index = 2
        while slug in self.taken:
            slug = f"{base}-{index}"
            index += 1
        self.taken.add(slug)
        self.by_weights[weights_id] = slug
        return slug


def iter_items(search: str, *, sleep: float, timeout: float):
    offset = 0
    while True:
        page = fetch_page(search, offset, timeout=timeout)
        items = page.get("items") or []
        if not items:
            return
        yield from items
        if not page.get("hasMore"):
            return
        offset += len(items)
        time.sleep(sleep)


def scrape(
    searches: list[str],
    *,
    existing: dict[str, Any],
    limit: int = 0,
    sleep: float = 0.3,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], dict[str, int]]:
    queries = searches or [""]
    stats = {"fetched": 0, "kept": 0, "skipped_source": 0, "skipped_shape": 0}
    pool = SlugPool(existing)
    catalog: dict[str, Any] = {}
    for query in queries:
        for item in iter_items(query, sleep=sleep, timeout=timeout):
            stats["fetched"] += 1
            if limit and stats["kept"] >= limit:
                return catalog, stats
            url = str(item.get("url") or "").strip()
            name = str(item.get("name") or "").strip()
            weights_id = item.get("weightsId")
            if not url or not name or ".zip" not in url.lower():
                stats["skipped_shape"] += 1
                continue
            if not is_huggingface_zip(url):
                stats["skipped_source"] += 1
                continue
            try:
                weights_id = int(weights_id)
            except (TypeError, ValueError):
                stats["skipped_shape"] += 1
                continue
            slug = pool.for_weights(weights_id, name)
            catalog[slug] = {
                "label": clean_label(name),
                "url": normalize_url(url),
                "favorites": int((item.get("stats") or {}).get("favoriteCount") or 0),
                "weights_id": weights_id,
            }
            stats["kept"] += 1
    return catalog, stats


def merge_into(existing: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Merge fresh entries into the existing file, preserving unknown keys."""
    merged = dict(existing)
    for slug, entry in fresh.items():
        current = merged.get(slug)
        if isinstance(current, dict):
            merged[slug] = {**current, **entry}
        else:
            merged[slug] = entry
    return merged


def sort_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    def key(item: tuple[str, Any]) -> tuple[int, str]:
        slug, entry = item
        favorites = entry.get("favorites") if isinstance(entry, dict) else 0
        return (-(favorites or 0), slug)

    return dict(sorted(catalog.items(), key=key))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape the public RVC voice catalog.")
    parser.add_argument("--out", type=Path, default=None, help="catalog JSON path")
    parser.add_argument(
        "--search",
        action="append",
        default=[],
        help="repeatable search keyword; omit for the full catalog",
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N kept entries")
    parser.add_argument("--sleep", type=float, default=0.3, help="seconds between pages")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout")
    args = parser.parse_args(argv)

    target = (args.out or default_out()).expanduser()
    existing = load_existing(target)
    fresh, stats = scrape(
        list(args.search),
        existing=existing,
        limit=args.limit,
        sleep=args.sleep,
        timeout=args.timeout,
    )
    merged = sort_catalog(merge_into(existing, fresh))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(target)
    print(
        f"fetched={stats['fetched']} kept={stats['kept']} "
        f"skipped_source={stats['skipped_source']} skipped_shape={stats['skipped_shape']} "
        f"total={len(merged)} -> {target} ({target.stat().st_size / 1024:.0f} KiB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
