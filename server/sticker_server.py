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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


ALAPI_DOUTU_URL = "https://v3.alapi.cn/api/doutu"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
BAIDU_IMAGE_TAG_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"
GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
GIPHY_TRENDING_URL = "https://api.giphy.com/v1/gifs/trending"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
SEARCH_PAGE_LIMIT = 24
MAX_ANALYSIS_BODY_BYTES = 6 * 1024 * 1024
MAX_PROXY_IMAGE_BYTES = 12 * 1024 * 1024
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
_request_log: dict[str, list[float]] = {}
_baidu_token_cache: dict[str, Any] = {}


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
        "importDatetime": item.get("import_datetime", ""),
        "trendingDatetime": item.get("trending_datetime", ""),
    }


def search_giphy(query: str, page: int, limit: int = SEARCH_PAGE_LIMIT) -> dict[str, Any]:
    api_key = os.environ.get("GIPHY_API_KEY")
    if not api_key:
        raise RuntimeError("GIPHY_API_KEY is not configured")

    page = max(page, 1)
    resolved_query, queryMode = resolve_search_query(query)
    fetch_limit = min(max(limit * 2, limit), 50)
    params = {
        "api_key": api_key,
        "limit": str(fetch_limit),
        "offset": str((page - 1) * fetch_limit),
        "rating": "pg-13",
    }
    url = GIPHY_TRENDING_URL
    if resolved_query:
        params["q"] = resolved_query
        url = GIPHY_SEARCH_URL

    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(request_url, timeout=12) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    ranked_items = _rank_giphy_items(payload.get("data", []), resolved_query)[:limit]
    return {
        "items": [normalize_giphy_item(item) for item in ranked_items],
        "page": page,
        "source": "GIPHY",
        "sources": ["GIPHY"],
        "query": query,
        "resolvedQuery": resolved_query,
        "queryMode": queryMode,
        "sortMode": "giphy_popularity_recency_proxy",
        "hasMore": len(ranked_items) >= limit,
    }


def search_alapi(query: str, page: int, limit: int = SEARCH_PAGE_LIMIT) -> dict[str, Any]:
    token = os.environ.get("ALAPI_TOKEN")
    if not token:
        raise RuntimeError("ALAPI_TOKEN is not configured")

    page = max(page, 1)
    search_query = _clean_domestic_query(query)
    params = urllib.parse.urlencode({"token": token, "keyword": search_query, "page": str(page)})
    request = urllib.request.Request(
        f"{ALAPI_DOUTU_URL}?{params}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    code = payload.get("code")
    if code not in (0, 200, "0", "200", None):
        raise RuntimeError(payload.get("message") or f"ALAPI returned code {code}")

    urls = _extract_urls(payload.get("data", {}))[:limit]
    title = f"{query.strip() or '热门'} 表情包"
    return {
        "items": [normalize_url_item(url, "ALAPI", title, index) for index, url in enumerate(urls)],
        "page": page,
        "source": "ALAPI",
        "sources": ["ALAPI"],
        "query": query,
        "resolvedQuery": query.strip(),
        "queryMode": "domestic",
        "sortMode": "alapi_default",
        "hasMore": len(urls) >= limit,
    }


def search_stickers(query: str, page: int) -> dict[str, Any]:
    alapi_result = None
    alapi_error = None
    if os.environ.get("ALAPI_TOKEN"):
        try:
            alapi_result = search_alapi(query, page)
            if len(alapi_result["items"]) >= SEARCH_PAGE_LIMIT:
                return alapi_result
        except (RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            alapi_error = str(exc)
            print(f"ALAPI search failed, falling back to GIPHY: {exc}")

    try:
        giphy_result = search_giphy(query, page)
    except RuntimeError:
        if alapi_result and alapi_result["items"]:
            return alapi_result
        raise

    if not alapi_result or not alapi_result["items"]:
        if alapi_error:
            giphy_result["fallbackReason"] = f"ALAPI: {alapi_error}"
        return giphy_result

    items = _dedupe_items(alapi_result["items"] + giphy_result["items"])
    return {
        "items": items,
        "page": page,
        "source": "ALAPI+GIPHY",
        "sources": ["ALAPI", "GIPHY"],
        "query": query,
        "resolvedQuery": alapi_result.get("resolvedQuery") or giphy_result.get("resolvedQuery", query),
        "queryMode": "domestic_plus_fallback",
        "sortMode": "alapi_then_giphy",
        "hasMore": bool(alapi_result.get("hasMore") or giphy_result.get("hasMore")),
    }


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
    if not content_type.startswith("image/"):
        guessed = _guess_mime_type(url)
        if guessed == "image/gif":
            raise ValueError("upstream did not return an image")
        content_type = guessed
    return data, content_type


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
        try:
            page = int(params.get("page", ["1"])[0])
        except ValueError:
            page = 1

        try:
            result = search_stickers(query, page)
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
