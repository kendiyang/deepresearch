#!/usr/bin/env python3
"""
生成测试数据并插入 pgvector，用于验证索引效果
"""
import os
import sys
sys.path.append(os.path.abspath('.'))

from datetime import datetime, timezone, timedelta
from services.knowledge.storageAndRetrieval import BaseStorage
from modules.unifiedComment import UnifiedComment
from langchain_core.embeddings import Embeddings
import numpy as np
from typing import List
import random

class DummyEmbeddings(Embeddings):
    """可重复的测试 Embeddings"""
    def __init__(self, dim: int = 8):
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        rng = np.random.default_rng(abs(hash(text)) % (2 ** 32))
        return rng.random(self.dim).tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)


def generate_test_comments(count: int = 2000) -> List[UnifiedComment]:
    """生成测试评论数据"""
    platforms = ["reddit", "youtube", "twitter"]
    topics = [
        "battery life is excellent",
        "overheating issue with projector",
        "great sound quality",
        "screen brightness too low",
        "easy to set up",
        "expensive but worth it",
        "compact design fits anywhere",
        "remote control not responsive",
        "picture quality amazing",
        "fan noise too loud"
    ]
    
    comments = []
    base_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
    
    for i in range(count):
        platform = random.choice(platforms)
        text = f"{random.choice(topics)} - comment {i}"
        date = base_date + timedelta(days=random.randint(0, 365))
        score = random.randint(1, 50)
        
        comment = UnifiedComment(
            id=f"test_{platform}_{i}",
            platform=platform,
            original_text=text,
            cleaned_text=text,
            author_id=f"user_{i % 100}",
            created_at=date,
            engagement_score=score,
            content_hash="",  # 会自动生成
        )
        comments.append(comment)
    
    return comments


def main():
    conn_str = os.getenv('PGVECTOR_URL') or os.getenv('DATABASE_URL')
    if not conn_str:
        print("错误：需要设置 PGVECTOR_URL 或 DATABASE_URL")
        sys.exit(1)
    
    if conn_str.startswith("postgres://"):
        conn_str = conn_str.replace("postgres://", "postgresql+psycopg2://", 1)
    
    print("[1/3] 生成测试数据...")
    comments = generate_test_comments(2000)
    print(f"已生成 {len(comments)} 条测试评论")
    
    print("[2/3] 连接数据库并入库...")
    embedding_model = DummyEmbeddings()
    storage = BaseStorage(conn_str, embedding_model, allow_collection_reset=False)
    
    # 分批入库，避免单次过大
    batch_size = 200
    for i in range(0, len(comments), batch_size):
        batch = comments[i:i+batch_size]
        storage.ingest_batch(batch)
        print(f"  已入库 {min(i+batch_size, len(comments))}/{len(comments)}")
    
    print("[3/3] 完成！数据已插入")
    print(f"\n下一步：运行索引创建脚本启用向量索引")
    print("  1. 编辑 scripts/sql/pgvector_indexes.sql 取消 IVFFLAT 注释")
    print("  2. 或直接运行: python scripts/create_vector_index.py")


if __name__ == "__main__":
    main()
