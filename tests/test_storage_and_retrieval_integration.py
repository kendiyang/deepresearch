#!/usr/bin/env python3
"""
PGVector 集成测试：验证 storageAndRetrieval 的存取一致性
- 前置条件：已启动 PGVector 容器，并通过环境变量提供连接串
  支持的环境变量：
    PGVECTOR_URL 或 DATABASE_URL，例如：
    postgres://user:pass@localhost:5432/pgvector
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from typing import List
import logging
import warnings

# 允许本地模块导入
sys.path.append(os.path.abspath('.'))

# Silence langchain pydantic v1 warning on Python 3.14+ **before** importing langchain modules
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="langchain_core._api.deprecation",
)

from services.knowledge.storageAndRetrieval import (
    BaseStorage,
    AdvancedRetriever,
    SemanticClusterSampler,
    SamplerConfig,
    MetadataKeys,
    TABLE_NAME,
)
from modules.unifiedComment import UnifiedComment
from langchain_core.embeddings import Embeddings
from sqlalchemy import text, create_engine
import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Silence langchain pydantic v1 warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="langchain_core._api.deprecation",
)


class DummyEmbeddings(Embeddings):
    """可重复的假 Embeddings，用于离线测试，避免真实模型调用"""

    def __init__(self, dim: int = 8):
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        # 根据字符串 hash 生成稳定向量，保证同输入同输出
        rng = np.random.default_rng(abs(hash(text)) % (2 ** 32))
        return rng.random(self.dim).tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)


class StorageRetrievalIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw_conn = os.getenv('PGVECTOR_URL') or os.getenv('DATABASE_URL')
        if not raw_conn:
            raise unittest.SkipTest("缺少 PGVECTOR_URL / DATABASE_URL，跳过集成测试")

        # 兼容常见写法：postgres:// -> postgresql+psycopg2:// 以匹配 SQLAlchemy 方言
        if raw_conn.startswith("postgres://"):
            raw_conn = raw_conn.replace("postgres://", "postgresql+psycopg2://", 1)

        cls.connection_string = raw_conn

        cls.embedding_model = DummyEmbeddings()

        # 清理旧版 langchain_pg_* 表，防止列名不匹配
        engine = create_engine(cls.connection_string)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS langchain_pg_embedding CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS langchain_pg_collection CASCADE"))

        # 测试环境允许重建集合以确保表结构一致
        cls.storage = BaseStorage(cls.connection_string, cls.embedding_model, allow_collection_reset=True)

        sampler_cfg = SamplerConfig(candidate_fetch_k=50, target_cluster_count=2, samples_per_cluster=3)
        cls.retriever = AdvancedRetriever(
            cls.connection_string,
            cls.embedding_model,
            SemanticClusterSampler(sampler_cfg),
            allow_collection_reset=True,
        )

        # 测试数据
        now = datetime.now(timezone.utc)
        cls.comments = [
            UnifiedComment(
                id="cmt_1",
                platform="reddit",
                original_text="Battery life is too short",
                cleaned_text="Battery life is too short",
                author_id="user_a",
                created_at=now,
                engagement_score=10,
                content_hash="",  # 会由校验器重算
            ),
            UnifiedComment(
                id="cmt_2",
                platform="youtube",
                original_text="Projector overheats quickly",
                cleaned_text="Projector overheats quickly",
                author_id="user_b",
                created_at=now,
                engagement_score=5,
                content_hash="",
            ),
        ]

        # 先清理同 hash 的旧数据，避免重复
        cls._cleanup_hashes([c.content_hash for c in cls.comments])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'connection_string', None):
            cls._cleanup_hashes([c.content_hash for c in cls.comments])

    @classmethod
    def _cleanup_hashes(cls, hashes: List[str]):
        if not hashes:
            return
        sql = text(f"""
            DELETE FROM {TABLE_NAME}
            WHERE cmetadata->>'hash' = ANY(:hashes)
        """)
        with cls.storage.engine.begin() as conn:
            conn.execute(sql, {"hashes": hashes})

    def test_ingest_and_retrieve(self):
        # 入库
        self.storage.ingest_batch(self.comments)

        # 检索
        docs = self.retriever.deep_research_retrieval("battery life is short")

        self.assertGreater(len(docs), 0, "检索结果为空")

        # 校验 metadata 和文本
        hashes = {c.content_hash for c in self.comments}
        returned_hashes = {d.metadata.get(MetadataKeys.HASH) for d in docs}
        self.assertTrue(hashes & returned_hashes, "返回结果未包含已写入的 hash")

        # 文本应为存储的纯净文本
        for d in docs:
            self.assertNotRegex(d.page_content, r"^\[.*\] ", "文本中不应包含平台前缀")


if __name__ == "__main__":
    unittest.main()
