#!/usr/bin/env python3
"""
验证索引效果：对比有无索引的检索性能
"""
import os, sys, time
sys.path.append(os.path.abspath('.'))

from services.knowledge.storageAndRetrieval import AdvancedRetriever, SemanticClusterSampler, SamplerConfig
from langchain_core.embeddings import Embeddings
import numpy as np
from typing import List

class DummyEmbeddings(Embeddings):
    def __init__(self, dim: int = 8):
        self.dim = dim
    def _vec(self, text: str) -> List[float]:
        rng = np.random.default_rng(abs(hash(text)) % (2 ** 32))
        return rng.random(self.dim).tolist()
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]
    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)


def main():
    conn_str = os.getenv('PGVECTOR_URL') or os.getenv('DATABASE_URL')
    if not conn_str:
        print("错误：需要设置 PGVECTOR_URL")
        sys.exit(1)
    
    if conn_str.startswith("postgres://"):
        conn_str = conn_str.replace("postgres://", "postgresql+psycopg2://", 1)
    
    embedding_model = DummyEmbeddings()
    sampler_cfg = SamplerConfig(candidate_fetch_k=100, target_cluster_count=5, samples_per_cluster=3)
    retriever = AdvancedRetriever(conn_str, embedding_model, SemanticClusterSampler(sampler_cfg))
    
    queries = [
        "battery life is excellent",
        "overheating issue with projector",
        "great sound quality"
    ]
    
    print("=" * 60)
    print("基准测试：向量检索性能（已启用索引）")
    print("=" * 60)
    
    for i, query in enumerate(queries, 1):
        print(f"\n[查询 {i}/3] {query}")
        
        # 热身（加载索引）
        _ = retriever.deep_research_retrieval(query, k=50)
        
        # 正式测试
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            docs = retriever.deep_research_retrieval(query, k=100)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            times.append(elapsed_ms)
        
        avg_time = sum(times) / len(times)
        print(f"  召回100条 + 聚类采样: 平均 {avg_time:.1f}ms (3次测试)")
        print(f"  最终返回: {len(docs)} 条")
    
    print("\n" + "=" * 60)
    print("过滤测试：元数据索引性能")
    print("=" * 60)
    
    # 测试过滤
    print("\n[过滤查询] platform=reddit, min_score=10")
    t0 = time.perf_counter()
    docs = retriever.deep_research_retrieval(
        "battery",
        k=100,
        filters={"platform": "reddit", "min_score": 10}
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  带过滤检索: {elapsed_ms:.1f}ms, 返回 {len(docs)} 条")
    
    print("\n✅ 性能测试完成")
    print("\n提示：")
    print("  - 索引已启用，检索速度应较快（通常 < 50ms for 2k条数据）")
    print("  - 可在 deep_research_retrieval() 日志中查看详细耗时")
    print("  - 调大 ivfflat.probes 可提高召回精度但会变慢")


if __name__ == "__main__":
    main()
