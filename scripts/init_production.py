#!/usr/bin/env python3
"""
生产环境初始化脚本：智能创建索引
- 元数据索引：立即创建（platform/date/score）
- 向量索引：仅在数据充足时创建（可选）
"""
import os
import sys
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def get_connection():
    """获取数据库连接"""
    conn_str = os.getenv('PGVECTOR_URL') or os.getenv('DATABASE_URL')
    if not conn_str:
        raise RuntimeError("缺少 PGVECTOR_URL / DATABASE_URL 环境变量")
    
    if conn_str.startswith("postgres://"):
        conn_str = conn_str.replace("postgres://", "postgresql+psycopg2://", 1)
    
    return create_engine(conn_str)


def ensure_extension(engine):
    """确保 pgvector 扩展已安装"""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("✅ pgvector 扩展已就绪")
    except Exception as e:
        logger.warning(f"创建扩展失败（可能无权限）：{e}")


def create_metadata_indexes(engine):
    """创建元数据索引（安全，可在空表执行）"""
    # 先检查表是否存在
    try:
        with engine.connect() as conn:
            table_exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'langchain_pg_embedding'
                )
            """)).fetchone()[0]
            
            if not table_exists:
                logger.warning("⚠️  表尚未创建，跳过元数据索引")
                logger.info("  💡 应用首次写入数据时会自动创建表")
                return 0
    except Exception as e:
        logger.warning(f"检查表失败: {e}")
        return 0
    
    indexes = [
        ("platform", "CREATE INDEX IF NOT EXISTS idx_embedding_meta_platform ON langchain_pg_embedding ((cmetadata->>'platform'))"),
        ("date_function", """
            CREATE OR REPLACE FUNCTION cmetadata_date_immutable(jsonb)
            RETURNS timestamptz AS $$
              SELECT to_timestamp(($1->>'date'),'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            $$ LANGUAGE SQL IMMUTABLE
        """),
        ("date", "CREATE INDEX IF NOT EXISTS idx_embedding_meta_date ON langchain_pg_embedding (cmetadata_date_immutable(cmetadata))"),
        ("score", "CREATE INDEX IF NOT EXISTS idx_embedding_meta_score ON langchain_pg_embedding (((cmetadata->>'score')::float))"),
    ]
    
    created = 0
    for name, sql in indexes:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
            logger.info(f"✅ 元数据索引已创建/已存在: {name}")
            created += 1
        except Exception as e:
            logger.error(f"❌ 创建 {name} 失败: {e}")
    
    return created


def check_vector_index_readiness(engine):
    """检查是否可以创建向量索引"""
    try:
        with engine.connect() as conn:
            # 检查表是否存在
            table_exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'langchain_pg_embedding'
                )
            """)).fetchone()[0]
            
            if not table_exists:
                return False, "表不存在", 0
            
            # 检查数据量
            count = conn.execute(text("SELECT COUNT(*) FROM langchain_pg_embedding")).fetchone()[0]
            
            if count == 0:
                return False, "表为空", 0
            elif count < 100:
                return False, f"数据不足（{count} < 100）", count
            else:
                return True, f"数据充足（{count}条）", count
    except Exception as e:
        return False, f"检查失败: {e}", 0


def create_vector_index(engine, count):
    """创建向量索引（仅在数据充足时）"""
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
        logger.info(f"✅ 向量索引已创建（lists={lists}）")
        return True
    except Exception as e:
        logger.error(f"❌ 向量索引创建失败: {e}")
        return False


def analyze_table(engine):
    """更新统计信息"""
    try:
        with engine.begin() as conn:
            conn.execute(text("ANALYZE langchain_pg_embedding"))
        logger.info("✅ 统计信息已更新")
    except Exception as e:
        logger.warning(f"ANALYZE 失败: {e}")


def main():
    """
    生产环境初始化流程：
    1. 确保扩展
    2. 创建元数据索引（必须）
    3. 检查数据量
    4. 条件创建向量索引（可选）
    """
    logger.info("=" * 60)
    logger.info("生产环境数据库初始化")
    logger.info("=" * 60)
    
    engine = get_connection()
    
    # 步骤1：扩展
    logger.info("\n[1/4] 检查 pgvector 扩展...")
    ensure_extension(engine)
    
    # 步骤2：元数据索引（必须，安全）
    logger.info("\n[2/4] 创建元数据索引...")
    meta_count = create_metadata_indexes(engine)
    
    # 步骤3：检查向量索引条件
    logger.info("\n[3/4] 检查向量索引创建条件...")
    can_create, reason, count = check_vector_index_readiness(engine)
    logger.info(f"  状态: {reason}")
    
    if can_create:
        logger.info("\n[4/4] 创建向量索引...")
        if create_vector_index(engine, count):
            analyze_table(engine)
    else:
        logger.info("\n[4/4] 跳过向量索引（条件不满足）")
        logger.info("  💡 提示：首次数据入库后，运行以下命令创建向量索引：")
        logger.info("     python scripts/create_vector_index.py")
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 初始化完成")
    logger.info("=" * 60)
    
    if not can_create:
        logger.info("\n后续步骤：")
        logger.info("  1. 启动应用并入库数据")
        logger.info("  2. 数据量达到100+后，执行: python scripts/create_vector_index.py")
        logger.info("  3. 或重新运行本脚本")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        sys.exit(1)
