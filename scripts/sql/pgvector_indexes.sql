-- pgvector 索引与元数据过滤索引
-- 注意：请确保已安装 pgvector 扩展，并与应用中的距离度量保持一致（cosine/IP/L2）

-- 1) 向量索引（IVFFLAT for cosine）
-- 要求：表中至少有 lists*10 条数据（此处 100*10=1000 条）
-- 若表为空或数据不足，索引创建会失败，建议在有数据后再执行
-- lists 可按数据规模调大（10k~1M+ 建议 100~1000+）
-- 注释掉默认索引，请在有足够数据后手动创建或取消注释
-- CREATE INDEX IF NOT EXISTS idx_langchain_embedding_cos_ivfflat
-- ON langchain_pg_embedding
-- USING ivfflat (embedding vector_cosine_ops)
-- WITH (lists = 100);

-- 可选：HNSW（若 pgvector 版本支持；通常二选一）
-- CREATE INDEX IF NOT EXISTS idx_langchain_embedding_cos_hnsw
-- ON langchain_pg_embedding
-- USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 200);

-- 2) 元数据表达式索引（加速常用过滤）

-- 2.1) platform 索引（简单 jsonb 提取）
CREATE INDEX IF NOT EXISTS idx_embedding_meta_platform
ON langchain_pg_embedding ((cmetadata->>'platform'));

-- 2.2) 日期索引需IMMUTABLE函数，先创建辅助函数
CREATE OR REPLACE FUNCTION cmetadata_date_immutable(jsonb)
RETURNS timestamptz AS $$
  SELECT to_timestamp(($1->>'date'),'YYYY-MM-DD"T"HH24:MI:SS"Z"')
$$ LANGUAGE SQL IMMUTABLE;

-- 然后创建日期索引
CREATE INDEX IF NOT EXISTS idx_embedding_meta_date
ON langchain_pg_embedding (cmetadata_date_immutable(cmetadata));

-- 2.3) score 索引（float 转换）
CREATE INDEX IF NOT EXISTS idx_embedding_meta_score
ON langchain_pg_embedding (((cmetadata->>'score')::float));

-- 更新统计信息
ANALYZE langchain_pg_embedding;
