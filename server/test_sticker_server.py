import json
import os
import urllib.parse
import unittest
from unittest import mock

import sticker_server


class StickerServerTests(unittest.TestCase):
    def setUp(self):
        sticker_server._request_log.clear()

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

        self.assertEqual(resolved, "lol laughing reaction")
        self.assertEqual(mode, "hot_term")

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
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

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

        self.assertEqual(result["resolvedQuery"], "lol laughing reaction")
        self.assertEqual(result["queryMode"], "hot_term")
        requested_url = urlopen.call_args.args[0]
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(requested_url).query)
        self.assertEqual(parsed["q"], ["lol laughing reaction"])
        self.assertEqual(parsed["limit"], ["48"])


if __name__ == "__main__":
    unittest.main()
