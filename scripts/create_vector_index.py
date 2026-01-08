#!/usr/bin/env python3
"""
创建向量索引（IVFFLAT）- 需要表中已有足够数据
"""
import os
import sys
from sqlalchemy import create_engine, text

def main():
    conn_str = os.getenv('PGVECTOR_URL') or os.getenv('DATABASE_URL')
    if not conn_str:
        print("错误：需要设置 PGVECTOR_URL 或 DATABASE_URL")
        sys.exit(1)
    
    if conn_str.startswith("postgres://"):
        conn_str = conn_str.replace("postgres://", "postgresql+psycopg2://", 1)
    
    engine = create_engine(conn_str)
    
    print("[1/3] 检查数据量...")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM langchain_pg_embedding")).fetchone()
        count = result[0]
        print(f"  表中有 {count} 条记录")
        
        if count < 100:
            print(f"警告：数据量不足（{count} < 100），索引可能创建失败")
            print("建议先运行: python scripts/generate_test_data.py")
            return
    
    print("[2/3] 创建向量索引...")
    # lists 参数：10~100 适合 1k~100k 数据；100~1000 适合更大数据量
    lists = min(100, max(10, count // 100))
    
    sql = f"""
    CREATE INDEX IF NOT EXISTS idx_langchain_embedding_cos_ivfflat
    ON langchain_pg_embedding
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = {lists})
    """
    
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
        print(f"  ✅ 向量索引创建成功（lists={lists}）")
    except Exception as e:
        print(f"  ❌ 索引创建失败: {e}")
        return
    
    print("[3/3] 更新统计信息...")
    with engine.begin() as conn:
        conn.execute(text("ANALYZE langchain_pg_embedding"))
    
    print("✅ 完成！向量索引已就绪")
    print(f"\n查询调优建议（在查询前执行）：")
    print(f"  SET LOCAL ivfflat.probes = {max(1, lists // 10)};  -- 默认1，越大越准越慢")


if __name__ == "__main__":
    main()
