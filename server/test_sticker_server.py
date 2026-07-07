import json
import os
import urllib.parse
import urllib.error
import unittest
from unittest import mock

import sticker_server


class StickerServerTests(unittest.TestCase):
    def setUp(self):
        sticker_server._request_log.clear()
        sticker_server._baidu_token_cache.clear()
        sticker_server._search_cache.clear()

    def test_normalize_giphy_item(self):
        item = {
            "id": "abc",
            "title": "thanks",
            "url": "https://giphy.com/gifs/abc",
            "import_datetime": "2026-01-01 00:00:00",
            "trending_datetime": "2026-01-02 00:00:00",
            "images": {
                "original": {"url": "https://media.giphy.com/a.gif", "width": "320", "height": "240"},
                "fixed_width_small": {"url": "https://media.giphy.com/a-small.gif", "width": "100", "height": "75"},
            },
        }

        result = sticker_server.normalize_giphy_item(item)

        self.assertEqual(result["id"], "abc")
        self.assertEqual(result["title"], "thanks")
        self.assertEqual(result["thumbnailUrl"], "https://media.giphy.com/a-small.gif")
        self.assertEqual(result["originalUrl"], "https://media.giphy.com/a.gif")
        self.assertEqual(result["mimeType"], "image/gif")
        self.assertEqual(result["width"], 320)
        self.assertEqual(result["importDatetime"], "2026-01-01 00:00:00")
        self.assertEqual(result["trendingDatetime"], "2026-01-02 00:00:00")

    def test_rate_limit_blocks_after_window_quota(self):
        now = 1000.0
        for _ in range(sticker_server.RATE_LIMIT_MAX_REQUESTS):
            self.assertFalse(sticker_server._rate_limited("client", now))

        self.assertTrue(sticker_server._rate_limited("client", now))

    def test_hot_term_query_resolution(self):
        resolved, mode = sticker_server.resolve_search_query("哈哈")

        self.assertEqual(resolved, "haha")
        self.assertEqual(mode, "hot_term")

    def test_extract_urls_from_alapi_shapes(self):
        payload = {
            "data": [
                {"url": "https://example.com/one.gif"},
                {"img": "https://example.com/two.webp"},
            ]
        }

        urls = sticker_server._extract_urls(payload)

        self.assertEqual(urls, ["https://example.com/one.gif", "https://example.com/two.webp"])

    def test_normalize_url_item(self):
        result = sticker_server.normalize_url_item("https://example.com/a.webp", "ALAPI", "哈哈 表情包", 0)

        self.assertEqual(result["title"], "哈哈 表情包")
        self.assertTrue(result["thumbnailUrl"].startswith("/api/stickers/proxy?url="))
        self.assertEqual(result["upstreamUrl"], "https://example.com/a.webp")
        self.assertEqual(result["source"], "ALAPI")
        self.assertEqual(result["mimeType"], "image/webp")

    def test_clean_domestic_query_removes_generic_words(self):
        self.assertEqual(sticker_server._clean_domestic_query("塔菲 表情包 gif"), "塔菲")

    def test_parse_keywords_text_json(self):
        parsed = sticker_server._parse_keywords_text(
            '{"query":"大笑 哈哈","searchQueries":["大笑 哈哈 表情包"],"keywords":["大笑","哈哈"],"characterCandidates":["蜡笔小新"]}'
        )

        self.assertEqual(parsed["query"], "大笑 哈哈")
        self.assertEqual(parsed["keywords"], ["大笑", "哈哈"])
        self.assertEqual(parsed["searchQueries"], ["大笑 哈哈", "大笑 哈哈 表情包"])
        self.assertEqual(parsed["characterCandidates"], ["蜡笔小新"])

    def test_extract_openai_text_from_output(self):
        payload = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"query":"震惊 表情包","keywords":["震惊"]}'}
                    ]
                }
            ]
        }

        self.assertIn("震惊", sticker_server._extract_openai_text(payload))

    def test_rank_prefers_trending_recent_items(self):
        old = {
            "id": "old",
            "title": "old lol",
            "import_datetime": "2015-01-01 00:00:00",
            "images": {"original": {"url": "https://example.com/old.gif"}},
        }
        recent = {
            "id": "recent",
            "title": "fresh lol",
            "import_datetime": "2099-01-01 00:00:00",
            "trending_datetime": "2099-01-01 00:00:00",
            "images": {"original": {"url": "https://example.com/recent.gif"}},
        }

        ranked = sticker_server._rank_giphy_items([old, recent], "lol")

        self.assertEqual(ranked[0]["id"], "recent")

    @mock.patch.dict(os.environ, {"GIPHY_API_KEY": "test-key"}, clear=False)
    @mock.patch("urllib.request.urlopen")
    def test_search_giphy_maps_response(self, urlopen):
        payload = {
            "data": [
                {
                    "id": "one",
                    "title": "thanks",
                    "images": {
                        "original": {"url": "https://example.com/one.gif", "width": "10", "height": "20"}
                    },
                }
            ]
        }
        api_response = mock.MagicMock()
        api_response.__enter__().read.return_value = json.dumps(payload).encode("utf-8")
        image_response = mock.MagicMock()
        image_response.__enter__().read.return_value = b"GIF89a"
        urlopen.side_effect = [api_response, image_response]

        result = sticker_server.search_giphy("thanks", 2, limit=1)

        self.assertEqual(result["page"], 2)
        self.assertEqual(result["items"][0]["originalUrl"], "https://example.com/one.gif")
        requested_url = urlopen.call_args.args[0]
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(requested_url).query)
        self.assertEqual(parsed["q"], ["thanks"])
        self.assertEqual(parsed["offset"], ["2"])

    @mock.patch.dict(os.environ, {"GIPHY_API_KEY": "test-key"}, clear=False)
    @mock.patch("urllib.request.urlopen")
    def test_search_giphy_uses_hot_term_query(self, urlopen):
        payload = {"data": []}
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

        result = sticker_server.search_giphy("哈哈", 1, limit=24)

        self.assertEqual(result["resolvedQuery"], "haha")
        self.assertEqual(result["queryMode"], "hot_term")
        requested_url = urlopen.call_args.args[0]
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(requested_url).query)
        self.assertEqual(parsed["q"], ["haha"])
        self.assertEqual(parsed["limit"], ["48"])
        self.assertNotIn("lang", parsed)

    @mock.patch.dict(os.environ, {"KLIPY_API_KEY": "klipy-key"}, clear=False)
    @mock.patch("urllib.request.urlopen")
    def test_search_klipy_maps_response(self, urlopen):
        payload = {
            "data": [
                {
                    "title": "funny cat",
                    "slug": "funny-cat",
                    "images": {"original": {"url": "https://cdn.example.com/cat.gif"}},
                    "share_url": "https://klipy.example.com/cat",
                }
            ]
        }
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

        result = sticker_server.search_klipy("cat", 1, limit=1)

        self.assertEqual(result["source"], "Klipy")
        self.assertEqual(result["items"][0]["source"], "Klipy")
        self.assertEqual(result["items"][0]["upstreamUrl"], "https://cdn.example.com/cat.gif")
        request = urlopen.call_args.args[0]
        self.assertIn("/api/v1/klipy-key/gifs/search", request.full_url)

    @mock.patch("urllib.request.urlopen")
    @mock.patch("sticker_server.can_proxy_image", return_value=True)
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_alapi_maps_response(self, can_proxy_image, urlopen):
        payload = {
            "code": 200,
            "message": "success",
            "data": {"data": ["https://example.com/one.gif", "https://example.com/two.jpg"]},
        }
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

        result = sticker_server.search_alapi("哈哈", 2, limit=1)

        self.assertEqual(result["source"], "ALAPI")
        self.assertEqual(result["queryMode"], "domestic")
        self.assertTrue(result["items"][0]["originalUrl"].startswith("/api/stickers/proxy?url="))
        self.assertEqual(result["items"][0]["upstreamUrl"], "https://example.com/one.gif")
        request = urlopen.call_args.args[0]
        parsed_url = urllib.parse.urlparse(request.full_url)
        self.assertEqual(f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}", sticker_server.ALAPI_DOUTU_URL)
        params = urllib.parse.parse_qs(parsed_url.query)
        self.assertEqual(params["token"], ["test-token"])
        self.assertEqual(params["keyword"], ["哈哈"])
        self.assertEqual(params["page"], ["2"])

    @mock.patch("urllib.request.urlopen")
    @mock.patch("sticker_server.can_proxy_image", return_value=True)
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_alapi_applies_category_query(self, can_proxy_image, urlopen):
        payload = {
            "code": 200,
            "message": "success",
            "data": {"data": ["https://example.com/anime.gif"]},
        }
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

        result = sticker_server.search_alapi("", 1, limit=1, category="anime")

        self.assertEqual(result["categoryName"], "动漫")
        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(params["keyword"], ["动漫"])

    @mock.patch("urllib.request.urlopen")
    def test_proxy_image_returns_image_bytes(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.headers.get.return_value = "image/png"
        response.read.return_value = b"\x89PNG\r\n\x1a\npng-data"

        data, content_type = sticker_server.proxy_image("http://i0.hdslb.com/test.png")

        self.assertEqual(data, b"\x89PNG\r\n\x1a\npng-data")
        self.assertEqual(content_type, "image/png")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://i0.hdslb.com/test.png")

    @mock.patch("urllib.request.urlopen")
    def test_proxy_image_rejects_non_image_body(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.headers.get.return_value = "text/html"
        response.read.return_value = b"<html>forbidden</html>"

        with self.assertRaises(ValueError):
            sticker_server.proxy_image("http://example.com/not-image.jpg")

    @mock.patch("sticker_server.search_giphy")
    @mock.patch("sticker_server.search_alapi")
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_stickers_falls_back_when_alapi_empty(self, search_alapi, search_giphy):
        search_alapi.return_value = {"items": []}
        search_giphy.return_value = {"items": [{"id": "giphy"}], "source": "GIPHY"}

        result = sticker_server.search_stickers("哈哈", 1)

        self.assertEqual(result["source"], "GIPHY")

    @mock.patch("sticker_server.search_giphy")
    @mock.patch("sticker_server.search_alapi")
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_stickers_merges_giphy_when_alapi_is_sparse(self, search_alapi, search_giphy):
        search_alapi.return_value = {
            "items": [
                {"id": "alapi-one", "upstreamUrl": "https://example.com/one.gif", "source": "ALAPI"},
            ],
            "source": "ALAPI",
            "resolvedQuery": "haha",
            "hasMore": False,
        }
        search_giphy.return_value = {
            "items": [
                {"id": "giphy-duplicate", "originalUrl": "https://example.com/one.gif", "source": "GIPHY"},
                {"id": "giphy-two", "originalUrl": "https://example.com/two.gif", "source": "GIPHY"},
            ],
            "source": "GIPHY",
            "resolvedQuery": "haha",
            "hasMore": True,
        }

        result = sticker_server.search_stickers("鍝堝搱", 1)

        self.assertEqual(result["source"], "ALAPI+GIPHY")
        self.assertEqual(result["sources"], ["ALAPI", "GIPHY"])
        self.assertEqual([item["id"] for item in result["items"]], ["alapi-one", "giphy-two"])
        self.assertTrue(result["hasMore"])

    @mock.patch("sticker_server.search_giphy_stickers")
    @mock.patch("sticker_server.search_giphy")
    @mock.patch("sticker_server.search_alapi")
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_stickers_uses_short_cache(self, search_alapi, search_giphy, search_giphy_stickers):
        search_alapi.return_value = {
            "items": [{"id": "alapi-one", "title": "哈哈 表情包", "source": "ALAPI"}],
            "source": "ALAPI",
            "sources": ["ALAPI"],
            "resolvedQuery": "哈哈",
            "hasMore": False,
        }
        search_giphy.return_value = {"items": [], "source": "GIPHY", "sources": ["GIPHY"], "hasMore": False}
        search_giphy_stickers.return_value = {
            "items": [],
            "source": "GIPHY Sticker",
            "sources": ["GIPHY Sticker"],
            "hasMore": False,
        }

        first = sticker_server.search_stickers("哈哈", 1, "hot")
        second = sticker_server.search_stickers("哈哈", 1, "hot")

        self.assertEqual(first["cache"], "miss")
        self.assertEqual(second["cache"], "hit")
        self.assertEqual(search_alapi.call_count, 1)

    @mock.patch("sticker_server.search_giphy_stickers")
    @mock.patch("sticker_server.search_giphy")
    @mock.patch("sticker_server.search_alapi")
    @mock.patch.dict(os.environ, {"ALAPI_TOKEN": "test-token"}, clear=False)
    def test_search_stickers_ranks_relevance_above_domestic_source(self, search_alapi, search_giphy, search_giphy_stickers):
        search_alapi.return_value = {
            "items": [
                {"id": "alapi-generic", "title": "热门 表情包", "upstreamUrl": "https://example.com/generic.gif", "source": "ALAPI"},
            ],
            "source": "ALAPI",
            "sources": ["ALAPI"],
            "resolvedQuery": "猫猫",
            "hasMore": False,
        }
        search_giphy.return_value = {
            "items": [
                {"id": "giphy-cat", "title": "猫猫 生气 表情包", "originalUrl": "https://example.com/cat.gif", "source": "GIPHY"},
            ],
            "source": "GIPHY",
            "sources": ["GIPHY"],
            "resolvedQuery": "猫猫",
            "hasMore": False,
        }
        search_giphy_stickers.return_value = {"items": [], "source": "GIPHY Sticker", "sources": ["GIPHY Sticker"], "hasMore": False}

        result = sticker_server.search_stickers("猫猫", 1)

        self.assertEqual(result["items"][0]["id"], "giphy-cat")
        self.assertEqual(result["sortMode"], "semantic_relevance_domestic_first_cached")

    @mock.patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "test-key", "OPENAI_VISION_MODEL": "test-model", "OPENAI_VISION_FALLBACK_MODEL": ""},
        clear=False,
    )
    @mock.patch("urllib.request.urlopen")
    def test_analyze_media_calls_openai(self, urlopen):
        payload = {
            "output": [
                {
                    "content": [
                        {
                            "text": (
                                '{"query":"初音未来 微笑 表情包",'
                                '"searchQueries":["初音未来 微笑 表情包","蓝发双马尾 微笑 表情包"],'
                                '"keywords":["初音未来","微笑"],'
                                '"characterCandidates":["初音未来"]}'
                            )
                        }
                    ]
                }
            ]
        }
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

        result = sticker_server.analyze_media("abc123", "image/jpeg", "test.jpg")

        self.assertEqual(result["query"], "初音未来 微笑 表情包")
        self.assertEqual(result["keywords"], ["初音未来", "微笑"])
        self.assertEqual(result["characterCandidates"], ["初音未来"])
        self.assertEqual(result["searchQueries"][0], "初音未来 微笑 表情包")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, sticker_server.OPENAI_RESPONSES_URL)
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "test-model")
        self.assertIn("data:image/jpeg;base64,abc123", body["input"][0]["content"][1]["image_url"])

    @mock.patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "test-key", "OPENAI_VISION_MODEL": "bad-model", "OPENAI_VISION_FALLBACK_MODEL": "fallback-model"},
        clear=False,
    )
    @mock.patch("urllib.request.urlopen")
    def test_analyze_media_falls_back_for_model_error(self, urlopen):
        error = urllib.error.HTTPError(
            sticker_server.OPENAI_RESPONSES_URL,
            404,
            "Not Found",
            {},
            None,
        )
        error.read = mock.Mock(return_value=b'{"error":{"message":"model not found"}}')
        success = mock.MagicMock()
        success.__enter__().read.return_value = json.dumps(
            {"output": [{"content": [{"text": '{"query":"可爱 表情包","keywords":["可爱"]}'}]}]}
        ).encode("utf-8")
        urlopen.side_effect = [error, success]

        result = sticker_server.analyze_media("abc123", "image/jpeg", "test.jpg")

        self.assertEqual(result["query"], "可爱 表情包")
        self.assertEqual(result["model"], "fallback-model")

    @mock.patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_VISION_MODEL": "bad-model",
            "OPENAI_VISION_FALLBACK_MODEL": "",
            "BAIDU_API_KEY": "baidu-key",
            "BAIDU_SECRET_KEY": "baidu-secret",
        },
        clear=False,
    )
    @mock.patch("urllib.request.urlopen")
    def test_analyze_media_uses_baidu_when_openai_fails(self, urlopen):
        openai_error = urllib.error.HTTPError(
            sticker_server.OPENAI_RESPONSES_URL,
            429,
            "Too Many Requests",
            {},
            None,
        )
        openai_error.read = mock.Mock(return_value=b'{"error":{"message":"quota exceeded"}}')
        token_response = mock.MagicMock()
        token_response.__enter__().read.return_value = json.dumps(
            {"access_token": "baidu-token", "expires_in": 3600}
        ).encode("utf-8")
        ocr_response = mock.MagicMock()
        ocr_response.__enter__().read.return_value = json.dumps(
            {"words_result": [{"words": "救命"}]}
        ).encode("utf-8")
        tag_response = mock.MagicMock()
        tag_response.__enter__().read.return_value = json.dumps(
            {"result": [{"keyword": "猫"}, {"keyword": "可爱"}]}
        ).encode("utf-8")
        urlopen.side_effect = [openai_error, token_response, ocr_response, tag_response]

        result = sticker_server.analyze_media("abc123", "image/jpeg", "test.jpg")

        self.assertEqual(result["source"], "baidu")
        self.assertEqual(result["model"], "baidu-ocr-advanced-general")
        self.assertEqual(result["query"], "救命 表情包")
        self.assertIn("猫", result["visualTags"])
        self.assertIn("quota exceeded", result["fallbackReason"])
        self.assertEqual(urlopen.call_args_list[1].args[0].full_url.split("?", 1)[0], sticker_server.BAIDU_TOKEN_URL)

    @mock.patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "",
            "BAIDU_API_KEY": "baidu-key",
            "BAIDU_SECRET_KEY": "baidu-secret",
        },
        clear=False,
    )
    @mock.patch("urllib.request.urlopen")
    def test_analyze_media_uses_baidu_without_openai_key(self, urlopen):
        token_response = mock.MagicMock()
        token_response.__enter__().read.return_value = json.dumps(
            {"access_token": "baidu-token", "expires_in": 3600}
        ).encode("utf-8")
        ocr_response = mock.MagicMock()
        ocr_response.__enter__().read.return_value = json.dumps({"words_result": []}).encode("utf-8")
        tag_response = mock.MagicMock()
        tag_response.__enter__().read.return_value = json.dumps(
            {"result": [{"keyword": "狗"}, {"keyword": "搞笑"}]}
        ).encode("utf-8")
        urlopen.side_effect = [token_response, ocr_response, tag_response]

        result = sticker_server.analyze_media("abc123", "image/jpeg", "dog.png")

        self.assertEqual(result["source"], "baidu")
        self.assertEqual(result["query"], "狗 表情包")
        self.assertEqual(result["searchQueries"][0], "狗 表情包")


if __name__ == "__main__":
    unittest.main()
