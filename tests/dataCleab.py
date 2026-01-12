import re
import emoji
from bs4 import BeautifulSoup
from modules.formatter import UnifiedComment
from typing import Dict
from datetime import datetime

# --- Compatibility shim for emoji>=2.x ---
# Some versions of cleantext import `UNICODE_EMOJI` from emoji, which was
# removed in emoji 2.x in favor of `EMOJI_DATA`. To keep compatibility
# without pinning emoji, expose `UNICODE_EMOJI` if it's missing.
if not hasattr(emoji, "UNICODE_EMOJI"):
    try:
        emoji.UNICODE_EMOJI = getattr(emoji, "EMOJI_DATA", {})
    except Exception:
        emoji.UNICODE_EMOJI = {}

from cleantext import clean

class DataRefinery:
    
    def __init__(self):
        # 垃圾词黑名单 (广告、机器人常用语)
        self.spam_patterns = [
            r"click link in bio",
            r"buy now",
            r"cryptocurrency",
            r"promoted",
            r"\[deleted\]",
            r"\[removed\]"
        ]
    
    def normalize_reddit(self, apify_item: Dict) -> UnifiedComment:
        """将 Reddit 原始数据转为统一格式"""
        raw_body = apify_item.get("body") or apify_item.get("title", "")
        clean_body = self._deep_clean(raw_body)
        
        return UnifiedComment(
            id=apify_item.get("id"),
            platform="reddit",
            original_text=raw_body,
            cleaned_text=clean_body,
            author_id=apify_item.get("author"),
            created_at=datetime.fromisoformat(apify_item.get("created_utc").replace('Z', '+00:00')),
            engagement_score=apify_item.get("upvotes", 0) + apify_item.get("numberOfComments", 0) * 2,
            content_hash="" # Validator 会自动填充
        )

    def _deep_clean(self, text: str) -> str:
        """企业级清洗逻辑"""
        if not text:
            return ""

        # 1. 去除 HTML 标签 (应对富文本编辑器残留)
        text = BeautifulSoup(text, "html.parser").get_text()

        # 2. 标准化处理 (基于 clean-text 库)
        # fix_unicode: 修复损坏的编码
        # to_ascii: 转为 ASCII (可选，如果是非英语研究则设为 False)
        # no_urls: 去除链接 (干扰项)
        # no_emails/no_phone_numbers: 基础 PII 脱敏
        text = clean(text,
            fix_unicode=True,
            to_ascii=False, 
            lower=False, # 保持大小写，有时候全大写代表愤怒
            no_line_breaks=True,
            no_urls=True,
            no_emails=True,
            no_phone_numbers=True,
            replace_with_url="<URL>",
            replace_with_email="<EMAIL>"
        )

        # 3. Emoji 处理
        # 策略：将 Emoji 转为文本描述，因为 Emoji 包含强烈情感 (e.g., 😡 -> :pouting_face:)
        # 这对 LLM 理解情绪很有帮助
        text = emoji.demojize(text)

        # 4. 去除多余空白
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def is_high_quality(self, comment: UnifiedComment) -> bool:
        """
        质检门控：决定这条数据是否值得存入 Vector DB
        """
        text = comment.cleaned_text
        
        # 1. 长度过滤: 太短通常无信息量 (例如 "Lol", "This")
        if len(text.split()) < 5:
            return False
            
        # 2. 垃圾正则匹配
        if any(re.search(p, text, re.IGNORECASE) for p in self.spam_patterns):
            return False
            
        # 3. 语言检测 (可选): 确保是目标语言
        # if not is_english(text): return False
        
        return True