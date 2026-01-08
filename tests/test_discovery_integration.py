#!/usr/bin/env python3
"""
services/discovery/discovery.py 集成测试
- 验证并发 + 重试后的结构化输出、去重、rank/title 填充逻辑
- 使用 mock 拦截 Serper 请求，避免真实网络依赖
"""
import os
import sys
import json
import unittest
import warnings
from typing import Dict, Any
from unittest.mock import patch

# 允许本地模块导入
sys.path.append(os.path.abspath('.'))

# 抑制 langchain 在 Python 3.14 上的 pydantic v1 警告
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="langchain_core._api.deprecation",
)

import requests
from requests import Response

from services.discovery.discovery import DiscoveryService, DorkResult, DiscoveryItem


def _make_response(payload: Dict[str, Any]) -> Response:
    resp = Response()
    resp.status_code = 200
    resp._content = json.dumps(payload).encode("utf-8")
    resp.url = "https://google.serper.dev/search"
    resp.encoding = "utf-8"
    return resp


class DiscoveryIntegrationTest(unittest.TestCase):
    def setUp(self):
        # 构造伪造的 serper 返回，覆盖两个 dork
        self.fake_results = {
            "dork_one": {
                "organic": [
                    {
                        "link": "https://www.reddit.com/r/test/comments/abc123/",
                        "title": "Reddit Thread A",
                        "position": 1,
                    },
                    {
                        "link": "https://www.reddit.com/u/profile",  # 无 /comments/，应被过滤
                        "title": "Profile",
                        "position": 2,
                    },
                ]
            },
            "dork_two": {
                "organic": [
                    {
                        "link": "https://www.reddit.com/r/test/comments/abc123",  # 与 dork_one 去重
                        "title": "Reddit Thread A (dup)",
                        # 无 position，使用 idx + 1 = 1
                    },
                    {
                        "link": "https://www.tiktok.com/@abc/video/999",
                        "title": "TikTok Clip",
                        "position": 5,
                    },
                ]
            },
        }

    def _fake_post(self, url, headers=None, data=None, timeout=None):
        # 模拟 serper，根据 payload 中的 dork 返还预置 organic
        try:
            payload = json.loads(data or "{}")
            dork = payload.get("q")
        except Exception:
            dork = None

        payload = self.fake_results.get(dork, {"organic": []})
        return _make_response(payload)

    @patch.object(requests.Session, "post")
    def test_find_discussion_urls_structured_and_deduped(self, mock_post):
        mock_post.side_effect = self._fake_post

        svc = DiscoveryService(
            serper_api_key="dummy",
            max_retries=1,
            max_concurrency=4,
            min_interval=0.0,
        )

        dork_result = DorkResult(dorks=["dork_one", "dork_two"])

        items = svc.find_discussion_urls(dork_result, per_dork=3)

        # 去重后应包含 reddit comments 链接和 tiktok 视频，各 1 条
        urls = {i.url for i in items}
        self.assertIn("https://www.reddit.com/r/test/comments/abc123", urls)
        self.assertIn("https://www.tiktok.com/@abc/video/999", urls)
        self.assertEqual(len(urls), 2)

        # 结构化字段检查
        by_url = {i.url: i for i in items}
        reddit_item: DiscoveryItem = by_url["https://www.reddit.com/r/test/comments/abc123"]
        self.assertEqual(reddit_item.domain, "www.reddit.com")
        self.assertEqual(reddit_item.dork, "dork_one")
        self.assertEqual(reddit_item.rank, 1)
        self.assertEqual(reddit_item.title, "Reddit Thread A")

        tiktok_item: DiscoveryItem = by_url["https://www.tiktok.com/@abc/video/999"]
        self.assertEqual(tiktok_item.domain, "www.tiktok.com")
        self.assertEqual(tiktok_item.dork, "dork_two")
        self.assertEqual(tiktok_item.rank, 5)
        self.assertEqual(tiktok_item.title, "TikTok Clip")


if __name__ == "__main__":
    unittest.main()
