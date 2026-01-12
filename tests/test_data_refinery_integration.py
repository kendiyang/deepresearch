#!/usr/bin/env python3
"""
Integration tests for services/processing/dataCleab.py
Covers end-to-end cleaning, normalization, and quality gate logic.
"""
import os
import sys
import unittest
from datetime import datetime, timezone

# Allow project imports
sys.path.append(os.path.abspath('.'))

from services.processing.dataCleab import DataRefinery
from modules.formatter import UnifiedComment


class DataRefineryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.refinery = DataRefinery()

    def test_normalize_reddit_end_to_end(self):
        raw_html = "<p>Check this 🔥 now! Visit https://example.com</p>"
        apify_item = {
            "id": "t3_abc123",
            "body": raw_html,
            "author": "alice",
            "created_utc": "2024-01-01T00:00:00Z",
            "upvotes": 10,
            "numberOfComments": 2,
        }

        comment: UnifiedComment = self.refinery.normalize_reddit(apify_item)

        # Basic field checks
        self.assertIsInstance(comment, UnifiedComment)
        self.assertEqual(comment.platform, "reddit")
        self.assertEqual(comment.id, "t3_abc123")
        self.assertEqual(comment.author_id, "alice")
        self.assertEqual(comment.engagement_score, 10 + 2 * 2)
        self.assertEqual(comment.created_at, datetime(2024, 1, 1, tzinfo=timezone.utc))

        # Cleaned text expectations
        cleaned = comment.cleaned_text
        self.assertNotIn("<p>", cleaned)
        self.assertIn(":fire:", cleaned)  # emoji demojized
        self.assertIn("<URL>", cleaned)   # urls removed/replaced
        self.assertTrue(comment.content_hash)  # auto-generated

    def test_is_high_quality_rejects_short(self):
        c = UnifiedComment(
            id="1",
            platform="reddit",
            original_text="short",
            cleaned_text="Too short",
            author_id=None,
            created_at=datetime.now(timezone.utc),
            engagement_score=0,
            content_hash="",
        )
        self.assertFalse(self.refinery.is_high_quality(c))

    def test_is_high_quality_rejects_spam(self):
        c = UnifiedComment(
            id="2",
            platform="reddit",
            original_text="spam",
            cleaned_text="You should buy now this product",
            author_id=None,
            created_at=datetime.now(timezone.utc),
            engagement_score=0,
            content_hash="",
        )
        self.assertFalse(self.refinery.is_high_quality(c))

    def test_is_high_quality_accepts_good(self):
        c = UnifiedComment(
            id="3",
            platform="reddit",
            original_text="useful",
            cleaned_text="Battery life is too short on long trips",
            author_id=None,
            created_at=datetime.now(timezone.utc),
            engagement_score=5,
            content_hash="",
        )
        self.assertTrue(self.refinery.is_high_quality(c))


if __name__ == "__main__":
    unittest.main()
