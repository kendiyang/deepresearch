from dataclasses import dataclass
from typing import Dict, Any, Tuple, List, Optional
from langchain_postgres.vectorstores import PGVector
from langchain_core.embeddings import Embeddings
from sqlalchemy import create_engine, text
from modules.formatter import UnifiedComment
import numpy as np
import logging
from sklearn.cluster import MiniBatchKMeans
from langchain_core.documents import Document
from services.storage.constant import COLLECTION_NAME, TABLE_NAME

# 配置日志
logger = logging.getLogger(__name__)

# Metadata 字段名常量 (存储和检索必须使用相同的键名)
class MetadataKeys:
    ID = "id"
    AUTHOR = "author"
    SCORE = "score"
    DATE = "date"
    HASH = "hash"
    PLATFORM = "platform"  # 新增：单独存储平台信息


class BaseStorage:
    def __init__(self, connection_string: str, embedding_model: Embeddings, *, allow_collection_reset: bool = False):
        self.engine = create_engine(connection_string)
        self.embedding_model = embedding_model
        self.connection_string = connection_string  # 保存连接字符串供子类使用
        # 初始化向量库连接；生产默认不删除集合，仅在测试/迁移场景显式开启
        self.vector_store = PGVector(
            embeddings=self.embedding_model,
            connection=connection_string,
            collection_name=COLLECTION_NAME,  # 使用常量
            use_jsonb=True,
            pre_delete_collection=allow_collection_reset
        )

    def ingest_batch(self, comments: List[UnifiedComment]):
        """
        批量入库，包含去重逻辑
        """
        # 1. 检查 Content Hash 是否已存在 (SQL快速去重)
        hashes = [c.content_hash for c in comments]
        existing_hashes = self._get_existing_hashes(hashes)
        
        new_records = []
        metadatas = []
        texts = []
        
        for c in comments:
            if c.content_hash not in existing_hashes:
                # 准备存入 Vector DB
                # 注意：仅存储纯净文本用于 embedding，平台信息存入 metadata
                texts.append(c.cleaned_text)
                metadatas.append({
                    MetadataKeys.ID: c.id,
                    MetadataKeys.AUTHOR: c.author_id,
                    MetadataKeys.SCORE: c.engagement_score,
                    MetadataKeys.DATE: c.created_at.isoformat(),
                    MetadataKeys.HASH: c.content_hash,
                    MetadataKeys.PLATFORM: c.platform  # 平台信息存入 metadata 而非文本
                })
        
        if texts:
            # 存入 PGVector
            self.vector_store.add_texts(texts=texts, metadatas=metadatas)
            logger.info("成功入库 %d 条新数据", len(texts))
        else:
            logger.info("没有新数据需要入库")

    def _get_existing_hashes(self, hashes: List[str]) -> set:
        """数据库层面快速查重"""
        if not hashes: return set()
        # 使用常量确保字段名一致
        sql = text(f"""
            SELECT cmetadata->>:hash_key 
            FROM {TABLE_NAME} 
            WHERE cmetadata->>:hash_key = ANY(:hashes)
        """)
        with self.engine.connect() as conn:
            result = conn.execute(sql, {
                "hash_key": MetadataKeys.HASH,
                "hashes": hashes  # PostgreSQL ANY 支持数组
            })
            return {row[0] for row in result if row[0]}
    
