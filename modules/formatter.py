from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Dict
from datetime import datetime
import hashlib

class UnifiedComment(BaseModel):
    """
    所有平台数据清洗后必须符合此标准结构
    """
    id: str = Field(..., description="平台原始ID或生成的哈希ID")
    platform: str = Field(..., description="reddit, tiktok, youtube, substack")
    original_text: str 
    cleaned_text: str = Field(..., description="清洗后的纯文本，用于Embedding")
    
    # 元数据对分析权重至关重要
    author_id: Optional[str]
    created_at: datetime
    engagement_score: int = Field(default=0, description="点赞+回复+转发的加权分")
    
    # 深度研究专用字段
    sentiment_score: Optional[float] = None
    topics: List[str] = Field(default_factory=list)
    content_hash: str | None = Field(None, description="用于去重的MD5")

    @model_validator(mode="after")
    def generate_hash(self):
        """自动生成内容指纹，忽略空格和大小写"""
        text = (self.cleaned_text or "").lower().replace(" ", "")
        self.content_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return self