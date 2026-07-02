import json
import os
import unittest
from unittest import mock

import sticker_server


class StickerServerTests(unittest.TestCase):
    def setUp(self):
        sticker_server._request_log.clear()

    def test_normalize_giphy_item(self):
        item = {
            "id": "abc",
            "title": "哈哈",
            "url": "https://giphy.com/gifs/abc",
            "images": {
                "original": {"url": "https://media.giphy.com/a.gif", "width": "320", "height": "240"},
                "fixed_width_small": {"url": "https://media.giphy.com/a-small.gif", "width": "100", "height": "75"},
            },
        }

        result = sticker_server.normalize_giphy_item(item)

        self.assertEqual(result["id"], "abc")
        self.assertEqual(result["title"], "哈哈")
        self.assertEqual(result["thumbnailUrl"], "https://media.giphy.com/a-small.gif")
        self.assertEqual(result["originalUrl"], "https://media.giphy.com/a.gif")
        self.assertEqual(result["mimeType"], "image/gif")
        self.assertEqual(result["width"], 320)

    def test_rate_limit_blocks_after_window_quota(self):
        now = 1000.0
        for _ in range(sticker_server.RATE_LIMIT_MAX_REQUESTS):
            self.assertFalse(sticker_server._rate_limited("client", now))

        self.assertTrue(sticker_server._rate_limited("client", now))

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

        result = sticker_server.search_giphy("谢谢", 2, limit=1)

        self.assertEqual(result["page"], 2)
        self.assertEqual(result["items"][0]["originalUrl"], "https://example.com/one.gif")
        requested_url = urlopen.call_args.args[0]
        self.assertIn("q=%E8%B0%A2%E8%B0%A2", requested_url)
        self.assertIn("offset=1", requested_url)


if __name__ == "__main__":
    unittest.main()
