import asyncio
import os
import json
import logging
import random
import time
import signal
from datetime import datetime
from typing import List, Dict, Any, Optional

# 第三方库
import redis.asyncio as redis
from dotenv import load_dotenv
from steel import Steel
from playwright.async_api import async_playwright, Browser, Page

# 加载 .env 文件
load_dotenv()

# --- 1. 全局配置 ---

class AppConfig:
    # Redis 配置
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_QUEUE_KEY = os.getenv("REDIS_QUEUE_KEY", "reddit_scraper_queue")
    REDIS_ERROR_QUEUE = os.getenv("REDIS_ERROR_QUEUE", "reddit_scraper_errors")
    
    # Steel & Proxy 配置
    STEEL_URL = os.getenv("STEEL_API_URL", "http://1.208.108.242:30738")
       # 代理池解析
    _proxy_str = os.getenv("PROXY_POOL", "http://5gye4972-region-US:7fsccucj@us.novproxy.io:443,http://5gye4972-region-US:7fsccucj@hk.novproxy.io:443,http://5gye4972-region-US:7fsccucj@us.novproxy.io:1000,http://5gye4972-region-US:7fsccucj@hk.novproxy.io:1000")

    PROXY_POOL = [p.strip() for p in _proxy_str.split(",") if p.strip()]
    
    # 兼容回退
    if not PROXY_POOL and os.getenv("PROXY_URL"):
        PROXY_POOL = [os.getenv("PROXY_URL")]
    
    # 并发控制
    CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "1"))
    
    # 重试策略
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    # 输出目录
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "data_output")

# 确保输出目录存在
os.makedirs(AppConfig.OUTPUT_DIR, exist_ok=True)

# 配置结构化日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(AppConfig.OUTPUT_DIR, "scraper.log"), encoding='utf-8')
    ]
)
logger = logging.getLogger("WorkerPool")

# --- 2. 核心逻辑工具 (保持不变) ---

def extract_comments_recursively(children_list: List[Dict], parent_id: str, depth: int = 0) -> List[Dict]:
    extracted_data = []
    for child in children_list:
        if child.get('kind') != 't1':
            continue
        data = child.get('data', {})
        extracted_data.append({
            "id": data.get("id"),
            "parent_id": parent_id,
            "author": data.get("author"),
            "body": data.get("body"),
            "score": data.get("score"),
            "created_utc": data.get("created_utc"),
            "depth": depth,
            "permalink": data.get("permalink")
        })
        replies = data.get("replies")
        if isinstance(replies, dict) and replies.get("data"):
            extracted_data.extend(extract_comments_recursively(
                replies['data']['children'], data.get("id"), depth + 1
            ))
    return extracted_data

# --- 3. 异步 Worker 类 ---

class ScraperWorker:
    def __init__(self, worker_id: int, semaphore: asyncio.Semaphore, redis_client: redis.Redis):
        self.worker_id = worker_id
        self.semaphore = semaphore
        self.redis = redis_client
        self.steel_client = Steel(base_url=AppConfig.STEEL_URL)

    async def _execute_crawl(self, url: str) -> Optional[Dict]:
        """
        单次完整的抓取尝试：创建会话 -> 连接 -> 抓取 -> 关闭
        如果不抛出异常则视为成功，返回数据
        """
        session = None
        browser = None
        try:
            # 1. 创建新会话 (每次尝试都是全新的环境)
            session = self.steel_client.sessions.create(
                use_proxy={"server": random.choice(AppConfig.PROXY_POOL)},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                dimensions={"width": 1920, "height": 1080}
            )
            
            # 2. 连接 Playwright
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(session.websocket_url)
                # 使用自带上下文，避免额外创建造成不稳定
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()

                # 3. 导航与抓取
                # 使用 commit 确保最快拿到响应流
                response = await page.goto(url, wait_until="commit", timeout=45000)
                
                if not response:
                    raise Exception("未获取到响应对象")
                
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    raise Exception("RateLimit 429") # 抛出异常以触发外层重试
                elif response.status == 404:
                    return {"error": "404 Not Found"} # 404 不需要重试，直接返回特殊标记
                else:
                    raise Exception(f"HTTP Status {response.status}")

        finally:
            # 确保每次尝试结束都彻底清理资源
            if browser:
                try: await browser.close()
                except: pass
            if session:
                try: self.steel_client.sessions.release(session.id)
                except: pass

    async def process_task(self, task_payload: Dict[str, str]):
        topic_id = task_payload.get("topic_id")
        url = task_payload.get("url")
        
        # 信号量控制并发数
        async with self.semaphore:
            logger.info(f"[Worker-{self.worker_id}] 开始处理 Topic: {topic_id}")
            
            # --- 重试循环 (Retry Loop) ---
            for attempt in range(1, AppConfig.MAX_RETRIES + 1):
                try:
                    # 执行完整的抓取流程
                    json_data = await self._execute_crawl(url)
                    
                    # 处理 404 的情况
                    if json_data and "error" in json_data and json_data["error"] == "404 Not Found":
                        logger.error(f"[Worker-{self.worker_id}] ❌ 页面不存在 (放弃重试): {url}")
                        return # 结束任务，不报错

                    if not json_data:
                        raise Exception("返回数据为空")

                    # --- 数据解析逻辑 (保持不变) ---
                    post_raw = json_data[0]['data']['children'][0]['data']
                    post_id = post_raw.get('id')
                    raw_comments = json_data[1]['data']['children']
                    all_comments = extract_comments_recursively(raw_comments, parent_id=post_id)

                    final_data = {
                        "topic_id": topic_id,
                        "source_url": url,
                        "reddit_post_id": post_id,
                        "title": post_raw.get("title"),
                        "author": post_raw.get("author"),
                        "selftext": post_raw.get("selftext"),
                        "crawled_at": datetime.now().isoformat(),
                        "comments_count": len(all_comments),
                        "comments": all_comments
                    }

                    # 写入文件
                    filename = os.path.join(AppConfig.OUTPUT_DIR, f"{topic_id}.json")
                    json_line = json.dumps(final_data, ensure_ascii=False) + "\n"
                    with open(filename, "a", encoding="utf-8") as f:
                        f.write(json_line)
                    
                    logger.info(f"[Worker-{self.worker_id}] ✅ 写入成功 | 尝试次数: {attempt} | 评论数: {len(all_comments)}")
                    return # 成功！退出重试循环

                except Exception as e:
                    is_last_attempt = attempt == AppConfig.MAX_RETRIES
                    log_level = logging.ERROR if is_last_attempt else logging.WARNING
                    logger.log(log_level, f"[Worker-{self.worker_id}] ⚠️ 尝试 ({attempt}/{AppConfig.MAX_RETRIES}) 失败: {e}")
                    
                    if not is_last_attempt:
                        # 指数退避: 第1次歇5秒，第2次歇10秒...
                        wait_time = AppConfig.RETRY_DELAY * attempt
                        await asyncio.sleep(wait_time)
                    else:
                        # 最后一次尝试也失败，推入错误队列
                        logger.error(f"[Worker-{self.worker_id}] ❌ 任务最终失败，推入错误队列")
                        await self.redis.lpush(AppConfig.REDIS_ERROR_QUEUE, json.dumps(task_payload))

async def main():
    logger.info(f"🚀 启动爬虫集群 | 监听队列: {AppConfig.REDIS_QUEUE_KEY}")
    
    redis_client = redis.from_url(AppConfig.REDIS_URL, decode_responses=True)
    semaphore = asyncio.Semaphore(AppConfig.CONCURRENCY_LIMIT)
    
    tasks = []
    worker_counter = 0

    try:
        while True:
            # 1. 阻塞式获取任务
            item = await redis_client.blpop(AppConfig.REDIS_QUEUE_KEY, timeout=5)
            
            if not item:
                # 清理已完成的任务
                tasks = [t for t in tasks if not t.done()]
                if not tasks:
                    # 仅在完全空闲时打印，避免日志刷屏
                    pass 
                continue
            
            # blpop 返回 (key, value)
            _, raw_payload = item
            
            # 2. 解析 Redis JSON 数据
            try:
                task_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                logger.error(f"❌ Redis 数据格式非 JSON: {raw_payload}")
                continue

            # 3. 分发任务
            worker_counter += 1
            worker = ScraperWorker(worker_id=worker_counter, semaphore=semaphore, redis_client=redis_client)
            
            task = asyncio.create_task(worker.process_task(task_payload))
            tasks.append(task)
            
            # 简单维护任务列表防止无限增长
            if len(tasks) > 200:
                tasks = [t for t in tasks if not t.done()]

    except asyncio.CancelledError:
        logger.info("🛑 正在停止服务...")
    except Exception as e:
        logger.critical(f"🔥 主进程崩溃: {e}", exc_info=True)
    finally:
        if tasks:
            logger.info("等待剩余任务完成...")
            await asyncio.gather(*tasks, return_exceptions=True)
        await redis_client.close()
        logger.info("服务已关闭")

# --- 5. 入口 ---

if __name__ == "__main__":
    try:
        # Windows 兼容性处理
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 信号处理 (优雅退出)
        if os.name != 'nt':
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: [t.cancel() for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass