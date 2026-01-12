import re
import emoji
from bs4 import BeautifulSoup
from cleantext import clean
from modules.formatter import UnifiedComment
from datetime import datetime
from typing import Any, Dict
from services.platforms.interface import DataPreprocessor

class RedditPreprocessor(DataPreprocessor):
	"""
	Reddit平台数据预处理器，实现统一接口
	"""
	def __init__(self):
		self.spam_patterns = [
			r"click link in bio",
			r"buy now",
			r"cryptocurrency",
			r"promoted",
			r"\[deleted\]",
			r"\[removed\]"
		]

	def preprocess(self, data: Any) -> UnifiedComment:
		"""
		预处理reddit原始数据，返回UnifiedComment
		:param data: dict，reddit原始数据
		:return: UnifiedComment
		"""
		raw_body = data.get("body") or data.get("title", "")
		clean_body = self._deep_clean(raw_body)
		return UnifiedComment(
			id=data.get("id"),
			platform="reddit",
			original_text=raw_body,
			cleaned_text=clean_body,
			author_id=data.get("author"),
			created_at=datetime.fromisoformat(data.get("created_utc").replace('Z', '+00:00')) if data.get("created_utc") else None,
			engagement_score=data.get("upvotes", 0) + data.get("numberOfComments", 0) * 2,
			content_hash=""
		)

	def _deep_clean(self, text: str) -> str:
		if not text:
			return ""
		text = BeautifulSoup(text, "html.parser").get_text()
		text = clean(text,
			fix_unicode=True,
			to_ascii=False,
			lower=False,
			no_line_breaks=True,
			no_urls=True,
			no_emails=True,
			no_phone_numbers=True,
			replace_with_url="<URL>",
			replace_with_email="<EMAIL>"
		)
		text = emoji.demojize(text)
		text = re.sub(r'\s+', ' ', text).strip()
		return text

	def is_high_quality(self, comment: UnifiedComment) -> bool:
		text = comment.cleaned_text
		if len(text.split()) < 5:
			return False
		if any(re.search(p, text, re.IGNORECASE) for p in self.spam_patterns):
			return False
		return True
