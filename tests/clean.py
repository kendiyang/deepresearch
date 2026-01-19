"""
PostgreSQL tasks表清理脚本
- 重置长时间未更新的PROCESSING任务为PENDING
- 删除重试次数超限的FAILED任务
- 可选：删除7天前COMPLETED任务
"""
import asyncio
import asyncpg
from datetime import datetime, timedelta

# === 配置 ===
PG_DSN = "postgresql://refly:test@localhost:35432/refly"
PROCESSING_TIMEOUT_MINUTES = 30
FAILED_RETRY_LIMIT = 3
CLEANUP_COMPLETED_DAYS = 7  # 可选

async def cleanup_tasks():
    pool = await asyncpg.create_pool(dsn=PG_DSN)
    async with pool.acquire() as conn:
        # 1. 重置卡住的PROCESSING任务
        processing_sql = f"""
        UPDATE tasks
        SET status = 'PENDING', updated_at = NOW()
        WHERE status = 'PROCESSING'
          AND updated_at < NOW() - INTERVAL '{PROCESSING_TIMEOUT_MINUTES} minutes';
        """
        res1 = await conn.execute(processing_sql)
        print(f"[PROCESSING->PENDING] {res1}")

        # 2. 删除重试次数超限的FAILED任务
        failed_sql = f"""
        DELETE FROM tasks
        WHERE status = 'FAILED'
          AND retry_count >= {FAILED_RETRY_LIMIT};
        """
        res2 = await conn.execute(failed_sql)
        print(f"[DELETE FAILED] {res2}")

        # 3. 可选：删除已完成的历史任务
        completed_sql = f"""
        DELETE FROM tasks
        WHERE status = 'COMPLETED'
          AND updated_at < NOW() - INTERVAL '{CLEANUP_COMPLETED_DAYS} days';
        """
        res3 = await conn.execute(completed_sql)
        print(f"[DELETE COMPLETED] {res3}")
        drop_table = f"""
        DROP TABLE tasks;
        """

        res3 = await conn.execute(drop_table)
    await pool.close()

if __name__ == "__main__":
    asyncio.run(cleanup_tasks())

