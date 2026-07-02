#!/usr/bin/env python3
"""Tiny authorized-source sticker search server.

Set GIPHY_API_KEY before running:
    $env:GIPHY_API_KEY="your-key"
    python server/sticker_server.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
GIPHY_TRENDING_URL = "https://api.giphy.com/v1/gifs/trending"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
_request_log: dict[str, list[float]] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _client_id(handler: BaseHTTPRequestHandler) -> str:
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return handler.client_address[0]


def _rate_limited(client: str, now: float | None = None) -> bool:
    now = now or time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    recent = [stamp for stamp in _request_log.get(client, []) if stamp >= cutoff]
    if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
        _request_log[client] = recent
        return True
    recent.append(now)
    _request_log[client] = recent
    return False


def _pick_image(images: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = images.get(name)
        if value and value.get("url"):
            return value
    return {}


def normalize_giphy_item(item: dict[str, Any]) -> dict[str, Any]:
    images = item.get("images") or {}
    original = _pick_image(images, "original")
    preview = _pick_image(images, "fixed_width_small", "downsized", "preview_gif", "original")
    return {
        "id": item.get("id", ""),
        "title": item.get("title") or "Untitled sticker",
        "thumbnailUrl": preview.get("url", ""),
        "originalUrl": original.get("url") or preview.get("url", ""),
        "source": "GIPHY",
        "width": int(original.get("width") or preview.get("width") or 0),
        "height": int(original.get("height") or preview.get("height") or 0),
        "mimeType": "image/gif",
        "pageUrl": item.get("url", ""),
    }


def search_giphy(query: str, page: int, limit: int = 24) -> dict[str, Any]:
    api_key = os.environ.get("GIPHY_API_KEY")
    if not api_key:
        raise RuntimeError("GIPHY_API_KEY is not configured")

    page = max(page, 1)
    params = {
        "api_key": api_key,
        "limit": str(limit),
        "offset": str((page - 1) * limit),
        "rating": "pg-13",
        "lang": "zh-CN",
    }
    url = GIPHY_TRENDING_URL
    if query:
        params["q"] = query
        url = GIPHY_SEARCH_URL

    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(request_url, timeout=12) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    return {
        "items": [normalize_giphy_item(item) for item in payload.get("data", [])],
        "page": page,
        "source": "GIPHY",
    }


class StickerHandler(BaseHTTPRequestHandler):
    server_version = "StickerSaver/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            _json_response(self, 200, {"ok": True})
            return
        if parsed.path != "/api/stickers/search":
            _json_response(self, 404, {"error": "not_found"})
            return

        client = _client_id(self)
        if _rate_limited(client):
            _json_response(self, 429, {"error": "rate_limited", "message": "Too many requests"})
            return

        params = urllib.parse.parse_qs(parsed.query)
        query = (params.get("q", [""])[0] or "").strip()
        try:
            page = int(params.get("page", ["1"])[0])
        except ValueError:
            page = 1

        try:
            result = search_giphy(query, page)
        except RuntimeError as exc:
            _json_response(self, 500, {"error": "not_configured", "message": str(exc)})
            return
        except (urllib.error.URLError, TimeoutError) as exc:
            _json_response(self, 502, {"error": "upstream_failed", "message": str(exc)})
            return
        except json.JSONDecodeError:
            _json_response(self, 502, {"error": "bad_upstream_response"})
            return

        _json_response(self, 200, result)


def run(host: str = "127.0.0.1", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), StickerHandler)
    print(f"Sticker server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run(
        os.environ.get("STICKER_SERVER_HOST", "127.0.0.1"),
        int(os.environ.get("STICKER_SERVER_PORT", "8080")),
    )
