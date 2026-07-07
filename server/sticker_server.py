#!/usr/bin/env python3
"""Tiny authorized-source sticker search server.

Set GIPHY_API_KEY before running:
    $env:GIPHY_API_KEY="your-key"
    python server/sticker_server.py
"""

from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


ALAPI_DOUTU_URL = "https://v3.alapi.cn/api/doutu"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
BAIDU_IMAGE_TAG_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"
GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
GIPHY_STICKERS_SEARCH_URL = "https://api.giphy.com/v1/stickers/search"
GIPHY_TRENDING_URL = "https://api.giphy.com/v1/gifs/trending"
KLIPY_API_BASE_URL = "https://api.klipy.com"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
SEARCH_PAGE_LIMIT = 24
SEARCH_CACHE_TTL_SECONDS = 300
PROVIDER_TIMEOUT_SECONDS = {
    "ALAPI": 6,
    "GIPHY": 4,
    "GIPHY Sticker": 4,
    "Klipy": 4,
}
MAX_ANALYSIS_BODY_BYTES = 6 * 1024 * 1024
MAX_PROXY_IMAGE_BYTES = 12 * 1024 * 1024
PROXY_IMAGE_PEEK_BYTES = 4096
HOT_TERM_QUERIES = {
    "哈哈": "haha",
    "笑死": "laughing",
    "绷不住": "laughing",
    "破防": "crying",
    "无语": "speechless",
    "谢谢": "thank you cute reaction",
    "离谱": "confused",
    "绝绝子": "amazing",
    "yyds": "goat",
    "尊嘟假嘟": "really",
    "吗喽": "monkey reaction meme",
    "鼠鼠": "cute mouse",
    "塔菲": "taffy",
}
CONTENT_CATEGORIES = {
    "hot": {"name": "热门", "domestic": "热门 表情包", "global": "trending reaction meme"},
    "anime": {"name": "动漫", "domestic": "动漫 表情包", "global": "anime reaction"},
    "acg": {"name": "二次元", "domestic": "二次元 表情包", "global": "anime cute reaction"},
    "funny": {"name": "搞笑", "domestic": "搞笑 表情包", "global": "funny reaction meme"},
    "cute": {"name": "可爱", "domestic": "可爱 表情包", "global": "cute reaction"},
    "game": {"name": "游戏", "domestic": "游戏 表情包", "global": "game reaction"},
    "movie": {"name": "影视", "domestic": "影视 表情包", "global": "movie reaction"},
    "wallpaper": {"name": "壁纸", "domestic": "壁纸 动漫", "global": "wallpaper anime"},
}
CATEGORY_SEMANTIC_TERMS = {
    "hot": ["热门", "热梗", "表情包", "斗图", "reaction", "meme", "trending"],
    "anime": ["动漫", "番剧", "动画", "角色", "anime", "manga", "cartoon"],
    "acg": ["二次元", "动漫", "ACG", "萌", "番剧", "角色", "anime", "cute"],
    "funny": ["搞笑", "哈哈", "笑死", "绷不住", "沙雕", "funny", "laugh", "lol"],
    "cute": ["可爱", "萌", "猫猫", "狗狗", "贴贴", "cute", "kawaii", "cat", "dog"],
    "game": ["游戏", "手游", "电竞", "角色", "game", "gaming", "player"],
    "movie": ["影视", "电影", "电视剧", "台词", "movie", "film", "drama"],
    "wallpaper": ["壁纸", "头像", "背景图", "高清", "wallpaper", "background", "avatar"],
}
QUERY_SEMANTIC_TERMS = {
    "哈哈": ["哈哈", "大笑", "笑死", "乐", "laugh", "lol", "haha"],
    "笑": ["哈哈", "大笑", "笑死", "laugh", "funny"],
    "无语": ["无语", "沉默", "尴尬", "speechless", "awkward"],
    "谢谢": ["谢谢", "感谢", "thank", "thanks"],
    "猫": ["猫", "猫猫", "喵", "cat", "kitten"],
    "狗": ["狗", "狗狗", "dog", "puppy"],
    "哭": ["哭", "流泪", "破防", "cry", "sad"],
}
_request_log: dict[str, list[float]] = {}
_baidu_token_cache: dict[str, Any] = {}
_search_cache: dict[str, dict[str, Any]] = {}


class UpstreamError(Exception):
    pass


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    handler.close_connection = True


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


def _guess_mime_type(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if path.endswith(".png"):
        return "image/png"
    return "image/gif"


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _extract_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith(("[", "{")):
            try:
                return _extract_urls(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        candidates = stripped.replace("\r", "\n").replace(",", "\n").splitlines()
        return [candidate.strip() for candidate in candidates if candidate.strip().startswith(("http://", "https://"))]
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_extract_urls(item))
        return urls
    if isinstance(value, dict):
        for key in ("url", "src", "image", "img", "gif", "path"):
            url = value.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return [url]
        urls: list[str] = []
        for nested in value.values():
            urls.extend(_extract_urls(nested))
        return urls
    return []


def _proxy_path(url: str) -> str:
    return "/api/stickers/proxy?url=" + urllib.parse.quote(url, safe="")


def normalize_url_item(url: str, source: str, title: str, index: int) -> dict[str, Any]:
    item_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    delivery_url = _proxy_path(url)
    return {
        "id": f"{source.lower()}-{item_id}",
        "title": title or f"{source} sticker {index + 1}",
        "thumbnailUrl": delivery_url,
        "originalUrl": delivery_url,
        "source": source,
        "width": 0,
        "height": 0,
        "mimeType": _guess_mime_type(url),
        "pageUrl": url,
        "importDatetime": "",
        "trendingDatetime": "",
        "upstreamUrl": url,
    }


def normalize_external_image_item(url: str, source: str, title: str, index: int, page_url: str = "") -> dict[str, Any]:
    item = normalize_url_item(url, source, title, index)
    item["pageUrl"] = page_url or url
    item["upstreamUrl"] = url
    return item


def _item_identity(item: dict[str, Any]) -> str:
    for key in ("upstreamUrl", "originalUrl", "thumbnailUrl", "pageUrl", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def _dedupe_items(items: list[dict[str, Any]], limit: int = SEARCH_PAGE_LIMIT) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for item in items:
        identity = _item_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _cache_key(query: str, page: int, category: str) -> str:
    providers = [
        "alapi" if os.environ.get("ALAPI_TOKEN") else "",
        "giphy" if os.environ.get("GIPHY_API_KEY") else "",
        "klipy" if os.environ.get("KLIPY_API_KEY") else "",
    ]
    raw = json.dumps(
        {
            "query": (query or "").strip(),
            "page": max(page, 1),
            "category": (category or "hot").strip().lower(),
            "providers": [provider for provider in providers if provider],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cached_search_result(query: str, page: int, category: str) -> dict[str, Any] | None:
    key = _cache_key(query, page, category)
    cached = _search_cache.get(key)
    now = time.time()
    if not cached or cached.get("expires_at", 0) <= now:
        _search_cache.pop(key, None)
        return None
    payload = json.loads(json.dumps(cached["payload"], ensure_ascii=False))
    payload["cache"] = "hit"
    return payload


def _store_search_result(query: str, page: int, category: str, payload: dict[str, Any]) -> None:
    _search_cache[_cache_key(query, page, category)] = {
        "expires_at": time.time() + SEARCH_CACHE_TTL_SECONDS,
        "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
    }


def _ranking_terms(query: str, category: str) -> list[str]:
    clean_query = (query or "").strip()
    category_key = (category or "hot").strip().lower()
    semantic_terms = CATEGORY_SEMANTIC_TERMS.get(category_key, [])
    if clean_query and category_key == "hot":
        raw_terms = [clean_query]
    else:
        raw_terms = [
            clean_query,
            _category_config(category)["domestic"],
            _category_config(category)["global"],
        ]
    raw_terms.extend(semantic_terms)
    for key, values in QUERY_SEMANTIC_TERMS.items():
        if key and key in clean_query:
            raw_terms.extend(values)
    terms = []
    for raw in raw_terms:
        for part in raw.replace("-", " ").replace("_", " ").split():
            cleaned = part.strip().lower()
            if len(cleaned) >= 2:
                terms.append(cleaned)
        cleaned_raw = raw.strip().lower()
        if len(cleaned_raw) >= 2:
            terms.append(cleaned_raw)
    return _dedupe_terms(terms, 12)


def _source_affinity(source: str) -> int:
    if source == "ALAPI":
        return 120
    if source == "Klipy":
        return 28
    if source == "GIPHY Sticker":
        return 18
    return 10


def _item_search_haystack(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key, ""))
        for key in ("title", "source", "upstreamUrl", "pageUrl", "originalUrl", "mimeType")
    ).lower()


def _score_search_item(item: dict[str, Any], query: str, category: str, index: int) -> int:
    terms = _ranking_terms(query, category)
    haystack = _item_search_haystack(item)
    source = str(item.get("source", ""))
    mime_type = str(item.get("mimeType", "")).lower()
    category_key = (category or "hot").strip().lower()
    clean_query = (query or "").strip().lower()

    score = max(0, 100 - min(index, 100))
    score += _source_affinity(source)

    if clean_query and clean_query in haystack:
        score += 320
    for term in terms:
        if not term:
            continue
        if term in haystack:
            score += 180 if (" " in term or len(term) >= 4) else 70

    category_hits = sum(1 for term in CATEGORY_SEMANTIC_TERMS.get(category_key, []) if term.lower() in haystack)
    score += min(category_hits, 3) * 90

    if source == "ALAPI" and category_hits:
        score += 80
    if source != "ALAPI" and not clean_query and category_key != "hot" and not category_hits:
        score -= 120
    if category_key == "wallpaper":
        if any(token in haystack for token in ("wallpaper", "壁纸", "background", "头像")):
            score += 180
        if "gif" in mime_type:
            score -= 60
    else:
        if "gif" in mime_type or "webp" in mime_type:
            score += 35
    return score


def _rank_search_items(items: list[dict[str, Any]], query: str, category: str, limit: int = SEARCH_PAGE_LIMIT) -> list[dict[str, Any]]:
    scored = []
    seen = set()
    for index, item in enumerate(items):
        identity = _item_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        score = _score_search_item(item, query, category, index)
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _clean_domestic_query(query: str) -> str:
    cleaned = query.strip()
    for word in ("表情包", "斗图", "动图", "gif", "GIF"):
        cleaned = cleaned.replace(word, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned or query.strip()


def resolve_search_query(query: str) -> tuple[str, str]:
    normalized = query.strip().lower()
    if normalized in HOT_TERM_QUERIES:
        return HOT_TERM_QUERIES[normalized], "hot_term"
    return query.strip(), "direct"


def _category_config(category: str) -> dict[str, str]:
    key = (category or "hot").strip().lower()
    return CONTENT_CATEGORIES.get(key, CONTENT_CATEGORIES["hot"])


def _category_name(category: str) -> str:
    return _category_config(category)["name"]


def _category_query(query: str, category: str, provider: str) -> str:
    clean_query = (query or "").strip()
    config = _category_config(category)
    category_key = (category or "hot").strip().lower()
    if category_key == "hot":
        return clean_query or config[provider]
    category_term = config[provider]
    if not clean_query:
        return category_term
    if category_term in clean_query:
        return clean_query
    return f"{clean_query} {category_term}"


def _parse_giphy_datetime(value: str) -> float:
    if not value or value.startswith("0000-00-00"):
        return 0
    try:
        parsed = time.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0
    return time.mktime(parsed)


def _contains_query_hint(item: dict[str, Any], query: str) -> bool:
    title = (item.get("title") or "").lower()
    slug = (item.get("slug") or "").lower()
    for token in query.lower().replace("-", " ").split():
        if len(token) >= 3 and (token in title or token in slug):
            return True
    return False


def _rank_giphy_items(items: list[dict[str, Any]], resolved_query: str) -> list[dict[str, Any]]:
    now = time.time()
    scored = []
    for index, item in enumerate(items):
        import_time = _parse_giphy_datetime(item.get("import_datetime", ""))
        trending_time = _parse_giphy_datetime(item.get("trending_datetime", ""))
        recency_time = max(import_time, trending_time)
        age_days = (now - recency_time) / 86400 if recency_time else 3650
        recency_score = max(0, 80 - min(age_days, 365) * 0.2)
        trend_score = 80 if trending_time else 0
        query_score = 240 if _contains_query_hint(item, resolved_query) else 0
        giphy_order_score = max(0, 10000 - index * 220)
        score = giphy_order_score + recency_score + trend_score + query_score
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def normalize_giphy_item(item: dict[str, Any], source: str = "GIPHY") -> dict[str, Any]:
    images = item.get("images") or {}
    original = _pick_image(images, "original")
    preview = _pick_image(images, "fixed_width_small", "downsized", "preview_gif", "original")
    return {
        "id": item.get("id", ""),
        "title": item.get("title") or "Untitled sticker",
        "thumbnailUrl": preview.get("url", ""),
        "originalUrl": original.get("url") or preview.get("url", ""),
        "source": source,
        "width": int(original.get("width") or preview.get("width") or 0),
        "height": int(original.get("height") or preview.get("height") or 0),
        "mimeType": "image/gif",
        "pageUrl": item.get("url", ""),
        "importDatetime": item.get("import_datetime", ""),
        "trendingDatetime": item.get("trending_datetime", ""),
    }


def search_giphy(
    query: str,
    page: int,
    limit: int = SEARCH_PAGE_LIMIT,
    search_url: str = GIPHY_SEARCH_URL,
    source: str = "GIPHY",
    category: str = "hot",
    timeout: int = PROVIDER_TIMEOUT_SECONDS["GIPHY"],
) -> dict[str, Any]:
    api_key = os.environ.get("GIPHY_API_KEY")
    if not api_key:
        raise RuntimeError("GIPHY_API_KEY is not configured")

    page = max(page, 1)
    search_query = _category_query(query, category, "global")
    resolved_query, queryMode = resolve_search_query(search_query)
    fetch_limit = min(max(limit * 2, limit), 50)
    params = {
        "api_key": api_key,
        "limit": str(fetch_limit),
        "offset": str((page - 1) * fetch_limit),
        "rating": "pg-13",
    }
    url = GIPHY_TRENDING_URL if search_url == GIPHY_SEARCH_URL else search_url
    if resolved_query:
        params["q"] = resolved_query
        url = search_url

    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(request_url, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    ranked_items = _rank_giphy_items(payload.get("data", []), resolved_query)[:limit]
    return {
        "items": [normalize_giphy_item(item, source) for item in ranked_items],
        "page": page,
        "source": source,
        "sources": [source],
        "query": query,
        "resolvedQuery": resolved_query,
        "category": category or "hot",
        "categoryName": _category_name(category),
        "queryMode": queryMode,
        "sortMode": "giphy_popularity_recency_proxy",
        "hasMore": len(ranked_items) >= limit,
    }


def search_giphy_stickers(
    query: str,
    page: int,
    limit: int = SEARCH_PAGE_LIMIT,
    category: str = "hot",
    timeout: int = PROVIDER_TIMEOUT_SECONDS["GIPHY Sticker"],
) -> dict[str, Any]:
    return search_giphy(query, page, limit, GIPHY_STICKERS_SEARCH_URL, "GIPHY Sticker", category, timeout)


def _find_first_url(value: Any) -> str:
    urls = _extract_urls(value)
    if not urls:
        return ""
    image_urls = [url for url in urls if _guess_mime_type(url).startswith("image/")]
    return image_urls[0] if image_urls else urls[0]


def _extract_klipy_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("data", "results", "items", "gifs"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_klipy_items(value)
            if nested:
                return nested
    return []


def normalize_klipy_item(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    url = ""
    for key in ("url", "gif_url", "media_url", "image_url", "preview_url", "thumbnail_url"):
        candidate = item.get(key)
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            url = candidate
            break
    if not url:
        for key in ("media", "images", "assets", "files", "renditions"):
            url = _find_first_url(item.get(key))
            if url:
                break
    if not url:
        url = _find_first_url(item)
    if not url:
        return None
    title = str(item.get("title") or item.get("name") or item.get("slug") or f"Klipy sticker {index + 1}")
    page_url = str(item.get("share_url") or item.get("page_url") or item.get("url") or url)
    return normalize_external_image_item(url, "Klipy", title, index, page_url)


def search_klipy(
    query: str,
    page: int,
    limit: int = SEARCH_PAGE_LIMIT,
    category: str = "hot",
    timeout: int = PROVIDER_TIMEOUT_SECONDS["Klipy"],
) -> dict[str, Any]:
    app_key = os.environ.get("KLIPY_API_KEY")
    if not app_key:
        raise RuntimeError("KLIPY_API_KEY is not configured")

    page = max(page, 1)
    search_query = _category_query(query, category, "global")
    params = urllib.parse.urlencode(
        {
            "page": str(page),
            "per_page": str(limit),
            "q": search_query,
            "locale": "zh-CN",
        }
    )
    request = urllib.request.Request(
        f"{KLIPY_API_BASE_URL}/api/v1/{urllib.parse.quote(app_key)}/gifs/search?{params}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    items = []
    for item in _extract_klipy_items(payload):
        normalized = normalize_klipy_item(item, len(items))
        if normalized:
            items.append(normalized)
        if len(items) >= limit:
            break
    return {
        "items": items,
        "page": page,
        "source": "Klipy",
        "sources": ["Klipy"],
        "query": query,
        "resolvedQuery": search_query,
        "category": category or "hot",
        "categoryName": _category_name(category),
        "queryMode": "klipy",
        "sortMode": "klipy_default",
        "hasMore": len(items) >= limit,
    }


def _filter_proxyable_urls(urls: list[str], limit: int = SEARCH_PAGE_LIMIT) -> list[str]:
    if not urls:
        return []
    accepted: list[str] = []
    pool = ThreadPoolExecutor(max_workers=min(8, max(1, len(urls))))
    try:
        future_to_url = {pool.submit(can_proxy_image, url): url for url in urls}
        for future in as_completed(future_to_url):
            if future.result():
                accepted.append(future_to_url[future])
            if len(accepted) >= limit:
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return accepted[:limit]


def search_alapi(
    query: str,
    page: int,
    limit: int = SEARCH_PAGE_LIMIT,
    category: str = "hot",
    timeout: int = PROVIDER_TIMEOUT_SECONDS["ALAPI"],
) -> dict[str, Any]:
    token = os.environ.get("ALAPI_TOKEN")
    if not token:
        raise RuntimeError("ALAPI_TOKEN is not configured")

    page = max(page, 1)
    search_query = _clean_domestic_query(_category_query(query, category, "domestic"))
    params = urllib.parse.urlencode({"token": token, "keyword": search_query, "page": str(page)})
    request = urllib.request.Request(
        f"{ALAPI_DOUTU_URL}?{params}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    code = payload.get("code")
    if code not in (0, 200, "0", "200", None):
        raise RuntimeError(payload.get("message") or f"ALAPI returned code {code}")

    candidate_urls = _extract_urls(payload.get("data", {}))[: max(limit * 2, limit)]
    urls = _filter_proxyable_urls(candidate_urls, limit)
    title = f"{query.strip() or '热门'} 表情包"
    return {
        "items": [normalize_url_item(url, "ALAPI", title, index) for index, url in enumerate(urls)],
        "page": page,
        "source": "ALAPI",
        "sources": ["ALAPI"],
        "query": query,
        "resolvedQuery": search_query,
        "category": category or "hot",
        "categoryName": _category_name(category),
        "queryMode": "domestic",
        "sortMode": "alapi_default",
        "hasMore": len(urls) >= limit,
    }


def search_stickers(query: str, page: int, category: str = "hot") -> dict[str, Any]:
    cached = _cached_search_result(query, page, category)
    if cached:
        return cached

    provider_results = []
    errors = []
    providers = []
    if os.environ.get("ALAPI_TOKEN"):
        providers.append(("ALAPI", lambda: search_alapi(query, page, category=category)))
    providers.extend(
        [
            ("GIPHY", lambda: search_giphy(query, page, category=category)),
            ("GIPHY Sticker", lambda: search_giphy_stickers(query, page, category=category)),
        ]
    )
    if os.environ.get("KLIPY_API_KEY"):
        providers.append(("Klipy", lambda: search_klipy(query, page, category=category)))

    with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:
        future_to_name = {pool.submit(search_fn): name for name, search_fn in providers}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
                if result.get("items"):
                    provider_results.append(result)
            except RuntimeError as exc:
                errors.append(f"{name}: {exc}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{name}: {exc}")

    if not provider_results:
        raise RuntimeError("; ".join(errors) or "No sticker search provider configured")

    all_items = []
    all_sources = []
    has_more = False
    resolved_queries = []
    for result in provider_results:
        all_items.extend(result.get("items", []))
        all_sources.extend(result.get("sources", [result.get("source", "")]))
        has_more = has_more or bool(result.get("hasMore"))
        resolved_query = result.get("resolvedQuery")
        if resolved_query:
            resolved_queries.append(str(resolved_query))

    ranked_items = _rank_search_items(all_items, query, category)
    deduped_sources = _dedupe_terms([source for source in all_sources if source], 6)
    response = {
        "items": ranked_items,
        "page": page,
        "source": "+".join(deduped_sources),
        "sources": deduped_sources,
        "query": query,
        "resolvedQuery": " / ".join(_dedupe_terms(resolved_queries, 4)),
        "category": category or "hot",
        "categoryName": _category_name(category),
        "queryMode": "multi_source_ranked",
        "sortMode": "semantic_relevance_domestic_first_cached",
        "hasMore": has_more,
        "cache": "miss",
    }
    if errors:
        response["fallbackReason"] = "; ".join(errors)
    _store_search_result(query, page, category, response)
    return response


def proxy_image(url: str) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("invalid image url")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 StickerSaver/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        data = response.read(MAX_PROXY_IMAGE_BYTES + 1)
    if len(data) > MAX_PROXY_IMAGE_BYTES:
        raise ValueError("image too large")
    sniffed_type = _sniff_image_mime(data)
    if not sniffed_type:
        raise ValueError("upstream did not return a supported image")
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        content_type = sniffed_type
    return data, content_type


def can_proxy_image(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 StickerSaver/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = response.read(PROXY_IMAGE_PEEK_BYTES)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False
    return bool(_sniff_image_mime(data))


def _binary_response(handler: BaseHTTPRequestHandler, status: int, data: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    handler.close_connection = True


def _extract_openai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _openai_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    if not body:
        return f"OpenAI HTTP {exc.code}: {exc.reason}"
    try:
        payload = json.loads(body)
        message = payload.get("error", {}).get("message") or body
    except json.JSONDecodeError:
        message = body
    return f"OpenAI HTTP {exc.code}: {message}"


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _parse_keywords_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
        cleaned = _coerce_string_list(payload.get("keywords") or [])
        query = str(payload.get("query") or " ".join(cleaned[:3])).strip()
        search_queries = _coerce_string_list(payload.get("searchQueries") or [])
        if query and query not in search_queries:
            search_queries.insert(0, query)
        return {
            "query": query,
            "keywords": cleaned,
            "characterCandidates": _coerce_string_list(payload.get("characterCandidates") or []),
            "visualTags": _coerce_string_list(payload.get("visualTags") or []),
            "emotionTags": _coerce_string_list(payload.get("emotionTags") or []),
            "searchQueries": search_queries[:5],
        }
    except json.JSONDecodeError:
        cleaned = [part.strip() for part in stripped.replace("，", ",").replace("\n", ",").split(",") if part.strip()]
        query = " ".join(cleaned[:3])
        return {
            "query": query,
            "keywords": cleaned,
            "characterCandidates": [],
            "visualTags": [],
            "emotionTags": [],
            "searchQueries": [query] if query else [],
        }


def _baidu_credentials() -> tuple[str, str]:
    api_key = os.environ.get("BAIDU_API_KEY") or os.environ.get("BAIDU_OCR_API_KEY")
    secret_key = os.environ.get("BAIDU_SECRET_KEY") or os.environ.get("BAIDU_OCR_SECRET_KEY")
    return (api_key or "").strip(), (secret_key or "").strip()


def _baidu_access_token() -> str:
    api_key, secret_key = _baidu_credentials()
    if not api_key or not secret_key:
        raise RuntimeError("BAIDU_API_KEY and BAIDU_SECRET_KEY are not configured")

    cache_key = hashlib.sha1(f"{api_key}:{secret_key}".encode("utf-8")).hexdigest()
    cached = _baidu_token_cache.get(cache_key)
    now = time.time()
    if cached and cached.get("expires_at", 0) > now + 60:
        return str(cached["token"])

    params = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        }
    )
    request = urllib.request.Request(f"{BAIDU_TOKEN_URL}?{params}", method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise UpstreamError(payload.get("error_description") or payload.get("error") or "Baidu token request failed")

    expires_in = int(payload.get("expires_in") or 2592000)
    _baidu_token_cache[cache_key] = {"token": token, "expires_at": now + expires_in}
    return str(token)


def _post_baidu_image(endpoint: str, token: str, data_base64: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({"image": data_base64}).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}?access_token={urllib.parse.quote(token)}",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("error_code") or payload.get("error_msg"):
        raise UpstreamError(f"Baidu error {payload.get('error_code')}: {payload.get('error_msg')}")
    return payload


def _dedupe_terms(terms: list[str], limit: int = 8) -> list[str]:
    seen = set()
    result = []
    for term in terms:
        cleaned = " ".join(str(term).strip().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _baidu_analyze_media(data_base64: str, file_name: str = "") -> dict[str, Any]:
    token = _baidu_access_token()
    ocr_payload: dict[str, Any] = {}
    tag_payload: dict[str, Any] = {}
    errors = []
    try:
        ocr_payload = _post_baidu_image(BAIDU_OCR_URL, token, data_base64)
    except Exception as exc:
        errors.append(f"OCR: {exc}")
    try:
        tag_payload = _post_baidu_image(BAIDU_IMAGE_TAG_URL, token, data_base64)
    except Exception as exc:
        errors.append(f"image tags: {exc}")

    words = _dedupe_terms([item.get("words", "") for item in ocr_payload.get("words_result", []) if isinstance(item, dict)], 5)
    tags = _dedupe_terms([item.get("keyword", "") for item in tag_payload.get("result", []) if isinstance(item, dict)], 6)
    keywords = _dedupe_terms(words + tags, 8)
    if not keywords:
        if errors:
            raise UpstreamError("; ".join(errors))
        raise UpstreamError("Baidu did not return OCR text or image tags")

    search_queries = []
    for word in words[:3]:
        search_queries.append(f"{word} 表情包")
    if words and tags:
        search_queries.append(f"{words[0]} {tags[0]} 表情包")
    for tag in tags[:4]:
        search_queries.append(f"{tag} 表情包")
    if file_name:
        cleaned_name = file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
        if cleaned_name:
            search_queries.append(f"{cleaned_name} 表情包")
    search_queries = _dedupe_terms(search_queries, 5)
    query = search_queries[0] if search_queries else f"{keywords[0]} 表情包"
    return {
        "query": query,
        "keywords": keywords,
        "characterCandidates": [],
        "visualTags": tags,
        "emotionTags": [],
        "searchQueries": search_queries or [query],
        "model": "baidu-ocr-advanced-general",
        "source": "baidu",
    }


def _openai_analyze_media(data_base64: str, mime_type: str, file_name: str = "") -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    safe_mime = mime_type if mime_type and mime_type.startswith("image/") else "image/jpeg"
    primary_model = os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini")
    fallback_model = os.environ.get("OPENAI_VISION_FALLBACK_MODEL", "gpt-4o-mini")
    prompt = (
        "你是高级中文表情包搜索策划器。观察这张图片或视频截图，生成更像抖音、QQ、微信斗图场景会用的搜索词。"
        "优先级：1. 如果能较确定识别动漫/影视/游戏角色或作品 IP，先给角色名或作品名；"
        "2. 再补充情绪、动作、台词含义、外观特征；3. 如果角色不确定，不要硬猜，只用外观特征和情绪。"
        "返回严格 JSON，不要解释，不要 Markdown。格式："
        "{\"query\":\"最推荐搜索词\","
        "\"searchQueries\":[\"角色名 情绪 表情包\",\"作品名 表情包\",\"外观特征 情绪 表情包\"],"
        "\"characterCandidates\":[\"可能角色或IP\"],"
        "\"visualTags\":[\"发色\",\"服装\",\"场景\"],"
        "\"emotionTags\":[\"微笑\",\"挥手\"],"
        "\"keywords\":[\"用于搜索的短词\"]}。"
        "searchQueries 最多 5 个，优先中文，关键词要短，适合表情包搜索。"
    )
    if file_name:
        prompt += f" 文件名：{file_name}"
    last_error = ""
    payload = None
    used_model = primary_model
    models = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models.append(fallback_model)
    for model in models:
        body = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:{safe_mime};base64,{data_base64}"},
                    ],
                }
            ],
            "max_output_tokens": 180,
        }
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            used_model = model
            break
        except urllib.error.HTTPError as exc:
            last_error = _openai_error_message(exc)
            if exc.code not in (400, 404):
                break
    if payload is None:
        raise UpstreamError(last_error or "OpenAI request failed")
    parsed = _parse_keywords_text(_extract_openai_text(payload))
    if not parsed["query"]:
        parsed["query"] = "热门 表情包"
    if not parsed["searchQueries"]:
        parsed["searchQueries"] = [parsed["query"]]
    parsed["model"] = used_model
    parsed["source"] = "openai"
    return parsed


def analyze_media(data_base64: str, mime_type: str, file_name: str = "") -> dict[str, Any]:
    if not data_base64:
        raise ValueError("dataBase64 is required")

    errors = []
    try:
        return _openai_analyze_media(data_base64, mime_type, file_name)
    except (RuntimeError, UpstreamError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    try:
        result = _baidu_analyze_media(data_base64, file_name)
        if errors:
            result["fallbackReason"] = "; ".join(errors)
        return result
    except RuntimeError as exc:
        errors.append(str(exc))
    except (UpstreamError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    raise UpstreamError("; ".join(error for error in errors if error) or "media analysis failed")


class StickerHandler(BaseHTTPRequestHandler):
    server_version = "StickerSaver/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            _json_response(self, 200, {"ok": True})
            return
        if parsed.path == "/api/stickers/proxy":
            params = urllib.parse.parse_qs(parsed.query)
            url = (params.get("url", [""])[0] or "").strip()
            try:
                data, content_type = proxy_image(url)
            except ValueError as exc:
                _json_response(self, 400, {"error": "bad_request", "message": str(exc)})
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                _json_response(self, 502, {"error": "upstream_failed", "message": str(exc)})
                return
            _binary_response(self, 200, data, content_type)
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
        category = (params.get("category", ["hot"])[0] or "hot").strip()
        try:
            page = int(params.get("page", ["1"])[0])
        except ValueError:
            page = 1

        try:
            result = search_stickers(query, page, category)
        except RuntimeError as exc:
            _json_response(self, 500, {"error": "not_configured", "message": str(exc)})
            return
        except (UpstreamError, urllib.error.URLError, TimeoutError) as exc:
            _json_response(self, 502, {"error": "upstream_failed", "message": str(exc)})
            return
        except json.JSONDecodeError:
            _json_response(self, 502, {"error": "bad_upstream_response"})
            return

        _json_response(self, 200, result)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/stickers/analyze-media":
            _json_response(self, 404, {"error": "not_found"})
            return

        client = _client_id(self)
        if _rate_limited(client):
            _json_response(self, 429, {"error": "rate_limited", "message": "Too many requests"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_ANALYSIS_BODY_BYTES:
            _json_response(
                self,
                413,
                {
                    "error": "payload_too_large",
                    "message": f"request body must be 1-{MAX_ANALYSIS_BODY_BYTES} bytes",
                },
            )
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = analyze_media(
                payload.get("dataBase64", ""),
                payload.get("mimeType", "image/jpeg"),
                payload.get("fileName", ""),
            )
        except RuntimeError as exc:
            _json_response(self, 500, {"error": "not_configured", "message": str(exc)})
            return
        except ValueError as exc:
            _json_response(self, 400, {"error": "bad_request", "message": str(exc)})
            return
        except (urllib.error.URLError, TimeoutError) as exc:
            _json_response(self, 502, {"error": "upstream_failed", "message": str(exc)})
            return
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "bad_json"})
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
