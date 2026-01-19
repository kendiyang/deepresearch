import asyncio
import os
import json
import logging
import signal
import random  # 新增：用于生成随机延迟
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

# 第三方库
import redis.asyncio as redis
from dotenv import load_dotenv
from steel import Steel
from playwright.async_api import async_playwright, Playwright, Browser, Page

load_dotenv()

# ==========================================
# 1. 基础配置
# ==========================================
class AppConfig:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_QUEUE_KEY = os.getenv("REDIS_QUEUE_KEY", "reddit_scraper_queue")
    REDIS_ERROR_QUEUE = os.getenv("REDIS_ERROR_QUEUE", "reddit_scraper_errors")
    
    STEEL_URL = os.getenv("STEEL_API_URL", "http://1.208.108.242:30445")
    PROXY_URL = os.getenv("PROXY_URL", "http://5gye4972-region-US:7fsccucj@hk.novproxy.io:443")
    
    # 建议根据 Steel 服务器性能适当降低，如果是单机部署，建议 3-5
    CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "5"))
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    TASK_TIMEOUT = 120
    
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "data_output")

os.makedirs(AppConfig.OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(AppConfig.OUTPUT_DIR, "scraper.log"), encoding='utf-8')
    ]
)
logger = logging.getLogger("ScraperCluster")
STOP_EVENT = asyncio.Event()

# ==========================================
# 2. 核心逻辑工具 (防御性增强版)
# ==========================================
def extract_comments_recursively(children_list: List[Any], parent_id: str, depth: int = 0) -> List[Dict]:
    """
    递归提取评论数据 (增加类型检查，防止 'list' has no attribute 'get' 错误)
    """
    extracted_data = []
    
    # 【防御性编程】确保输入是列表
    if not isinstance(children_list, list):
        return []

    for child in children_list:
        # 【防御性编程】Reddit 有时会在 children 里塞入非字典数据，必须过滤
        if not isinstance(child, dict):
            continue

        if child.get('kind') != 't1':
            continue
            
        data = child.get('data', {})
        if not isinstance(data, dict):
            continue

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
        # Reddit 的 replies 可能是空字符串 ""，也可能是字典
        if isinstance(replies, dict) and replies.get("data"):
            # 确保下一层 children 也是列表
            next_children = replies['data'].get('children')
            if isinstance(next_children, list):
                extracted_data.extend(extract_comments_recursively(
                    next_children, data.get("id"), depth + 1
                ))
    return extracted_data

# ==========================================
# 3. Worker 类
# ==========================================
class ScraperWorker:
    def __init__(self, worker_id: int, semaphore: asyncio.Semaphore, redis_client: redis.Redis, playwright: Playwright, steel_client: Steel):
        self.worker_id = worker_id
        self.semaphore = semaphore
        self.redis = redis_client
        self.playwright = playwright
        self.steel_client = steel_client

    async def _safe_create_session(self) -> Any:
        return await asyncio.to_thread(
            self.steel_client.sessions.create,
            use_proxy={"server": AppConfig.PROXY_URL},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            dimensions={"width": 1920, "height": 1080},
            timeout=45000 # 增加超时时间以应对并发拥堵
        )

    async def _safe_release_session(self, session_id: str):
        try:
            await asyncio.to_thread(self.steel_client.sessions.release, session_id)
        except Exception:
            pass

    async def _safe_write_file(self, filename: str, data: Dict):
        def write_sync():
            json_line = json.dumps(data, ensure_ascii=False) + "\n"
            with open(filename, "a", encoding="utf-8") as f:
                f.write(json_line)
        await asyncio.to_thread(write_sync)

    async def _execute_crawl(self, url: str) -> Optional[Dict]:
        session = None
        browser = None
        try:
            # 创建会话
            session = await self._safe_create_session()
            
            # 连接 CDP
            browser = await self.playwright.chromium.connect_over_cdp(session.websocket_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # 抓取
            response = await page.goto(url, wait_until="commit", timeout=60000)
            
            if not response: raise Exception("Empty Response")
            
            if response.status == 200:
                return await response.json()
            elif response.status == 429:
                raise Exception("RateLimit 429")
            elif response.status == 403:
                # 403 通常是 Reddit 拒绝了该 IP 或请求指纹
                return {"_error_": "403 Forbidden"} 
            elif response.status == 404:
                return {"_error_": "404 Not Found"}
            else:
                raise Exception(f"HTTP Status {response.status}")

        finally:
            if browser:
                try: await browser.close()
                except: pass
            if session:
                await self._safe_release_session(session.id)

    async def process_task(self, task_payload: Dict[str, str]):
        topic_id = task_payload.get("topic_id")
        url = task_payload.get("url")
        
        # 【重要优化】启动抖动 (Jitter)
        # 防止所有 Worker 同时发起 Session 创建请求，打挂 Steel Server
        # 每个 Worker 随机等待 0.1 到 2.0 秒
        await asyncio.sleep(random.uniform(0.1, 2.0))

        async with self.semaphore:
            logger.info(f"[Worker-{self.worker_id}] 开始处理: {topic_id}")
            
            for attempt in range(1, AppConfig.MAX_RETRIES + 1):
                try:
                    json_data = await asyncio.wait_for(
                        self._execute_crawl(url), 
                        timeout=AppConfig.TASK_TIMEOUT
                    )
                    
                    # 处理业务错误
                    if json_data and isinstance(json_data, dict) and "_error_" in json_data:
                        err_msg = json_data["_error_"]
                        logger.error(f"[Worker-{self.worker_id}] ❌ 业务失败 ({err_msg}): {url}")
                        return # 这种错误重试通常无效，直接放弃

                    if not json_data: raise Exception("No Data")

                    # 【防御性解析】
                    # Reddit 正常返回是一个列表: [PostData, CommentsData]
                    if not isinstance(json_data, list) or len(json_data) < 2:
                        raise Exception(f"Unexpected JSON Structure: Not a list or len < 2")

                    # 解析帖子
                    post_container = json_data[0].get('data', {}).get('children', [])
                    if not post_container or not isinstance(post_container, list):
                         raise Exception("Invalid Post Structure")
                    
                    post_raw = post_container[0].get('data', {})
                    post_id = post_raw.get('id', 'unknown')
                    
                    # 解析评论
                    comments_container = json_data[1].get('data', {}).get('children', [])
                    all_comments = extract_comments_recursively(comments_container, parent_id=post_id)

                    final_data = {
                        "topic_id": topic_id,
                        "source_url": url,
                        "reddit_post_id": post_id,
                        "title": post_raw.get("title"),
                        "crawled_at": datetime.now().isoformat(),
                        "comments_count": len(all_comments),
                        "comments": all_comments
                    }

                    filename = os.path.join(AppConfig.OUTPUT_DIR, f"{topic_id}.json")
                    await self._safe_write_file(filename, final_data)
                    
                    logger.info(f"[Worker-{self.worker_id}] ✅ 完成 | 尝试: {attempt} | 评论: {len(all_comments)}")
                    return 

                except Exception as e:
                    is_last = attempt == AppConfig.MAX_RETRIES
                    if is_last:
                        logger.error(f"[Worker-{self.worker_id}] ❌ 最终失败: {e}")
                        await self.redis.lpush(AppConfig.REDIS_ERROR_QUEUE, json.dumps(task_payload))
                    else:
                        logger.warning(f"[Worker-{self.worker_id}] ⚠️ 失败重试: {e}")
                        # 指数退避 + 随机抖动
                        sleep_time = (AppConfig.RETRY_DELAY * attempt) + random.uniform(0, 2)
                        await asyncio.sleep(sleep_time)

# ==========================================
# 4. 主程序
# ==========================================
async def main():
    logger.info(f"🚀 集群启动 | Redis: {AppConfig.REDIS_URL} | 并发: {AppConfig.CONCURRENCY_LIMIT}")
    
    redis_client = redis.from_url(AppConfig.REDIS_URL, decode_responses=True)
    semaphore = asyncio.Semaphore(AppConfig.CONCURRENCY_LIMIT)
    steel_client = Steel(base_url=AppConfig.STEEL_URL)
    
    async with async_playwright() as global_playwright:
        logger.info("✅ 资源就绪")
        
        tasks = []
        worker_id_counter = 0

        while not STOP_EVENT.is_set():
            try:
                try:
                    item = await asyncio.wait_for(
                        redis_client.blpop(AppConfig.REDIS_QUEUE_KEY, timeout=5),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    tasks = [t for t in tasks if not t.done()]
                    continue

                if not item: continue
                
                _, raw_payload = item
                try:
                    task_payload = json.loads(raw_payload)
                    worker_id_counter += 1
                    
                    worker = ScraperWorker(
                        worker_id_counter, 
                        semaphore, 
                        redis_client, 
                        global_playwright, 
                        steel_client
                    )
                    
                    task = asyncio.create_task(worker.process_task(task_payload))
                    tasks.append(task)

                except json.JSONDecodeError:
                    logger.error(f"脏数据丢弃: {raw_payload}")

            except Exception as e:
                if not STOP_EVENT.is_set():
                    logger.error(f"Loop Error: {e}")
                    await asyncio.sleep(1)

        logger.info("🛑 正在优雅停机...")
        pending = [t for t in tasks if not t.done()]
        if pending:
            logger.info(f"⏳ 等待 {len(pending)} 个任务收尾...")
            await asyncio.wait(pending, timeout=30)
        
        await redis_client.aclose()
        logger.info("👋 Bye!")

def handle_sigint(loop):
    STOP_EVENT.set()

if __name__ == "__main__":
    try:
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if os.name != 'nt':
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: handle_sigint(loop))
        loop.run_until_complete(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass