import redis
import json
import uuid
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CrawlerTaskProducer:
    def __init__(self, redis_url='redis://localhost:6379/0'):
        self.client = redis.from_url(redis_url)
        self.main_queue = 'crawler:tasks'

    def create_task(self, url, label, user_data=None):
        """构造符合 Node.js 消费端解析规范的任务结构"""
        task = {
            "url": url,
            "label": label,
            "userData": user_data or {},
            # 预计算 uniqueKey 减少消费端哈希开销
            "uniqueKey": str(uuid.uuid5(uuid.NAMESPACE_URL, url))
        }
        return task

    def publish_tasks(self, tasks):
        """使用 Pipeline 批量推送任务"""
        pipe = self.client.pipeline()
        for task in tasks:
            pipe.lpush(self.main_queue, json.dumps(task))
        
        results = pipe.execute()
        logger.info(f"✅ 成功推送 {len(results)} 个测试任务到队列")

def main():
    producer = CrawlerTaskProducer()

    # 定义不同平台的初始化示例
    test_tasks = [
        # --- Reddit 测试用例 ---
        producer.create_task(
            url="https://www.reddit.com/r/DIYfragrance/comments/1oj09hp/recommendations_for_incense_fragrance",
            label="REDDIT",
            user_data={"priority": "high", "source": "python_manager"}
        ),
    ]

    producer.publish_tasks(test_tasks)
    
    # 检查队列当前长度
    q_len = producer.client.llen(producer.main_queue)
    logger.info(f"🚀 当前 Redis 队列任务总数: {q_len}")

if __name__ == "__main__":
    main()