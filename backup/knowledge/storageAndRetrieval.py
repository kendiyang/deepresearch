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
import json
import math

# 配置日志
logger = logging.getLogger(__name__)

# ================= 常量定义 (确保存取一致性) =================
COLLECTION_NAME = "social_media_insights"
TABLE_NAME = "langchain_pg_embedding"  # PGVector 默认表名

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
    

@dataclass
class SamplerConfig:
    """采样器配置"""
    candidate_fetch_k: int = 500       # 从数据库初筛的数量 (漏斗顶部)
    target_cluster_count: int = 10     # 期望聚成的观点类别数
    samples_per_cluster: int = 5       # 每个簇最终选取的条数
    outlier_ratio: float = 0.3         # 每个簇中保留"边缘观点"的比例 (0.3 表示 30% 是边缘观点)
    random_seed: int = 42

class SemanticClusterSampler:
    """
    企业级语义采样器：基于 K-Means 聚类筛选高代表性与高多样性的样本
    """
    
    def __init__(self, config: SamplerConfig = SamplerConfig()):
        self.config = config

    def run(self, documents: List[Document], embeddings: List[List[float]]) -> List[Document]:
        """
        核心执行方法
        :param documents: LangChain Document 对象列表
        :param embeddings: 对应的向量列表 (维度必须一致，如 1536)
        :return: 精选后的 Document 列表
        """
        if not documents or not embeddings:
            logger.warning("Empty documents or embeddings provided to sampler.")
            return []

        # 1. 数据校验
        num_docs = len(documents)
        if num_docs < self.config.target_cluster_count:
            logger.info(f"文档数量 ({num_docs}) 少于目标簇数量，直接返回所有文档。")
            return documents

        # 转换为 Numpy 数组用于计算
        X = np.array(embeddings)
        
        # 2. 执行聚类 (使用 MiniBatchKMeans 提升性能)
        # n_init='auto' 在新版 sklearn 中推荐
        kmeans = MiniBatchKMeans(
            n_clusters=self.config.target_cluster_count,
            random_state=self.config.random_seed,
            batch_size=256,
            n_init=3 
        ).fit(X)
        
        # 获取每个点的簇标签和距离中心的距离
        labels = kmeans.labels_
        # transform 返回样本到每个簇中心的距离矩阵
        dist_matrix = kmeans.transform(X) 
        
        selected_indices = set()
        
        # 3. 簇内采样逻辑
        for cluster_id in range(self.config.target_cluster_count):
            # 找到属于当前簇的所有样本索引
            cluster_indices = np.where(labels == cluster_id)[0]
            
            if len(cluster_indices) == 0:
                continue
                
            # 获取该簇内样本到该簇中心的距离
            # dist_matrix[i, cluster_id] 即样本 i 到簇 cluster_id 中心的距离
            distances = [dist_matrix[i, cluster_id] for i in cluster_indices]
            
            # 将 (index, distance) 打包并按距离排序
            indexed_distances = sorted(
                zip(cluster_indices, distances), 
                key=lambda x: x[1] # 按距离从小到大排序 (0 = 中心)
            )
            
            # 计算采样数量
            n_total = self.config.samples_per_cluster
            n_outliers = int(n_total * self.config.outlier_ratio)
            n_representatives = n_total - n_outliers
            
            # 3.1 选取代表性观点 (Centroids) - 距离中心最近
            # 即使 n_representatives > len，切片操作也是安全的
            for idx, _ in indexed_distances[:n_representatives]:
                selected_indices.add(idx)
                
            # 3.2 选取边缘观点 (Outliers) - 距离中心最远
            # 如果簇内样本很少，可能这里的点和上面是重合的，set 会自动去重
            if n_outliers > 0 and len(indexed_distances) > n_representatives:
                for idx, _ in indexed_distances[-n_outliers:]:
                    selected_indices.add(idx)

        # 4. 构建返回结果
        # 将 set 转回 list 并保持原始顺序 (可选，取决于是否需要特定排序)
        final_docs = [documents[i] for i in sorted(list(selected_indices))]
        
        logger.info(f"采样完成: 从 {num_docs} 条原始数据中精选出 {len(final_docs)} 条。")
        return final_docs
    
class AdvancedRetriever:
    def __init__(self, connection_string, embedding_model, sampler: SemanticClusterSampler, *, allow_collection_reset: bool = False):
        self.engine = create_engine(connection_string)  # ✅ 添加 engine 属性
        self.embedding_model = embedding_model  # ✅ 保存 embedding_model
        self.store = PGVector(
            embeddings=embedding_model,
            connection=connection_string,
            collection_name=COLLECTION_NAME,  # 使用常量
            use_jsonb=True,
            pre_delete_collection=allow_collection_reset
        )
        self.sampler = sampler

    def deep_research_retrieval(
        self,
        query: str,
        *,
        k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        query_timeout_ms: int = 2000,
    ) -> List[Document]:
        """
        全流程：召回 -> 提取向量 -> 聚类采样
        
        参数
        - query: 查询语句
        - k: 召回候选数量（默认取采样器配置的 candidate_fetch_k）
        - filters: 可选过滤条件字典，支持：
          - platform: str | List[str]
          - date_from: datetime | str(ISO8601)
          - date_to: datetime | str(ISO8601)
          - min_score: float
        - query_timeout_ms: SQL 查询超时（毫秒），默认 2000ms

        返回
        - 经过语义聚类采样后的 `Document` 列表

        示例
        - 基础用法：
            retriever.deep_research_retrieval("battery life is short")
        - 带过滤与超时：
            retriever.deep_research_retrieval(
                "projector overheating",
                k=200,
                filters={
                    "platform": ["reddit", "youtube"],
                    "date_from": "2025-12-01T00:00:00Z",
                    "min_score": 3.0,
                },
                query_timeout_ms=1500,
            )
        """
        # 1. 宽泛召回 (Fetch K Candidates)
        # 注意：这里我们不仅需要文档，还需要分数和向量
        # LangChain 的标准接口可能不支持直接返回 vector，这里使用 vector_store 的原生方法或扩展
        
        # 技巧：如果 vector store 不支持直接返回 vector，
        # 我们可以存储向量在 metadata 中 (虽然有点冗余，但在读取性能上是值得的)
        # 或者，如果使用 PGVector，我们可以自定义 SQL 查询
        
        candidates_k = k or self.sampler.config.candidate_fetch_k
        
        # 假设我们扩展了 search 方法，或者 metadata 中存了 'embedding_vector'
        # 这里演示更通用的做法：直接执行 SQL (针对 PGVector) 以获得最高性能
        docs, embeddings = self._sql_fetch_with_vectors(
            query,
            k=candidates_k,
            filters=filters,
            query_timeout_ms=query_timeout_ms,
        )
        
        if not docs:
            return []
            
        # 2. 智能采样
        refined_docs = self.sampler.run(docs, embeddings)
        
        return refined_docs

    def _sql_fetch_with_vectors(
        self,
        query: str,
        k: int,
        *,
        filters: Optional[Dict[str, Any]] = None,
        query_timeout_ms: int = 2000,
    ) -> Tuple[List[Document], List[List[float]]]:
        """
        针对 PGVector 的优化查询，直接取回 Embedding
        """
        query_vec = self.embedding_model.embed_query(query)

        # 向量数值校验，避免 nan/inf 导致 SQL 失败
        if any((v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))) for v in query_vec):
            logger.warning("Query embedding contains NaN/Inf, skip retrieval. query=%s", query)
            return [], []
        
        # 将向量转换为 PostgreSQL 数组格式 (pgvector 需要)
        # 格式: '[1.0,2.0,3.0]'
        vec_str = '[' + ','.join(map(str, query_vec)) + ']'
        
        # 构建可选过滤条件
        filters = filters or {}
        where_clauses = ["cmetadata->>'hash' IS NOT NULL"]
        params: Dict[str, Any] = {"query_vec": vec_str, "k": k}

        platform = filters.get("platform")
        if platform:
            if isinstance(platform, (list, tuple, set)):
                where_clauses.append("cmetadata->>'platform' = ANY(:platforms)")
                params["platforms"] = list(platform)
            else:
                where_clauses.append("cmetadata->>'platform' = :platform")
                params["platform"] = platform

        date_from = filters.get("date_from")
        if date_from is not None:
            where_clauses.append("(cmetadata->>'date')::timestamptz >= :date_from")
            params["date_from"] = date_from

        date_to = filters.get("date_to")
        if date_to is not None:
            where_clauses.append("(cmetadata->>'date')::timestamptz <= :date_to")
            params["date_to"] = date_to

        min_score = filters.get("min_score")
        if min_score is not None:
            where_clauses.append("(cmetadata->>'score')::float >= :min_score")
            params["min_score"] = float(min_score)

        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT document, cmetadata, embedding
            FROM {TABLE_NAME}
            WHERE {where_sql}
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :k
        """)
        
        docs = []
        vectors = []
        
        try:
            # 在事务内设置语句超时，避免慢查询阻塞
            with self.engine.begin() as conn:
                if query_timeout_ms and query_timeout_ms > 0:
                    conn.execute(text("SET LOCAL statement_timeout = :timeout_ms"), {"timeout_ms": int(query_timeout_ms)})

                import time
                t0 = time.perf_counter()
                result = conn.execute(sql, params)
                
                for row in result:
                    text_content = row[0]
                    metadata = row[1] if isinstance(row[1], dict) else json.loads(row[1])
                    vec = row[2]
                    
                    # 处理多种可能的向量格式
                    if isinstance(vec, str):
                        # 字符串格式: '[1.0,2.0,3.0]'
                        vec = json.loads(vec)
                    elif isinstance(vec, (bytes, bytearray)):
                        # 二进制格式
                        vec = np.frombuffer(vec, dtype=np.float32).tolist()
                    elif hasattr(vec, '__iter__'):
                        # 已经是可迭代对象
                        vec = list(vec)
                    else:
                        logger.warning(f"Unknown vector format: {type(vec)}")
                        continue
                    
                    docs.append(Document(page_content=text_content, metadata=metadata))
                    vectors.append(vec)
                elapsed = (time.perf_counter() - t0) * 1000.0
                logger.info("PGVector召回: k=%d, 返回=%d, 耗时=%.1fms, 过滤=%s", k, len(docs), elapsed, bool(filters))
        except Exception as e:
            logger.error(f"Error fetching vectors: {e}")
            raise
                
        return docs, vectors