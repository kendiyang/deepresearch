from dataclasses import dataclass

# ================= 常量定义 (确保存取一致性) =================
COLLECTION_NAME = "drae_corpus"  # PGVector 默认集合名
TABLE_NAME = "research_knowledge"  # PGVector 默认表名

#===================  采样器配置 ====================

from functools import lru_cache

@dataclass
class SamplerConfig:
    """采样器配置"""
    candidate_fetch_k: int = 500       # 从数据库初筛的数量 (漏斗顶部)
    target_cluster_count: int = 10     # 期望聚成的观点类别数
    samples_per_cluster: int = 5       # 每个簇最终选取的条数
    outlier_ratio: float = 0.3         # 每个簇中保留"边缘观点"的比例 (0.3 表示 30% 是边缘观点)
    random_seed: int = 42

@lru_cache()
def get_sampler_config() -> SamplerConfig:
    """全局唯一采样器配置实例"""
    return SamplerConfig()


DefaultSamplerConfig = get_sampler_config()  # 确保配置已加载